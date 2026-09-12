import hashlib
import uuid
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt
from fastapi import HTTPException

from app.config import get_settings


def unauthorized() -> HTTPException:
    return HTTPException(
        401, "Invalid or expired credentials", headers={"WWW-Authenticate": "Bearer"}
    )


def hash_password(password: str) -> str:
    return bcrypt.hashpw(
        password.encode(), bcrypt.gensalt(rounds=get_settings().bcrypt_rounds)
    ).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def encode_token(user_id: str, session_id: str, kind: str, expires: datetime) -> str:
    settings = get_settings()
    return jwt.encode(
        {
            "sub": user_id,
            "sid": session_id,
            "type": kind,
            "exp": expires,
            "iat": datetime.now(UTC),
            "jti": str(uuid.uuid4()),
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
        },
        settings.jwt_secret.get_secret_value(),
        algorithm="HS256",
    )


def decode_token(token: str, kind: str) -> dict:
    settings = get_settings()
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=["HS256"],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            options={"require": ["exp", "iat", "sub", "sid", "type", "jti", "iss", "aud"]},
        )
        if claims["type"] != kind:
            raise ValueError("Wrong token type")
        uuid.UUID(claims["sub"])
        uuid.UUID(claims["sid"])
        return claims
    except (jwt.InvalidTokenError, ValueError, TypeError, AttributeError) as exc:
        raise unauthorized() from exc


def access_token(user_id: str, session_id: str) -> str:
    return encode_token(
        user_id,
        session_id,
        "access",
        datetime.now(UTC) + timedelta(minutes=get_settings().access_token_minutes),
    )
