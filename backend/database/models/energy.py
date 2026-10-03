"""
Energy domain models - energetic objects monitoring, Cerbo measurements, schedules
"""

from sqlalchemy import Column, String, ForeignKey, DateTime, Integer, Float, Boolean, Time, Interval, Index, Enum
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from sqlalchemy.dialects.postgresql import JSONB, ARRAY
import uuid

from .base import Base
from .enums import AccessLevel


class EnergeticObject(Base):
    """
    Энергетический объект - объект для мониторинга энергопотребления и управления
    """
    __tablename__ = "energetic_objects"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    owner_cor_id = Column(String(250), ForeignKey("users.cor_id"), nullable=True, index=True)
    name = Column(String, nullable=False, unique=True, comment="Имя/название объекта")
    model_name = Column(String, nullable=True, comment="Модель устройства/инвертора")
    description = Column(String, nullable=True, comment="Описание объекта")
    protocol = Column(String, nullable=True, comment="Протокол связи")
    vendor = Column(String, nullable=True, comment="Производитель/вендор инвертора")
    ip_address = Column(String, nullable=True, comment="IP-адрес объекта")
    port = Column(Integer, nullable=True, comment="Порт объекта")
    inverter_login = Column(String, nullable=True, comment="Логин для доступа к инвертору")
    inverter_password = Column(String, nullable=True, comment="Пароль для доступа к инвертору")
    timezone = Column(
        String,
        nullable=True,
        default='Europe/Kyiv',
        server_default='Europe/Kyiv',
        comment="Часовой пояс объекта (например: Europe/Kyiv)"
    )
    
    modbus_config_file = Column(
        String,
        nullable=True,
        comment="Имя файла конфигурации Modbus регистров (например: victron_cerbo_gx.json)"
    )

    cor_bridges = Column(
        ARRAY(String),
        nullable=True,
        default=list,
        server_default='{}',
        comment="Список доступных device_id энергетических устройств, связанных с объектом"
    )

    slave_ids = Column(
        ARRAY(Integer),
        nullable=True,
        default=list,
        server_default='{}',
        comment="Список Modbus slave_id для опроса устройства"
    )

    # Telegram bot configuration
    telegram_bot_token = Column(
        String,
        nullable=True,
        comment="Telegram bot token для этого объекта (опционально, если None - используется глобальный)"
    )
    telegram_bot_name = Column(
        String,
        nullable=True,
        comment="Имя Telegram бота (опционально, для информации)"
    )
    telegram_chat_ids = Column(
        String,
        nullable=True,
        comment="Список Telegram chat IDs через запятую для уведомлений"
    )
    telegram_battery_threshold = Column(
        Integer,
        nullable=True,
        default=70,
        comment="Порог низкого заряда батареи для уведомлений (%)"
    )
    telegram_cooldown_minutes = Column(
        Integer,
        nullable=True,
        default=60,
        comment="Интервал между повторными уведомлениями (минуты)"
    )

    is_active = Column(Boolean, default=False, comment="Активен ли фоновый опрос")

    # Relationships
    owner = relationship("User", back_populates="energetic_objects", foreign_keys=[owner_cor_id])
    accesses = relationship("EnergeticObjectAccess", back_populates="energetic_object", cascade="all, delete-orphan")
    measurements = relationship("CerboMeasurement", back_populates="energetic_object", cascade="all, delete-orphan")
    flywheel_measurements = relationship("FlywheelMeasurements", back_populates="energetic_object", cascade="all, delete-orphan")
    power_measurements = relationship("PowerMeasurement", back_populates="energetic_object", cascade="all, delete-orphan")
    energy_meter_measurements = relationship("EnergyMeterMeasurement", back_populates="energetic_object", cascade="all, delete-orphan")
    schedules = relationship("EnergeticSchedule", back_populates="energetic_object", cascade="all, delete-orphan")
    polling_tasks = relationship("DevicePollingTask", back_populates="energetic_object", cascade="all, delete-orphan")


