from __future__ import annotations
from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import ConfigDict, Field, field_validator
from backend.schemas.base import BaseSchema


class WSMessageBase(BaseSchema):
    """Модель для отправки сообщений на энергетические устройства"""
    session_token: str
    data: Dict

class WebSocketBroadcastTaskBase(BaseSchema):
    task_name: str = Field(..., description="Имя задачи (уникальное)")
    session_id: str = Field(..., description="Session ID устройства")
    command_type: str = Field(..., description="Тип команды: pi30, modbus_read или modbus_tcp")
    command_payload: Dict[str, Any] = Field(..., description="Данные команды")
    interval_ms: float = Field(5000, gt=0, le=3600000, description="Интервал отправки (мс, >0 .. 3600000)")
    is_active: bool = Field(True, description="Активна ли задача")


class WebSocketBroadcastTaskCreate(BaseSchema):
    task_name: str = Field(..., description="Имя задачи")
    session_id: str = Field(..., description="Session ID устройства")
    command_type: str = Field(..., description="pi30, modbus_read или modbus_tcp")
    
    # Для pi30 команд
    pi30_command: Optional[str] = Field(None, description="PI30 команда (будет автоматически отформатирована)")
    
    # Для modbus команд  
    hex_data: Optional[str] = Field(None, description="Hex данные для modbus_read")
    command_name: Optional[str] = Field(None, description="Имя команды для modbus_read")
    command_payload: Optional[Dict[str, Any]] = Field(
        None,
        description="Raw payload для command_type='modbus_tcp' (ip, port, unit_id, func, start_addr, quantity, value)",
    )
    
    interval_ms: float = Field(5000, gt=0, le=3600000, description="Интервал в миллисекундах")
    is_active: bool = Field(True, description="Активна ли задача")
    created_by: Optional[str] = Field(None, description="ID пользователя")


class WebSocketBroadcastTaskUpdate(BaseSchema):
    task_name: Optional[str] = None
    interval_ms: Optional[float] = Field(None, gt=0, le=3600000)
    is_active: Optional[bool] = None
    pi30_command: Optional[str] = None
    hex_data: Optional[str] = None
    command_name: Optional[str] = None
    command_payload: Optional[Dict[str, Any]] = None


class WebSocketBroadcastTaskResponse(WebSocketBroadcastTaskBase):
    id: str
    created_at: datetime
    updated_at: datetime
    created_by: Optional[str]
    
    model_config = ConfigDict(from_attributes=True)


class WebSocketBroadcastTaskListResponse(BaseSchema):
    session_id: str
    tasks: List[WebSocketBroadcastTaskResponse]
    total_tasks: int
    active_tasks: int



class WSMessageBase(BaseSchema):
    session_token: str
    data: str = Field(..., description="HEX команда в виде строки, например: '090300000000ac485'")
    
    @field_validator("data")
    @classmethod
    def validate_hex_string(cls, v: str) -> str:
        # Удаляем пробелы, если они есть
        v = v.replace(" ", "")
        # Проверяем, что строка содержит только hex символы
        if not all(c in "0123456789ABCDEFabcdef" for c in v):
            raise ValueError("Строка должна содержать только HEX символы")
        # Проверяем длину (должна быть четной)
        if len(v) % 2 != 0:
            raise ValueError("Длина HEX строки должна быть четной")
        return v

    def get_bytes(self) -> bytes:
        """Преобразует HEX строку в bytes"""
        return bytes.fromhex(self.data)