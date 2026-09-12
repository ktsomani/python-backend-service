from unittest.mock import AsyncMock, patch

from redis.exceptions import ConnectionError

from app.api import rate_limit
from app.main import app


async def test_auth_rate_limit_and_redis_failure(client):
    app.dependency_overrides.pop(rate_limit)
    redis = AsyncMock()
    redis.__aenter__.return_value = redis
    with patch("app.api.Redis.from_url", return_value=redis):
        redis.eval.return_value = 21
        response = await client.post(
            "/api/v1/auth/login", json={"email": "x@example.com", "password": "long-password-value"}
        )
        assert response.status_code == 429
        assert response.headers["retry-after"] == "60"
        redis.eval.side_effect = ConnectionError("offline")
        response = await client.post(
            "/api/v1/auth/login", json={"email": "x@example.com", "password": "long-password-value"}
        )
        assert response.status_code == 503