class EnergeticObjectAccess(Base):
    """
    Доступ к энергетическому объекту (шаринг между пользователями)
    """

    __tablename__ = "energetic_object_access"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    energetic_object_id = Column(String(36), ForeignKey("energetic_objects.id"), nullable=False)
    granting_user_cor_id = Column(String(36), ForeignKey("users.cor_id"), nullable=True)
    accessing_user_cor_id = Column(String(36), ForeignKey("users.cor_id"), nullable=False)
    access_level = Column(
        Enum(
            AccessLevel,
            name="accesslevel",
            native_enum=True,
            values_callable=lambda x: [e.name for e in x],
        ),
        nullable=True,
    )
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    energetic_object = relationship("EnergeticObject", back_populates="accesses")
    granting_user = relationship("User", foreign_keys=[granting_user_cor_id], back_populates="energetic_object_granted_accesses")
    accessing_user = relationship("User", foreign_keys=[accessing_user_cor_id], back_populates="energetic_object_received_accesses")


class EnergeticDevice(Base):
    """
    Энергетическое устройство, подключаемое по WebSocket (modbus/Cerbo и т.д.)
    device_id соответствует session_id при подключении.
    """

    __tablename__ = "energetic_devices"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, nullable=True)
    model_name = Column(String, nullable=True, comment="Модель устройства")
    device_id = Column(String(255), unique=True, index=True, nullable=False)
    owner_cor_id = Column(String(250), ForeignKey("users.cor_id"), nullable=False, index=True)
    protocol = Column(String, nullable=True)
    description = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)
    last_seen = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    owner = relationship("User", back_populates="energetic_devices", foreign_keys=[owner_cor_id])
    accesses = relationship("EnergeticDeviceAccess", back_populates="device", cascade="all, delete-orphan")

class EnergeticDeviceAccess(Base):
    """
    Доступ к энергетическому устройству (шаринг между пользователями)
    """

    __tablename__ = "energetic_device_access"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    device_id = Column(String(36), ForeignKey("energetic_devices.id"), nullable=False)
    granting_user_cor_id = Column(String(36), ForeignKey("users.cor_id"), nullable=True)
    accessing_user_cor_id = Column(String(36), ForeignKey("users.cor_id"), nullable=False)
    access_level = Column(
        Enum(
            AccessLevel,
            name="accesslevel",
            native_enum=True,
            values_callable=lambda x: [e.name for e in x],
        ),
        nullable=True,
    )
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    device = relationship("EnergeticDevice", back_populates="accesses")
    granting_user = relationship("User", foreign_keys=[granting_user_cor_id], back_populates="energetic_granted_accesses")
    accessing_user = relationship("User", foreign_keys=[accessing_user_cor_id], back_populates="energetic_received_accesses")


class CerboMeasurement(Base):
    """
    Измерение Cerbo - данные с устройств мониторинга энергопотребления Cerbo GX
    """
    __tablename__ = "cerbo_measurements"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    energetic_object_id = Column(String(36), ForeignKey("energetic_objects.id"), nullable=False, index=True)
    
    created_at = Column(DateTime, nullable=False, default=func.now())
    measured_at = Column(DateTime, nullable=False, comment="Дата и время измерения")

    object_name: Column[str] = Column(String, nullable=True, index=True)

    # Данные из battery_status
    general_battery_power: Column[float] = Column(Float, nullable=False)
    battery_voltage: Column[float] = Column(Float, nullable=True)

    # Данные из inverter_power_status
    inverter_total_ac_output: Column[float] = Column(Float, nullable=False)

    # Данные из ess_ac_status
    ess_total_input_power: Column[float] = Column(Float, nullable=False)

    # Данные из solarchargers_status
    solar_total_pv_power: Column[float] = Column(Float, nullable=False)

    soc: Column[float] = Column(Float, nullable=True)

    # Relationships
    energetic_object = relationship("EnergeticObject", back_populates="measurements")

    def __repr__(self):
        return (
            f"<CerboMeasurement(id={self.id}, measured_at='{self.measured_at}', "
            f"object_name='{self.object_name}', general_battery_power={self.general_battery_power})>"
        )


