import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.config import get_settings
from app.models import Job
from app.security import decode_token, encode_token
from app.worker import process_job

CREDS = {"email": "alice@example.com", "password": "a-strong-password-123"}


async def signup(client, email="alice@example.com"):
    credentials = {**CREDS, "email": email}
    assert (await client.post("/api/v1/auth/register", json=credentials)).status_code == 201
    response = await client.post("/api/v1/auth/login", json=credentials)
    assert response.status_code == 200
    return response.json()


def auth(tokens):
    return {"Authorization": "Bearer " + tokens["access_token"]}


async def test_register_login_validation(client):
    tokens = await signup(client)
    me = await client.get("/api/v1/users/me", headers=auth(tokens))
    assert me.status_code == 200 and me.json()["email"] == CREDS["email"]
    assert "password" not in me.text
    duplicate = await client.post(
        "/api/v1/auth/register", json={**CREDS, "email": "ALICE@example.com"}
    )
    assert duplicate.status_code == 409
    wrong = await client.post(
        "/api/v1/auth/login", json={**CREDS, "password": "incorrect-password"}
    )
    assert wrong.status_code == 401
    invalid = await client.post("/api/v1/auth/register", json={**CREDS, "password": "é" * 40})
    assert invalid.status_code == 422
    assert "é" * 40 not in invalid.text
    assert invalid.headers["x-request-id"]


async def test_refresh_rotation_reuse_revokes_session(client):
    original = await signup(client)
    refreshed = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": original["refresh_token"]}
    )
    assert refreshed.status_code == 200
    tokens = refreshed.json()
    assert tokens["refresh_token"] != original["refresh_token"]
    assert (await client.get("/api/v1/users/me", headers=auth(tokens))).status_code == 200
    assert (
        await client.post("/api/v1/auth/refresh", json={"refresh_token": original["refresh_token"]})
    ).status_code == 401
    assert (await client.get("/api/v1/users/me", headers=auth(tokens))).status_code == 401


async def test_logout_revokes_access_and_refresh(client):
    tokens = await signup(client)
    assert (await client.post("/api/v1/auth/logout", headers=auth(tokens))).status_code == 204
    assert (await client.get("/api/v1/users/me", headers=auth(tokens))).status_code == 401
    assert (
        await client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    ).status_code == 401


async def test_token_types_expiry_and_signature(client):
    tokens = await signup(client)
    claims = decode_token(tokens["access_token"], "access")
    expired = encode_token(
        claims["sub"], claims["sid"], "access", datetime.now(UTC) - timedelta(seconds=1)
    )
    for token in (expired, tokens["refresh_token"], tokens["access_token"] + "bad", "garbage"):
        assert (
            await client.get("/api/v1/users/me", headers={"Authorization": "Bearer " + token})
        ).status_code == 401
    assert (await client.get("/api/v1/users/me")).status_code == 401
    assert (
        await client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["access_token"]})
    ).status_code == 401


async def test_jobs_idempotency_and_ownership(client):
    alice = await signup(client)
    headers = {**auth(alice), "Idempotency-Key": "report-1"}
    response = await client.post(
        "/api/v1/jobs", json={"text": "hello hello world"}, headers=headers
    )
    assert response.status_code == 202
    job = response.json()
    assert job["status"] == "queued"
    repeat = await client.post("/api/v1/jobs", json={"text": "hello hello world"}, headers=headers)
    assert repeat.json()["id"] == job["id"]
    assert (
        await client.post("/api/v1/jobs", json={"text": "different"}, headers=headers)
    ).status_code == 409
    bob = await signup(client, "bob@example.com")
    assert (await client.get("/api/v1/jobs/" + job["id"], headers=auth(bob))).status_code == 404
    assert (await client.get("/api/v1/jobs", headers=auth(bob))).json() == []
    assert len((await client.get("/api/v1/jobs", headers=auth(alice))).json()) == 1
    assert (await client.get("/api/v1/jobs?limit=101", headers=auth(alice))).status_code == 422


async def test_job_worker_repeat_is_safe(client, db_factory, monkeypatch):
    tokens = await signup(client)
    response = await client.post(
        "/api/v1/jobs",
        json={"text": "hello hello world"},
        headers={**auth(tokens), "Idempotency-Key": "worker"},
    )
    job_id = response.json()["id"]
    monkeypatch.setattr(
        get_settings(),
        "database_url",
        db_factory.kw["bind"].url.render_as_string(hide_password=False),
    )
    await process_job(job_id)
    await process_job(job_id)
    async with db_factory() as db:
        job = await db.get(Job, job_id)
        assert job.status == "completed"
        assert job.result["words"] == 3
        assert job.result["unique_words"] == 2
        assert job.completed_at is not None


async def test_postgres_concurrent_refresh(client, db_factory):
    if db_factory.kw["bind"].dialect.name != "postgresql":
        pytest.skip("Row locking requires PostgreSQL")
    tokens = await signup(client)
    replies = await asyncio.gather(
        *[
            client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
            for _ in range(2)
        ]
    )
    assert sorted(reply.status_code for reply in replies) == [200, 401]
    fresh = next(reply.json() for reply in replies if reply.status_code == 200)
    assert (await client.get("/api/v1/users/me", headers=auth(fresh))).status_code == 401
