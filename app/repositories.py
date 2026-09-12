from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Job, LoginSession, User


async def user_by_email(db: AsyncSession, email: str) -> User | None:
    return await db.scalar(select(User).where(User.email == email))


async def login_session(db: AsyncSession, session_id: str, lock: bool = False):
    statement = select(LoginSession).where(LoginSession.id == session_id)
    if lock:
        statement = statement.with_for_update()
    return await db.scalar(statement)


async def job_by_key(db: AsyncSession, user_id: str, key: str) -> Job | None:
    return await db.scalar(select(Job).where(Job.user_id == user_id, Job.idempotency_key == key))


async def owned_job(db: AsyncSession, user_id: str, job_id: str) -> Job | None:
    return await db.scalar(select(Job).where(Job.user_id == user_id, Job.id == job_id))


async def list_jobs(db: AsyncSession, user_id: str, limit: int, offset: int):
    return (
        await db.scalars(
            select(Job)
            .where(Job.user_id == user_id)
            .order_by(Job.created_at.desc(), Job.id)
            .limit(limit)
            .offset(offset)
        )
    ).all()
