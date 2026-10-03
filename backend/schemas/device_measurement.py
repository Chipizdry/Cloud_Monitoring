from __future__ import annotations
from datetime import datetime
from typing import Generic, List, Optional, TypeVar
from pydantic import Field
from backend.schemas.base import BaseSchema



class FullDeviceMeasurementCreate(BaseSchema):
    # Общая информация о измерении
    measured_at: datetime = Field(..., description="Время измерения")
    object_name: Optional[str] = Field(
        None, description="Имя устройства, если применимо"
    )
    energetic_object_id: str = Field(
        ..., description="ID объекта"
    )  

    # агрегированные данные
    general_battery_power: float = Field(
        ..., description="Общая мощность батареи"
    )  
    battery_voltage: Optional[float] = Field(None, description="Напряжение батареи, V")
    inverter_total_ac_output: float = Field(
        ..., description="Общая выходная мощность AC инвертора"
    )  
    ess_total_input_power: float = Field(
        ..., description="Общая входная мощность ESS"
    )  
    solar_total_pv_power: float = Field(
        ..., description="Общая мощность солнечных панелей"
    )  
    soc: float = Field(
        ..., description="SOC - State of charge"
    )  

    class Config:
        from_attributes = True




class FullDeviceMeasurementResponse(FullDeviceMeasurementCreate):
    id: str
    created_at: datetime


class CerboMeasurementResponse(BaseSchema):
    id: str = Field(..., description="Уникальный идентификатор записи")
    created_at: datetime = Field(..., description="Дата и время сохранения записи в БД")
    measured_at: datetime = Field(
        ..., description="Дата и время измерения, полученное с устройства"
    )
    object_name: Optional[str] = Field(None, description="Имя объекта/устройства")
    general_battery_power: Optional[float] = Field(None, description="Мощность батареи")
    inverter_total_ac_output: Optional[float] = Field(
        None, description="Общая выходная мощность инвертора AC"
    )
    ess_total_input_power: Optional[float] = Field(
        None, description="Общая входная мощность ESS AC"
    )
    solar_total_pv_power: Optional[float] = Field(
        None, description="Общая мощность солнечных панелей"
    )
    battery_voltage: Optional[float] = Field(None, description="Напряжение батареи, V")
    soc: Optional[float] = Field(
        None, description="SOC - State of charge"
    )  

    class Config:
        from_attributes = True


class PowerMeasurementCreate(BaseSchema):
    measured_at: datetime = Field(..., description="Время измерения")
    energetic_object_id: str = Field(..., description="ID объекта")
    object_name: Optional[str] = Field(None, description="Имя объекта")
    source_protocol: Optional[str] = Field(None, description="Источник/протокол")
    source_task_id: Optional[str] = Field(None, description="ID polling-задачи")
    solar_power_w: Optional[float] = Field(None, description="Солнечная мощность, Вт")
    battery_power_w: Optional[float] = Field(None, description="Мощность батареи, Вт")
    battery_voltage_v: Optional[float] = Field(None, description="Напряжение батареи, V")
    battery_soc: Optional[float] = Field(None, description="SOC батареи, %")
    grid_power_w: Optional[float] = Field(None, description="Мощность сети, Вт")
    load_power_w: Optional[float] = Field(None, description="Мощность нагрузки, Вт")
    generator_power_w: Optional[float] = Field(None, description="Мощность генератора, Вт")
    raw_snapshot: Optional[dict] = Field(None, description="Исходный snapshot")


class PowerMeasurementResponse(PowerMeasurementCreate):
    id: str
    created_at: datetime


class EnergyMeterMeasurementResponse(BaseSchema):
    id: str
    energetic_object_id: str
    created_at: datetime
    measured_at: datetime
    active_power_kw: float
    reactive_power_kvar: float
    apparent_power_kva: float
    power_factor: float
    frequency_hz: float
    angle_deg: float
    voltage_l1_n_v: float
    voltage_l2_n_v: float
    voltage_l3_n_v: float
    voltage_l1_l2_v: float
    voltage_l2_l3_v: float
    voltage_l3_l1_v: float
    apparent_energy_kvah: float


T = TypeVar("T")


class PaginatedResponse(BaseSchema, Generic[T]):
    items: List[T] = Field(..., description="Список элементов на текущей странице")
    total_count: int = Field(..., description="Общее количество элементов")
    page: int = Field(..., description="Текущий номер страницы (начиная с 1)")
    page_size: int = Field(..., description="Количество элементов на странице")
    total_pages: int = Field(..., description="Общее количество страниц")
