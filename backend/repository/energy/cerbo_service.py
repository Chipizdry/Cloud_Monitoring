import asyncio
from datetime import datetime, timedelta
from uuid import uuid4
from fastapi import FastAPI, HTTPException
from sqlalchemy import UUID, delete, func, or_, select, update
from typing import Any, Dict, List, Optional, Tuple
from math import ceil

from backend.database.models import CerboMeasurement, DevicePollingTask, EnergeticObject, EnergeticSchedule
from backend.database.models.energy import DeyeAlarmEvent, EnergeticObjectAccess, WebSocketBroadcastTask
from backend.repository.energy.cor_bridges import normalize_cor_bridge_ids
from sqlalchemy.ext.asyncio import AsyncSession
from backend.schemas import (
    EnergeticObjectCreate,
    EnergeticObjectUpdate,
    EnergeticScheduleBase,
    EnergeticScheduleCreate,
    EnergeticScheduleCreateForObject,
    FullDeviceMeasurementCreate,
    FullDeviceMeasurementResponse,
    CerboMeasurementResponse
)
from loguru import logger
from pymodbus.client import AsyncModbusTcpClient
from backend.database.db import async_session_maker

error_count = 0

COLLECTION_INTERVAL_SECONDS = 2

# Конфигурация Modbus
MODBUS_IP = "91.203.25.12"
MODBUS_PORT = 502
BATTERY_ID = 225  # Основная батарея
INVERTER_ID = 100  # Инвертор
ESS_UNIT_ID = 227  # Система управления (ESS)

# Определение регистров Modbus
REGISTERS = {
    "soc": 266,  # % SoC (0.1%)
    "voltage": 259,  # Напряжение (x100)
    "current": 261,  # Ток (x10)
    "temperature": 262,
    "power_int32": 256,  # Мощность (signed int16)
    "power": 258,  # Мощность (signed int16)
    "soh": 304,  # Состояние здоровья (0.1%)
}

INVERTER_REGISTERS = {
    "inverter_power": 870,  # Мощность инвертора/зарядного устройства (DC)
    "output_power_l1": 878,  # Мощность на выходе инвертора (L1)
    "output_power_l2": 880,  # Мощность на выходе инвертора (L2)
    "output_power_l3": 882,  # Мощность на выходе инвертора (L3)
}

ESS_REGISTERS = {
    # Базовые регистры
    "switch_position": 33,  # Положение переключателя
    "temperature_alarm": 34,  # Температурная тревога
    "low_battery_alarm": 35,  # Тревога низкого заряда
    "overload_alarm": 36,  # Тревога перегрузки
    "disable_charge": 38,  # Запрет на заряд (0/1)
    "disable_feed": 39,  # Запрет на подачу в сеть (0/1)
    # 32-битные регистры мощности
    "ess_power_setpoint_l1": 96,  # Установка мощности фаза 1 (int32)
    "ess_power_setpoint_l2": 98,  # Установка мощности фаза 2 (int32)
    "ess_power_setpoint_l3": 100,  # Установка мощности фаза 3 (int32)
    # Дополнительные параметры
    "disable_ov_feed": 65,  # Запрет фид-ина при перегрузке
    "ov_feed_limit_l1": 66,  # Лимит мощности для L1
    "ov_feed_limit_l2": 67,  # Лимит мощности для L2
    "ov_feed_limit_l3": 68,  # Лимит мощности для L3
    "setpoints_as_limit": 71,  # Использовать setpoints как лимит
    "ov_offset_mode": 72,  # Режим оффсета (0=1V, 1=100mV)
}


ESS_REGISTERS_MODE = {
    "switch_position": 33,
}

ESS_REGISTERS_FLAGS = {
    "disable_charge": 38,
    "disable_feed": 39,
    "disable_pv_inverter": 56,
    "do_not_feed_in_ov": 65,
    "setpoints_as_limit": 71,
    "ov_offset_mode": 72,
    "prefer_renewable": 102,
}

ESS_REGISTERS_POWER = {
    "ess_power_setpoint_l1": 96,  # 32-bit
    "ess_power_setpoint_l2": 98,
    "ess_power_setpoint_l3": 100,
    "max_feed_in_l1": 66,
    "max_feed_in_l2": 67,
    "max_feed_in_l3": 68,
}

ESS_REGISTERS_ALARMS = {
    "temperature_alarm": 34,
    "low_battery_alarm": 35,
    "overload_alarm": 36,
    "temp_sensor_alarm": 42,
    "voltage_sensor_alarm": 43,
    "grid_lost": 64,
}


async def create_modbus_client(app):
    try:
        if hasattr(app.state, "modbus_client") and app.state.modbus_client:
            await app.state.modbus_client.close()
            logger.info("🔌 Старый клиент Modbus закрыт")

        app.state.modbus_client = AsyncModbusTcpClient(host=MODBUS_IP, port=MODBUS_PORT)
        await app.state.modbus_client.connect()

        if not app.state.modbus_client.connected:
            logger.error("❌ Не удалось подключиться к Modbus серверу")
        else:
            logger.info("✅ Подключение к Modbus серверу установлено")

    except Exception as e:
        logger.exception("❗ Ошибка при создании Modbus клиента", exc_info=e)


#

