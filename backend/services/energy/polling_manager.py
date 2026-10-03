"""
Dynamic Polling Manager
Управляет фоновыми задачами опроса устройств на основе конфигурации в БД
"""
import asyncio
import json
from pathlib import Path
from typing import Dict, Optional, Callable, Any
from datetime import datetime, timedelta
from loguru import logger
from sqlalchemy import select

from backend.database.db import async_session_maker
from backend.services.energy.modbus_cache import (
    make_coils_cache_key,
    make_discrete_inputs_cache_key,
    set_modbus_cache,
    set_modbus_register_cache,
    set_polling_snapshot_cache,
)

from backend.database.models.energy import DevicePollingTask, EnergeticObject, DeyeAlarmEvent
from backend.database.models.enums import PollingTaskType
from backend.repository.energy.power_measurements import persist_power_measurement_snapshot
from backend.services.energy.modbus_client import decode_signed_16, decode_signed_32, register_modbus_error, register_modbus_success


def _func_code_to_cache_channel(func_code: int) -> tuple[str, str]:
    if func_code == 1:
        return "read_coils", "/v1_cached/read_coils"
    if func_code == 2:
        return "read_discrete_inputs", "/v1_cached/read_discrete_inputs"
    return "read", "/v1_cached/read"


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_non_zero_number(data: Dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _number(data.get(key))
        if value is not None and value != 0:
            return value
    return None


def _combine_signed_32(low_word: Any, high_word: Any) -> float | None:
    low = _number(low_word)
    high = _number(high_word)
    if low is None or high is None:
        return None
    combined = (int(high) << 16) | (int(low) & 0xFFFF)
    if combined > 0x7FFFFFFF:
        combined -= 0x100000000
    return float(combined)


def _calculate_deye_voltage_soc(data: Dict[str, Any]) -> float | None:
    battery_work_mode = data.get("battery_work_mode")
    if battery_work_mode not in (0, 0.0, "0", "voltage"):
        return None

    voltages = [
        value
        for value in (
            _number(data.get("battery1_voltage")),
            _number(data.get("battery2_voltage")),
        )
        if value is not None and value > 0
    ]
    if not voltages:
        return None

    voltage_max = _number(data.get("battery_float_voltage"))
    voltage_min = _number(data.get("battery_voltage_shutdown"))
    if voltage_max is None or voltage_min is None or voltage_max <= voltage_min:
        return None

    voltage_avg = sum(voltages) / len(voltages)
    soc = ((voltage_avg - voltage_min) / (voltage_max - voltage_min)) * 100
    soc = max(0.0, min(100.0, soc))
    return round(soc, 1)


def _enrich_deye_polling_snapshot(data: Dict[str, Any]) -> None:
    battery_total_power = _number(data.get("general_battery_power"))
    if battery_total_power is None:
        battery_parts = [
            value
            for value in (
                _number(data.get("battery1_power")),
                _number(data.get("battery2_power")),
            )
            if value is not None
        ]
        if battery_parts:
            battery_total_power = sum(battery_parts)
            data["general_battery_power"] = battery_total_power
    if battery_total_power is not None:
        data["batteryTotalPower"] = battery_total_power

    first_battery_soc = _number(data.get("battery1_soc"))
    soc = first_battery_soc if first_battery_soc not in (None, 0) else _number(data.get("soc"))
    if soc in (None, 0):
        soc = _calculate_deye_voltage_soc(data)
        if soc is not None:
            data["calculated_soc"] = soc
    if soc in (None, 0):
        soc = _first_non_zero_number(data, "battery2_soc")
    if soc in (None, 0) and first_battery_soc is not None:
        soc = first_battery_soc
    if soc is not None:
        data["soc"] = soc

    load_total_power = _combine_signed_32(
        data.get("load_total_power_low"),
        data.get("load_total_power_high"),
    )
    if load_total_power is None:
        load_total_power = _first_non_zero_number(
            data,
            "load_total_power",
            "load_total_power_high",
            "inverter_total_power_high",
            "inverter_total_power",
        )
    if load_total_power is not None:
        data["LoadTotalPower"] = load_total_power
        if _number(data.get("inverter_total_ac_output")) in (None, 0):
            data["inverter_total_ac_output"] = load_total_power

    solar_total_power = _number(data.get("solar_total_pv_power"))
    if solar_total_power is None:
        solar_total_power = _number(data.get("pv_total_power_raw_high"))
    if solar_total_power is not None:
        data["solar_total_pv_power"] = solar_total_power

    grid_power = _number(data.get("ess_total_input_power"))
    if grid_power is None:
        grid_power = _number(data.get("total_power"))
    if grid_power is not None:
        data["ess_total_input_power"] = grid_power


class PollingManager:
    """
    Динамический менеджер фоновых задач опроса устройств
    
    Особенности:
    - Загружает активные задачи из БД при старте
    - Создаёт asyncio.Task для каждой активной задачи
    - Поддерживает динамическое управление задачами
    - Читает конфигурации Modbus из JSON файлов
    """
    
    def __init__(self):
        # Словарь: task_id -> {"task": asyncio.Task, "config": dict}
        self.tasks: Dict[str, Dict[str, Any]] = {}

        # Для диагностики первого чтения gen_relay по объекту
        self._gen_relay_first_seen: set[str] = set()

        # Чтобы не спамить логами про пустые telegram_chat_ids
        self._telegram_chat_ids_empty_warned: set[tuple[str, str]] = set()
        self._deye_alarm_last_signature: Dict[str, tuple[int, int, int, int]] = {}
        
        # Конфиги находятся в backend/modbus_configs/
        backend_dir = Path(__file__).parent.parent.parent  # Из backend/services/energy -> backend
        self.modbus_configs_dir = backend_dir / "modbus_configs"
        
        # Кэш загруженных конфигов: filename -> config dict
        self.modbus_configs_cache: Dict[str, dict] = {}
        
        # Регистрация обработчиков типов задач
        self.task_handlers: Dict[str, Callable] = {
            PollingTaskType.CERBO_COLLECTION.value: self._run_cerbo_collection_task,
            PollingTaskType.SCHEDULE_CHECK.value: self._run_schedule_check_task,
            PollingTaskType.MODBUS_REGISTERS.value: self._run_modbus_registers_task,
            PollingTaskType.CUSTOM_COMMAND.value: self._run_custom_command_task,
        }
    
    def load_modbus_config(self, config_filename: str) -> Optional[dict]:
        """Загрузка Modbus конфигурации из JSON файла"""
        if config_filename in self.modbus_configs_cache:
            return self.modbus_configs_cache[config_filename]
        
        config_path = self.modbus_configs_dir / config_filename
        
        if not config_path.exists():
            logger.error(f"Modbus config file not found: {config_path}")
            return None
        
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
                self.modbus_configs_cache[config_filename] = config
                logger.info(f"Loaded Modbus config: {config_filename}")
                return config
        except Exception as e:
            logger.error(f"Error loading Modbus config {config_filename}: {e}", exc_info=True)
            return None
    
    async def start_polling_task(self, polling_task: DevicePollingTask, energetic_object: EnergeticObject):
        """Запуск задачи опроса"""
        task_id = polling_task.id
        
        if task_id in self.tasks:
            # logger.warning(f"Polling task {task_id} is already running")
            return
        
        # Выбираем обработчик по типу задачи
        handler = self.task_handlers.get(polling_task.task_type)
        
        if not handler:
            logger.error(f"Unknown task type: {polling_task.task_type}")
            return
        
        # Создаём asyncio.Task
        async_task = asyncio.create_task(
            handler(
                task_id=task_id,
                object_id=energetic_object.id,
                object_name=energetic_object.name,
                interval=polling_task.interval_ms,
                command_config=polling_task.command_config,
                modbus_config_file=energetic_object.modbus_config_file
            )
        )
        
        self.tasks[task_id] = {
            "task": async_task,
            "config": {
                "task_type": polling_task.task_type,
                "object_id": energetic_object.id,
                "object_name": energetic_object.name,
                "interval": polling_task.interval_ms,
                "command_config": polling_task.command_config,
            }
        }
        
        logger.info(
            f"Started polling task {task_id} ({polling_task.task_type}) "
            f"for object {energetic_object.name} with interval {polling_task.interval_ms}ms"
        )
    
    async def stop_polling_task(self, task_id: str):
        """Остановка задачи опроса"""
        if task_id not in self.tasks:
            logger.warning(f"Polling task {task_id} is not running")
            return
        
        task_info = self.tasks[task_id]
        async_task = task_info["task"]
        
        # Отменяем задачу
        async_task.cancel()
        
        try:
            await async_task
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error stopping task {task_id}: {e}", exc_info=True)
        
        del self.tasks[task_id]
        logger.info(f"Stopped polling task {task_id}")
    
    async def reload_tasks_from_db(self):
        """Загрузка/обновление задач из БД"""
        try:
            async with async_session_maker() as db:
                # Получаем все активные задачи
                result = await db.execute(
                    select(DevicePollingTask, EnergeticObject)
                    .join(EnergeticObject, DevicePollingTask.energetic_object_id == EnergeticObject.id)
                    .where(
                        DevicePollingTask.is_active == True,
                        EnergeticObject.is_active == True
                    )
                )
                rows = result.all()
                
                # SQLAlchemy возвращает список Row(tuple), распаковываем явно
                active_tasks = [(row[0], row[1]) for row in rows]

                # Дополнительно получаем все активные объекты, чтобы выявить объекты без задач
                objects_result = await db.execute(
                    select(EnergeticObject).where(EnergeticObject.is_active == True)
                )
                active_objects = objects_result.scalars().all()
                
                if active_tasks:
                    pass
                    # logger.info(f"Found {len(active_tasks)} active polling tasks in DB")
                else:
                    logger.warning("No active polling tasks found in DB")

                if active_objects:
                    # logger.info(f"Found {len(active_objects)} active energetic objects in DB")
                    pass
                else:
                    logger.warning("No active energetic objects found in DB")
                
                active_task_ids = {polling_task.id for (polling_task, _) in active_tasks}
                active_object_ids_with_tasks = {obj.id for (_, obj) in active_tasks}

                for obj in active_objects:
                    if obj.id not in active_object_ids_with_tasks:
                        pass
                        # logger.warning(
                        #     "Active object has no polling tasks: "
                        #     f"name='{obj.name}', id={obj.id}, protocol={obj.protocol}, "
                        #     f"modbus_config_file='{obj.modbus_config_file}'"
                        # )
                
                # Запускаем новые задачи
                for polling_task, energetic_object in active_tasks:
                    
                    if polling_task.id not in self.tasks:
                        logger.info(
                            f"Starting new task {polling_task.id}: {polling_task.task_type} "
                            f"for object {energetic_object.name} ({energetic_object.protocol})"
                        )
                        await self.start_polling_task(polling_task, energetic_object)
                    else:
                        # logger.debug(f"Task {polling_task.id} already running")
                        pass
                
                # Останавливаем удалённые/неактивные задачи
                for task_id in list(self.tasks.keys()):
                    if task_id not in active_task_ids:
                        logger.info(f"Stopping inactive task {task_id}")
                        await self.stop_polling_task(task_id)
                
                # logger.info(f"Polling manager status: {len(self.tasks)} tasks running")
                
        except Exception as e:
            logger.error(f"Error reloading tasks from DB: {e}", exc_info=True)
    
    def _decode_deye_faults_warnings(self, word1: int, word2: int, info_dict: dict) -> list:
        """
        Декодирует битовые поля ошибок/предупреждений Deye инвертора
        
        Args:
            word1: Первое 16-битное слово (биты 1-16)
            word2: Второе 16-битное слово (биты 17-32)
            info_dict: Справочник с информацией об ошибках/предупреждениях
        
        Returns:
            Список активных ошибок/предупреждений с информацией
        """
        active_issues = []
        
        # Обрабатываем первое слово (биты 1-16)
        for bit in range(16):
            if word1 & (1 << bit):
                issue_num = bit + 1
                issue_info = info_dict.get(str(issue_num))
                if issue_info:
                    active_issues.append({
                        "bit": issue_num,
                        "name": issue_info.get("name", f"Unknown-{issue_num}"),
                        "description": issue_info.get("description", ""),
                        "description_ru": issue_info.get("description_ru", ""),
                        "solution": issue_info.get("solution", ""),
                        "solution_ru": issue_info.get("solution_ru", "")
                    })
        
        # Обрабатываем второе слово (биты 17-32)
        for bit in range(16):
            if word2 & (1 << bit):
                issue_num = bit + 17
                issue_info = info_dict.get(str(issue_num))
                if issue_info:
                    active_issues.append({
                        "bit": issue_num,
                        "name": issue_info.get("name", f"Unknown-{issue_num}"),
                        "description": issue_info.get("description", ""),
                        "description_ru": issue_info.get("description_ru", ""),
                        "solution": issue_info.get("solution", ""),
                        "solution_ru": issue_info.get("solution_ru", "")
                    })
        
        return active_issues

    async def _persist_deye_alarm_snapshot_if_changed(
        self,
        object_id: str,
        object_name: str,
        task_id: str,
        measured_at: datetime,
        raw_fault_1: int,
        raw_fault_2: int,
        raw_warning_1: int,
        raw_warning_2: int,
        faults: list,
        warnings: list,
    ) -> None:
        signature = (raw_fault_1, raw_fault_2, raw_warning_1, raw_warning_2)
        previous_signature = self._deye_alarm_last_signature.get(object_id)
        if previous_signature == signature:
            return

        self._deye_alarm_last_signature[object_id] = signature

        try:
            async with async_session_maker() as db:
                event = DeyeAlarmEvent(
                    energetic_object_id=object_id,
                    object_name=object_name,
                    measured_at=measured_at,
                    source_task_id=task_id,
                    fault_word_1=raw_fault_1,
                    fault_word_2=raw_fault_2,
                    warning_word_1=raw_warning_1,
                    warning_word_2=raw_warning_2,
                    faults=faults or [],
                    warnings=warnings or [],
                )
                db.add(event)
                await db.commit()
        except Exception as persist_error:
            logger.error(
                f"[{task_id}] Failed to persist Deye alarm snapshot for {object_name} ({object_id}): {persist_error}"
            )

    async def _store_polling_snapshot(
        self,
        object_id: str,
        object_name: str,
        collected_data: Dict[str, Any],
        channel_event: Optional[Dict[str, Any]] = None,
        persist_history: bool = False,
        source_protocol: Optional[str] = None,
        source_task_id: Optional[str] = None,
    ) -> None:
        """Stores latest polling snapshot for WebSocket consumers."""
        measured_at_raw = collected_data.get("measured_at")
        measured_at_iso = (
            measured_at_raw.isoformat()
            if isinstance(measured_at_raw, datetime)
            else (measured_at_raw if isinstance(measured_at_raw, str) else datetime.now().isoformat())
        )

        payload: Dict[str, Any] = {}
        for key, value in collected_data.items():
            payload[key] = value.isoformat() if isinstance(value, datetime) else value

        payload.setdefault("object_name", object_name)
        payload.setdefault("energetic_object_id", object_id)
        payload["measured_at"] = measured_at_iso

        await set_polling_snapshot_cache(
            object_id=object_id,
            data=payload,
            measured_at=measured_at_iso,
            channel_event=channel_event,
        )

        if persist_history:
            await persist_power_measurement_snapshot(
                object_id=object_id,
                object_name=object_name,
                snapshot=collected_data,
                source_protocol=source_protocol,
                source_task_id=source_task_id,
            )
    
    # ==================== Task Handlers ====================
    
    async def _run_cerbo_collection_task(
        self,
        task_id: str,
        object_id: str,
        object_name: str,
        interval: int,
        command_config: dict,
        modbus_config_file: Optional[str]
    ):
        """Задача сбора данных с Cerbo GX (совместимость с существующим кодом)"""
        from backend.repository.energy.tasks import cerbo_collection_task_worker
        
        # logger.debug(f"[{task_id}] Starting CERBO_COLLECTION task for {object_name}")
        
        try:
            # Используем существующую функцию
            await cerbo_collection_task_worker(object_id=object_id, object_name=object_name)
        except asyncio.CancelledError:
            # logger.debug(f"[{task_id}] CERBO_COLLECTION task cancelled")
            raise
        except Exception as e:
            logger.error(f"[{task_id}] Error in CERBO_COLLECTION task: {e}", exc_info=True)
    
    async def _run_schedule_check_task(
        self,
        task_id: str,
        object_id: str,
        object_name: str,
        interval: int,
        command_config: dict,
        modbus_config_file: Optional[str]
    ):
        """Задача проверки расписания (совместимость с существующим кодом)"""
        from backend.repository.energy.tasks import energetic_schedule_task_worker
        
        # logger.debug(f"[{task_id}] Starting SCHEDULE_CHECK task for {object_name}")
        
        try:
            # Используем существующую функцию
            pass
        # временно глушим проверку расписаний
            # await energetic_schedule_task_worker(object_id=object_id, object_name=object_name)
        except asyncio.CancelledError:
            # logger.debug(f"[{task_id}] SCHEDULE_CHECK task cancelled")
            raise
        except Exception as e:
            logger.error(f"[{task_id}] Error in SCHEDULE_CHECK task: {e}", exc_info=True)
    
    async def _run_modbus_registers_task(
        self,
        task_id: str,
        object_id: str,
        object_name: str,
        interval: int,
        command_config: dict,
        modbus_config_file: Optional[str]
    ):
        """
        Универсальная задача чтения Modbus регистров на основе JSON конфига
        
        command_config format:
        {
            "register_groups": ["battery", "solar"],  # какие группы читать
            "preset": "battery_only"  # или использовать пресет из конфига
        }
        """
        if not modbus_config_file:
            logger.error(f"[{task_id}] No modbus_config_file specified for object {object_name}")
            return
        
        # Загружаем конфиг
        modbus_config = self.load_modbus_config(modbus_config_file)
        if not modbus_config:
            logger.error(f"[{task_id}] Failed to load Modbus config: {modbus_config_file}")
            return

        logger.info(
            f"[{task_id}] Modbus config for {object_name}: file='{modbus_config_file}', "
            f"config_protocol='{modbus_config.get('protocol')}'"
        )
        
        # logger.debug(
        #     f"[{task_id}] Starting MODBUS_REGISTERS task for {object_name} "
        #     f"with config {modbus_config_file}"
        # )
        
        # Определяем какие группы регистров читать
        register_groups = []
        
        if "preset" in command_config:
            preset_name = command_config["preset"]
            preset = modbus_config.get("polling_presets", {}).get(preset_name)
            
            if preset:
                register_groups = preset.get("groups", [])
                # Можно переопределить интервал из пресета
                if "interval_ms" in preset and interval == 5000:  # если дефолтный
                    interval = preset["interval_ms"]
                # logger.debug(f"[{task_id}] Using preset '{preset_name}': {register_groups}")
            else:
                logger.warning(f"[{task_id}] Preset '{preset_name}' not found in config")
        else:
            logger.info(f"[{task_id}] No preset specified in command_config")
        
        if "register_groups" in command_config:
            # Переопределяем/дополняем группы из command_config
            register_groups = command_config["register_groups"]
            # logger.debug(f"[{task_id}] Using register_groups from config: {register_groups}")
            logger.info(f"[{task_id}] register_groups from command_config: {register_groups}")
        else:
            if register_groups:
                logger.info(f"[{task_id}] register_groups from preset: {register_groups}")
        
        if modbus_config_file == "deye_inverter.json":
            if "gen_relay_status" not in register_groups:
                register_groups.append("gen_relay_status")
                # logger.debug(f"[{task_id}] Added gen_relay_status to register_groups for generator monitoring")
            if "battery" not in register_groups:
                register_groups.append("battery")
                # logger.debug(f"[{task_id}] Added battery to register_groups for battery voltage monitoring")

        logger.info(f"[{task_id}] Final register_groups for {object_name}: {register_groups}")

        register_groups = sorted(
            register_groups,
            key=lambda group_name: 1
            if modbus_config.get("register_groups", {}).get(group_name, {}).get("func_code", 3) in (1, 2)
            else 0,
        )
        
        if not register_groups:
            logger.error(f"[{task_id}] No register groups specified")
            return
        
        # Основной цикл опроса
        from backend.services.energy.modbus_client import get_or_create_modbus_client, ModbusTCP, DEFAULT_MODBUS_PORT
        from backend.repository.energy.modbus_broker import get_broker, RequestPriority
        
        # Получаем IP-адрес объекта из БД
        async with async_session_maker() as db:
            obj_data = await db.execute(
                select(EnergeticObject).where(EnergeticObject.id == object_id)
            )
            obj = obj_data.scalar_one_or_none()
            if not obj or not obj.ip_address:
                logger.error(f"[{task_id}] IP-адрес объекта {object_id} не найден")
                return
            
            ip_address = obj.ip_address
            port = obj.port
            protocol = obj.protocol or modbus_config.get("protocol", "modbus_tcp")

            obj_slave_id = obj.slave_id if hasattr(obj, 'slave_id') else None
            config_slave_id = modbus_config.get("slave_id")
            config_unit_id = modbus_config.get("unit_id")

            if protocol == "modbus_tcp" and config_unit_id is not None and (obj_slave_id is None or obj_slave_id == 1):
                slave_id = config_unit_id
                slave_id_source = "config.unit_id"
            elif obj_slave_id:
                slave_id = obj_slave_id
                slave_id_source = "object.slave_id"
            elif config_slave_id is not None:
                slave_id = config_slave_id
                slave_id_source = "config.slave_id"
            elif config_unit_id is not None:
                slave_id = config_unit_id
                slave_id_source = "config.unit_id"
            else:
                slave_id = 1
                slave_id_source = "default"
        logger.info(
            f"[{task_id}] Protocol: {protocol}, IP: {ip_address}, Port: {port}, "
            f"Slave: {slave_id} (source={slave_id_source})"
        )
        
        # Используем брокер
        broker = get_broker()
        group_timeout_streaks: Dict[str, int] = {}
        group_backoff_until: Dict[str, datetime] = {}

        groups_count = len(register_groups)
        group_stagger_delay = 0.0
        if groups_count > 1:
            group_stagger_delay = min(1.0, max(0.1, interval / 1000 / groups_count))
            logger.info(
                f"[{task_id}] Polling stagger enabled: {groups_count} groups, "
                f"delay={group_stagger_delay:.2f}s between groups"
            )
        
        while True:
            try:
                collected_data = {}
                
                # Читаем каждую группу регистров
                for group_index, group_name in enumerate(register_groups):
                    channel_event: Optional[Dict[str, Any]] = None
                    if group_index > 0 and group_stagger_delay > 0:
                        await asyncio.sleep(group_stagger_delay)
                    group = modbus_config["register_groups"].get(group_name)
                    
                    if not group:
                        logger.warning(f"[{task_id}] Register group '{group_name}' not found")
                        continue
                    
                    # logger.debug(f"[{task_id}] Reading group '{group_name}'")
                    
                    # Для modbus_over_tcp (Deye) читаем всю группу за раз
                    if protocol == "modbus_over_tcp":
                        backoff_until = group_backoff_until.get(group_name)
                        if backoff_until and datetime.now() < backoff_until:
                            continue
                        try:
                            start_address = group.get("start_address")
                            count = group.get("count")
                            func_code = group.get("func_code", 3)
                            group_slave_id = group.get("slave_id", slave_id)
                            channel_name, channel_endpoint = _func_code_to_cache_channel(func_code)
                            
                            if start_address is None or count is None:
                                logger.error(f"[{task_id}] Group '{group_name}' missing start_address or count")
                                continue
                            
                            # logger.debug(
                            #     f"[{task_id}] Reading {count} registers from address {start_address} "
                            #     f"(func={func_code})"
                            # )
                            
                            # Используем брокер с приоритетом POLLING
                            result = await broker.submit_request(
                                protocol=protocol,
                                host=ip_address,
                                port=port,
                                operation="read",
                                params={"start": start_address, "count": count, "func_code": func_code},
                                slave_id=group_slave_id,
                                object_id=object_id,
                                priority=RequestPriority.POLLING,
                                timeout=8.0,
                                request_id=f"polling_{task_id}_{group_name}",
                            )
                            
                            raw_registers = result.get("data", [])

                            if len(raw_registers) < count:
                                logger.warning(
                                    f"[{task_id}] Short response for group '{group_name}': "
                                    f"expected {count}, got {len(raw_registers)}"
                                )

                            group_timeout_streaks.pop(group_name, None)
                            group_backoff_until.pop(group_name, None)

                            await set_modbus_register_cache(
                                protocol=protocol,
                                host=ip_address,
                                port=port,
                                slave_id=group_slave_id,
                                start=start_address,
                                count=count,
                                func_code=func_code,
                                object_id=object_id,
                                data=raw_registers,
                            )

                            if func_code == 1:
                                coils_key = make_coils_cache_key(
                                    protocol=protocol,
                                    host=ip_address,
                                    port=port,
                                    slave_id=group_slave_id,
                                    start=start_address,
                                    count=count,
                                    object_id=object_id,
                                )
                                await set_modbus_cache(coils_key, raw_registers)
                            elif func_code == 2:
                                discrete_key = make_discrete_inputs_cache_key(
                                    protocol=protocol,
                                    host=ip_address,
                                    port=port,
                                    slave_id=group_slave_id,
                                    start=start_address,
                                    count=count,
                                    object_id=object_id,
                                )
                                await set_modbus_cache(discrete_key, raw_registers)

                            channel_event = {
                                "channel": channel_name,
                                "endpoint": channel_endpoint,
                                "group": group_name,
                                "func_code": func_code,
                                "ok": True,
                                "data": raw_registers,
                                "source": "cache",
                                "meta": {
                                    "start": start_address,
                                    "count": count,
                                    "slave_id": group_slave_id,
                                },
                                "request": {
                                    "protocol": protocol,
                                    "host": ip_address,
                                    "port": port,
                                    "slave_id": group_slave_id,
                                    "object_id": object_id,
                                    "start": start_address,
                                    "count": count,
                                    "func_code": func_code,
                                },
                                "measured_at": datetime.now().isoformat(),
                            }
                            # logger.debug(f"[{task_id}] Got {len(raw_registers)} registers: {raw_registers[:5]}...")
                            
                            register_modbus_success(object_id)
                            
                            # Обрабатываем каждый регистр в группе
                            for register in group.get("registers", []):
                                try:
                                    offset = register.get("offset", 0)
                                    reg_type = register.get("type", "uint16")
                                    scale = register.get("scale", 1.0)
                                    name = register["name"]
                                    bit_index = register.get("bit", None)  # Индекс бита для извлечения
                                    
                                    if offset >= len(raw_registers):
                                        continue
                                    
                                    raw_value = raw_registers[offset]
                                    
                                    # Декодируем значение
                                    if reg_type == "int16":
                                        value = decode_signed_16(raw_value)
                                    elif reg_type == "uint16":
                                        value = raw_value
                                    elif reg_type == "int32" and offset + 1 < len(raw_registers):
                                        value = decode_signed_32(raw_registers[offset], raw_registers[offset + 1])
                                    elif reg_type == "uint32" and offset + 1 < len(raw_registers):
                                        value = (raw_registers[offset] << 16) | raw_registers[offset + 1]
                                    else:
                                        value = raw_value
                                    
                                    # Если нужно извлечь конкретный бит
                                    if bit_index is not None:
                                        # logger.debug(f"[{task_id}] Bit extraction for {name}: raw={value}, bit_index={bit_index}, extracted={(int(value) >> bit_index) & 1}")
                                        value = (int(value) >> bit_index) & 1
                                    
                                    # Применяем масштабирование
                                    scaled_value = value * scale
                                    collected_data[name] = scaled_value
                                    
                                    # logger.debug(
                                    #     f"[{task_id}] {name}={scaled_value} (raw={raw_value}, scale={scale}, bit={bit_index})"
                                    # )
                                    
                                except Exception as e:
                                    logger.error(
                                        f"[{task_id}] Error processing register {register.get('name')}: {e}"
                                    )
                            
                        except TimeoutError:
                            streak = group_timeout_streaks.get(group_name, 0) + 1
                            group_timeout_streaks[group_name] = streak
                            backoff_seconds = min(60, 2 ** min(streak, 5))
                            group_backoff_until[group_name] = datetime.now() + timedelta(seconds=backoff_seconds)
                            logger.warning(f"[{task_id}] Timeout reading group '{group_name}'")
                            logger.warning(
                                f"[{task_id}] Backoff for group '{group_name}': {backoff_seconds}s "
                                f"(streak={streak})"
                            )
                            register_modbus_error(object_id)
                        except Exception as e:
                            error_text = str(e).lower()
                            if "таймаут" in error_text or "timeout" in error_text:
                                streak = group_timeout_streaks.get(group_name, 0) + 1
                                group_timeout_streaks[group_name] = streak
                                backoff_seconds = min(60, 2 ** min(streak, 5))
                                group_backoff_until[group_name] = datetime.now() + timedelta(seconds=backoff_seconds)
                            logger.error(
                                f"[{task_id}] Error reading group '{group_name}': {e}"
                            )
                            register_modbus_error(object_id)
                    
                    # Для modbus_tcp (Victron) читаем регистры по отдельности
                    else:
                        for register in group.get("registers", []):
                            try:
                                # Чтение регистра
                                address = register["address"]
                                count = register.get("count", 1)
                                reg_type = register.get("type", "uint16")
                                scale = register.get("scale", 1.0)
                                name = register["name"]
                                bit_index = register.get("bit", None)  # Индекс бита для извлечения
                                
                                # Определяем func_code из типа функции
                                func_code = 4  # input registers по умолчанию
                                if "function" in register:
                                    if register["function"] == "holding":
                                        func_code = 3
                                
                                # Используем брокер с приоритетом POLLING
                                result = await broker.submit_request(
                                    protocol=protocol,
                                    host=ip_address,
                                    port=port,
                                    operation="read",
                                    params={"start": address, "count": count, "func_code": func_code},
                                    slave_id=slave_id,
                                    object_id=object_id,
                                    priority=RequestPriority.POLLING,
                                    timeout=8.0,
                                    request_id=f"polling_{task_id}_{name}",
                                )
                                
                                registers_data = result.get("data", [])
                                await set_modbus_register_cache(
                                    protocol=protocol,
                                    host=ip_address,
                                    port=port,
                                    slave_id=slave_id,
                                    start=address,
                                    count=count,
                                    func_code=func_code,
                                    object_id=object_id,
                                    data=registers_data,
                                )
                                
                                register_modbus_success(object_id)
                                
                                # Декодируем значение в зависимости от типа
                                raw_value = registers_data[0] if len(registers_data) > 0 else 0
                                
                                if reg_type == "int16":
                                    value = decode_signed_16(raw_value)
                                elif reg_type == "uint16":
                                    value = raw_value
                                elif reg_type == "int32" and len(registers_data) >= 2:
                                    value = decode_signed_32(registers_data[0], registers_data[1])
                                elif reg_type == "uint32" and len(registers_data) >= 2:
                                    value = (registers_data[0] << 16) | registers_data[1]
                                else:
                                    value = raw_value
                                
                                # Если нужно извлечь конкретный бит
                                if bit_index is not None:
                                    # logger.debug(f"[{task_id}] Bit extraction for {name}: raw={value}, bit_index={bit_index}, extracted={(int(value) >> bit_index) & 1}")
                                    value = (int(value) >> bit_index) & 1
                                
                                # Применяем масштабирование
                                scaled_value = value * scale
                                
                                collected_data[name] = scaled_value
                                
                                # logger.debug(
                                #     f"[{task_id}] Read {name}={scaled_value} "
                                #     f"(raw={raw_value}, scale={scale}, bit={bit_index})"
                                # )
                                
                            except TimeoutError:
                                logger.warning(f"[{task_id}] Timeout reading register {register.get('name')}")
                                register_modbus_error(object_id)
                            except Exception as e:
                                logger.error(
                                    f"[{task_id}] Error reading register {register.get('name')}: {e}"
                                )
                                register_modbus_error(object_id)

                    if collected_data or channel_event:
                        await self._store_polling_snapshot(
                            object_id=object_id,
                            object_name=object_name,
                            collected_data=collected_data,
                            channel_event=channel_event,
                        )
                
                if collected_data and protocol == "modbus_over_tcp" and modbus_config_file == "deye_inverter.json":
                    _enrich_deye_polling_snapshot(collected_data)

                if collected_data:
                    register_modbus_success(object_id)

                    collected_data["measured_at"] = datetime.now()
                    collected_data["object_name"] = object_name
                    collected_data["energetic_object_id"] = object_id
                    await self._store_polling_snapshot(
                        object_id=object_id,
                        object_name=object_name,
                        collected_data=collected_data,
                        persist_history=True,
                        source_protocol=protocol,
                        source_task_id=task_id,
                    )

                    # Обновляем данные объекта для Telegram команд (/status, /power, /grid)
                    try:
                        from backend.services.telegram.telegram_bot import update_object_data

                        update_object_data(object_id, {
                            "object_name": object_name,
                            "soc": collected_data.get("soc", collected_data.get("battery1_soc")),
                            "general_battery_power": collected_data.get(
                                "general_battery_power",
                                collected_data.get("battery1_power"),
                            ),
                            "solar_total_pv_power": collected_data.get("solar_total_pv_power"),
                            "inverter_total_ac_output": collected_data.get(
                                "inverter_total_ac_output",
                                collected_data.get("inverter_total_power"),
                            ),
                            "ess_total_input_power": collected_data.get("ess_total_input_power"),
                            "phase_voltage_a": collected_data.get("phase_voltage_a"),
                            "phase_voltage_b": collected_data.get("phase_voltage_b"),
                            "phase_voltage_c": collected_data.get("phase_voltage_c"),
                            "grid_total_power": collected_data.get("total_power"),
                            "battery1_voltage": collected_data.get("battery1_voltage"),
                            "battery2_voltage": collected_data.get("battery2_voltage"),
                            "gen_relay": collected_data.get("gen_relay"),
                            "relay_input_0": collected_data.get("relay_input_0"),
                            "relay_input_1": collected_data.get("relay_input_1"),
                        })
                    except Exception as e:
                        logger.error(f"[{task_id}] Error updating object data for Telegram commands: {e}")
                    
                    # logger.info(
                    #     f"[{task_id}] Collected {len(collected_data)} values from {object_name}"
                    # )
                
                    # Пока просто логируем
                    # logger.debug(f"[{task_id}] Data: {collected_data}")
                    
                    # Проверяем gen_relay для уведомлений в Telegram
                    if "gen_relay" in collected_data:
                        try:
                            from backend.services.telegram.telegram_manager import telegram_manager
                            
                            telegram_monitor = telegram_manager.get_monitor(object_id)
                            if telegram_monitor:
                                # Получаем telegram_chat_ids из БД
                                async with async_session_maker() as db:
                                    obj_data = await db.execute(
                                        select(EnergeticObject).where(EnergeticObject.id == object_id)
                                    )
                                    obj = obj_data.scalar_one_or_none()
                                    # telegram_chat_ids это строка через запятую - парсим в список
                                    if obj and hasattr(obj, 'telegram_chat_ids') and obj.telegram_chat_ids:
                                        chat_ids = [cid.strip() for cid in str(obj.telegram_chat_ids).split(',') if cid.strip()]
                                    else:
                                        chat_ids = []
                                
                                gen_relay_value = int(collected_data["gen_relay"])
                                battery1_voltage = collected_data.get("battery1_voltage")
                                battery2_voltage = collected_data.get("battery2_voltage")

                                if object_id not in self._gen_relay_first_seen:
                                    self._gen_relay_first_seen.add(object_id)
                                    logger.info(
                                        f"[{task_id}] First gen_relay read for {object_name}: {gen_relay_value}"
                                    )

                                empty_key = (object_id, "generator")
                                if not chat_ids and empty_key not in self._telegram_chat_ids_empty_warned:
                                    self._telegram_chat_ids_empty_warned.add(empty_key)
                                    logger.warning(
                                        f"[{task_id}] telegram_chat_ids empty for {object_name}, "
                                        "generator уведомление не будет отправлено"
                                    )
                                
                                await telegram_monitor.check_generator_status(
                                    object_id=object_id,
                                    object_name=object_name,
                                    gen_relay=gen_relay_value,
                                    input_running=collected_data.get("relay_input_0"),
                                    input_manual=collected_data.get("relay_input_1"),
                                    battery1_voltage=battery1_voltage,
                                    battery2_voltage=battery2_voltage,
                                    chat_ids=chat_ids
                                )
                            else:
                                logger.warning(
                                    f"[{task_id}] Telegram monitor not available for object {object_name} ({object_id})"
                                )
                        except Exception as e:
                            logger.error(f"[{task_id}] Error checking gen_relay for Telegram: {e}")
                    
                    # Проверяем напряжение сети для уведомлений в Telegram
                    grid_voltage_fields = ["phase_voltage_a", "phase_voltage_b", "phase_voltage_c"]
                    has_all_grid_voltage_fields = all(key in collected_data for key in grid_voltage_fields)
                    has_partial_grid_voltage_fields = any(key in collected_data for key in grid_voltage_fields) and not has_all_grid_voltage_fields

                    if has_all_grid_voltage_fields:
                        try:
                            from backend.services.telegram.telegram_manager import telegram_manager
                            
                            telegram_monitor = telegram_manager.get_monitor(object_id)
                            if telegram_monitor:
                                # Получаем telegram_chat_ids из БД
                                async with async_session_maker() as db:
                                    obj_data = await db.execute(
                                        select(EnergeticObject).where(EnergeticObject.id == object_id)
                                    )
                                    obj = obj_data.scalar_one_or_none()
                                    # telegram_chat_ids это строка через запятую - парсим в список
                                    if obj and hasattr(obj, 'telegram_chat_ids') and obj.telegram_chat_ids:
                                        chat_ids = [cid.strip() for cid in str(obj.telegram_chat_ids).split(',') if cid.strip()]
                                    else:
                                        chat_ids = []
                                
                                voltage_l1 = collected_data.get("phase_voltage_a")
                                voltage_l2 = collected_data.get("phase_voltage_b")
                                voltage_l3 = collected_data.get("phase_voltage_c")
                                battery1_voltage = collected_data.get("battery1_voltage")
                                battery2_voltage = collected_data.get("battery2_voltage")

                                empty_key = (object_id, "grid")
                                if not chat_ids and empty_key not in self._telegram_chat_ids_empty_warned:
                                    self._telegram_chat_ids_empty_warned.add(empty_key)
                                    logger.warning(
                                        f"[{task_id}] telegram_chat_ids empty for {object_name}, "
                                        "grid уведомление не будет отправлено"
                                    )
                                
                                await telegram_monitor.check_grid_voltage(
                                    object_id=object_id,
                                    object_name=object_name,
                                    voltage_l1=voltage_l1,
                                    voltage_l2=voltage_l2,
                                    voltage_l3=voltage_l3,
                                    battery1_voltage=battery1_voltage,
                                    battery2_voltage=battery2_voltage,
                                    chat_ids=chat_ids
                                )
                            else:
                                logger.warning(
                                    f"[{task_id}] Telegram monitor not available for object {object_name} ({object_id})"
                                )
                        except Exception as e:
                            logger.error(f"[{task_id}] Error checking grid voltage for Telegram: {e}")
                    elif has_partial_grid_voltage_fields:
                        logger.warning(
                            f"[{task_id}] Skip grid voltage check due to incomplete phase data: "
                            f"required={grid_voltage_fields}"
                        )
                    
                    
                    # Проверяем ошибки и предупреждения для уведомлений в Telegram (Deye inverter)
                    deye_fault_fields = ["fault_1", "fault_2", "warning_1", "warning_2"]
                    has_all_deye_fault_fields = all(key in collected_data for key in deye_fault_fields)
                    has_partial_deye_fault_fields = any(key in collected_data for key in deye_fault_fields) and not has_all_deye_fault_fields

                    if has_all_deye_fault_fields:
                        try:
                            raw_fault_1 = int(collected_data.get("fault_1", 0) or 0)
                            raw_fault_2 = int(collected_data.get("fault_2", 0) or 0)
                            raw_warning_1 = int(collected_data.get("warning_1", 0) or 0)
                            raw_warning_2 = int(collected_data.get("warning_2", 0) or 0)

                            faults = self._decode_deye_faults_warnings(
                                raw_fault_1,
                                raw_fault_2,
                                modbus_config.get("fault_info", {})
                            )

                            warnings = self._decode_deye_faults_warnings(
                                raw_warning_1,
                                raw_warning_2,
                                modbus_config.get("warning_info", {})
                            )

                            await self._persist_deye_alarm_snapshot_if_changed(
                                object_id=object_id,
                                object_name=object_name,
                                task_id=task_id,
                                measured_at=collected_data["measured_at"],
                                raw_fault_1=raw_fault_1,
                                raw_fault_2=raw_fault_2,
                                raw_warning_1=raw_warning_1,
                                raw_warning_2=raw_warning_2,
                                faults=faults,
                                warnings=warnings,
                            )

                            if faults or warnings:
                                fault_codes = [issue.get("name", "") for issue in faults if issue.get("name")]
                                warning_codes = [issue.get("name", "") for issue in warnings if issue.get("name")]
                                logger.warning(
                                    f"[{task_id}] Deye alarm snapshot for {object_name} ({object_id}): "
                                    f"fault_words=({raw_fault_1},{raw_fault_2}), "
                                    f"warning_words=({raw_warning_1},{raw_warning_2}), "
                                    f"faults={fault_codes}, warnings={warning_codes}"
                                )
                        except Exception as e:
                            logger.error(f"[{task_id}] Error processing Deye faults/warnings snapshot: {e}")
                    elif has_partial_deye_fault_fields:
                        logger.warning(
                            f"[{task_id}] Skip Deye faults/warnings check due to incomplete data: "
                            f"required={deye_fault_fields}"
                        )
                else:
                    logger.warning(f"[{task_id}] No data collected from {object_name}")
                
            except asyncio.CancelledError:
                logger.info(f"[{task_id}] MODBUS_REGISTERS task cancelled")
                raise
            except Exception as e:
                logger.error(f"[{task_id}] Error in MODBUS_REGISTERS task: {e}", exc_info=True)
            
            await asyncio.sleep(interval / 1000)
    
    async def _run_custom_command_task(
        self,
        task_id: str,
        object_id: str,
        object_name: str,
        interval: int,
        command_config: dict,
        modbus_config_file: Optional[str]
    ):
        """Пользовательская задача (для будущего расширения)"""
        logger.warning(f"[{task_id}] CUSTOM_COMMAND task type not yet implemented")
        
        # Заглушка для будущей реализации
        while True:
            try:
                # logger.debug(f"[{task_id}] CUSTOM_COMMAND task tick for {object_name}")
                await asyncio.sleep(interval / 1000)
            except asyncio.CancelledError:
                logger.info(f"[{task_id}] CUSTOM_COMMAND task cancelled")
                raise
    
    async def get_running_tasks_info(self) -> list:
        """Получение информации о запущенных задачах"""
        tasks_info = []
        
        for task_id, task_data in self.tasks.items():
            config = task_data["config"]
            async_task = task_data["task"]
            
            tasks_info.append({
                "task_id": task_id,
                "task_type": config["task_type"],
                "object_id": config["object_id"],
                "object_name": config["object_name"],
                "interval": config["interval"],
                "is_running": not async_task.done(),
                "is_cancelled": async_task.cancelled(),
            })
        
        return tasks_info
