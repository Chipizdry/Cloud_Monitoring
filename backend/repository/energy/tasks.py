import asyncio
from datetime import datetime, time as dt_time
from typing import Optional
from uuid import uuid4
from zoneinfo import ZoneInfo

from loguru import logger
from backend.database.db import async_session_maker
from backend.schemas.device_measurement import FullDeviceMeasurementCreate
from backend.services.energy.modbus_client import (
    get_or_create_modbus_client,
    register_modbus_success,
    get_modbus_error_stats,
    BATTERY_ID,
    ESS_UNIT_ID,
    INVERTER_ID,
)
from backend.services.energy.modbus_cache import set_modbus_register_cache
from backend.repository.energy.data_collector import (
    collect_battery_data,
    collect_inverter_power_data,
    collect_ess_ac_data,
    get_solarchargers_current_sum,
    get_battery_status,
    send_grid_feed_w_command,
)
from backend.repository.energy.db_operations import create_full_device_measurement, get_all_schedules, update_schedule_is_active_status
from backend.repository.energy.schedule_task import send_dvcc_max_charge_current_command, send_vebus_soc_command
from backend.services.telegram.telegram_bot import (
    send_schedule_change_notification, 
    send_connection_loss_notification,
    update_object_data  
)
from backend.repository.energy.cerbo_service import get_energetic_object



DEFAULT_grid_feed_kw = 70000
DEFAULT_battery_level_percent = 30
DEFAULT_charge_battery_value = 300

COLLECTION_INTERVAL_SECONDS = 2
SCHEDULE_CHECK_INTERVAL_SECONDS = 3

current_active_schedule_id: Optional[str] = None

async def set_inverter_parameters(
    object_id: str,
    grid_feed_w: int,
    battery_level_percent: int,
    charge_battery_value: int
):
    # Получаем IP-адрес объекта из базы
    async with async_session_maker() as db:
        obj = await get_energetic_object(db, object_id)
        if not obj or not obj.ip_address:
            logger.error(f"[{object_id}] IP-адрес объекта не найден")
            return
        
        ip_address = obj.ip_address
        port = obj.port
        protocol = obj.protocol
    
    modbus_client_instance = await get_or_create_modbus_client(
        protocol=protocol,
        ip_address=ip_address,
        port=port,
        object_id=object_id
    )
    if not modbus_client_instance:
        logger.error(f"[{object_id}] Не удалось получить Modbus клиент для установки параметров инвертора.")
        return

    logger.info(f"[{object_id}] Установка параметров инвертора: grid_feed_w={grid_feed_w}, battery_level_percent={battery_level_percent}, charge_battery_value={charge_battery_value}")
    
    await send_grid_feed_w_command(modbus_client=modbus_client_instance, grid_feed_w=grid_feed_w)
    await send_vebus_soc_command(modbus_client=modbus_client_instance, battery_level_percent=battery_level_percent)
    await send_dvcc_max_charge_current_command(modbus_client=modbus_client_instance, charge_battery_value=charge_battery_value)


async def _cache_write(
    modbus_client,
    object_id: str,
    protocol: str,
    ip_address: str,
    port: int,
    slave_id: int,
    start: int,
    count: int,
    func_code: int,
    ttl: int = 120,
) -> bool:
    try:
        if func_code == 4:
            res = await modbus_client.read_input_registers(start, count=count, slave=slave_id)
        else:
            res = await modbus_client.read_holding_registers(start, count=count, slave=slave_id)
        if res.isError() or not hasattr(res, "registers"):
            logger.warning(f"[{object_id}] cache_write modbus error slave={slave_id} start={start} func={func_code}")
            return False
        await set_modbus_register_cache(
            protocol=protocol,
            host=ip_address,
            port=port,
            slave_id=slave_id,
            start=start,
            count=count,
            func_code=func_code,
            data=list(res.registers),
            object_id=object_id,
            ttl_seconds=ttl,
        )
        return True
    except Exception as e:
        logger.warning(f"[{object_id}] cache_write error slave={slave_id} start={start}: {e}")
        return False


