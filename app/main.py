import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import router
from app.config import get_settings
from app.db import engine

logger = logging.getLogger("task_service")
logging.basicConfig(level=logging.INFO, format="%(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await engine.dispose()


app = FastAPI(
    title="Task Service",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if get_settings().environment != "production" else None,
    redoc_url=None,
)
app.include_router(router)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request.state.request_id = str(uuid.uuid4())
    started = time.monotonic()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("Unhandled request error; request_id=%s", request.state.request_id)
        response = JSONResponse(
            status_code=500,
            content={
                "error": {
                    "message": "Internal server error",
                    "request_id": request.state.request_id,
                }
            },
        )
    response.headers["X-Request-ID"] = request.state.request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    logger.info(
        json.dumps(
            {
                "event": "request",
                "request_id": request.state.request_id,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": round((time.monotonic() - started) * 1000, 2),
            }
        )
    )
    return response


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        headers=exc.headers,
        content={"error": {"message": exc.detail, "request_id": request.state.request_id}},
    )


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    # Never echo input values (especially passwords/tokens) in validation responses.
    details = [{"location": e["loc"], "message": e["msg"], "type": e["type"]} for e in exc.errors()]
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "message": "Validation failed",
                "details": details,
                "request_id": request.state.request_id,
            }
        },
    )


@app.get("/health/live", tags=["health"])
async def live():
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
async def ready():
    try:
        async with asyncio.timeout(3):
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            async with Redis.from_url(get_settings().redis_url) as redis:
                await redis.ping()
    except Exception as exc:
        raise HTTPException(503, "Dependencies unavailable") from exc
    return {"status": "ready"}
