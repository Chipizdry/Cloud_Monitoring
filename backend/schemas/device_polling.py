from __future__ import annotations
from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import Field
from backend.database.models.enums import PollingTaskType
from backend.schemas.base import BaseSchema
from backend.schemas.websocket import WebSocketBroadcastTaskResponse
from backend.services.energy.pi30_commands import PI30Command


class Pi30CommandRequest(BaseSchema):
    """Запрос на отправку PI30 команды"""
    session_token: str
    pi30: PI30Command

class DevicePollingTaskBase(BaseSchema):
    """Базовая схема задачи опроса"""
    task_type: PollingTaskType = Field(..., description="Тип задачи опроса")
    command_config: Optional[Dict[str, Any]] = Field(
        None,
        description="Конфигурация команды в JSON (какие регистры читать, параметры)"
    )
    interval_ms: float = Field(
        5000,
        gt=0,
        le=3600000,
        description="Интервал опроса в миллисекундах (>0 .. 3600000 мс = 1 ч)"
    )
    is_active: bool = Field(True, description="Активна ли задача")


class DevicePollingTaskCreate(DevicePollingTaskBase):
    """Схема создания задачи опроса"""
    energetic_object_id: str = Field(..., description="ID энергетического объекта")


class DevicePollingTaskUpdate(BaseSchema):
    """Схема обновления задачи опроса"""
    task_type: Optional[PollingTaskType] = None
    command_config: Optional[Dict[str, Any]] = None
    interval_ms: Optional[float] = Field(None, gt=0, le=3600000)
    is_active: Optional[bool] = None


class DevicePollingTasksActivationUpdate(BaseSchema):
    """Схема массового включения/выключения задач опроса"""
    is_active: bool = Field(..., description="Новый статус активности задач")


class DevicePollingTaskResponse(DevicePollingTaskBase):
    """Схема ответа с задачей опроса"""
    id: str = Field(..., description="ID задачи")
    energetic_object_id: str = Field(..., description="ID энергетического объекта")
    created_at: datetime = Field(..., description="Дата создания")
    updated_at: datetime = Field(..., description="Дата обновления")
    
    class Config:
        from_attributes = True


class DevicePollingTaskListResponse(BaseSchema):
    """Список задач опроса для объекта"""
    energetic_object_id: str
    energetic_object_name: str
    tasks: List[DevicePollingTaskResponse]
    total_tasks: int
    active_tasks: int


class CombinedObjectPollingTasksResponse(BaseSchema):
    """Список обычных polling-задач объекта и broadcast-задач связанных COR Bridge."""
    energetic_object_id: str
    energetic_object_name: str
    bridge_session_ids: List[str]
    object_tasks: List[DevicePollingTaskResponse]
    bridge_tasks: List[WebSocketBroadcastTaskResponse]
    object_total_tasks: int
    object_active_tasks: int
    bridge_total_tasks: int
    bridge_active_tasks: int
    total_tasks: int
    active_tasks: int


class DevicePollingTasksBulkUpdateResponse(BaseSchema):
    """Результат массового включения/выключения задач опроса"""
    scope: str = Field(..., description="Тип области изменения: object или device")
    scope_id: str = Field(..., description="ID объекта или устройства")
    energetic_object_ids: List[str] = Field(default_factory=list, description="Затронутые энергетические объекты")
    is_active: bool = Field(..., description="Установленный статус активности")
    total_tasks: int = Field(..., description="Всего найдено задач")
    updated_tasks: int = Field(..., description="Количество задач, статус которых был изменён")
    active_tasks: int = Field(..., description="Количество активных задач после изменения")
    tasks: List[DevicePollingTaskResponse]