async def cache_victron_registers(
    modbus_client,
    object_id: str,
    protocol: str,
    ip_address: str,
    port: int,
) -> None:
    """Read raw Modbus registers and write to register cache for all Victron cached API routes."""
    CACHE_TTL = 120

    # cerbo_routes.py imports REGISTERS from cerbo_service where power_int32=256 is present
    # so start = min(256,258,259,261,262,266,304) = 256, count = 304-256+2 = 50
    BATTERY_START = 256
    BATTERY_COUNT = 50

    # Battery (slave 225, input registers)
    await _cache_write(modbus_client, object_id, protocol, ip_address, port,
                       BATTERY_ID, BATTERY_START, BATTERY_COUNT, 4, CACHE_TTL)

    # Inverter DC + AC output — read each pair individually (as the route does)
    # to avoid potential device errors on bulk 14-reg reads
    for addr in [870, 878, 880, 882]:
        await _cache_write(modbus_client, object_id, protocol, ip_address, port,
                           INVERTER_ID, addr, 2, 3, CACHE_TTL)

    # ESS AC (3-25) + VE.Bus (21-41): single read covers both (input, slave 227)
    await _cache_write(modbus_client, object_id, protocol, ip_address, port,
                       ESS_UNIT_ID, 3, 39, 4, CACHE_TTL)

    # ESS advanced settings (slave 100, input)
    await _cache_write(modbus_client, object_id, protocol, ip_address, port,
                       INVERTER_ID, 2700, 13, 4, CACHE_TTL)

    # ESS settings (slave 100, holding)
    await _cache_write(modbus_client, object_id, protocol, ip_address, port,
                       INVERTER_ID, 2900, 4, 3, CACHE_TTL)

    # Dynamic ESS (slave 100, holding)
    await _cache_write(modbus_client, object_id, protocol, ip_address, port,
                       INVERTER_ID, 5420, 10, 3, CACHE_TTL)

    # Solar chargers: slaves 1-13 + 100, registers 3700-3730 (input)
    for slave in list(range(1, 14)) + [100]:
        await _cache_write(modbus_client, object_id, protocol, ip_address, port,
                           slave, 3700, 31, 4, CACHE_TTL)


