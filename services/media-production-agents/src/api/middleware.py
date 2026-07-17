"""
Middleware for media-production-agents.
"""

from fastapi import Request, Response
import time
import uuid

from babyai_observability import get_logger
from babyai_auth import verify_service_token

logger = get_logger("media-production-agents")


async def logging_middleware(request: Request, call_next):
    """Log all requests and responses."""
    start_time = time.time()
    request_id = str(uuid.uuid4())

    logger.info("Request started", extra={
        "request_id": request_id,
        "method": request.method,
        "url": str(request.url),
        "user_agent": request.headers.get("user-agent")
    })

    response = await call_next(request)

    process_time = time.time() - start_time
    logger.info("Request completed", extra={
        "request_id": request_id,
        "status_code": response.status_code,
        "process_time": process_time
    })

    return response


async def auth_middleware(request: Request, call_next):
    """Authenticate inter-service requests."""
    # Skip auth for health checks
    if request.url.path.startswith("/health"):
        return await call_next(request)

    # Validate service token for /v1/ endpoints
    if request.url.path.startswith("/v1/"):
        auth_header = request.headers.get("authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return Response("Unauthorized", status_code=401)

        try:
            token = auth_header.split(" ")[1]
            service_info = await verify_service_token(token)
            request.state.calling_service = service_info
        except Exception as e:
            logger.warning(f"Auth validation failed: {e}")
            return Response("Unauthorized", status_code=401)

    return await call_next(request)
