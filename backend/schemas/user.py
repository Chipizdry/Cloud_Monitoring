from __future__ import annotations

from pydantic import Field, EmailStr, StrictInt, field_validator
from typing import List, Literal, Optional
from datetime import datetime, date

from backend.schemas.base import BaseSchema


class UserModel(BaseSchema):
    email: EmailStr
    password: str = Field(min_length=8, max_length=32)
    birth: Optional[StrictInt] = Field(
        None,
        ge=1900,
        description="Год рождения (например: 1990). Должен быть не раньше 1900 и не позже текущего года",
    )
    user_sex: Optional[Literal["M", "F", "*"]] = Field(
        None,
        description="Пол пользователя: 'M' (мужской), 'F' (женский), '*' (другое/не указано)",
    )

    @field_validator("email")
    @classmethod
    def validate_email_ascii(cls, v: EmailStr) -> EmailStr:
        email_str = str(v)
        if not email_str.isascii():
            raise ValueError("Email must contain only ASCII characters")
        return v

    @field_validator("birth")
    @classmethod
    def validate_birth_year(cls, v: Optional[StrictInt]) -> Optional[StrictInt]:
        """Валидация года рождения - не может быть в будущем"""
        if v is None:
            return v

        current_year = datetime.now().year
        if v > current_year:
            raise ValueError(
                f"Год рождения не может быть больше текущего года ({current_year})"
            )

        return v

    @field_validator("user_sex")
    @classmethod
    def user_sex_must_be_m_or_f(cls, v):
        if v is not None and v not in ["M", "F", "*"]:
            raise ValueError(
                'user_sex должен быть "M" (мужской), "F" (женский) или "*" (другое)'
            )
        return v


class UserDb(BaseSchema):
    id: str
    cor_id: Optional[str] = Field(None, max_length=15)
    email: str
    is_active: bool
    last_password_change: datetime
    user_sex: Optional[str] = Field(None, max_length=1)
    birth: Optional[int] = Field(None, ge=1900)
    user_index: int
    created_at: datetime
    last_active: Optional[datetime] = None


class UserDbResponse(BaseSchema):
    id: str
    cor_id: Optional[str]
    email: Optional[str]
    is_active: Optional[bool]
    last_password_change: Optional[datetime]
    user_sex: Optional[str]
    birth: Optional[int]
    user_index: Optional[int]
    created_at: datetime
    last_active: Optional[datetime] = None


class ResponseUser(BaseSchema):
    user: UserDb
    detail: str = "User successfully created"
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    session_id: Optional[str] = None
    device_id: Optional[str] = None


class NewUserRegistration(BaseSchema):
    email: EmailStr = Field(..., description="Email пользователя")
    birth_date: Optional[date] = Field(None, description="Дата рождения пациента")
    sex: Optional[str] = Field(
        None,
        max_length=1,
        description="Пол пациента, может быть 'M'(мужской) или 'F'(женский)",
    )

    @field_validator("sex")
    def user_sex_must_be_m_or_f(cls, v):
        if v not in ["M", "F"]:
            raise ValueError('user_sex must be "M" or "F"')
        return v


class TokenModel(BaseSchema):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class LoginResponseModel(BaseSchema):
    access_token: str
    refresh_token: str
    token_type: str
    session_id: Optional[str] = None
    requires_master_key: bool = False
    message: Optional[str] = None
    device_id: Optional[str] = None


class RecoveryResponseModel(BaseSchema):
    access_token: str
    refresh_token: str
    token_type: str
    message: Optional[str] = None
    confirmation: Optional[bool] = False
    session_id: Optional[str] = None
    device_id: Optional[str] = None


class EmailSchema(BaseSchema):
    email: EmailStr


class VerificationModel(BaseSchema):
    email: EmailStr
    verification_code: int


class ChangePasswordModel(BaseSchema):
    email: Optional[str]
    password: str = Field(min_length=8, max_length=32)


class ChangeMyPasswordModel(BaseSchema):
    old_password: str = Field(min_length=8, max_length=32)
    new_password: str = Field(min_length=8, max_length=32)


class RecoveryCodeModel(BaseSchema):
    email: EmailStr
    recovery_code: str