# --- Клиент хранения ---
# async def create_modbus_client(app):
#    app.state.modbus_client = AsyncModbusTcpClient(host=MODBUS_IP, port=MODBUS_PORT)
#    await app.state.modbus_client.connect()


async def close_modbus_client(app):
    client = getattr(app.state, "modbus_client", None)
    if client and client.connected:
        await client.close()
        logger.info("🔌 Клиент Modbus отключён")


# Получение клиента с реконнектом при ошибках
async def get_modbus_client(app):
    global error_count
    client = getattr(app.state, "modbus_client", None)

    # Если клиент не подключен — создаём новый
    if client is None or not client.connected:
        logger.warning(f"🔄 Переподключение Modbus клиента... (errors: {error_count})")

        # Закрытие старого клиента, если есть
        if client:
            try:
                await client.close()
            except Exception as e:
                logger.warning(f"⚠️ Ошибка при закрытии клиента: {e}")

        # Новый клиент
        new_client = AsyncModbusTcpClient(host=MODBUS_IP, port=MODBUS_PORT)
        await new_client.connect()

        if not new_client.connected:
            logger.error("❌ Не удалось переподключиться к Modbus серверу")
        else:
            logger.info("✅ Новое подключение к Modbus успешно")
        error_count = 0  # сброс после успешного подключения
        app.state.modbus_client = new_client

        return new_client

    # Если клиент есть и подключён
    return client


def register_modbus_error():
    global error_count
    error_count += 1
    logger.warning(f"❗ Modbus ошибка #{error_count}")


# Функции декодирования
def decode_signed_16(value: int) -> int:
    return value - 0x10000 if value >= 0x8000 else value


def decode_signed_32(high: int, low: int) -> int:
    combined = (high << 16) | low
    return combined - 0x100000000 if combined >= 0x80000000 else combined



