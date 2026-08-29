"""FixIt Agent FastAPI application aligned with Self_Dev_Docs v2/v3."""

import logging
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.api.openapi_responses import INTERNAL_SERVER_ERROR_RESPONSE
from src.api.router import api_router
from src.config import get_settings
from src.database.session import engine
from src.models.api.errors import (
    DESCRIPTION_REQUIRED,
    INTERNAL_ERROR,
    OVERRIDE_REASON_REQUIRED,
    VALIDATION_ERROR,
    DomainError,
)
from src.models.api.health import HealthResponse, ReadinessResponse
from src.observability import flush_traces, setup_tracing
from src.request_context import request_id_context
from src.services.readiness_service import ReadinessService

logger = logging.getLogger(__name__)
settings = get_settings()

OPENAPI_DESCRIPTION = """
FixIt Agent là backend FastAPI cho hệ thống phân loại và ưu tiên phản ánh chung cư.

**Nguồn chuẩn sản phẩm:** `Self_Dev_Docs` v2/v3.

Ba actor người dùng:
- Cư dân: Supabase Auth số điện thoại + OTP.
- Điều phối viên BQL: Supabase Auth email + password, được backend cấp quyền COORDINATOR.
- Kỹ thuật viên: Supabase Auth + hồ sơ Technician tin cậy do backend cấp.

Điều phối viên duyệt và phân công; Kỹ thuật viên thực hiện assignment.
P0 là `classification_status=MANUAL_REVIEW`, không phải Priority. Priority chỉ P1/P2/P3.

Các endpoint nghiệp vụ dùng `Authorization: Bearer <Supabase access token>`.
"""


def openapi_tags() -> list[dict[str, str]]:
    tags = [
        {"name": "Health", "description": "Liveness/readiness công khai."},
        {"name": "Auth", "description": "Hồ sơ hai actor và bind căn hộ."},
        {"name": "Catalog", "description": "Danh mục vị trí và Category do backend quản lý."},
        {"name": "Storage", "description": "Signed upload extension cho ảnh ticket."},
        {"name": "Resident Tickets", "description": "Tạo/theo dõi/hủy/bổ sung ticket của Cư dân."},
        {"name": "Notifications", "description": "Thông báo theo user."},
        {"name": "Coordinator", "description": "Dashboard, P0, override, assignment, audit và reports."},
        {"name": "Technician", "description": "Assignment workflow for technicians."},
    ]
    return tags


@asynccontextmanager
async def lifespan(app: FastAPI):
    runtime = get_settings()
    runtime.validate_runtime_safety()
    # Before the first request, and so before any provider client is built:
    # analysis runs as a background task off a request, and its LLM calls must
    # already be instrumented by then. A no-op without BRAINTRUST_API_KEY.
    setup_tracing()
    print(f"Starting {runtime.app_name} in {runtime.app_env} mode")
    try:
        yield
    finally:
        # Background tasks may have finished moments ago; do not lose them.
        flush_traces()
        print("Shutting down...")


app = FastAPI(
    title="FixIt Agent API",
    description=OPENAPI_DESCRIPTION,
    version="2.0.0",
    openapi_tags=openapi_tags(),
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.parsed_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api/v1")


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    supplied = request.headers.get("x-request-id")
    try:
        request_id = str(UUID(supplied)) if supplied else str(uuid4())
    except (TypeError, ValueError):
        request_id = str(uuid4())
    request.state.request_id = request_id
    token = request_id_context.set(request_id)
    try:
        response = await call_next(request)
    finally:
        request_id_context.reset(token)
    response.headers["x-request-id"] = request_id
    return response


@app.exception_handler(DomainError)
async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "data": None,
            "error": {"code": exc.code, "message": exc.message},
            "request_id": getattr(request.state, "request_id", None),
        },
    )


@app.exception_handler(RequestValidationError)
async def request_validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = exc.errors()
    messages = " ".join(str(item.get("msg", "")) for item in errors)
    if "Description is required" in messages:
        code = DESCRIPTION_REQUIRED
        message = "Mô tả sự cố là bắt buộc."
    elif request.url.path.endswith("/classification") and any(
        item.get("loc", ())[-1:] == ("reason",) for item in errors
    ):
        code = OVERRIDE_REASON_REQUIRED
        message = "Override Category/Priority bắt buộc phải có lý do hợp lệ."
    else:
        code = VALIDATION_ERROR
        message = "Dữ liệu yêu cầu không hợp lệ."
    return JSONResponse(
        status_code=422,
        content={
            "data": None,
            "error": {"code": code, "message": message},
            "request_id": getattr(request.state, "request_id", None),
        },
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception(
        "Unhandled request error",
        extra={
            "request_id": getattr(request.state, "request_id", None),
            "method": request.method,
            "path": request.url.path,
            "exception_type": type(exc).__name__,
        },
    )
    return JSONResponse(
        status_code=500,
        content={
            "data": None,
            "error": {"code": INTERNAL_ERROR, "message": "Internal server error."},
            "request_id": getattr(request.state, "request_id", None),
        },
    )


@app.get("/health", tags=["Health"], response_model=HealthResponse, responses={500: INTERNAL_SERVER_ERROR_RESPONSE})
async def health() -> HealthResponse:
    return {"application": settings.app_name, "environment": settings.app_env, "status": "ok"}


@app.get("/ready", tags=["Health"], response_model=ReadinessResponse)
async def ready() -> ReadinessResponse | JSONResponse:
    payload, status_code = ReadinessService(settings, engine).check()
    if status_code != 200:
        return JSONResponse(status_code=status_code, content=payload)
    return payload