class DemoSolarBatteryObject(Base):
    """
    Виртуальный солнечный объект для второй версии демо АКБ-Солар.
    """

    __tablename__ = "demo_solar_battery_objects"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, nullable=False, comment="Название объекта")
    peak_power_kw = Column(Float, nullable=False, comment="Пиковая мощность объекта, kW")
    battery_capacity_kwh = Column(Float, nullable=False, comment="Емкость АКБ объекта, kWh")
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    correction_algorithm = relationship(
        "DemoSolarBatteryObjectAlgorithm",
        back_populates="object",
        cascade="all, delete-orphan",
        uselist=False,
    )


class DemoSolarBatteryObjectAlgorithm(Base):
    """
    Настройки локальной коррекции АКБ для одного виртуального объекта.
    """

    __tablename__ = "demo_solar_battery_object_algorithms"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    object_id = Column(
        String(36),
        ForeignKey("demo_solar_battery_objects.id"),
        nullable=False,
        unique=True,
        index=True,
    )
    support_peak_minutes = Column(
        Integer,
        nullable=False,
        comment="Сколько минут АКБ должна держать пиковую мощность объекта",
    )
    correction_threshold_percent = Column(
        Float,
        nullable=False,
        default=10,
        comment="Допустимое изменение отдачи за период, %",
    )
    min_soc_percent = Column(
        Float,
        nullable=False,
        default=20,
        comment="Минимальный SOC АКБ, %",
    )
    recalculation_period_minutes = Column(
        Integer,
        nullable=False,
        default=16,
        comment="Период пересчета алгоритма, минуты",
    )
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    object = relationship("DemoSolarBatteryObject", back_populates="correction_algorithm")


class DemoSolarBatteryGlobalAlgorithm(Base):
    """
    Настройки второй, общей коррекции после локальных АКБ объектов.
    """

    __tablename__ = "demo_solar_battery_global_algorithms"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, nullable=False, default="default", unique=True)
    external_battery_capacity_kwh = Column(
        Float,
        nullable=False,
        comment="Емкость внешней АКБ, kWh",
    )
    support_power_kw = Column(
        Float,
        nullable=True,
        comment="Заданная мощность поддержки внешней АКБ, kW",
    )
    support_peak_minutes = Column(
        Integer,
        nullable=False,
        default=16,
        comment="Сколько минут внешняя АКБ должна держать заданную мощность",
    )
    correction_threshold_percent = Column(
        Float,
        nullable=False,
        default=10,
        comment="Допустимое изменение общей отдачи за период, %",
    )
    min_soc_percent = Column(
        Float,
        nullable=False,
        default=20,
        comment="Минимальный SOC внешней АКБ, %",
    )
    recalculation_period_minutes = Column(
        Integer,
        nullable=False,
        default=16,
        comment="Период пересчета глобального алгоритма, минуты",
    )
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class DeyeAlarmEvent(Base):
    """
    История событий ошибок/предупреждений Deye инвертора.
    Хранит raw bitmap-слова и декодированные fault/warning списки.
    """
    __tablename__ = "deye_alarm_events"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    energetic_object_id = Column(String(36), ForeignKey("energetic_objects.id"), nullable=False, index=True)
    object_name = Column(String, nullable=True, index=True)
    measured_at = Column(DateTime, nullable=False, index=True, comment="Время snapshot состояния алармов")
    source_task_id = Column(String, nullable=True, comment="ID polling task, которая зафиксировала событие")

    fault_word_1 = Column(Integer, nullable=False, default=0)
    fault_word_2 = Column(Integer, nullable=False, default=0)
    warning_word_1 = Column(Integer, nullable=False, default=0)
    warning_word_2 = Column(Integer, nullable=False, default=0)

    faults = Column(JSONB, nullable=False, default=list, server_default='[]')
    warnings = Column(JSONB, nullable=False, default=list, server_default='[]')

    created_at = Column(DateTime, nullable=False, server_default=func.now())

    energetic_object = relationship("EnergeticObject")

    __table_args__ = (
        Index("ix_deye_alarm_events_object_measured_at", "energetic_object_id", "measured_at"),
    )


