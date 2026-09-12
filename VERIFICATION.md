# Verification performed

Verified locally on Windows with Python 3.11.5 on September 12, 2026:

- `pytest -q`: **9 passed, 1 skipped**. Covers registration, validation, credential rejection, refresh rotation and replay revocation, logout, token signature/type/expiry, job ownership and idempotency, repeat worker processing, auth rate-limit/error behavior, and production configuration constraints.
- The skipped test exercises concurrent refresh rotation using PostgreSQL row locks. SQLite cannot verify that guarantee.
- `ruff check .`: passed.
- `ruff format --check .`: all 19 Python files formatted.
- `pip check`: no broken requirements.
- Alembic upgrade, schema drift check, downgrade, and re-upgrade: passed against a disposable SQLite database.
- PostgreSQL migration SQL: generated successfully in offline mode.
- Docker Compose configuration: validated successfully with temporary environment values.
- FastAPI OpenAPI schema: generated successfully.

Docker CLI was present, but Docker Engine was unavailable. A container image build, live PostgreSQL/Redis checks, the PostgreSQL concurrency test, and the full Celery smoke test **were not executed locally**. GitHub Actions includes those checks; its workflow has not been run or deployed from this workspace. No server, registry credentials, GitHub repository, or production secrets were configured.

Follow `README.md` to start the stack and run `python scripts/smoke.py`. Review operational limitations there before deployment.
