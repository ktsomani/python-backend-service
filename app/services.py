import hmac
from datetime import UTC, datetime, timedelta
from functools import lru_cache

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app import repositories as repo
from app.config import get_settings
from app.models import Job, LoginSession, User, new_id
from app.schemas import Credentials, TokenPair
from app.security import (
    access_token,
    decode_token,
    digest,
    encode_token,
    hash_password,
    unauthorized,
    verify_password,
)


@lru_cache
def dummy_hash() -> str:
    return hash_password("dummy-password-for-timing")


async def register(db: AsyncSession, data: Credentials) -> User:
    user = User(
        email=str(data.email), password_hash=await run_in_threadpool(hash_password, data.password)
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, "Email already registered") from exc
    return user


def pair(session: LoginSession) -> TokenPair:
    refresh = encode_token(session.user_id, session.id, "refresh", session.expires_at)
    session.refresh_hash = digest(refresh)
    return TokenPair(
        access_token=access_token(session.user_id, session.id),
        refresh_token=refresh,
        expires_in=get_settings().access_token_minutes * 60,
    )


async def login(db: AsyncSession, data: Credentials) -> TokenPair:
    user = await repo.user_by_email(db, str(data.email))
    hashed = user.password_hash if user else await run_in_threadpool(dummy_hash)
    valid = await run_in_threadpool(verify_password, data.password, hashed)
    if not user or not valid:
        raise unauthorized()
    session = LoginSession(
        id=new_id(),
        user_id=user.id,
        revoked=False,
        expires_at=datetime.now(UTC) + timedelta(days=get_settings().refresh_token_days),
    )
    tokens = pair(session)
    db.add(session)
    await db.commit()
    return tokens


def is_expired(value: datetime) -> bool:
    return value.replace(tzinfo=UTC) <= datetime.now(UTC)


async def refresh(db: AsyncSession, token: str) -> TokenPair:
    claims = decode_token(token, "refresh")
    session = await repo.login_session(db, claims["sid"], lock=True)
    if (
        not session
        or session.revoked
        or session.user_id != claims["sub"]
        or is_expired(session.expires_at)
    ):
        raise unauthorized()
    if not hmac.compare_digest(session.refresh_hash, digest(token)):
        # Reuse of a rotated token revokes the entire login session.
        session.revoked = True
        await db.commit()
        raise unauthorized()
    tokens = pair(session)
    await db.commit()
    return tokens


async def create_job(db: AsyncSession, user_id: str, text: str, key: str) -> Job:
    payload_hash = digest(text)
    existing = await repo.job_by_key(db, user_id, key)
    if existing:
        if existing.payload_hash != payload_hash:
            raise HTTPException(409, "Idempotency key already used with different content")
        return existing
    job = Job(user_id=user_id, text=text, idempotency_key=key, payload_hash=payload_hash)
    db.add(job)
    try:
        # The committed row is the durable dispatch intent. Beat rediscovers pending jobs.
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = await repo.job_by_key(db, user_id, key)
        if not existing:
            raise
        if existing.payload_hash != payload_hash:
            raise HTTPException(
                409, "Idempotency key already used with different content"
            ) from None
        return existing
    return job