class EnergeticSchedule(Base):
    """
    Расписание энергетических задач - управление режимами работы инвертора
    """
    __tablename__ = "energetic_schedule"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    energetic_object_id = Column(String(36), ForeignKey("energetic_objects.id"), nullable=False, index=True)

    # Параметры времени
    start_time = Column(Time, nullable=False, comment="Время начала работы режима (ЧЧ:ММ)")
    duration = Column(Interval, nullable=False, comment="Продолжительность режима")
    end_time = Column(Time, nullable=False, comment="Время окончания работы режима")

    # Параметры работы инвертора
    grid_feed_w = Column(Integer, nullable=False, comment="Отдача в сеть (Вт)")
    battery_level_percent = Column(Integer, nullable=False, comment="Целевой уровень батареи (%)")

    # Статусы расписания
    is_active = Column(Boolean, nullable=False, default=False)
    is_manual_mode = Column(Boolean, nullable=False, default=False)
    charge_battery_value = Column(Integer, nullable=False, default=300)

    # Relationships
    energetic_object = relationship("EnergeticObject", back_populates="schedules")

    def __repr__(self):
        return (
            f"<EnergeticSchedule(id='{self.id}', start_time={self.start_time}, "
            f"duration={self.duration}, end_time={self.end_time}, "
            f"grid_feed_w={self.grid_feed_w}, battery_level_percent={self.battery_level_percent}, "
            f"charge_battery_value={self.charge_battery_value}, is_active={self.is_active}, "
            f"is_manual_mode={self.is_manual_mode})>"
        )


class DevicePollingTask(Base):
    """
    Задача фонового опроса устройства - настройка периодического опроса энергетических объектов
    """
    __tablename__ = "device_polling_tasks"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    energetic_object_id = Column(String(36), ForeignKey("energetic_objects.id"), nullable=False, index=True)
    
    task_type = Column(
        String, 
        nullable=False, 
        comment="Тип задачи: cerbo_collection | schedule_check | modbus_registers | custom_command"
    )
    
    command_config = Column(
        JSONB,
        nullable=True,
        comment="Конфигурация команд в JSON формате (какие регистры читать, параметры команды)"
    )
    
    interval_ms = Column(
        Float,
        nullable=False,
        default=5000,
        comment="Интервал опроса в миллисекундах"
    )
    
    is_active = Column(
        Boolean,
        nullable=False,
        default=True,
        comment="Активна ли задача опроса"
    )
    
    created_at = Column(DateTime, nullable=False, default=func.now())
    updated_at = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())
    
    # Relationships
    energetic_object = relationship("EnergeticObject", back_populates="polling_tasks")
    
    def __repr__(self):
        return (
            f"<DevicePollingTask(id='{self.id}', object_id='{self.energetic_object_id}', "
            f"task_type='{self.task_type}', interval={self.interval_ms}ms, is_active={self.is_active})>"
        )


class WebSocketBroadcastTask(Base):
    """
    Задача фоновой рассылки команд через WebSocket на конкретное устройство
    """
    __tablename__ = "websocket_broadcast_tasks"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    task_name = Column(String, nullable=False, comment="Имя задачи")
    session_id = Column(String, nullable=False, index=True, comment="Session ID устройства")
    
    command_type = Column(
        String,
        nullable=False,
        comment="Тип команды: pi30 | modbus_read | modbus_tcp"
    )
    
    command_payload = Column(
        JSONB,
        nullable=False,
        comment="Полезная нагрузка команды (pi30/modbus_read/modbus_tcp)"
    )
    
    interval_ms = Column(
        Float,
        nullable=False,
        default=5000,
        comment="Интервал отправки команды в миллисекундах"
    )
    
    is_active = Column(
        Boolean,
        nullable=False,
        default=True,
        comment="Активна ли задача рассылки"
    )
    
    created_at = Column(DateTime, nullable=False, default=func.now())
    updated_at = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())
    created_by = Column(String, nullable=True, comment="Кто создал задачу (user_id или admin)")
    
    def __repr__(self):
        return (
            f"<WebSocketBroadcastTask(id='{self.id}', task_name='{self.task_name}', "
            f"session_id='{self.session_id}', command_type='{self.command_type}', "
            f"interval={self.interval_ms}ms, is_active={self.is_active})>"
        )