async def cerbo_collection_task_worker(object_id: str, object_name: str):

    CONSECUTIVE_ERROR_THRESHOLD = 10  # После 10 последовательных ошибок - уведомление
    
    # Получаем IP-адрес объекта один раз при старте
    async with async_session_maker() as db:
        obj = await get_energetic_object(db, object_id)
        if not obj or not obj.ip_address:
            logger.error(f"[{object_id}] IP-адрес объекта не найден, завершение задачи")
            return
        
        ip_address = obj.ip_address
        port = obj.port
        protocol = obj.protocol
    
    while True:
        transaction_id = uuid4()
        modbus_client_instance = await get_or_create_modbus_client(
            protocol=protocol,
            ip_address=ip_address,
            port=port,
            object_id=object_id
        )

        try:
            if not modbus_client_instance or not modbus_client_instance.connected:
                logger.critical(f"[{object_id}] [{transaction_id}] Modbus client not connected. Skipping cycle.")
                await asyncio.sleep(COLLECTION_INTERVAL_SECONDS)
                continue

            collected_data = {}

            try:
                collected_data.update(await collect_battery_data(modbus_client_instance, transaction_id))
            except Exception:
                pass
            try:
                collected_data.update(await collect_inverter_power_data(modbus_client_instance, transaction_id))
            except Exception:
                pass
            try:
                collected_data.update(await collect_ess_ac_data(modbus_client_instance, transaction_id))
            except Exception:
                pass
            try:
                collected_data.update(await get_solarchargers_current_sum(modbus_client_instance, transaction_id))
            except Exception:
                pass
            try:
                collected_data.update(await get_battery_status(modbus_client_instance, transaction_id))
            except Exception:
                pass

            if not collected_data:
                logger.warning(f"[{object_id}] [{transaction_id}] No data collected. Skipping save.")
                
                # Получаем статистику ошибок из modbus_client
                error_stats = get_modbus_error_stats(object_id)
                consecutive_errors = error_stats['consecutive_errors']
                
                # Проверяем порог последовательных ошибок
                if consecutive_errors == CONSECUTIVE_ERROR_THRESHOLD:
                    chat_ids = None
                    try:
                        async with async_session_maker() as db:
                            obj = await get_energetic_object(db, object_id)
                            if obj and getattr(obj, 'telegram_chat_ids', None):
                                chat_ids = [cid.strip() for cid in str(obj.telegram_chat_ids).split(',') if cid.strip()]
                    except Exception:
                        pass

                    await send_connection_loss_notification(
                        object_id=object_id,
                        object_name=object_name,
                        is_connection_lost=True,
                        consecutive_errors=consecutive_errors,
                        error_rate_percent=0.0,
                        chat_ids=chat_ids
                    )
                
                await asyncio.sleep(COLLECTION_INTERVAL_SECONDS)
                continue

            collected_data["measured_at"] = datetime.now()
            collected_data["object_name"] = object_name  # связываем с объектом
            collected_data["energetic_object_id"] = object_id

            required_fields = ["general_battery_power", "inverter_total_ac_output", "ess_total_input_power", "solar_total_pv_power", "measured_at", "object_name", "soc"]
            missing_fields = [f for f in required_fields if f not in collected_data or collected_data[f] is None]
            if missing_fields:
                logger.error(f"[{object_id}] Missing fields: {missing_fields}. Skipping save.", extra={"collected_data": collected_data})
                
                error_stats = get_modbus_error_stats(object_id)
                consecutive_errors = error_stats['consecutive_errors']
                
                # Проверяем порог последовательных ошибок
                if consecutive_errors == CONSECUTIVE_ERROR_THRESHOLD:
                    chat_ids = None
                    try:
                        async with async_session_maker() as db:
                            obj = await get_energetic_object(db, object_id)
                            if obj and getattr(obj, 'telegram_chat_ids', None):
                                chat_ids = [cid.strip() for cid in str(obj.telegram_chat_ids).split(',') if cid.strip()]
                    except Exception:
                        pass

                    await send_connection_loss_notification(
                        object_id=object_id,
                        object_name=object_name,
                        is_connection_lost=True,
                        consecutive_errors=consecutive_errors,
                        error_rate_percent=0.0,
                        chat_ids=chat_ids
                    )
                
                await asyncio.sleep(COLLECTION_INTERVAL_SECONDS)
                continue

            register_modbus_success(object_id)

            try:
                await cache_victron_registers(
                    modbus_client_instance, object_id, protocol, ip_address, port
                )
            except Exception as e:
                logger.warning(f"[{object_id}] cache_victron_registers error: {e}")
            
            error_stats = get_modbus_error_stats(object_id)
            
            if error_stats['last_error_time'] is not None:
                time_since_error = (datetime.now() - error_stats['last_error_time']).total_seconds()
                if time_since_error < 60: 
                    chat_ids = None
                    try:
                        async with async_session_maker() as db:
                            obj = await get_energetic_object(db, object_id)
                            if obj and getattr(obj, 'telegram_chat_ids', None):
                                chat_ids = [cid.strip() for cid in str(obj.telegram_chat_ids).split(',') if cid.strip()]
                    except Exception:
                        pass

                    await send_connection_loss_notification(
                        object_id=object_id,
                        object_name=object_name,
                        is_connection_lost=False,
                        consecutive_errors=0,
                        error_rate_percent=0.0,
                        chat_ids=chat_ids
                    )
            
            full_measurement = FullDeviceMeasurementCreate(**collected_data)
            async with async_session_maker() as db:
                await create_full_device_measurement(db=db, data=full_measurement)
            
            try:
                update_object_data(object_id, {
                    'object_name': object_name,
                    'soc': collected_data.get("soc", 0),
                    'general_battery_power': collected_data.get("general_battery_power", 0),
                    'battery_voltage': collected_data.get("battery_voltage"),
                    'solar_total_pv_power': collected_data.get("solar_total_pv_power", 0),
                    'inverter_total_ac_output': collected_data.get("inverter_total_ac_output", 0),
                    'ess_total_input_power': collected_data.get("ess_total_input_power", 0),
                })
            except Exception as e:
                logger.error(f"[{object_id}] Error updating object data for commands: {e}", exc_info=True)
        
            try:
                # Получаем монитор для этого объекта
                from backend.services.telegram.telegram_manager import telegram_manager
                monitor = telegram_manager.get_monitor(object_id)
                
                if monitor:
                    # Получаем chat_ids из объекта
                    chat_ids = None
                    try:
                        async with async_session_maker() as db:
                            obj = await get_energetic_object(db, object_id)
                            if obj and getattr(obj, 'telegram_chat_ids', None):
                                chat_ids = [cid.strip() for cid in str(obj.telegram_chat_ids).split(',') if cid.strip()]
                    except Exception:
                        pass
                    
                    # logger.debug(
                    #     f"[{object_id}] Checking battery level: "
                    #     f"SOC={collected_data.get('battery_soc', 0)}%, chat_ids={chat_ids}"
                    # )
                    
                    await monitor.check_battery_level(
                        object_id=object_id,
                        object_name=object_name,
                        battery_soc=collected_data.get("battery_soc", 0),
                        battery_voltage=collected_data.get("battery_voltage"),
                        battery_power=collected_data.get("general_battery_power"),
                        chat_ids=chat_ids
                    )
            except Exception as e:
                logger.error(f"[{object_id}] Error checking battery level for Telegram: {e}", exc_info=True)
            
            # Проверяем потерю электроэнергии на входе (только если есть данные ESS)
            if "ess_total_input_power" in collected_data:
                try:
                    # Получаем монитор для этого объекта
                    from backend.services.telegram.telegram_manager import telegram_manager
                    monitor = telegram_manager.get_monitor(object_id)
                    
                    if monitor:
                        # Получаем chat_ids для уведомлений
                        chat_ids = None
                        try:
                            async with async_session_maker() as db:
                                obj = await get_energetic_object(db, object_id)
                                if obj and getattr(obj, 'telegram_chat_ids', None):
                                    chat_ids = [cid.strip() for cid in str(obj.telegram_chat_ids).split(',') if cid.strip()]
                        except Exception:
                            pass
                        
                        voltage_l1 = collected_data.get("input_voltage_l1", 0)
                        voltage_l2 = collected_data.get("input_voltage_l2", 0)
                        voltage_l3 = collected_data.get("input_voltage_l3", 0)
                        
                        # Проверка сети через монитор
                        await monitor.check_grid_voltage(
                            object_id=object_id,
                            object_name=object_name,
                            voltage_l1=voltage_l1,
                            voltage_l2=voltage_l2,
                            voltage_l3=voltage_l3,
                            chat_ids=chat_ids,
                        )
                except Exception as e:
                    logger.error(f"[{object_id}] Error checking grid voltage for Telegram: {e}", exc_info=True)

        except Exception as e:
            logger.error(f"[{object_id}] Error in collection task: {e}", exc_info=True)
            
            # Получаем статистику ошибок из modbus_client
            error_stats = get_modbus_error_stats(object_id)
            consecutive_errors = error_stats['consecutive_errors']
            
            # Проверяем порог последовательных ошибок
            if consecutive_errors == CONSECUTIVE_ERROR_THRESHOLD:
                try:
                    # Получаем chat_ids для уведомлений
                    chat_ids = None
                    try:
                        async with async_session_maker() as db:
                            obj = await get_energetic_object(db, object_id)
                            if obj and getattr(obj, 'telegram_chat_ids', None):
                                chat_ids = [cid.strip() for cid in str(obj.telegram_chat_ids).split(',') if cid.strip()]
                    except Exception:
                        pass
                    
                    await send_connection_loss_notification(
                        object_id=object_id,
                        object_name=object_name,
                        is_connection_lost=True,
                        consecutive_errors=consecutive_errors,
                        error_rate_percent=0.0,
                        chat_ids=chat_ids
                    )
                except Exception as notification_error:
                    logger.error(
                        f"[{object_id}] Failed to send connection loss notification: {notification_error}",
                        exc_info=True
                    )

        await asyncio.sleep(COLLECTION_INTERVAL_SECONDS)


