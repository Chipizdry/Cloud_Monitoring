from __future__ import annotations

from pydantic import Field, EmailStr, field_validator
from backend.schemas.base import BaseSchema
from typing import Literal, Optional
from datetime import datetime


class InviteUserRequest(BaseSchema):
    """Запрос на создание приглашения пользователя"""

    email: EmailStr = Field(..., description="Email приглашаемого пользователя")
    expires_in_days: Optional[int] = Field(
        7,
        ge=1,
        le=30,
        description="Срок действия приглашения в днях (по умолчанию 7 дней)",
    )


class InviteUserResponse(BaseSchema):
    """Ответ с данными приглашения"""

    invitation_id: str = Field(..., description="ID приглашения")
    email: str = Field(..., description="Email приглашённого пользователя")
    token: str = Field(..., description="Уникальный токен приглашения")
    invitation_link: str = Field(..., description="Полная ссылка для регистрации")
    expires_at: datetime = Field(..., description="Дата истечения приглашения")
    created_at: datetime = Field(..., description="Дата создания приглашения")


class ValidateInvitationRequest(BaseSchema):
    """Запрос на проверку валидности токена приглашения"""

    token: str = Field(..., min_length=32, description="Токен приглашения из URL")


class ValidateInvitationResponse(BaseSchema):
    """Ответ с данными валидного приглашения"""

    is_valid: bool = Field(..., description="Валиден ли токен")
    email: Optional[str] = Field(None, description="Email для регистрации (readonly)")
    expires_at: Optional[datetime] = Field(None, description="Когда истекает")
    message: Optional[str] = Field(
        None, description="Сообщение об ошибке, если невалидно"
    )


class AcceptInvitationRequest(BaseSchema):
    """Запрос на регистрацию по приглашению"""

    token: str = Field(..., min_length=32, description="Токен приглашения")
    password: str = Field(
        ..., min_length=8, max_length=32, description="Пароль пользователя"
    )
    birth: Optional[int] = Field(
        None, ge=1900, description="Год рождения (например: 1990)"
    )
    user_sex: Optional[Literal["M", "F", "*"]] = Field(
        None, description="Пол: 'M' (мужской), 'F' (женский), '*' (другое)"
    )

    @field_validator("birth")
    @classmethod
    def validate_birth_year(cls, v: Optional[int]) -> Optional[int]:
        """Валидация года рождения - не может быть в будущем"""
        if v is None:
            return v

        current_year = datetime.now().year
        if v > current_year:
            raise ValueError(
                f"Год рождения не может быть больше текущего года ({current_year})"
            )

        return v