class UserSessionModel(BaseSchema):
    cor_id: Optional[str] = Field(None, max_length=15)
    device_type: str
    device_info: str
    ip_address: str
    device_os: str
    refresh_token: str
    jti: str
    access_token: str
    app_id: Optional[str] = None
    device_id: Optional[str] = None


class UserSessionResponseModel(BaseSchema):
    id: str
    user_id: str
    device_type: str
    device_info: str
    ip_address: str
    device_os: str
    created_at: datetime
    updated_at: datetime
    jti: Optional[str]
    country_code: Optional[str] = None
    country_name: Optional[str] = None
    region_name: Optional[str] = None
    city_name: Optional[str] = None


class UserSessionDBModel(BaseSchema):
    id: str
    cor_id: Optional[str] = Field(None, max_length=15)
    device_type: str
    device_info: str
    ip_address: str
    device_os: str
    refresh_token: str
    created_at: datetime
    updated_at: datetime


class UserMeResponse(BaseSchema):
    corid: str | None
    roles: list[str]
    first_name: Optional[str] = None
    surname: Optional[str] = None
    middle_name: Optional[str] = None


class CreateCorIdModel(BaseSchema):
    medical_institution_code: str = Field(max_length=3)
    patient_number: str = Field(max_length=3)
    patient_birth: int = Field(ge=1900, le=2100)
    patient_sex: str = Field(max_length=1)

    @field_validator("patient_sex")
    def patient_sex_must_be_m_or_f(cls, v):
        if v not in ["M", "F"]:
            raise ValueError('patient_sex must be "M" or "F"')
        return v


class ResponseCorIdModel(BaseSchema):
    cor_id: str = None


class AcceptInvitationResponse(BaseSchema):
    """Ответ после успешной регистрации по приглашению"""

    user: UserDb = Field(..., description="Данные созданного пользователя")
    access_token: str = Field(..., description="JWT access token")
    refresh_token: str = Field(..., description="JWT refresh token")
    token_type: str = Field(default="bearer", description="Тип токена")
    device_id: str = Field(..., description="ID устройства/сессии")
    message: str = Field(default="Регистрация по приглашению успешно завершена")


class ProfileCreate(BaseSchema):
    surname: Optional[str] = Field(None, max_length=25, description="Фамилия")
    first_name: Optional[str] = Field(None, max_length=25, description="Имя")
    middle_name: Optional[str] = Field(None, max_length=25, description="Отчество")
    birth_date: Optional[date] = Field(None, description="Дата рождения")
    phone_number: Optional[str] = Field(
        None, max_length=15, description="Номер телефона"
    )
    city: Optional[str] = Field(None, max_length=50, description="Город")
    car_brand: Optional[str] = Field(None, max_length=50, description="Марка авто")
    engine_type: Optional[str] = Field(None, max_length=50, description="Тип двигателя")
    fuel_tank_volume: Optional[int] = Field(
        None, ge=0, le=1000, description="Обьем бензобака"
    )


class ProfileResponse(BaseSchema):
    email: str = Field(description="Имейл пользователя")
    sex: str = Field(description="Пол пользователя")
    surname: Optional[str] = Field(None, description="Фамилия")
    first_name: Optional[str] = Field(None, description="Имя")
    middle_name: Optional[str] = Field(None, description="Отчество")
    birth_date: Optional[date] = Field(None, description="Дата рождения")
    phone_number: Optional[str] = Field(None, description="Номер телефона")
    city: Optional[str] = Field(None, description="Город")
    car_brand: Optional[str] = Field(None, description="Марка авто")
    engine_type: Optional[str] = Field(None, description="Тип двигателя")
    fuel_tank_volume: Optional[int] = Field(
        None, ge=0, le=1000, description="Обьем бензобака"
    )


class DeleteMyAccount(BaseSchema):
    password: str = Field(min_length=6, max_length=20)


class UserProfileResponseForAdmin(BaseSchema):
    profile: Optional[ProfileResponse] = None


class UserDataResponse(BaseSchema):
    user_info: UserDb


class FullUserInfoResponse(BaseSchema):
    user_info: UserDb
    user_roles: Optional[List[str]] = None
    profile: Optional[ProfileResponse] = None

    class Config:
        from_attributes = True