async def energetic_schedule_task_worker(object_id: str, object_name: str):
    current_active_schedule_id: str | None = None
    # logger.debug(f"[{object_id}] Starting energetic schedule task worker.")
    # logger.debug(f"current_active_schedule_id initialized to: {current_active_schedule_id}")

    while True:
        try:
            async with async_session_maker() as db:
                # Получаем объект для доступа к его timezone
                energetic_object = await get_energetic_object(db, object_id)
                if not energetic_object:
                    logger.error(f"[{object_id}] Energetic object not found!")
                    break
                
                # Получаем текущее время в timezone объекта
                object_timezone = ZoneInfo(energetic_object.timezone)
                now_time = datetime.now(object_timezone).time()
                
                all_schedules = await get_all_schedules(db)
                # фильтруем только для этого объекта
                object_schedules = [s for s in all_schedules if s.energetic_object_id == object_id]

                operational_schedules = [s for s in object_schedules if not s.is_manual_mode]
                
                # logger.debug(f"[{object_id}] 🔍 Проверка расписаний: найдено {len(object_schedules)} расписаний для объекта, {len(operational_schedules)} операционных, текущее время {now_time} ({energetic_object.timezone}), активное расписание ID: {current_active_schedule_id}")

                active_schedule = None
                for schedule in operational_schedules:
                    if schedule.start_time <= schedule.end_time:
                        if schedule.start_time <= now_time < schedule.end_time:
                            active_schedule = schedule
                            break
                    else:
                        if now_time >= schedule.start_time or now_time < schedule.end_time:
                            active_schedule = schedule
                            break

                if active_schedule:
                    # logger.debug(f"[{object_id}] ✅ Найдено активное расписание ID={active_schedule.id}, период {active_schedule.start_time}-{active_schedule.end_time}")
                    
                    if active_schedule.id != current_active_schedule_id:
                        # logger.info(f"[{object_id}] 🔄 Переключение расписания: {current_active_schedule_id} → {active_schedule.id}")
                        
                        # Получаем параметры предыдущего расписания для уведомления
                        old_grid_feed_kw = None
                        old_battery_level_percent = None
                        old_charge_battery_value = None
                        
                        if current_active_schedule_id:
                            old_schedule = next((s for s in all_schedules if s.id == current_active_schedule_id), None)
                            if old_schedule:
                                old_grid_feed_kw = old_schedule.grid_feed_w / 1000  # W -> kW
                                old_battery_level_percent = old_schedule.battery_level_percent
                                old_charge_battery_value = old_schedule.charge_battery_value
                        
                        # деактивация предыдущей
                        if current_active_schedule_id:
                            await update_schedule_is_active_status(db, current_active_schedule_id, False)
                        
                        current_active_schedule_id = active_schedule.id
                        
                        # установка параметров инвертора для объекта
                        await set_inverter_parameters(
                            object_id,
                            active_schedule.grid_feed_w,
                            active_schedule.battery_level_percent,
                            active_schedule.charge_battery_value,
                        )
                        await update_schedule_is_active_status(db, active_schedule.id, True)
                        
                        # Отправляем уведомление в Telegram
                        try:
                            await send_schedule_change_notification(
                                object_id=object_id,
                                object_name=object_name,
                                object_timezone=energetic_object.timezone,
                                old_grid_feed_kw=old_grid_feed_kw,
                                old_battery_level_percent=old_battery_level_percent,
                                old_charge_battery_value=old_charge_battery_value,
                                new_grid_feed_kw=active_schedule.grid_feed_w / 1000,  # W -> kW
                                new_battery_level_percent=active_schedule.battery_level_percent,
                                new_charge_battery_value=active_schedule.charge_battery_value,
                                is_manual_mode=False,
                                active_schedule_start_time=active_schedule.start_time,
                                active_schedule_end_time=active_schedule.end_time
                            )
                        except Exception as e:
                            logger.error(f"[{object_id}] Error sending schedule change notification: {e}", exc_info=True)
                    else:
                        pass
                        # logger.debug(f"[{object_id}] ⏸️ Расписание ID={active_schedule.id} уже активно, параметры не меняем")
                else:
                    # logger.debug(f"[{object_id}] ⚠️ Активное расписание не найдено, сбрасываем на дефолт")
                    # сброс к дефолтным параметрам
                    if current_active_schedule_id:
                        # logger.info(f"[{object_id}] 🔄 Сброс расписания {current_active_schedule_id} на дефолтные параметры")
                        await update_schedule_is_active_status(db, current_active_schedule_id, False)
                        current_active_schedule_id = None
                        await set_inverter_parameters(object_id, DEFAULT_grid_feed_kw, DEFAULT_battery_level_percent, DEFAULT_charge_battery_value)
                        
                        # Отправляем уведомление о сбросе на дефолт
                        try:
                            await send_schedule_change_notification(
                                object_id=object_id,
                                object_name=object_name,
                                object_timezone=energetic_object.timezone,
                                old_grid_feed_kw=None,
                                old_battery_level_percent=None,
                                old_charge_battery_value=None,
                                new_grid_feed_kw=DEFAULT_grid_feed_kw / 1000,  # W -> kW
                                new_battery_level_percent=DEFAULT_battery_level_percent,
                                new_charge_battery_value=DEFAULT_charge_battery_value,
                                is_manual_mode=False
                            )
                        except Exception as e:
                            logger.error(f"[{object_id}] Error sending schedule reset notification: {e}", exc_info=True)
                    # Если current_active_schedule_id уже None, не вызываем set_inverter_parameters повторно

        except Exception as e:
            logger.error(f"[{object_id}] Error in schedule task: {e}", exc_info=True)

        await asyncio.sleep(SCHEDULE_CHECK_INTERVAL_SECONDS)