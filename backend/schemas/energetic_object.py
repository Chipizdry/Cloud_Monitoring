from __future__ import annotations
from datetime import datetime
from typing import List, Optional
from pydantic import Field, field_validator

from backend.database.models.enums import AccessLevel
from backend.schemas.base import BaseSchema



class EnergeticObjectBase(BaseSchema):
    name: str
    model_name: Optional[str] = Field(None, description="Модель устройства/инвертора")
    description: Optional[str] = None
    protocol: Optional[str] = Field(None, description="Протокол связи")
    vendor: Optional[str] = Field(None, description="Производитель/вендор инвертора")
    ip_address: Optional[str] = Field(None, description="IP-адрес объекта")
    port: Optional[int] = Field(None, description="Порт объекта")
    inverter_login: Optional[str] = Field(None, description="Логин для доступа к инвертору")
    inverter_password: Optional[str] = Field(None, description="Пароль для доступа к инвертору")
    is_active: Optional[bool] = None
    timezone: str = Field(default="Europe/Kyiv", description="Часовой пояс объекта (например: Europe/Kyiv, America/New_York)")
    modbus_config_file: Optional[str] = Field(None, description="Имя файла конфигурации Modbus (например: victron_cerbo_gx.json)")
    cor_bridges: List[str] = Field(default_factory=list, description="Список доступных device_id энергетических устройств")
    slave_ids: List[int] = Field(default_factory=list, description="Список Modbus slave_id")
    
    # Telegram bot configuration
    telegram_bot_token: Optional[str] = Field(None, description="Telegram bot token для этого объекта (если None - используется глобальный)")
    telegram_bot_name: Optional[str] = Field(None, description="Имя Telegram бота")
    telegram_chat_ids: Optional[str] = Field(None, description="Список Telegram chat IDs через запятую для уведомлений")
    telegram_battery_threshold: Optional[int] = Field(70, description="Порог низкого заряда батареи для уведомлений (%)")
    telegram_cooldown_minutes: Optional[int] = Field(60, description="Интервал между повторными уведомлениями (минуты)")


class EnergeticObjectCreate(EnergeticObjectBase):
    pass

class EnergeticObjectUpdate(BaseSchema):
    name: Optional[str] = None
    model_name: Optional[str] = Field(None, description="Модель устройства/инвертора")
    description: Optional[str] = None
    protocol: Optional[str] = Field(None, description="Протокол связи")
    vendor: Optional[str] = Field(None, description="Производитель/вендор инвертора")
    ip_address: Optional[str] = Field(None, description="IP-адрес объекта")
    port: Optional[int] = Field(None, description="Порт объекта")
    inverter_login: Optional[str] = Field(None, description="Логин для доступа к инвертору")
    inverter_password: Optional[str] = Field(None, description="Пароль для доступа к инвертору")
    is_active: Optional[bool] = None
    timezone: Optional[str] = Field(None, description="Часовой пояс объекта (например: Europe/Kyiv, America/New_York)")
    modbus_config_file: Optional[str] = Field(None, description="Имя файла конфигурации Modbus")
    cor_bridges: Optional[List[str]] = Field(None, description="Список доступных device_id энергетических устройств")
    slave_ids: Optional[List[int]] = Field(None, description="Список Modbus slave_id для опроса")
    
    # Telegram bot configuration
    telegram_bot_token: Optional[str] = Field(None, description="Telegram bot token для этого объекта")
    telegram_bot_name: Optional[str] = Field(None, description="Имя Telegram бота")
    telegram_chat_ids: Optional[str] = Field(None, description="Список Telegram chat IDs через запятую")
    telegram_battery_threshold: Optional[int] = Field(None, description="Порог низкого заряда батареи (%)")
    telegram_cooldown_minutes: Optional[int] = Field(None, description="Интервал между уведомлениями (минуты)")

class EnergeticObjectResponse(EnergeticObjectBase):
    id: str
    owner_cor_id: Optional[str] = None

    class Config:
        from_attributes = True


class EnergeticObjectAccessBase(BaseSchema):
    energetic_object_id: str
    accessing_user_cor_id: str
    access_level: AccessLevel

    @field_validator("access_level", mode="before")
    @classmethod
    def normalize_access_level(cls, v):
        if isinstance(v, AccessLevel):
            return v
        if isinstance(v, str):
            try:
                return AccessLevel[v.upper()]
            except KeyError:
                try:
                    return AccessLevel(v.lower())
                except ValueError:
                    raise ValueError("Invalid access_level. Use one of: read, read_write, share")
        raise ValueError("access_level must be a string or AccessLevel enum")


class EnergeticObjectAccessCreate(EnergeticObjectAccessBase):
    pass


class EnergeticObjectAccessResponse(EnergeticObjectAccessBase):
    id: str
    granting_user_cor_id: Optional[str] = None
    created_at: datetime


class EnergeticObjectShareUserResponse(BaseSchema):
    email: str
    cor_id: str
    first_name: Optional[str] = None
    surname: Optional[str] = None
