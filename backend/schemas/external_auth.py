from __future__ import annotations
from datetime import datetime
from typing import List, Optional
from pydantic import EmailStr, model_validator
from backend.database.models.enums import SessionLoginStatus
from backend.schemas.base import BaseSchema


class InitiateLoginRequest(BaseSchema):
    email: Optional[EmailStr] = None
    cor_id: Optional[str] = None
    app_id: Optional[str] = None

    @model_validator(mode="before")
    def check_either_email_or_cor_id(cls, data: dict):
        email = data.get("email")
        cor_id = data.get("cor_id")
        if not email and not cor_id:
            raise ValueError("Требуется указать либо email, либо cor_id")
        return data


class InitiateLoginResponse(BaseSchema):
    session_token: str
    # New for Cor-ID OAuth (backend-to-backend) flow: URL to open in WebView
    authorize_url: Optional[str] = None
    deep_link: Optional[str] = None
    corid_session_token: Optional[str] = None


class ConfirmLoginRequest(BaseSchema):
    email: Optional[EmailStr] = None
    cor_id: Optional[str] = None
    session_token: str
    status: SessionLoginStatus

    @model_validator(mode="before")
    def check_either_email_or_cor_id(cls, data: dict):
        email = data.get("email")
        cor_id = data.get("cor_id")
        if not email and not cor_id:
            raise ValueError("Требуется указать либо email, либо cor_id")
        return data


class ConfirmLoginResponse(BaseSchema):
    message: str


class CheckSessionRequest(BaseSchema):
    email: Optional[EmailStr] = None
    cor_id: Optional[str] = None
    session_token: str

    @model_validator(mode="before")
    def check_either_email_or_cor_id(cls, data: dict):
        email = data.get("email")
        cor_id = data.get("cor_id")
        if not email and not cor_id:
            raise ValueError("Требуется указать либо email, либо cor_id")
        return data


class ConfirmCheckSessionResponse(BaseSchema):
    status: str = "approved"
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    device_id: Optional[str] = None


class WebInitiateLoginRequest(BaseSchema):
    """Запрос для инициации входа с веб-фронтенда через COR-ID приложение"""

    email: EmailStr


class WebInitiateLoginResponse(BaseSchema):
    """Ответ с данными для авторизации через COR-ID приложение"""

    session_token: str
    deep_link: str
    qr_code: str  # Base64 encoded PNG QR code (data:image/png;base64,...)
    expires_at: datetime


class QrScannedRequest(BaseSchema):
    """Уведомление о сканировании QR-кода"""

    session_token: str


class UserRolesResponseForAdmin(BaseSchema):
    user_roles: Optional[List[str]] = None

    class Config:
        from_attributes = True


class ActionRequest(BaseSchema):
    session_token: str
    status: SessionLoginStatus


class StatusResponse(BaseSchema):
    session_token: str
    status: str
    deep_link: Optional[str] = None
    expires_at: datetime
