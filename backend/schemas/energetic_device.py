from __future__ import annotations
from datetime import datetime
from ipaddress import ip_address
import re
from typing import Optional
from pydantic import Field, field_validator
from backend.database.models.enums import AccessLevel
from backend.schemas.base import BaseSchema




class EnergeticDeviceBase(BaseSchema):
    name: Optional[str] = None
    model_name: Optional[str] = Field(None, description="Модель устройства")
    device_id: str
    protocol: Optional[str] = None
    description: Optional[str] = None


class EnergeticDeviceCreate(EnergeticDeviceBase):
    owner_cor_id: str
    is_active: Optional[bool] = True


class EnergeticDeviceUpdate(BaseSchema):
    name: Optional[str] = None
    model_name: Optional[str] = Field(None, description="Модель устройства")
    protocol: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None


class EnergeticDeviceCreate(BaseSchema):
    device_id: str = Field(..., description="ID устройства (например, serial number)")
    name: Optional[str] = Field(None, description="Название устройства")
    model_name: Optional[str] = Field(None, description="Модель устройства")
    protocol: Optional[str] = Field(None, description="Протокол (например, modbus)")
    description: Optional[str] = Field(None, description="Описание устройства")



class EnergeticDeviceResponse(EnergeticDeviceBase):
    id: str
    owner_cor_id: str
    is_active: bool
    last_seen: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class EnergeticDeviceListResponse(EnergeticDeviceResponse):
    is_assigned: bool = False

class EnergeticDeviceAccessBase(BaseSchema):
    device_id: str
    accessing_user_cor_id: str
    access_level: AccessLevel

    @field_validator("access_level", mode="before")
    @classmethod
    def normalize_access_level(cls, v):
        """Normalize access_level to accept any case."""
        if isinstance(v, AccessLevel):
            return v
        if isinstance(v, str):
            # Try by enum name (uppercase)
            try:
                return AccessLevel[v.upper()]
            except KeyError:
                # Try by enum value (lowercase)
                try:
                    return AccessLevel(v.lower())
                except ValueError:
                    raise ValueError(f"Invalid access_level '{v}'. Use one of: read, read_write, share")
        raise ValueError("access_level must be a string or AccessLevel enum")



class EnergeticDeviceAccessCreate(EnergeticDeviceAccessBase):
    # granting_user_cor_id is set from the authenticated user server-side
    pass


class EnergeticDeviceAccessResponse(EnergeticDeviceAccessBase):
    id: str
    granting_user_cor_id: Optional[str] = None
    created_at: datetime


class SendSettingsRequest(BaseSchema):
    """Запрос на отправку настроек на энергетическое устройство"""
    session_token: str
    user: Optional[str] = None
    account: Optional[str] = None
    network: Optional[str] = None
    wifi: Optional[str] = None
    uart: Optional[str] = None
    system: Optional[str] = None
    all: Optional[str] = None


class GetSettingsRequest(BaseSchema):
    """Запрос на получение настроек с энергетического устройства"""
    session_token: str
    user: bool = False
    account: bool = False
    network: bool = False
    wifi: bool = False
    uart: bool = False
    system: bool = False
    all: bool = False


class TracerouteRequest(BaseSchema):
    """Запрос трассировки с подключённого устройства до IP-адреса или домена."""

    session_token: str
    url: str = Field(..., description="IP-адрес или доменное имя без схемы и пути")

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        host = value.strip()
        if "%" in host:
            raise ValueError("Укажите IP-адрес или доменное имя без схемы, порта и пути")
        try:
            ip_address(host)
            return host
        except ValueError:
            pass

        label = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
        if (
            len(host) > 253
            or not re.fullmatch(rf"{label}(?:\.{label})*", host)
            or not re.search(r"[A-Za-z]", host)
        ):
            raise ValueError("Укажите IP-адрес или доменное имя без схемы, порта и пути")
        return host