class PowerMeasurement(Base):
    """
    Протокол-независимая история мощностей для графиков и энергобаланса.
    """

    __tablename__ = "power_measurements"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    energetic_object_id = Column(
        String(36),
        ForeignKey("energetic_objects.id"),
        nullable=False,
        index=True,
    )
    object_name = Column(String, nullable=True, index=True)
    source_protocol = Column(String, nullable=True, index=True)
    source_task_id = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False, default=func.now())
    measured_at = Column(DateTime, nullable=False, index=True)

    solar_power_w = Column(Float, nullable=True)
    battery_power_w = Column(Float, nullable=True)
    battery_voltage_v = Column(Float, nullable=True)
    battery_soc = Column(Float, nullable=True)
    grid_power_w = Column(Float, nullable=True)
    load_power_w = Column(Float, nullable=True)
    generator_power_w = Column(Float, nullable=True)

    raw_snapshot = Column(JSONB, nullable=True)

    energetic_object = relationship("EnergeticObject", back_populates="power_measurements")


class EnergyMeterMeasurement(Base):
    """Scaled TAC4300CT meter readings in the units shown by the meter UI."""

    __tablename__ = "energy_meter_measurements"
    __table_args__ = (Index("ix_energy_meter_object_time", "energetic_object_id", "measured_at"),)

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    energetic_object_id = Column(String(36), ForeignKey("energetic_objects.id"), nullable=False)
    measured_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    active_power_kw = Column(Float, nullable=False)
    reactive_power_kvar = Column(Float, nullable=False)
    apparent_power_kva = Column(Float, nullable=False)
    power_factor = Column(Float, nullable=False)
    frequency_hz = Column(Float, nullable=False)
    angle_deg = Column(Float, nullable=False)
    voltage_l1_n_v = Column(Float, nullable=False)
    voltage_l2_n_v = Column(Float, nullable=False)
    voltage_l3_n_v = Column(Float, nullable=False)
    voltage_l1_l2_v = Column(Float, nullable=False)
    voltage_l2_l3_v = Column(Float, nullable=False)
    voltage_l3_l1_v = Column(Float, nullable=False)
    apparent_energy_kvah = Column(Float, nullable=False)

    energetic_object = relationship("EnergeticObject", back_populates="energy_meter_measurements")


class FlywheelMeasurements(Base):
    """
    Таблица с измерениями маховика
    """
    __tablename__ = "flywheel_measurements"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    energetic_object_id = Column(String(36), ForeignKey("energetic_objects.id"), nullable=False, index=True)
    created_at = Column(DateTime, nullable=False, default=func.now())
    measured_at = Column(DateTime, nullable=False, comment="Дата и время измерения")
    flywheel_speed_rpm = Column(Float, nullable=True, comment="Скорость маховика в оборотах в минуту")
    flywheel_pwm = Column(Float, nullable=True, comment="Текущее значение ШИМ для управления маховиком")
    flywheel_frequency = Column(Float, nullable=True, comment="Текущая частота управления маховиком в Гц")
    flywheel_arr_timer = Column(Float, nullable=True, comment="Текущее значение ARR таймера для управления маховиком")
    flywheel_pwm_raw = Column(Float, nullable=True, comment="Текущее сырое значение ШИМ")
    flywheel_voltage = Column(Float, nullable=True, comment="Напряжение")
    flywheel_current = Column(Float, nullable=True, comment="Ток")
    flywheel_power = Column(Float, nullable=True, comment="Мощность")
    stator_temperature = Column(JSONB, nullable=True, comment="Значения температур статоров в виде массива JSON")

    energetic_object = relationship("EnergeticObject", back_populates="flywheel_measurements")
