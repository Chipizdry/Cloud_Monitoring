from __future__ import annotations
from datetime import time, timedelta
from typing import Optional
from pydantic import Field, computed_field
from backend.schemas.base import BaseSchema



class EnergeticScheduleBase(BaseSchema):
    start_time: time = Field(..., description="Время начала работы режима (ЧЧ:ММ)")
    duration_hours: int = Field(
        ..., ge=0, description="Продолжительность режима в часах"
    )
    duration_minutes: int = Field(
        ..., ge=0, lt=60, description="Продолжительность режима в минутах (0-59)"
    )
    grid_feed_w: int = Field(..., ge=-100000, le=100000, description="Параметр отдачи в сеть (Вт)")
    battery_level_percent: int = Field(
        ..., ge=0, le=100, description="Целевой уровень батареи (%)"
    )
    charge_battery_value: int = Field(..., ge=-1, le=10000, description="заряжать батарею в этом режиме и с каким значением")
    is_manual_mode: bool = Field(
        False, description="Флаг: находится ли инвертор в ручном режиме"
    )


class EnergeticScheduleCreate(EnergeticScheduleBase):
    pass

class EnergeticScheduleCreateForObject(EnergeticScheduleBase):
    energetic_object_id: str = Field(..., description="ID Энергетического обьекта")


class EnergeticScheduleResponse(BaseSchema):

    id: str = Field(..., description="Уникальный идентификатор расписания")
    start_time: time = Field(..., description="Время начала работы режима (ЧЧ:ММ)")
    grid_feed_w: int = Field(..., ge=-100000, le=100000, description="Параметр отдачи в сеть (Вт)")
    battery_level_percent: int = Field(
        ..., ge=0, le=100, description="Целевой уровень батареи (%)"
    )
    charge_battery_value: int = Field(..., ge=-1, le=10000, description="заряжать батарею в этом режиме и с каким значением")
    is_active: bool = Field(True, description="Флаг: активно ли это расписание")
    is_manual_mode: bool = Field(
        False, description="Флаг: находится ли инвертор в ручном режиме"
    )
    duration: Optional[timedelta] = None

    @computed_field
    @property
    def formatted_duration(self) -> str:
        if isinstance(self.duration, timedelta):
            total_seconds = int(self.duration.total_seconds())
            hours, remainder = divmod(total_seconds, 3600)
            minutes, seconds = divmod(remainder, 60)

            parts = []
            if hours > 0:
                parts.append(f"{hours}h")
            if minutes > 0:
                parts.append(f"{minutes}m")
            if seconds > 0 or not parts:
                parts.append(f"{seconds}s")

            return " ".join(parts)
        return "N/A"
