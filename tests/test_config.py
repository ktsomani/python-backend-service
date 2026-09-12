import pytest
from pydantic import ValidationError

from app.config import Settings


def test_production_rejects_weak_hash_cost_and_sqlite():
    with pytest.raises(ValidationError, match="BCRYPT_ROUNDS"):
        Settings(environment="production", bcrypt_rounds=4)
    with pytest.raises(ValidationError, match="PostgreSQL"):
        Settings(environment="production", bcrypt_rounds=12, database_url="sqlite+aiosqlite://")


def test_signing_key_cannot_be_short():
    with pytest.raises(ValidationError):
        Settings(jwt_secret="short")
