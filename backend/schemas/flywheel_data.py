from __future__ import annotations
from datetime import datetime
from typing import Generic, List, Optional, TypeVar
from pydantic import Field
from backend.schemas.base import BaseSchema



class FlywheelMeasurementsCreate(BaseSchema):
    measured_at: datetime = Field(..., description="Время измерения")
    energetic_object_id: str = Field(..., description="ID объекта")

    flywheel_speed_rpm: Optional[float]= Field(..., description="Скорость маховика в оборотах в минуту")
    flywheel_pwm: Optional[float] = Field(..., description="Текущее значение ШИМ для управления маховиком")
    flywheel_frequency: Optional[float] = Field(..., description="Текущая частота управления маховиком в Гц")
    flywheel_arr_timer: Optional[float] = Field(..., description="Текущее значение ARR таймера для управления маховиком")
    flywheel_pwm_raw: Optional[float] = Field(..., description="Текущее сырое значение ШИМ")
    flywheel_voltage: Optional[float] = Field(..., description="Напряжение")
    flywheel_current: Optional[float] = Field(..., description="Ток")
    flywheel_power: Optional[float] = Field(..., description="Мощность")
    stator_temperature: Optional[List[float]] = Field(..., description="Значения температур статоров в виде массива JSON") 

class FlywheelMeasurementsResponse(BaseSchema):
    id: str = Field(..., description="Уникальный идентификатор записи")
    created_at: datetime = Field(..., description="Дата и время сохранения записи в БД")
    energetic_object_id: str = Field(..., description="ID объекта")
    measured_at: datetime = Field(
        ..., description="Дата и время измерения, полученное с устройства"
    )
    flywheel_speed_rpm: Optional[float] = Field(..., description="Скорость маховика в оборотах в минуту")
    flywheel_pwm: Optional[float] = Field(..., description="Текущее значение ШИМ для управления маховиком")
    flywheel_frequency: Optional[float] = Field(..., description="Текущая частота управления маховиком в Гц")
    flywheel_arr_timer: Optional[float] = Field(..., description="Текущее значение ARR таймера для управления маховиком")
    flywheel_pwm_raw: Optional[float] = Field(..., description="Текущее сырое значение ШИМ")
    flywheel_voltage: Optional[float] = Field(..., description="Напряжение")
    flywheel_current: Optional[float] = Field(..., description="Ток")
    flywheel_power: Optional[float] = Field(..., description="Мощность")
    stator_temperature: Optional[List[float]] = Field(..., description="Значения температур статоров в виде массива JSON")
    kinetic_energy_wh: Optional[float] = Field(
        None, description="Кинетическая энергия маховика в Вт·ч"
    )


    class Config:
        from_attributes = True


T = TypeVar("T")


class PaginatedResponse(BaseSchema, Generic[T]):
    items: List[T] = Field(..., description="Список элементов на текущей странице")
    total_count: int = Field(..., description="Общее количество элементов")
    page: int = Field(..., description="Текущий номер страницы (начиная с 1)")
    page_size: int = Field(..., description="Количество элементов на странице")
    total_pages: int = Field(..., description="Общее количество страниц")
