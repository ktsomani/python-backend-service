from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from app import repositories as repo
from app import services
from app.config import get_settings
from app.db import get_db
from app.models import LoginSession, User
from app.schemas import Credentials, JobIn, JobOut, RefreshIn, TokenPair, UserOut
from app.security import decode_token, digest, unauthorized

router = APIRouter(prefix="/api/v1")
Db = Annotated[AsyncSession, Depends(get_db)]
bearer = HTTPBearer(auto_error=False)


async def rate_limit(request: Request):
    settings = get_settings()
    # Trust only the direct peer. Configure Uvicorn's proxy allowlist at deployment.
    peer = request.client.host if request.client else "unknown"
    key = "auth-rate:" + digest(peer)
    try:
        async with Redis.from_url(
            settings.redis_url, socket_timeout=2, socket_connect_timeout=2
        ) as redis:
            count = await redis.eval(
                "local n=redis.call('INCR',KEYS[1]); "
                "if n==1 then redis.call('EXPIRE',KEYS[1],60) end; return n",
                1,
                key,
            )
    except RedisError as exc:
        raise HTTPException(503, "Authentication temporarily unavailable") from exc
    if count > settings.auth_requests_per_minute:
        raise HTTPException(429, "Too many authentication requests", headers={"Retry-After": "60"})


async def current_session(
    db: Db, token: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]
):
    if not token:
        raise unauthorized()
    claims = decode_token(token.credentials, "access")
    session = await repo.login_session(db, claims["sid"])
    if (
        not session
        or session.revoked
        or session.user_id != claims["sub"]
        or services.is_expired(session.expires_at)
    ):
        raise unauthorized()
    return session


Session = Annotated[LoginSession, Depends(current_session)]
auth_limits = [Depends(rate_limit)]


@router.post("/auth/register", response_model=UserOut, status_code=201, dependencies=auth_limits)
async def register(data: Credentials, db: Db):
    return await services.register(db, data)


@router.post("/auth/login", response_model=TokenPair, dependencies=auth_limits)
async def login(data: Credentials, db: Db):
    return await services.login(db, data)


@router.post("/auth/refresh", response_model=TokenPair, dependencies=auth_limits)
async def refresh(data: RefreshIn, db: Db):
    return await services.refresh(db, data.refresh_token)


@router.post("/auth/logout", status_code=204)
async def logout(session: Session, db: Db):
    # Refresh and logout use the same lock; logout cannot be undone by rotation.
    locked = await repo.login_session(db, session.id, lock=True)
    locked.revoked = True
    await db.commit()
    return Response(status_code=204)


@router.get("/users/me", response_model=UserOut)
async def me(session: Session, db: Db):
    return await db.get(User, session.user_id)


@router.post("/jobs", response_model=JobOut, status_code=202)
async def create_job(
    data: JobIn,
    session: Session,
    db: Db,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=128)],
):
    return await services.create_job(db, session.user_id, data.text, idempotency_key)


@router.get("/jobs", response_model=list[JobOut])
async def list_jobs(
    session: Session,
    db: Db,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=10000)] = 0,
):
    return await repo.list_jobs(db, session.user_id, limit, offset)


@router.get("/jobs/{job_id}", response_model=JobOut)
async def get_job(job_id: UUID, session: Session, db: Db):
    job = await repo.owned_job(db, session.user_id, str(job_id))
    if not job:
        raise HTTPException(404, "Job not found")
    return job
