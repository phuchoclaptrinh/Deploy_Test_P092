"""Authenticated profile endpoints for Resident and Coordinator."""

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from src.api.dependencies.auth import CurrentActor, get_current_actor, require_resident
from src.api.dependencies.database import get_db
from src.models.api.auth import (
    BindUnitRequest,
    CurrentUserResponse,
    OtpRequestBody,
    OtpRequestResponse,
    OtpVerifyBody,
    OtpVerifyResponse,
)
from src.models.api.common import ApiResponse
from src.services.auth_service import AuthService
from src.services.supabase_otp_service import SupabaseOtpService

router = APIRouter()


def _ok(request: Request, data):
    return {"data": data, "meta": {}, "error": None, "request_id": request.state.request_id}


def get_otp_service() -> SupabaseOtpService:
    return SupabaseOtpService()


@router.post(
    "/auth/otp/request",
    response_model=ApiResponse[OtpRequestResponse],
    summary="Yêu cầu gửi OTP đăng nhập Cư dân",
    operation_id="request_resident_otp",
)
def request_otp(
    request: Request,
    body: OtpRequestBody,
    service: SupabaseOtpService = Depends(get_otp_service),
):
    service.request_otp(body.phone)
    return _ok(request, OtpRequestResponse())


@router.post(
    "/auth/otp/verify",
    response_model=ApiResponse[OtpVerifyResponse],
    summary="Xác minh OTP đăng nhập Cư dân",
    operation_id="verify_resident_otp",
)
def verify_otp(
    request: Request,
    body: OtpVerifyBody,
    service: SupabaseOtpService = Depends(get_otp_service),
):
    payload = service.verify_otp(body.phone, body.otp)
    data = OtpVerifyResponse(
        access_token=str(payload["access_token"]),
        refresh_token=str(payload["refresh_token"]) if payload.get("refresh_token") else None,
        token_type=str(payload.get("token_type") or "bearer"),
        expires_in=int(payload["expires_in"]) if payload.get("expires_in") is not None else None,
    )
    return _ok(request, data)


@router.get(
    "/me",
    response_model=ApiResponse[CurrentUserResponse],
    summary="Lấy hồ sơ người dùng hiện tại",
    operation_id="get_me",
)
def me(
    request: Request,
    actor: CurrentActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    return _ok(request, AuthService(db).current_user_response(actor))


@router.post(
    "/me/bind-unit",
    response_model=ApiResponse[CurrentUserResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Liên kết tài khoản Cư dân với mã căn hộ lần đầu",
    operation_id="bind_my_unit",
)
def bind_unit(
    request: Request,
    body: BindUnitRequest,
    actor: CurrentActor = Depends(require_resident),
    db: Session = Depends(get_db),
):
    return _ok(request, AuthService(db).bind_unit(actor, body.unit_code))
