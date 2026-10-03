"""
Database Enums
All enum types used across database models
"""

import enum


class AuthSessionStatus(enum.Enum):
    """COR-ID auth session status"""

    PENDING: str = "pending"
    APPROVED: str = "approved"
    REJECTED: str = "rejected"
    TIMEOUT: str = "timeout"


class SessionLoginStatus(str, enum.Enum):
    approved = "approved"
    rejected = "rejected"


class AccessLevel(enum.Enum):
    """Access level for device sharing"""
    READ = "read"
    READ_WRITE = "read_write"
    SHARE = "share"

class PollingTaskType(enum.Enum):
    """Типы задач фонового опроса устройств"""
    CERBO_COLLECTION = "cerbo_collection"  # Сбор данных с Cerbo GX
    SCHEDULE_CHECK = "schedule_check"  # Проверка и применение расписания
    MODBUS_REGISTERS = "modbus_registers"  # Чтение Modbus регистров
    CUSTOM_COMMAND = "custom_command"  # Пользовательская команда



class RequestPriority(enum.IntEnum):
    """Приоритеты запросов (меньше = выше приоритет)."""
    CRITICAL = 0      # Критичные операции
    USER_WRITE = 1    # Пользовательские команды записи
    USER_READ = 2     # Пользовательские команды чтения
    POLLING = 3       # Фоновый опрос