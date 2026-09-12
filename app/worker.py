import asyncio
import re
from collections import Counter

from celery import Celery
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.models import Job, now

celery_app = Celery("task_service", broker=get_settings().redis_url)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    task_ignore_result=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    broker_transport_options={"visibility_timeout": 300},
    task_soft_time_limit=45,
    task_time_limit=60,
    beat_schedule={"dispatch-pending": {"task": "jobs.dispatch", "schedule": 5.0}},
    timezone="UTC",
)


async def pending_jobs() -> list[str]:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine)() as db:
            return list(
                await db.scalars(
                    select(Job.id).where(Job.status == "queued").order_by(Job.created_at).limit(100)
                )
            )
    finally:
        await engine.dispose()


async def process_job(job_id: str) -> None:
    # Each Celery invocation owns its event loop and engine. Never reuse an async pool
    # across asyncio.run calls or prefork children.
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine)() as db, db.begin():
            job = await db.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if not job or job.status == "completed":
                return
            # Bounded, deterministic report. The lock + single commit make delivery
            # retries safe. External side effects would require their own idempotency.
            words = re.findall(r"\b\w+\b", job.text.lower())
            job.result = {
                "characters": len(job.text),
                "words": len(words),
                "unique_words": len(set(words)),
                "top_words": Counter(words).most_common(10),
            }
            job.status = "completed"
            job.completed_at = now()
    finally:
        await engine.dispose()


@celery_app.task(name="jobs.dispatch")
def dispatch():
    for job_id in asyncio.run(pending_jobs()):
        process.delay(job_id)


@celery_app.task(
    name="jobs.process",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def process(job_id: str):
    asyncio.run(process_job(job_id))