async def get_device_measurements_paginated(
    db: AsyncSession,
    page: int = 1,
    page_size: int = 10,
    object_name: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> Tuple[List[CerboMeasurement], int]:
    """
    Получает записи CerboMeasurement с пагинацией и необязательными фильтрами.

    Args:
        db: Асинхронная сессия базы данных.
        page: Номер текущей страницы (начиная с 1).
        page_size: Количество записей на странице.
        object_name: Необязательный фильтр по имени объекта.
        start_date: Необязательный фильтр по начальной дате measured_at.
        end_date: Необязательный фильтр по конечной дате measured_at.

    Returns:
        Кортеж, содержащий список объектов CerboMeasurement и общее количество записей.
    """

    query = select(CerboMeasurement)
    count_query = select(func.count()).select_from(CerboMeasurement)

    if object_name:
        query = query.where(CerboMeasurement.object_name == object_name)
        count_query = count_query.where(CerboMeasurement.object_name == object_name)

    if start_date:
        query = query.where(CerboMeasurement.measured_at >= start_date)
        count_query = count_query.where(CerboMeasurement.measured_at >= start_date)

    if end_date:
        query = query.where(CerboMeasurement.measured_at <= end_date)
        count_query = count_query.where(CerboMeasurement.measured_at <= end_date)

    offset = (page - 1) * page_size
    query = (
        query.offset(offset)
        .limit(page_size)
        .order_by(CerboMeasurement.measured_at.desc())
    )

    result = await db.execute(query)
    measurements = result.scalars().all()

    total_count_result = await db.execute(count_query)
    total_count = total_count_result.scalar_one()

    return measurements, total_count


async def get_device_measurements_by_object_paginated(
    db: AsyncSession,
    page: int = 1,
    page_size: int = 10,
    energetic_object_id: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> Tuple[List[CerboMeasurement], int]:
    """
    Получает записи измерений инвертора с пагинацией и необязательными фильтрами по ID энергетического обьекта .

    Args:
        db: Асинхронная сессия базы данных.
        page: Номер текущей страницы (начиная с 1).
        page_size: Количество записей на странице.
        energetic_object_id: Фильтр по ID энергетического объекта.
        start_date: Необязательный фильтр по начальной дате measured_at.
        end_date: Необязательный фильтр по конечной дате measured_at.

    Returns:
        Кортеж, содержащий список объектов CerboMeasurement и общее количество записей.
    """

    query = select(CerboMeasurement)
    count_query = select(func.count()).select_from(CerboMeasurement)

    if energetic_object_id:
        query = query.where(CerboMeasurement.energetic_object_id == energetic_object_id)
        count_query = count_query.where(CerboMeasurement.energetic_object_id == energetic_object_id)

    if start_date:
        query = query.where(CerboMeasurement.measured_at >= start_date)
        count_query = count_query.where(CerboMeasurement.measured_at >= start_date)

    if end_date:
        query = query.where(CerboMeasurement.measured_at <= end_date)
        count_query = count_query.where(CerboMeasurement.measured_at <= end_date)

    offset = (page - 1) * page_size
    query = (
        query.offset(offset)
        .limit(page_size)
        .order_by(CerboMeasurement.measured_at.desc())
    )

    result = await db.execute(query)
    measurements = result.scalars().all()

    total_count_result = await db.execute(count_query)
    total_count = total_count_result.scalar_one()

    return measurements, total_count

async def create_schedule(
    db: AsyncSession, schedule_data: EnergeticScheduleCreate
) -> EnergeticSchedule:
    """
    Создает новое расписание в базе данных.
    """
    duration_delta = timedelta(
        hours=schedule_data.duration_hours, minutes=schedule_data.duration_minutes
    )

    temp_start_datetime = datetime.combine(
        datetime.min.date(), schedule_data.start_time
    )
    calculated_end_time = (temp_start_datetime + duration_delta).time()

    db_schedule = EnergeticSchedule(
        start_time=schedule_data.start_time,
        duration=duration_delta,
        end_time=calculated_end_time,
        grid_feed_w=schedule_data.grid_feed_w,
        battery_level_percent=schedule_data.battery_level_percent,
        charge_battery_value=schedule_data.charge_battery_value,
        is_manual_mode=schedule_data.is_manual_mode,
    )
    db.add(db_schedule)
    await db.commit()
    await db.refresh(db_schedule)
    return db_schedule


async def create_schedule_with_energetic_object_id(
    db: AsyncSession, schedule_data: EnergeticScheduleCreateForObject
) -> EnergeticSchedule:
    """
    Создает новое расписание в базе данных под энергетический обьект.
    """
    duration_delta = timedelta(
        hours=schedule_data.duration_hours, minutes=schedule_data.duration_minutes
    )

    temp_start_datetime = datetime.combine(
        datetime.min.date(), schedule_data.start_time
    )
    calculated_end_time = (temp_start_datetime + duration_delta).time()

    db_schedule = EnergeticSchedule(
        start_time=schedule_data.start_time,
        duration=duration_delta,
        end_time=calculated_end_time,
        grid_feed_w=schedule_data.grid_feed_w,
        battery_level_percent=schedule_data.battery_level_percent,
        charge_battery_value=schedule_data.charge_battery_value,
        is_manual_mode=schedule_data.is_manual_mode,
        energetic_object_id=schedule_data.energetic_object_id
    )
    db.add(db_schedule)
    await db.commit()
    await db.refresh(db_schedule)
    return db_schedule


async def get_schedule_by_id(
    db: AsyncSession, schedule_id: str
) -> Optional[EnergeticSchedule]:
    """
    Получает расписание по его ID.
    """
    result = await db.execute(
        select(EnergeticSchedule).where(EnergeticSchedule.id == schedule_id)
    )
    return result.scalars().first()


async def get_all_schedules_by_object_id(db: AsyncSession, energetic_object_id: str) -> List[EnergeticSchedule]:
    """
    Получает все расписания (активные и неактивные), отсортированные по времени начала по энергетическому обьекту.
    """
    result = await db.execute(
        select(EnergeticSchedule).where(EnergeticSchedule.energetic_object_id == energetic_object_id).order_by(EnergeticSchedule.start_time)
    )
    return result.scalars().all()


async def get_all_schedules(db: AsyncSession) -> List[EnergeticSchedule]:
    """
    Получает все расписания (активные и неактивные), отсортированные по времени начала.
    """
    result = await db.execute(
        select(EnergeticSchedule).order_by(EnergeticSchedule.start_time)
    )
    return result.scalars().all()


async def update_schedule(
    db: AsyncSession, schedule_id: str, schedule_data: EnergeticScheduleBase
) -> Optional[EnergeticSchedule]:
    """
    Обновляет существующее расписание по ID.
    """
    db_schedule = await get_schedule_by_id(db, schedule_id)
    if not db_schedule:
        return None

    duration_delta = timedelta(
        hours=schedule_data.duration_hours, minutes=schedule_data.duration_minutes
    )

    temp_start_datetime = datetime.combine(
        datetime.min.date(), schedule_data.start_time
    )
    calculated_end_time = (temp_start_datetime + duration_delta).time()

    db_schedule.start_time = schedule_data.start_time
    db_schedule.duration = duration_delta
    db_schedule.end_time = calculated_end_time
    db_schedule.grid_feed_w = schedule_data.grid_feed_w
    db_schedule.battery_level_percent = schedule_data.battery_level_percent
    db_schedule.charge_battery_value = schedule_data.charge_battery_value
    db_schedule.is_manual_mode = schedule_data.is_manual_mode

    await db.commit()
    await db.refresh(db_schedule)
    return db_schedule


async def delete_schedule(db: AsyncSession, schedule_id: str) -> bool:
    """
    Удаляет расписание по ID.
    """
    result = await db.execute(
        delete(EnergeticSchedule).where(EnergeticSchedule.id == schedule_id)
    )
    await db.commit()
    return result.rowcount > 0


async def update_schedule_is_active_status(
    db: AsyncSession, schedule_id: str, is_active_status: bool
):

    stmt = (
        update(EnergeticSchedule)
        .where(EnergeticSchedule.id == schedule_id)
        .values(is_active=is_active_status)
    )
    await db.execute(stmt)
    await db.commit()


async def ensure_modbus_connected(app: FastAPI):
    modbus_client = app.state.modbus_client
    if not modbus_client or not modbus_client.connected:
        logger.critical("Modbus client not connected. Attempting to reconnect...")
        try:
            if modbus_client is None:
                modbus_client = AsyncModbusTcpClient(host=MODBUS_IP, port=MODBUS_PORT)
                app.state.modbus_client = modbus_client
            await modbus_client.connect()
            # logger.debug("Modbus client reconnected.")
        except Exception as e:
            logger.error(f"Failed to reconnect Modbus client: {e}. Skipping this cycle.", exc_info=True)
            raise 
    return modbus_client 


async def read_grid_feed_w(app: FastAPI) -> Optional[int]:
    """
    Читает текущее значение AC Power Setpoint Fine (регистр 2703) и возвращает его в Ваттах.
    """
    modbus_client = await ensure_modbus_connected(app)
    if modbus_client is None:
        return None
    try:
        result = await modbus_client.read_holding_registers(address=2703, count=1, slave=INVERTER_ID)
        if result.isError():
            logger.error(f"Ошибка чтения регистра 2703: {result}")
            return None
        
        register_value = result.registers[0]
        
        if register_value > 32767:  
            actual_value = register_value - (1 << 16)
        else:
            actual_value = register_value
            
        actual_value_watts = actual_value * 100
        # logger.debug(f"Прочитано AC Power Setpoint Fine: {actual_value_watts} W (регистр 2703 = {register_value})")
        return actual_value_watts
    except Exception as e:
        logger.error(f"Ошибка при чтении AC Power Setpoint Fine: {e}", exc_info=True)
        return None

async def read_vebus_soc(app: FastAPI) -> Optional[int]:
    """
    Читает текущее значение VE.Bus SoC (регистр 2901) и возвращает его в процентах.
    """
    modbus_client = await ensure_modbus_connected(app)
    if modbus_client is None:
        return None
    try:
        result = await modbus_client.read_holding_registers(address=2901, count=1, slave=INVERTER_ID)
        if result.isError():
            logger.error(f"Ошибка чтения регистра 2901: {result}")
            return None
        
        register_value = result.registers[0]
        actual_value_percent = register_value / 10
        # logger.debug(f"Прочитано VE.Bus SoC: {actual_value_percent}% (регистр 2901 = {register_value})")
        return int(actual_value_percent) 
    except Exception as e:
        logger.error(f"Ошибка при чтении VE.Bus SoC: {e}", exc_info=True)
        return None

async def read_dvcc_max_charge_current(app: FastAPI) -> Optional[int]:
    """
    Читает текущее значение DVCC max charge current (регистр 2705) и возвращает его в Амперах.
    """
    modbus_client = await ensure_modbus_connected(app)
    if modbus_client is None:
        return None
    try:
        result = await modbus_client.read_holding_registers(address=2705, count=1, slave=INVERTER_ID)
        if result.isError():
            logger.error(f"Ошибка чтения регистра 2705: {result}")
            return None
        
        register_value = result.registers[0]
        
        if register_value > 32767:  
            actual_value = register_value - (1 << 16)
        else:
            actual_value = register_value
            
        # logger.debug(f"Прочитано DVCC max charge current: {actual_value} A (регистр 2705 = {register_value})")
        return actual_value
    except Exception as e:
        logger.error(f"Ошибка при чтении DVCC max charge current: {e}", exc_info=True)
        return None

async def send_grid_feed_w_command(app: FastAPI, grid_feed_w: int):
    modbus_client = await ensure_modbus_connected(app)
    if modbus_client is None: 
        return {"status": "error", "message": "Modbus client not available"}
    try:
        slave = INVERTER_ID
        # Преобразуем значение для записи в регистр
        register_value = int(grid_feed_w / 100)
        
        # Преобразование отрицательных чисел в формат Modbus (дополнительный код)
        if register_value < 0:
            register_value = (1 << 16) + register_value  # Преобразование в 16-битное представление
            
        # Проверяем, что значение вписывается в int16
        if register_value < 0 or register_value > 65535:
            raise HTTPException(status_code=400, detail="Значение выходит за допустимые пределы")
        
        # Записываем значение в регистр 2703
        await modbus_client.write_register(
            address=2703,
            value=register_value,
            slave=slave
        )
        global error_count
        error_count = 0  
        # logger.debug(f"✅ Установлено AC Power Setpoint Fine: {grid_feed_w} W (регистр 2703 = {register_value})")
        return {"status": "ok", "value": grid_feed_w}
    except Exception as e:
        logger.error(
            f" Unhandled error during periodic data collection: {e}",
            exc_info=True,
        )




async def send_vebus_soc_command(app: FastAPI, battery_level_percent: int):
    modbus_client = await ensure_modbus_connected(app)
    if modbus_client is None: 
        return {"status": "error", "message": "Modbus client not available"}
    try:
        scaled_value = int(battery_level_percent * 10)
        await modbus_client.write_register(
            address=2901,  # адрес регистра VE.Bus SoC
            value=scaled_value,
            slave =INVERTER_ID
        )
        global error_count
        error_count = 0
        return {"status": "ok"}
    except Exception as e:
        logger.error(
            f" Unhandled error during periodic data collection: {e}",
            exc_info=True,
        )



async def send_dvcc_max_charge_current_command(app: FastAPI, charge_battery_value: int):
    modbus_client = await ensure_modbus_connected(app)
    if modbus_client is None: 
        return {"status": "error", "message": "Modbus client not available"}
        
    try:
        slave = INVERTER_ID
        value = charge_battery_value
        # Проверка границ значений int16
        if not -32768 <= value <= 32767:
            raise HTTPException(status_code=400, detail="Значение выходит за пределы int16")
        # Преобразуем в формат Modbus (uint16) для передачи
        if value < 0:
            register_value = (1 << 16) + value  # преобразуем -1 в 0xFFFF
        else:
            register_value = value
        # Запись в регистр
        await modbus_client.write_register(address=2705, value=register_value, slave=slave)

        # logger.debug(f"✅ Установлен DVCC max charge current: {value} A (регистр 2705 = {register_value})")
        return {"status": "ok", "value": value}
    except Exception as e:
        logger.error(
            f" Unhandled error during periodic data collection: {e}",
            exc_info=True,
        )



async def get_averaged_measurements_service(
    db: AsyncSession,
    object_name: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    intervals: int = 60
) -> List[CerboMeasurementResponse]:
    if not start_date or not end_date:
        raise ValueError("Необходимо указать start_date и end_date")

    # Получаем все данные за период одним запросом
    query = select(CerboMeasurement).where(
        CerboMeasurement.measured_at >= start_date,
        CerboMeasurement.measured_at <= end_date
    )
    
    if object_name:
        query = query.where(CerboMeasurement.object_name == object_name)
    
    result = await db.execute(query)
    all_measurements = result.scalars().all()

    if not all_measurements:
        return []

    # Группируем измерения по интервалам
    interval_size = (end_date - start_date) / intervals
    grouped_measurements = [[] for _ in range(intervals)]
    
    for measurement in all_measurements:
        interval_idx = min(
            int((measurement.measured_at - start_date) / interval_size),
            intervals - 1
        )
        grouped_measurements[interval_idx].append(measurement)

    # Вычисляем средние значения
    averaged_results = []
    for i, measurements in enumerate(grouped_measurements):
        if not measurements:
            continue
            
        interval_start = start_date + i * interval_size
        
        def avg(field):
            values = [getattr(m, field) for m in measurements if getattr(m, field) is not None]
            return sum(values) / len(values) if values else None

        base = measurements[0]
        averaged_results.append(CerboMeasurementResponse(
            id=base.id,
            created_at=base.created_at,
            measured_at=interval_start,
            object_name=base.object_name,
            general_battery_power=avg("general_battery_power"),
            inverter_total_ac_output=avg("inverter_total_ac_output"),
            ess_total_input_power=avg("ess_total_input_power"),
            solar_total_pv_power=avg("solar_total_pv_power"),
            soc=avg("soc")
        ))

    return averaged_results    


async def get_energy_measurements_service(
    db: AsyncSession,
    object_name: Optional[str],
    start_date: datetime,
    end_date: datetime,
    interval_minutes: int = 30
) -> dict:
    if not start_date or not end_date:
        raise ValueError("Необходимо указать start_date и end_date")

  
 #   rounded_start = start_date.replace(minute=0, second=0, microsecond=0)
 #   rounded_end = end_date.replace(minute=0, second=0, microsecond=0)

# Округляем: начало вниз до часа, конец вверх до следующего часа
    rounded_start = start_date.replace(minute=0, second=0, microsecond=0)
    if end_date.minute == 0 and end_date.second == 0 and end_date.microsecond == 0:
        rounded_end = end_date
    else:
        rounded_end = (end_date.replace(minute=0, second=0, microsecond=0)
                       + timedelta(hours=1))

    
    # Создаем интервалы
    current_interval_start = rounded_start
    intervals = []
    while current_interval_start < rounded_end:
        current_interval_end = current_interval_start + timedelta(minutes=interval_minutes)
        intervals.append({
            "start": current_interval_start,
            "end": current_interval_end,
            "measurements": [],
            "measurement_count": 0,
            "has_sufficient_data": False
        })
        current_interval_start = current_interval_end

    # Загружаем все измерения (отсортированы)
    query = (
        select(CerboMeasurement)
        .where(CerboMeasurement.measured_at >= rounded_start,
               CerboMeasurement.measured_at <= rounded_end)
        .order_by(CerboMeasurement.measured_at.asc())
    )
    if object_name:
        query = query.where(CerboMeasurement.object_name == object_name)

    result = await db.execute(query)
    all_measurements = result.scalars().all()

    # Распределяем измерения по интервалам 
    for measurement in all_measurements:
        for interval in intervals:
            if interval["start"] <= measurement.measured_at < interval["end"]:
                interval["measurements"].append(measurement)
                interval["measurement_count"] += 1
                break

    # Подсчёт энергии внутри интервалов
    results = []
    for interval in intervals:
        measurements = interval["measurements"]
        interval["has_sufficient_data"] = len(measurements) >= 3

        if len(measurements) < 2:
            results.append({
                "interval_start": interval["start"],
                "interval_end": interval["end"],
                "solar_energy_kwh": 0.0,
                "load_energy_kwh": 0.0,
                "grid_energy_kwh": 0.0,
                "battery_energy_kwh": 0.0,
                "measurement_count": interval["measurement_count"],
                "has_sufficient_data": interval["has_sufficient_data"]
            })
            continue

        solar_energy = 0.0
        load_energy = 0.0
        grid_energy = 0.0
        battery_energy = 0.0

        for j in range(1, len(measurements)):
            prev = measurements[j - 1]
            curr = measurements[j]
            delta_h = (curr.measured_at - prev.measured_at).total_seconds() / 3600.0

            if prev.solar_total_pv_power is not None:
                solar_energy += (prev.solar_total_pv_power / 1000.0) * delta_h
            if prev.inverter_total_ac_output is not None:
                load_energy += (prev.inverter_total_ac_output / 1000.0) * delta_h
            if prev.ess_total_input_power is not None:
                grid_energy += (prev.ess_total_input_power / 1000.0) * delta_h
            if prev.general_battery_power is not None:
                battery_energy += (prev.general_battery_power / 1000.0) * delta_h

        results.append({
            "interval_start": interval["start"],
            "interval_end": interval["end"],
            "solar_energy_kwh": round(solar_energy, 3),
            "load_energy_kwh": round(load_energy, 3),
            "grid_energy_kwh": round(grid_energy, 3),
            "battery_energy_kwh": round(battery_energy, 3),
            "measurement_count": interval["measurement_count"],
            "has_sufficient_data": interval["has_sufficient_data"]
        })

    # точные totals как сумма квантов между всеми подряд идущими измерениями 
    total_solar = 0.0
    total_load = 0.0
    total_grid_import = 0.0
    total_grid_export = 0.0
    total_battery = 0.0

    for j in range(1, len(all_measurements)):
        prev = all_measurements[j - 1]
        curr = all_measurements[j]
        delta_h = (curr.measured_at - prev.measured_at).total_seconds() / 3600.0
        if delta_h <= 0:
            continue

        if prev.solar_total_pv_power is not None:
            total_solar += (prev.solar_total_pv_power / 1000.0) * delta_h
        if prev.inverter_total_ac_output is not None:
            total_load += (prev.inverter_total_ac_output / 1000.0) * delta_h
        if prev.ess_total_input_power is not None:
            grid_q = (prev.ess_total_input_power / 1000.0) * delta_h
            if grid_q >= 0:
                total_grid_import += grid_q
            else:
                total_grid_export += abs(grid_q)
        if prev.general_battery_power is not None:
            total_battery += (prev.general_battery_power / 1000.0) * delta_h

    return {
        "intervals": results,
        "totals": {
            # округляем с точностью до 0 знаков — точные интегральные суммы квантов
            "solar_energy_total": round(total_solar, 0),
            "load_energy_total": round(total_load, 0),
            "grid_import_total": round(total_grid_import, 0),
            "grid_export_total": round(total_grid_export, 0),
            "battery_energy_total": round(total_battery, 0),
        }
    }


# CRUD по энергетическим обьектам / инверторам


def _build_default_polling_tasks(
    protocol: Optional[str],
    modbus_config_file: Optional[str],
    vendor: Optional[str] = None,
    model_name: Optional[str] = None,
) -> list[dict[str, Any]]:
    if vendor and model_name:
        from backend.services.energy.modbus_polling_preset_loader import (
            build_polling_task_records,
            get_polling_preset,
        )
        preset = get_polling_preset(vendor, model_name)
        if preset is not None:
            return build_polling_task_records(preset)

    if protocol == "modbus_tcp" and modbus_config_file == "victron_cerbo_gx.json":
        return [
            {
                "task_type": "modbus_registers",
                "command_config": {"preset": "grid_monitoring"},
                "interval_ms": 5000,
                "is_active": True,
            },
            {
                "task_type": "schedule_check",
                "command_config": {},
                "interval_ms": 3000,
                "is_active": True,
            },
            {
                "task_type": "cerbo_collection",
                "command_config": {},
                "interval_ms": 2000,
                "is_active": True,
            },
        ]

    if protocol == "modbus_over_tcp" and modbus_config_file == "deye_inverter.json":
        return [
            {
                "task_type": "modbus_registers",
                "command_config": {
                    "register_groups": [
                        "grid_phase_voltage_power",
                        "grid_current",
                        "gen_relay_status",
                        "generator_extended",
                        "battery",
                        "load",
                        "service_extended",
                        "inverter",
                        "solar",
                        "solar_high",
                        "power32_v104",
                        "energy_service",
                    ]
                },
                "interval_ms": 15000,
                "is_active": True,
            }
        ]

    return []


async def _object_has_polling_tasks(db: AsyncSession, object_id: str) -> bool:
    result = await db.execute(
        select(func.count())
        .select_from(DevicePollingTask)
        .where(DevicePollingTask.energetic_object_id == object_id)
    )
    return bool(result.scalar_one())


async def _object_has_broadcast_tasks(
    db: AsyncSession,
    object_name: str,
    cor_bridges: list[str],
) -> bool:
    if not cor_bridges:
        return False

    result = await db.execute(
        select(func.count())
        .select_from(WebSocketBroadcastTask)
        .where(
            WebSocketBroadcastTask.session_id.in_(cor_bridges),
            WebSocketBroadcastTask.task_name.like(f"{object_name}:%"),
        )
    )
    return bool(result.scalar_one())


async def create_energetic_object(
    db: AsyncSession,
    obj_data: EnergeticObjectCreate,
    *,
    owner_cor_id: str | None = None,
) -> EnergeticObject:
    existing = await db.execute(
        select(EnergeticObject).where(EnergeticObject.name == obj_data.name)
    )
    if existing.scalars().first():
        raise HTTPException(
            status_code=409,
            detail=f"Энергетический объект с именем '{obj_data.name}' уже существует"
        )
    
    db_obj = EnergeticObject(**obj_data.model_dump(), owner_cor_id=owner_cor_id)
    db.add(db_obj)
    await db.flush()
    db_obj.cor_bridges = await normalize_cor_bridge_ids(db, list(db_obj.cor_bridges or []))

    default_tasks = _build_default_polling_tasks(
        protocol=db_obj.protocol,
        modbus_config_file=db_obj.modbus_config_file,
        vendor=db_obj.vendor,
        model_name=db_obj.model_name,
    )

    for task in default_tasks:
        db.add(
            DevicePollingTask(
                energetic_object_id=db_obj.id,
                task_type=task["task_type"],
                command_config=task["command_config"],
                interval_ms=task["interval_ms"],
                is_active=task["is_active"],
            )
        )

    # Auto-create WebSocket broadcast tasks from inverter preset (COR Bridge polling)
    if db_obj.vendor and db_obj.model_name and db_obj.cor_bridges:
        from backend.services.energy.inverter_preset_loader import (
            build_broadcast_task_records,
            get_preset,
        )
        preset = get_preset(db_obj.vendor, db_obj.model_name)
        if preset:
            slave_id = (db_obj.slave_ids[0] if db_obj.slave_ids else 1)
            for bridge_session_id in db_obj.cor_bridges:
                records = build_broadcast_task_records(
                    preset=preset,
                    session_id=bridge_session_id,
                    slave_id=slave_id,
                    host=db_obj.ip_address,
                    port=db_obj.port,
                    task_name_prefix=f"{db_obj.name}:{bridge_session_id}",
                    created_by=owner_cor_id,
                    is_active=bool(db_obj.is_active),
                )
                for record in records:
                    db.add(WebSocketBroadcastTask(**record))
            logger.info(
                f"Auto-created COR Bridge broadcast tasks for object '{db_obj.name}' "
                f"(vendor={db_obj.vendor}, model={db_obj.model_name}, "
                f"bridges={db_obj.cor_bridges}, is_active={bool(db_obj.is_active)})"
            )

    await db.commit()
    await db.refresh(db_obj)
    return db_obj

async def get_energetic_object(db: AsyncSession, object_id: str) -> EnergeticObject | None:
    result = await db.execute(select(EnergeticObject).where(EnergeticObject.id == object_id))
    return result.scalars().first()

async def get_all_energetic_objects(db: AsyncSession) -> list[EnergeticObject]:
    result = await db.execute(select(EnergeticObject))
    return result.scalars().all()

async def update_energetic_object(
    db: AsyncSession,
    object_id: str,
    obj_data: EnergeticObjectUpdate,
    *,
    owner_cor_id: str | None = None,
) -> EnergeticObject | None:
    db_obj = await get_energetic_object(db, object_id)
    if not db_obj:
        return None
    
    update_data = obj_data.model_dump(exclude_unset=True)
    
    if "name" in update_data:
        existing = await db.execute(
            select(EnergeticObject).where(
                EnergeticObject.name == update_data["name"],
                EnergeticObject.id != object_id
            )
        )
        if existing.scalars().first():
            raise HTTPException(
                status_code=409,
                detail=f"Энергетический объект с именем '{update_data['name']}' уже существует"
            )

    has_changes = bool(update_data)
    old_name = db_obj.name
    old_vendor = db_obj.vendor
    old_model_name = db_obj.model_name
    old_cor_bridges: list[str] = list(db_obj.cor_bridges or [])

    for field, value in update_data.items():
        setattr(db_obj, field, value)

    normalized_old_cor_bridges = await normalize_cor_bridge_ids(db, old_cor_bridges)
    new_cor_bridges = await normalize_cor_bridge_ids(db, list(db_obj.cor_bridges or []))
    db_obj.cor_bridges = new_cor_bridges

    polling_sync_configured = bool(
        (db_obj.vendor and db_obj.model_name)
        or (db_obj.protocol and db_obj.modbus_config_file)
    )
    had_bridge_config_before = bool(old_vendor and old_model_name and normalized_old_cor_bridges)
    has_bridge_config_now = bool(db_obj.vendor and db_obj.model_name and new_cor_bridges)

    polling_needs_sync = (has_changes and polling_sync_configured) or (
        polling_sync_configured
        and not await _object_has_polling_tasks(db, object_id)
    )

    bridge_cleanup_needed = has_changes and (had_bridge_config_before or has_bridge_config_now)
    bridge_needs_recreate = has_bridge_config_now and (
        has_changes
        or not await _object_has_broadcast_tasks(db, db_obj.name, list(new_cor_bridges or []))
    )

    if polling_needs_sync:
        await db.execute(
            delete(DevicePollingTask).where(DevicePollingTask.energetic_object_id == object_id)
        )
        new_tasks = _build_default_polling_tasks(
            protocol=db_obj.protocol,
            modbus_config_file=db_obj.modbus_config_file,
            vendor=db_obj.vendor,
            model_name=db_obj.model_name,
        )
        for task in new_tasks:
            db.add(
                DevicePollingTask(
                    energetic_object_id=db_obj.id,
                    task_type=task["task_type"],
                    command_config=task["command_config"],
                    interval_ms=task["interval_ms"],
                    is_active=task["is_active"],
                )
            )
        logger.info(
            f"Regenerated direct polling tasks for object '{db_obj.name}' "
            f"(vendor={db_obj.vendor}, model={db_obj.model_name}, "
            f"protocol={db_obj.protocol}, config={db_obj.modbus_config_file})"
        )

    if bridge_cleanup_needed:
        # Remove stale broadcast tasks created for this object across old/new bridge identifiers.
        cleanup_bridge_ids = list(
            dict.fromkeys(old_cor_bridges + normalized_old_cor_bridges + list(new_cor_bridges or []))
        )
        if cleanup_bridge_ids:
            name_prefixes = {prefix for prefix in (old_name, db_obj.name) if prefix}
            task_name_filters = [
                WebSocketBroadcastTask.task_name.like(f"{prefix}:%")
                for prefix in name_prefixes
            ]
            session_marker_filters = [
                WebSocketBroadcastTask.task_name.like(f"%:{bridge_session_id}:%")
                for bridge_session_id in cleanup_bridge_ids
            ]
            all_task_name_filters = task_name_filters + session_marker_filters
            await db.execute(
                delete(WebSocketBroadcastTask).where(
                    WebSocketBroadcastTask.session_id.in_(cleanup_bridge_ids),
                    or_(*all_task_name_filters),
                )
            )

        # Recreate broadcast tasks only when current object config still has bridges.
        new_vendor = db_obj.vendor
        new_model_name = db_obj.model_name
        if bridge_needs_recreate and new_vendor and new_model_name and new_cor_bridges:
            from backend.services.energy.inverter_preset_loader import (
                build_broadcast_task_records,
                get_preset,
            )
            preset = get_preset(new_vendor, new_model_name)
            if preset:
                slave_id = (db_obj.slave_ids[0] if db_obj.slave_ids else 1)
                for bridge_session_id in new_cor_bridges:
                    records = build_broadcast_task_records(
                        preset=preset,
                        session_id=bridge_session_id,
                        slave_id=slave_id,
                        host=db_obj.ip_address,
                        port=db_obj.port,
                        task_name_prefix=f"{db_obj.name}:{bridge_session_id}",
                        created_by=owner_cor_id,
                        is_active=bool(db_obj.is_active),
                    )
                    for record in records:
                        db.add(WebSocketBroadcastTask(**record))
                logger.info(
                    f"Regenerated COR Bridge broadcast tasks for object '{db_obj.name}' "
                    f"(vendor={new_vendor}, model={new_model_name}, "
                    f"bridges={new_cor_bridges}, is_active={bool(db_obj.is_active)})"
                )

    await db.commit()
    await db.refresh(db_obj)
    return db_obj

async def delete_energetic_object(db: AsyncSession, object_id: str, cascade: bool = False) -> bool:
    """
    Удаляет энергетический объект.
    
    Args:
        db: Сессия базы данных
        object_id: ID объекта для удаления
        cascade: Если True, удаляет объект вместе со всеми связанными данными
    
    Returns:
        True если объект был удален, False если объект не найден
    
    Raises:
        HTTPException(409): Если есть связанные данные и cascade=False
    """
    if cascade:
        # Удаляем связанные исторические данные
        await db.execute(
            delete(CerboMeasurement).where(CerboMeasurement.energetic_object_id == object_id)
        )
        await db.execute(
            delete(EnergeticSchedule).where(EnergeticSchedule.energetic_object_id == object_id)
        )
        await db.execute(
            delete(DeyeAlarmEvent).where(DeyeAlarmEvent.energetic_object_id == object_id)
        )

        logger.info(f"Каскадное удаление связанных данных для объекта {object_id}")
    else:
        # Проверяем наличие связанных измерений
        measurements_check = await db.execute(
            select(func.count()).select_from(CerboMeasurement).where(
                CerboMeasurement.energetic_object_id == object_id
            )
        )
        measurements_count = measurements_check.scalar_one()

        if measurements_count > 0:
            raise HTTPException(
                status_code=409,
                detail=f"Невозможно удалить энергетический объект: найдено {measurements_count} связанных измерений. Используйте cascade=true для удаления всех связанных данных."
            )

        # Проверяем наличие связанных расписаний
        schedules_check = await db.execute(
            select(func.count()).select_from(EnergeticSchedule).where(
                EnergeticSchedule.energetic_object_id == object_id
            )
        )
        schedules_count = schedules_check.scalar_one()

        if schedules_count > 0:
            raise HTTPException(
                status_code=409,
                detail=f"Невозможно удалить энергетический объект: найдено {schedules_count} связанных расписаний. Используйте cascade=true для удаления всех связанных данных."
            )

        alarm_events_check = await db.execute(
            select(func.count()).select_from(DeyeAlarmEvent).where(
                DeyeAlarmEvent.energetic_object_id == object_id
            )
        )
        alarm_events_count = alarm_events_check.scalar_one()

        if alarm_events_count > 0:
            raise HTTPException(
                status_code=409,
                detail=f"Невозможно удалить энергетический объект: найдено {alarm_events_count} связанных событий аварий. Используйте cascade=true для удаления всех связанных данных."
            )

    # Конфигурационные и access-сущности удаляем всегда вместе с объектом
    await db.execute(
        delete(DevicePollingTask).where(DevicePollingTask.energetic_object_id == object_id)
    )
    await db.execute(
        delete(EnergeticObjectAccess).where(EnergeticObjectAccess.energetic_object_id == object_id)
    )

    result = await db.execute(delete(EnergeticObject).where(EnergeticObject.id == object_id))
    await db.commit()
    return result.rowcount > 0
