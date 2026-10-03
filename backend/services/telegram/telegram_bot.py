"""
Telegram бот для мониторинга энергетических объектов.
Отправляет уведомления в группы при низком уровне заряда батарей, смене расписаний,
потере связи, включении/выключении генератора и других событиях.
Поддерживает команды для получения текущего статуса системы.
"""
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Optional, List
from zoneinfo import ZoneInfo
import math
import aiohttp
from loguru import logger
from backend.config.config import settings
from backend.database.models.energy import EnergeticObject, DeyeAlarmEvent
from backend.database.models.enums import RequestPriority
from backend.repository.energy.modbus_broker import get_broker
from backend.services.energy.modbus_client import get_or_create_modbus_client, ModbusTCP


GENERATOR_SLAVE_ID = 10
GENERATOR_RELAY_START = 1
GENERATOR_RELAY_BLOCK = 0  
GENERATOR_INPUT_RUNNING = 0
GENERATOR_INPUT_MANUAL_START = 1
GENERATOR_COMMAND_TIMEOUT_SECONDS = 300
GENERATOR_COMMAND_POLL_SECONDS = 2.0
GENERATOR_COMMAND_RETRY_ATTEMPTS = 3
GENERATOR_NOTIFICATION_DEBOUNCE_SECONDS = 30
GRID_NOTIFICATION_DEBOUNCE_SECONDS = 30
DEYE_FAULTS_NOTIFICATION_DEBOUNCE_SECONDS = 30
DEYE_FAULTS_NOTIFICATION_CONFIRM_SECONDS = 180
SEND_DEYE_RESOLVED_NOTIFICATIONS = False
GRID_MIN_VALID_VOLTAGE = 0.0
GRID_MAX_VALID_VOLTAGE = 300.0
GRID_SUSPICIOUS_LOW_VOLTAGE = 10.0
GRID_NOMINAL_PRESENT_VOLTAGE = 180.0


def _as_number(value, default: float = 0.0) -> float:
    """Безопасно приводит значение к числу для сообщений/расчетов."""
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_optional_bool(value) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return None


def _resolve_generator_status(
    gen_relay: int,
    input_running: Optional[bool],
    input_manual: Optional[bool],
) -> Dict[str, Optional[str] | bool]:
    running_by_relay = gen_relay == 1
    running_by_input = bool(input_running) if input_running is not None else False
    manual_panel = bool(input_manual) if input_manual is not None else False

    is_running = running_by_relay or running_by_input or manual_panel

    if manual_panel:
        start_reason = "ручной пуск с щитовой"
    elif running_by_input and not running_by_relay:
        start_reason = "ручной пуск (вход DI0)"
    elif running_by_relay:
        start_reason = "автоматический запуск инвертором"
    else:
        start_reason = None

    if is_running:
        status_icon = "⚡"
        status_text = "<b>РАБОТАЕТ</b>"
    else:
        status_icon = "🛑"
        status_text = "<b>ВЫКЛЮЧЕН</b>"

    return {
        "is_running": is_running,
        "start_reason": start_reason,
        "status_icon": status_icon,
        "status_text": status_text,
    }


def _is_valid_phase_voltage(value: Optional[float]) -> bool:
    if value is None:
        return False
    if not isinstance(value, (int, float)):
        return False
    if not math.isfinite(float(value)):
        return False
    return GRID_MIN_VALID_VOLTAGE <= float(value) <= GRID_MAX_VALID_VOLTAGE


def _is_complete_grid_snapshot(voltage_l1: Optional[float], voltage_l2: Optional[float], voltage_l3: Optional[float]) -> bool:
    return (
        _is_valid_phase_voltage(voltage_l1)
        and _is_valid_phase_voltage(voltage_l2)
        and _is_valid_phase_voltage(voltage_l3)
    )


def _is_suspicious_grid_snapshot(voltage_l1: float, voltage_l2: float, voltage_l3: float) -> bool:
    values = [float(voltage_l1), float(voltage_l2), float(voltage_l3)]
    has_very_low = any(v < GRID_SUSPICIOUS_LOW_VOLTAGE for v in values)
    has_nominal = any(v >= GRID_NOMINAL_PRESENT_VOLTAGE for v in values)
    return has_very_low and has_nominal


class TelegramBatteryMonitor:
    """Мониторинг батарей с уведомлениями в Telegram"""
    
    def __init__(
        self,
        bot_token: str = None,
        chat_ids: List[str] = None,
        alert_threshold: int = None,
        cooldown_minutes: int = None,
        timezone: str = None
    ):
        """
        Инициализация Telegram монитора
        
        Args:
            bot_token: Токен Telegram бота (из BotFather)
            chat_ids: Список ID чатов/групп для отправки уведомлений
            alert_threshold: Уровень заряда (%) для отправки уведомления
            cooldown_minutes: Минимальный интервал между уведомлениями
            timezone: Часовой пояс (например: 'Europe/Kyiv', 'Europe/Moscow')
        """
        # Используем токен из настроек или переданный параметр
        self.bot_token = bot_token or settings.telegram_bot_token
        
        # По умолчанию используем пустой список - chat_ids должны передаваться явно
        # для каждого объекта из EnergeticObject.telegram_chat_ids
        if chat_ids:
            self.chat_ids = chat_ids if isinstance(chat_ids, list) else [chat_ids]
        else:
            self.chat_ids = []
        
        self.alert_threshold = alert_threshold or getattr(settings, 'telegram_battery_alert_threshold', 70)
        self.cooldown_minutes = cooldown_minutes or getattr(settings, 'telegram_alert_cooldown_minutes', 60)
        self.timezone = ZoneInfo(timezone or getattr(settings, 'telegram_timezone', 'Europe/Kyiv'))
        
        # Хранилище последних уведомлений для каждого объекта
        self._last_alerts: Dict[str, datetime] = {}
        self._generator_states: Dict[str, bool] = {}
        self._generator_pending_states: Dict[str, Dict[str, datetime | bool]] = {}
        self._grid_voltage_states: Dict[str, bool] = {}
        self._grid_pending_states: Dict[str, Dict[str, datetime | bool]] = {}
        self._grid_last_notified_states: Dict[str, bool] = {}
        self._grid_retry_after: Dict[str, datetime] = {}
        self._deye_fault_states: Dict[str, bool] = {}
        self._deye_fault_pending_states: Dict[str, Dict[str, datetime | bool]] = {}
        self._deye_warning_states: Dict[str, bool] = {}
        self._deye_warning_pending_states: Dict[str, Dict[str, datetime | bool]] = {}
        
        self.api_url = f"https://api.telegram.org/bot{self.bot_token}"
        
        logger.info(
            f"TelegramBatteryMonitor initialized: "
            f"default_chats={len(self.chat_ids)}, threshold={self.alert_threshold}%, "
            f"cooldown={self.cooldown_minutes}min, timezone={self.timezone}"
        )
        logger.info(
            "ℹ️ Chat IDs should be provided per object from EnergeticObject.telegram_chat_ids"
        )
    
    def _should_send_alert(self, object_id: str) -> bool:
        """
        Проверяет, нужно ли отправлять уведомление (учитывая cooldown)
        
        Args:
            object_id: ID энергетического объекта
            
        Returns:
            True если можно отправить уведомление
        """
        if object_id not in self._last_alerts:
            return True
        
        last_alert = self._last_alerts[object_id]
        time_since_alert = datetime.now() - last_alert
        cooldown = timedelta(minutes=self.cooldown_minutes)
        
        return time_since_alert >= cooldown

    def _should_confirm_state_change(
        self,
        state_key: str,
        current_state: bool,
        stable_states: Dict[str, bool],
        pending_states: Dict[str, Dict[str, datetime | bool]],
        debounce_seconds: int
    ) -> bool:
        """
        Проверяет, подтверждено ли изменение состояния после debounce-задержки.

        Args:
            state_key: Ключ состояния
            current_state: Текущее состояние
            stable_states: Словарь подтвержденных состояний
            pending_states: Словарь ожидающих подтверждения состояний
            debounce_seconds: Задержка подтверждения в секундах

        Returns:
            True, если изменение подтверждено и можно отправлять уведомление
        """
        last_state = stable_states.get(state_key)

        if last_state is None:
            stable_states[state_key] = current_state
            pending_states.pop(state_key, None)
            return False

        if current_state == last_state:
            pending_states.pop(state_key, None)
            return False

        now = datetime.now()
        pending = pending_states.get(state_key)

        if pending is None or pending["state"] != current_state:
            pending_states[state_key] = {
                "state": current_state,
                "since": now
            }
            return False

        pending_since = pending["since"]
        if not isinstance(pending_since, datetime):
            pending_states[state_key] = {
                "state": current_state,
                "since": now
            }
            return False

        if (now - pending_since).total_seconds() < debounce_seconds:
            return False

        stable_states[state_key] = current_state
        pending_states.pop(state_key, None)
        return True
    
    async def send_message(self, text: str, chat_id: str = None, chat_ids: Optional[List[str]] = None) -> bool:
        """
        Отправляет сообщение в Telegram чат
        
        Args:
            text: Текст сообщения
            chat_id: Конкретный chat_id (если None, отправляет во все)
            chat_ids: Список chat_id для отправки
            
        Returns:
            True если сообщение отправлено успешно хотя бы в один чат
        """
        # Приоритет: chat_id (единственный) > chat_ids (список) > self.chat_ids (дефолт)
        if chat_id:
            target_chats = [chat_id]
        elif chat_ids and len(chat_ids) > 0:
            target_chats = chat_ids
        else:
            target_chats = self.chat_ids
        
        logger.info(f"📨 send_message called: chat_id={chat_id}, chat_ids={chat_ids}, target_chats={target_chats}")
        
        if not target_chats:
            logger.warning("⚠️ No target chats specified, message not sent")
            return False
        
        success_count = 0
        
        for chat in target_chats:
            max_retries = 3
            retry_delay = 1
            
            for attempt in range(max_retries):
                try:
                    url = f"{self.api_url}/sendMessage"
                    payload = {
                        "chat_id": chat,
                        "text": text,
                        "parse_mode": "HTML"
                    }
                    
                    # logger.debug(f"📤 Sending to chat {chat} (attempt {attempt + 1}/{max_retries})")
                    
                    async with aiohttp.ClientSession() as session:
                        async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as response:
                            if response.status == 200:
                                result = await response.json()
                                logger.info(f"✅ Telegram message sent successfully to chat {chat}")
                                success_count += 1
                                break  # Успешно отправлено, выходим из retry loop
                            else:
                                error_text = await response.text()
                                logger.error(
                                    f"❌ Failed to send Telegram message to chat {chat}. "
                                    f"Status: {response.status}, Error: {error_text}"
                                )
                                if attempt < max_retries - 1:
                                    await asyncio.sleep(retry_delay)
                                    retry_delay *= 2  # Exponential backoff
                
                except (asyncio.TimeoutError, aiohttp.ClientError) as e:
                    logger.warning(f"⚠️ Network error sending to chat {chat} (attempt {attempt + 1}): {e}")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(retry_delay)
                        retry_delay *= 2
                    else:
                        logger.error(f"💥 Failed to send message to chat {chat} after {max_retries} attempts")
                
                except Exception as e:
                    logger.error(f"💥 Unexpected error sending Telegram message to chat {chat}: {e}", exc_info=True)
                    break  # При неожиданной ошибке не ретраим
        
        if success_count > 0:
            logger.info(f"Telegram message sent to {success_count}/{len(target_chats)} chats")
            return True
        
        return False
    
    async def check_battery_level(
        self,
        object_id: str,
        object_name: str,
        battery_soc: float,
        battery_voltage: Optional[float] = None,
        battery_power: Optional[float] = None,
        chat_ids: Optional[List[str]] = None
    ):
        """
        Проверяет уровень заряда батареи и отправляет уведомление при необходимости
        
        Args:
            object_id: ID энергетического объекта
            object_name: Название объекта
            battery_soc: Уровень заряда батареи (%)
            battery_voltage: Напряжение батареи (V)
            battery_power: Мощность батареи (W)
            chat_ids: Список chat_id для отправки (из EnergeticObject.telegram_chat_ids)
        """
        # Проверяем порог
        if battery_soc > self.alert_threshold:
            # logger.debug(f"Battery level normal for {object_name} ({battery_soc}%), no alert needed. allowed threshold is {self.alert_threshold}%")  
            # Заряд нормальный, сбрасываем таймер если был
            if object_id in self._last_alerts:
                del self._last_alerts[object_id]
            return
        
        # Низкий заряд - проверяем cooldown
        if not self._should_send_alert(object_id):
            # logger.debug(
            #     f"Battery low for {object_name} ({battery_soc}%), "
            #     f"but cooldown not expired yet"
            # )
            return
        
        # Формируем сообщение
        message = self._format_alert_message(
            object_name=object_name,
            battery_soc=battery_soc,
            battery_voltage=battery_voltage,
            battery_power=battery_power
        )
        
        # Отправляем уведомление в чаты объекта
        success = await self.send_message(message, chat_ids=chat_ids)
        
        if success:
            # Обновляем время последнего уведомления
            self._last_alerts[object_id] = datetime.now()
            logger.warning(
                f"⚠️ Low battery alert sent for {object_name}: {battery_soc}%"
            )
    
    def _format_alert_message(
        self,
        object_name: str,
        battery_soc: float,
        battery_voltage: Optional[float] = None,
        battery_power: Optional[float] = None
    ) -> str:
        """
        Форматирует сообщение об уведомлении
        
        Args:
            object_name: Название объекта
            battery_soc: Уровень заряда (%)
            battery_voltage: Напряжение (V)
            battery_power: Мощность (W)
            
        Returns:
            Отформатированное сообщение
        """
        # Текущее время в настроенном часовом поясе
        now_local = datetime.now(self.timezone)
        timestamp = now_local.strftime("%Y-%m-%d %H:%M:%S")
        
        message_parts = [
            "🔋 <b>ПРЕДУПРЕЖДЕНИЕ: НИЗКИЙ УРОВЕНЬ ЗАРЯДА БАТАРЕИ</b>\n",
            f"📍 Объект: <b>{object_name}</b>",
            f"⚡ Уровень заряда: <b>{battery_soc:.1f}%</b>",
        ]
        
        if battery_voltage is not None:
            message_parts.append(f"🔌 Напряжение: {battery_voltage:.1f} V")
        
        if battery_power is not None:
            power_kw = battery_power / 1000
            message_parts.append(f"⚙️ Мощность: {power_kw:.2f} kW")
        
        message_parts.append(f"🕐 Время: {timestamp}")
        message_parts.append(f"\n⚠️ Пороговое значение: {self.alert_threshold}%")
        
        return "\n".join(message_parts)
    
    async def check_generator_status(
        self,
        object_id: str,
        object_name: str,
        gen_relay: int,
        input_running: Optional[bool] = None,
        input_manual: Optional[bool] = None,
        battery1_voltage: Optional[float] = None,
        battery2_voltage: Optional[float] = None,
        chat_ids: Optional[List[str]] = None
    ):
        """
        Проверяет статус генератора и отправляет уведомление при запуске/остановке
        
        Args:
            object_id: ID энергетического объекта
            object_name: Название объекта
            gen_relay: Значение реле генератора (1=включен, 0=выключен)
            input_running: DI0 - вход "генератор работает"
            input_manual: DI1 - вход "ручной запуск"
            battery1_voltage: Напряжение батареи 1 (V)
            battery2_voltage: Напряжение батареи 2 (V)
            chat_ids: Список chat_id для отправки (из EnergeticObject.telegram_chat_ids)
        """
        object_data = _objects_data.get(object_id, {})
        if input_running is None:
            input_running = _as_optional_bool(object_data.get("relay_input_0"))
        if input_manual is None:
            input_manual = _as_optional_bool(object_data.get("relay_input_1"))

        status = _resolve_generator_status(gen_relay, input_running, input_manual)

        # Ключ для хранения последнего состояния
        state_key = f"gen_{object_id}"
        is_on = bool(status["is_running"])
        
        last_state = self._generator_states.get(state_key)
        
        # Первая инициализация: отправляем старт, если генератор уже включен
        if last_state is None:
            self._generator_states[state_key] = is_on
            self._generator_pending_states.pop(state_key, None)
            if not is_on:
                logger.info(
                    f"Generator monitor initialized for {object_name}: relay={gen_relay}, "
                    f"di0={input_running}, di1={input_manual}"
                )
                return
            logger.info(
                f"Generator monitor initialized ON for {object_name}: relay={gen_relay}, "
                f"di0={input_running}, di1={input_manual}"
            )
        elif not self._should_confirm_state_change(
            state_key=state_key,
            current_state=is_on,
            stable_states=self._generator_states,
            pending_states=self._generator_pending_states,
            debounce_seconds=GENERATOR_NOTIFICATION_DEBOUNCE_SECONDS,
        ):
            return
        
        # Формируем сообщение в зависимости от нового состояния
        now_local = datetime.now(self.timezone)
        timestamp = now_local.strftime("%Y-%m-%d %H:%M:%S")
        
        # Формируем информацию о батареях
        battery_info = ""
        battery_parts = []
        if battery1_voltage is not None:
            battery_parts.append(f"Батарея 1: {battery1_voltage:.1f}V")
        if battery2_voltage is not None:
            battery_parts.append(f"Батарея 2: {battery2_voltage:.1f}V")
        
        if battery_parts:
            battery_info = f"\n🔋 <b>Напряжение батарей:</b>\n   " + "\n   ".join(battery_parts)
        
        status_details = _format_generator_status_details(gen_relay, input_running, input_manual)
        details_block = f"\n{status_details['details']}" if status_details["details"] else ""

        if is_on:
            # Генератор запустился
            message = (
                "⚡ <b>ГЕНЕРАТОР ЗАПУЩЕН</b>\n\n"
                f"📍 Объект: <b>{object_name}</b>\n"
                f"🔌 Статус: <b>РАБОТАЕТ</b>{details_block}{battery_info}\n"
                f"🕐 Время: {timestamp}"
            )
            log_msg = f"⚡ Generator STARTED for {object_name}"
        else:
            # Генератор остановился
            message = (
                "🛑 <b>ГЕНЕРАТОР ОСТАНОВЛЕН</b>\n\n"
                f"📍 Объект: <b>{object_name}</b>\n"
                f"🔌 Статус: <b>ВЫКЛЮЧЕН</b>{details_block}{battery_info}\n"
                f"🕐 Время: {timestamp}"
            )
            log_msg = f"🛑 Generator STOPPED for {object_name}"
        
        # Отправляем уведомление
        success = await self.send_message(message, chat_ids=chat_ids)
        logger.info(
            f"Generator status change for {object_name}: is_on={is_on}, "
            f"relay={gen_relay}, di0={input_running}, di1={input_manual}"
        )
        if success:
            logger.info(log_msg)
        else:
            logger.warning(f"Failed to send generator alert for {object_name}")
    
    async def check_grid_voltage(
        self,
        object_id: str,
        object_name: str,
        voltage_l1: Optional[float] = None,
        voltage_l2: Optional[float] = None,
        voltage_l3: Optional[float] = None,
        battery1_voltage: Optional[float] = None,
        battery2_voltage: Optional[float] = None,
        chat_ids: Optional[List[str]] = None
    ):
        """
        Проверяет напряжение сети и отправляет уведомление при пропаже/восстановлении
        
        Args:
            object_id: ID энергетического объекта
            object_name: Название объекта
            voltage_l1: Напряжение на фазе L1 (V)
            voltage_l2: Напряжение на фазе L2 (V)
            voltage_l3: Напряжение на фазе L3 (V)
            battery1_voltage: Напряжение батареи 1 (V)
            battery2_voltage: Напряжение батареи 2 (V)
            chat_ids: Список chat_id для отправки (из EnergeticObject.telegram_chat_ids)
        """
        # Ключ для хранения последнего состояния
        state_key = f"grid_{object_id}"

        if not _is_complete_grid_snapshot(voltage_l1, voltage_l2, voltage_l3):
            logger.warning(
                f"Skip grid alert for {object_name}: incomplete or invalid phase voltages "
                f"(L1={voltage_l1}, L2={voltage_l2}, L3={voltage_l3})"
            )
            return

        if _is_suspicious_grid_snapshot(float(voltage_l1), float(voltage_l2), float(voltage_l3)):
            logger.warning(
                f"Skip grid alert for {object_name}: suspicious mixed phase snapshot "
                f"(L1={voltage_l1}, L2={voltage_l2}, L3={voltage_l3})"
            )
            return
        
        # Пороговое значение для определения пропадания напряжения
        VOLTAGE_THRESHOLD = 100.0
        
        # Определяем текущее состояние - есть ли питание хотя бы на одной фазе
        has_power = (
            float(voltage_l1) >= VOLTAGE_THRESHOLD
            or float(voltage_l2) >= VOLTAGE_THRESHOLD
            or float(voltage_l3) >= VOLTAGE_THRESHOLD
        )
        
        last_state = self._grid_voltage_states.get(state_key)
        now_local_dt = datetime.now(self.timezone)
        state_change_confirmed = False
        
        # Первая инициализация: отправляем алерт, если питание уже отсутствует
        if last_state is None:
            self._grid_voltage_states[state_key] = has_power
            self._grid_pending_states.pop(state_key, None)
            if has_power:
                self._grid_last_notified_states[state_key] = True
                logger.info(
                    f"Grid voltage monitor initialized for {object_name}: "
                    f"L1={voltage_l1}V, L2={voltage_l2}V, L3={voltage_l3}V"
                )
                return
            logger.info(
                f"Grid voltage monitor initialized without power for {object_name}: "
                f"L1={voltage_l1}V, L2={voltage_l2}V, L3={voltage_l3}V"
            )
        elif has_power != last_state:
            if not self._should_confirm_state_change(
                state_key=state_key,
                current_state=has_power,
                stable_states=self._grid_voltage_states,
                pending_states=self._grid_pending_states,
                debounce_seconds=GRID_NOTIFICATION_DEBOUNCE_SECONDS,
            ):
                return
            state_change_confirmed = True

        last_notified_state = self._grid_last_notified_states.get(state_key)
        notification_pending = (last_notified_state is None) or (last_notified_state != has_power)

        if not notification_pending:
            return

        if not state_change_confirmed and last_state == has_power:
            retry_after = self._grid_retry_after.get(state_key)
            if retry_after and now_local_dt < retry_after:
                return
        
        # Формируем сообщение в зависимости от нового состояния
        timestamp = now_local_dt.strftime("%Y-%m-%d %H:%M:%S")
        
        if not has_power:
            # Напряжение пропало
            icon = "⚠️"
            title = "ПОТЕРЯ ЭЛЕКТРОЭНЕРГИИ"
            
            # Определяем какие фазы пропали
            lost_phases = []
            if voltage_l1 is not None and voltage_l1 < VOLTAGE_THRESHOLD:
                lost_phases.append(f"L1: {voltage_l1:.1f}V")
            if voltage_l2 is not None and voltage_l2 < VOLTAGE_THRESHOLD:
                lost_phases.append(f"L2: {voltage_l2:.1f}V")
            if voltage_l3 is not None and voltage_l3 < VOLTAGE_THRESHOLD:
                lost_phases.append(f"L3: {voltage_l3:.1f}V")
            
            if lost_phases:
                status = f"❌ <b>Потеря фаз: {', '.join(lost_phases)}</b>"
            else:
                status = "❌ <b>Отсутствует входное напряжение</b>"
            
            # Добавляем информацию о батареях
            battery_info = ""
            battery_parts = []
            if battery1_voltage is not None:
                battery_parts.append(f"Батарея 1: {battery1_voltage:.1f}V")
            if battery2_voltage is not None:
                battery_parts.append(f"Батарея 2: {battery2_voltage:.1f}V")
            
            if battery_parts:
                battery_info = f"\n🔋 <b>Статус батарей:</b>\n   " + "\n   ".join(battery_parts)
            
            message = (
                f"{icon} <b>{title}</b>\n\n"
                f"📍 Объект: <b>{object_name}</b>\n"
                f"{status}{battery_info}\n"
                f"🕐 Время: {timestamp}"
            )
            log_msg = f"⚠️ Grid power LOST for {object_name}"
        else:
            # Напряжение восстановилось
            icon = "✅"
            title = "ЭЛЕКТРОЭНЕРГИЯ ВОССТАНОВЛЕНА"
            
            # Показываем текущие напряжения сети
            voltages = []
            if voltage_l1 is not None:
                voltages.append(f"L1: {voltage_l1:.1f}V")
            if voltage_l2 is not None:
                voltages.append(f"L2: {voltage_l2:.1f}V")
            if voltage_l3 is not None:
                voltages.append(f"L3: {voltage_l3:.1f}V")
            
            if voltages:
                status = (
                    f"✅ <b>Входное напряжение восстановлено</b>\n"
                    f"   📊 {', '.join(voltages)}"
                )
            else:
                status = "✅ <b>Входное напряжение восстановлено</b>"
            
            # Добавляем информацию о батареях
            battery_info = ""
            battery_parts = []
            if battery1_voltage is not None:
                battery_parts.append(f"Батарея 1: {battery1_voltage:.1f}V")
            if battery2_voltage is not None:
                battery_parts.append(f"Батарея 2: {battery2_voltage:.1f}V")
            
            if battery_parts:
                battery_info = f"\n🔋 <b>Статус батарей:</b>\n   " + "\n   ".join(battery_parts)
            
            message = (
                f"{icon} <b>{title}</b>\n\n"
                f"📍 Объект: <b>{object_name}</b>\n"
                f"{status}{battery_info}\n"
                f"🕐 Время: {timestamp}"
            )
            log_msg = f"✅ Grid power RESTORED for {object_name}"
        
        # Отправляем уведомление
        success = await self.send_message(message, chat_ids=chat_ids)
        logger.info(f"Grid voltage status change for {object_name}: has_power={has_power}")
        if success:
            self._grid_last_notified_states[state_key] = has_power
            self._grid_retry_after.pop(state_key, None)
            if has_power:
                logger.info(log_msg)
            else:
                logger.warning(log_msg)
        else:
            self._grid_retry_after[state_key] = now_local_dt + timedelta(seconds=GRID_NOTIFICATION_DEBOUNCE_SECONDS)
            logger.warning(f"Failed to send grid voltage alert for {object_name}")
    
    async def check_deye_faults_warnings(
        self,
        object_id: str,
        object_name: str,
        faults: List[dict],
        warnings: List[dict],
        chat_ids: Optional[List[str]] = None
    ):
        """
        Проверяет ошибки и предупреждения Deye инвертора и отправляет уведомления
        
        Args:
            object_id: ID энергетического объекта
            object_name: Название объекта
            faults: Список активных ошибок [{"bit": 1, "name": "F01", "description": "...", "solution": "..."}]
            warnings: Список активных предупреждений [{"bit": 1, "name": "W01", "description": "...", "solution": "..."}]
            chat_ids: Список chat_id для отправки (из EnergeticObject.telegram_chat_ids)
        """
        # Ключ для хранения последних известных ошибок/предупреждений
        faults_key = f"faults_{object_id}"
        warnings_key = f"warnings_{object_id}"
        
        # Инициализируем хранилище если его нет
        if not hasattr(self, '_deye_faults'):
            self._deye_faults = {}
        if not hasattr(self, '_deye_warnings'):
            self._deye_warnings = {}
        
        # Получаем последние известные состояния
        last_faults = self._deye_faults.get(faults_key, set())
        last_warnings = self._deye_warnings.get(warnings_key, set())
        
        # Преобразуем текущие в множество кодов для сравнения
        current_fault_codes = {f["name"] for f in faults}
        current_warning_codes = {w["name"] for w in warnings}

        fault_details_by_code = {f["name"]: f for f in faults}
        warning_details_by_code = {w["name"]: w for w in warnings}

        confirmed_new_fault_codes = set()
        confirmed_resolved_fault_codes = set()
        for code in (last_faults | current_fault_codes):
            state_key = f"fault:{object_id}:{code}"
            current_state = code in current_fault_codes
            if self._should_confirm_state_change(
                state_key=state_key,
                current_state=current_state,
                stable_states=self._deye_fault_states,
                pending_states=self._deye_fault_pending_states,
                debounce_seconds=DEYE_FAULTS_NOTIFICATION_CONFIRM_SECONDS,
            ):
                if current_state:
                    confirmed_new_fault_codes.add(code)
                else:
                    confirmed_resolved_fault_codes.add(code)

        confirmed_new_warning_codes = set()
        confirmed_resolved_warning_codes = set()
        for code in (last_warnings | current_warning_codes):
            state_key = f"warning:{object_id}:{code}"
            current_state = code in current_warning_codes
            if self._should_confirm_state_change(
                state_key=state_key,
                current_state=current_state,
                stable_states=self._deye_warning_states,
                pending_states=self._deye_warning_pending_states,
                debounce_seconds=DEYE_FAULTS_NOTIFICATION_CONFIRM_SECONDS,
            ):
                if current_state:
                    confirmed_new_warning_codes.add(code)
                else:
                    confirmed_resolved_warning_codes.add(code)
        
        # Подтвержденные изменения (после debounce)
        new_faults = [fault_details_by_code[code] for code in sorted(confirmed_new_fault_codes) if code in fault_details_by_code]
        new_warnings = [warning_details_by_code[code] for code in sorted(confirmed_new_warning_codes) if code in warning_details_by_code]
        resolved_faults = confirmed_resolved_fault_codes
        resolved_warnings = confirmed_resolved_warning_codes
        
        # Обновляем подтвержденные active-состояния (после debounce)
        confirmed_fault_codes = {
            state_key.split(":", 2)[2]
            for state_key, is_active in self._deye_fault_states.items()
            if state_key.startswith(f"fault:{object_id}:") and is_active
        }
        confirmed_warning_codes = {
            state_key.split(":", 2)[2]
            for state_key, is_active in self._deye_warning_states.items()
            if state_key.startswith(f"warning:{object_id}:") and is_active
        }
        self._deye_faults[faults_key] = confirmed_fault_codes
        self._deye_warnings[warnings_key] = confirmed_warning_codes
        
        # Формируем и отправляем сообщения о новых ошибках
        now_local = datetime.now(self.timezone)
        timestamp = now_local.strftime("%Y-%m-%d %H:%M:%S")
        
        # Новые ОШИБКИ (Faults)
        if new_faults:
            for fault in new_faults:
                message = (
                    f"🔴 <b>КРИТИЧЕСКАЯ ОШИБКА ИНВЕРТОРА</b>\n\n"
                    f"📍 Объект: <b>{object_name}</b>\n"
                    f"⚠️ Код: <b>{fault['name']}</b>\n"
                    f"📋 Описание: <i>{fault['description']}</i>\n\n"
                    f"🔧 <b>Рекомендуемые действия:</b>\n"
                    f"<i>{fault['solution']}</i>\n\n"
                    f"🕐 Время обнаружения: {timestamp}"
                )
                
                success = await self.send_message(message, chat_ids=chat_ids)
                if success:
                    logger.warning(
                        f"🔴 FAULT detected for {object_name}: {fault['name']} - {fault['description']}"
                    )
                else:
                    logger.error(f"Failed to send fault alert for {object_name}: {fault['name']}")
        
        # Новые ПРЕДУПРЕЖДЕНИЯ (Warnings)
        if new_warnings:
            for warning in new_warnings:
                message = (
                    f"⚠️ <b>ПРЕДУПРЕЖДЕНИЕ ИНВЕРТОРА</b>\n\n"
                    f"📍 Объект: <b>{object_name}</b>\n"
                    f"⚠️ Код: <b>{warning['name']}</b>\n"
                    f"📋 Описание: <i>{warning['description']}</i>\n\n"
                    f"🔧 <b>Рекомендуемые действия:</b>\n"
                    f"<i>{warning['solution']}</i>\n\n"
                    f"🕐 Время обнаружения: {timestamp}"
                )
                
                success = await self.send_message(message, chat_ids=chat_ids)
                if success:
                    logger.info(
                        f"⚠️ WARNING detected for {object_name}: {warning['name']} - {warning['description']}"
                    )
                else:
                    logger.error(f"Failed to send warning alert for {object_name}: {warning['name']}")
        
        # Решенные ОШИБКИ
        if SEND_DEYE_RESOLVED_NOTIFICATIONS and resolved_faults:
            faults_list = ", ".join(sorted(resolved_faults))
            message = (
                f"✅ <b>ОШИБКИ УСТРАНЕНЫ</b>\n\n"
                f"📍 Объект: <b>{object_name}</b>\n"
                f"✅ Коды: <b>{faults_list}</b>\n"
                f"🕐 Время: {timestamp}"
            )
            
            success = await self.send_message(message, chat_ids=chat_ids)
            if success:
                logger.info(f"✅ FAULTS resolved for {object_name}: {faults_list}")
        
        # Решенные ПРЕДУПРЕЖДЕНИЯ
        if SEND_DEYE_RESOLVED_NOTIFICATIONS and resolved_warnings:
            warnings_list = ", ".join(sorted(resolved_warnings))
            message = (
                f"✅ <b>ПРЕДУПРЕЖДЕНИЯ УСТРАНЕНЫ</b>\n\n"
                f"📍 Объект: <b>{object_name}</b>\n"
                f"✅ Коды: <b>{warnings_list}</b>\n"
                f"🕐 Время: {timestamp}"
            )
            
            success = await self.send_message(message, chat_ids=chat_ids)
            if success:
                logger.info(f"✅ WARNINGS resolved for {object_name}: {warnings_list}")
    
    async def send_test_message(self):
        """Отправляет тестовое сообщение для проверки работы бота"""
        # Текущее время в настроенном часовом поясе
        now_local = datetime.now(self.timezone)
        timestamp = now_local.strftime("%Y-%m-%d %H:%M:%S")
        
        test_message = (
            "✅ <b>Telegram Bot для мониторинга батарей активирован</b>\n\n"
            f"📊 Порог уведомлений: {self.alert_threshold}%\n"
            f"⏱️ Интервал cooldown: {self.cooldown_minutes} минут\n"
            f"🕐 Время: {timestamp}"
        )
        
        # success = await self.send_message(test_message)
        success = await self.send_message(test_message)
        if success:
            logger.info("✅ Test message sent successfully to Telegram")
        else:
            logger.error("❌ Failed to send test message to Telegram")
        
        return success


# Глобальный экземпляр монитора
telegram_monitor: Optional[TelegramBatteryMonitor] = None


# Хранилище последних состояний для отслеживания изменений
_last_schedule_states: Dict[str, Optional[str]] = {}  # object_id -> schedule_id
_last_power_loss_states: Dict[str, bool] = {}  # object_id -> has_power
_last_connection_states: Dict[str, bool] = {}  # object_id -> has_connection


async def send_schedule_change_notification(
    object_id: str,
    object_name: str,
    object_timezone: str,
    old_grid_feed_kw: Optional[float],
    old_battery_level_percent: Optional[int],
    old_charge_battery_value: Optional[int],
    new_grid_feed_kw: Optional[float],
    new_battery_level_percent: Optional[int],
    new_charge_battery_value: Optional[int],
    is_manual_mode: bool = False,
    active_schedule_start_time: Optional[datetime] = None,
    active_schedule_end_time: Optional[datetime] = None
):
    """
    Отправляет уведомление о смене расписания с отображением параметров
    
    Args:
        object_id: ID энергетического объекта
        object_name: Название объекта
        object_timezone: Часовой пояс объекта (например: 'Europe/Kyiv')
        old_grid_feed_kw: Предыдущая отдача в сеть (kW)
        old_battery_level_percent: Предыдущий порог разряда батареи (%)
        old_charge_battery_value: Предыдущее значение зарядки батареи (W)
        new_grid_feed_kw: Новая отдача в сеть (kW)
        new_battery_level_percent: Новый порог разряда батареи (%)
        new_charge_battery_value: Новое значение зарядки батареи (W)
        is_manual_mode: True если переключено в ручной режим
        active_schedule_start_time: Время начала расписания
        active_schedule_end_time: Время окончания расписания
    """
    try:
        monitor = get_telegram_monitor()
        # Используем timezone объекта, а не глобальный timezone бота
        tz = ZoneInfo(object_timezone)
        now_local = datetime.now(tz)
        timestamp = now_local.strftime("%Y-%m-%d %H:%M:%S")
        
        if is_manual_mode:
            icon = "🔧"
            title = "ПЕРЕКЛЮЧЕНИЕ В РУЧНОЙ РЕЖИМ"
            params_text = f"Объект переведен в <b>ручной режим управления</b>"
        elif new_grid_feed_kw is not None:
            icon = "📅"
            title = "АВТОМАТИЧЕСКАЯ СМЕНА РАСПИСАНИЯ"
            
            # Формируем текст с параметрами
            params_parts = ["\n📊 <b>Новые параметры:</b>"]
            params_parts.append(f"   ⚡ Отдача в сеть: <b>{new_grid_feed_kw:.2f} kW</b>")
            params_parts.append(f"   🔋 Порог разряда: <b>{new_battery_level_percent}%</b>")
            params_parts.append(f"   🔌 Ток заряда батареи: <b>{new_charge_battery_value} А</b>")
            

            if active_schedule_start_time:

                if hasattr(active_schedule_start_time, 'strftime'):
                    start_time_str = active_schedule_start_time.strftime("%H:%M")
                else:
                    start_time_str = str(active_schedule_start_time)
                params_parts.append(f"   🕒 Время начала: <b>{start_time_str}</b>")
            
            if active_schedule_end_time:
                if hasattr(active_schedule_end_time, 'strftime'):
                    end_time_str = active_schedule_end_time.strftime("%H:%M")
                else:
                    end_time_str = str(active_schedule_end_time)
                params_parts.append(f"   🕒 Время окончания: <b>{end_time_str}</b>")

            # Если были старые параметры, показываем изменение
            if old_grid_feed_kw is not None:
                params_parts.append("\n📋 <b>Предыдущие параметры:</b>")
                params_parts.append(f"   ⚡ Отдача в сеть: {old_grid_feed_kw:.2f} kW")
                params_parts.append(f"   🔋 Порог разряда: {old_battery_level_percent}%")
                params_parts.append(f"   🔌 Ток заряда батареи: {old_charge_battery_value} А")

            params_text = "\n".join(params_parts)
        else:
            icon = "⚙️"
            title = "СБРОС НА ДЕФОЛТНЫЕ ПАРАМЕТРЫ"
            params_text = "❌ Активное расписание завершено, используются стандартные параметры"
        
        message = (
            f"{icon} <b>{title}</b>\n\n"
            f"📍 Объект: <b>{object_name}</b>\n"
            f"🌍 Часовой пояс: {object_timezone}\n"
            f"{params_text}\n"
            f"🕐 Время: {timestamp}"
        )
        
        # await monitor.send_message(message)
        logger.info(f"📅 Schedule change notification sent for {object_name}")
        
    except Exception as e:
        logger.error(f"Error sending schedule change notification: {e}", exc_info=True)


async def send_power_loss_notification(
    object_id: str,
    object_name: str,
    is_power_lost: bool,
    voltage_l1: float = None,
    voltage_l2: float = None,
    voltage_l3: float = None,
    chat_ids: Optional[List[str]] = None,
):
    """
    Отправляет уведомление о потере или восстановлении электроэнергии
    
    Args:
        object_id: ID энергетического объекта
        object_name: Название объекта
        is_power_lost: True если энергия потеряна, False если восстановлена
        voltage_l1: Напряжение на фазе L1 (V)
        voltage_l2: Напряжение на фазе L2 (V)
        voltage_l3: Напряжение на фазе L3 (V)
    """
    try:
        # Проверяем изменение состояния
        last_state = _last_power_loss_states.get(object_id, False)
        if last_state == is_power_lost:
            # Состояние не изменилось, не отправляем уведомление
            return
        
        # Обновляем состояние
        _last_power_loss_states[object_id] = is_power_lost
        
        monitor = get_telegram_monitor()
        now_local = datetime.now(monitor.timezone)
        timestamp = now_local.strftime("%Y-%m-%d %H:%M:%S")
        
        if is_power_lost:
            icon = "⚠️"
            title = "ПОТЕРЯ ЭЛЕКТРОЭНЕРГИИ"
            
            # Определяем какие фазы пропали
            VOLTAGE_THRESHOLD = 100.0
            lost_phases = []
            if voltage_l1 is not None and voltage_l1 < VOLTAGE_THRESHOLD:
                lost_phases.append(f"L1: {voltage_l1:.1f}V")
            if voltage_l2 is not None and voltage_l2 < VOLTAGE_THRESHOLD:
                lost_phases.append(f"L2: {voltage_l2:.1f}V")
            if voltage_l3 is not None and voltage_l3 < VOLTAGE_THRESHOLD:
                lost_phases.append(f"L3: {voltage_l3:.1f}V")
            
            if lost_phases:
                status = f"❌ <b>Потеря фаз: {', '.join(lost_phases)}</b>"
            else:
                status = "❌ <b>Отсутствует входное напряжение</b>"
        else:
            icon = "✅"
            title = "ЭЛЕКТРОЭНЕРГИЯ ВОССТАНОВЛЕНА"
            
            # Показываем текущие напряжения
            if voltage_l1 is not None and voltage_l2 is not None and voltage_l3 is not None:
                status = (
                    f"✅ <b>Входное напряжение восстановлено</b>\n"
                    f"   📊 L1: {voltage_l1:.1f}V, L2: {voltage_l2:.1f}V, L3: {voltage_l3:.1f}V"
                )
            else:
                status = "✅ <b>Входное напряжение восстановлено</b>"
        
        message_parts = [
            f"{icon} <b>{title}</b>\n",
            f"📍 Объект: <b>{object_name}</b>",
            status,
            f"🕐 Время: {timestamp}"
        ]
        
        message = "\n".join(message_parts)
        
        await monitor.send_message(message, chat_ids=chat_ids)
        
        if is_power_lost:
            logger.warning(f"⚠️ Power loss notification sent for {object_name}")
        else:
            logger.info(f"✅ Power restored notification sent for {object_name}")
        
    except Exception as e:
        logger.error(f"Error sending power loss notification: {e}", exc_info=True)


async def send_connection_loss_notification(
    object_id: str,
    object_name: str,
    is_connection_lost: bool,
    consecutive_errors: int = 0,
    error_rate_percent: float = 0.0,
    chat_ids: Optional[List[str]] = None,
):
    """
    Отправляет уведомление о потере или восстановлении связи с устройством
    
    Args:
        object_id: ID энергетического объекта
        object_name: Название объекта
        is_connection_lost: True если связь потеряна, False если восстановлена
        consecutive_errors: Количество последовательных ошибок
        error_rate_percent: Процент ошибок (0-100)
    """
    try:
        # Проверяем изменение состояния
        last_state = _last_connection_states.get(object_id, False)
        if last_state == is_connection_lost:
            # Состояние не изменилось, не отправляем уведомление
            return
        
        # Обновляем состояние
        _last_connection_states[object_id] = is_connection_lost
        
        monitor = get_telegram_monitor()
        now_local = datetime.now(monitor.timezone)
        timestamp = now_local.strftime("%Y-%m-%d %H:%M:%S")
        
        if is_connection_lost:
            icon = "🔴"
            title = "ПОТЕРЯ СВЯЗИ С УСТРОЙСТВОМ"
            status_parts = ["❌ <b>Связь с устройством потеряна</b>"]
            
            if consecutive_errors > 0:
                status_parts.append(f"📊 Последовательных ошибок: <b>{consecutive_errors}</b>")
            
            if error_rate_percent > 0:
                status_parts.append(f"⚠️ Процент ошибок: <b>{error_rate_percent:.1f}%</b>")
            
            status_parts.append("\n💡 <i>Проверьте Modbus соединение и устройство Cerbo GX</i>")
            status = "\n".join(status_parts)
        else:
            icon = "🟢"
            title = "СВЯЗЬ С УСТРОЙСТВОМ ВОССТАНОВЛЕНА"
            status = "✅ <b>Связь с устройством восстановлена</b>\n📡 <i>Сбор данных возобновлен</i>"
        
        message_parts = [
            f"{icon} <b>{title}</b>\n",
            f"📍 Объект: <b>{object_name}</b>",
            status,
            f"🕐 Время: {timestamp}"
        ]
        
        message = "\n".join(message_parts)
        
        await monitor.send_message(message, chat_ids=chat_ids)
        
        if is_connection_lost:
            logger.error(
                f"🔴 Connection loss notification sent for {object_name} "
                f"(errors: {consecutive_errors}, rate: {error_rate_percent:.1f}%)"
            )
        else:
            logger.info(f"🟢 Connection restored notification sent for {object_name}")
        
    except Exception as e:
        logger.error(f"Error sending connection loss notification: {e}", exc_info=True)


def get_telegram_monitor() -> TelegramBatteryMonitor:
    """
    Получает глобальный экземпляр Telegram монитора (singleton)
    
    Returns:
        Экземпляр TelegramBatteryMonitor
    """
    global telegram_monitor
    
    if telegram_monitor is None:
        telegram_monitor = TelegramBatteryMonitor()
    
    return telegram_monitor


async def init_telegram_monitor() -> bool:
    """
    Инициализирует Telegram монитор и отправляет тестовое сообщение
    
    Returns:
        True если инициализация успешна
    """
    try:
        monitor = get_telegram_monitor()
        
        # Проверяем что токен настроен
        if not monitor.bot_token:
            logger.warning("⚠️ Telegram bot token not configured, skipping initialization")
            return False
        
        # Удаляем возможный webhook, чтобы long polling работал корректно
        try:
            async with aiohttp.ClientSession() as session:
                resp = await session.post(f"{monitor.api_url}/deleteWebhook", json={})
                logger.info(f"deleteWebhook status={resp.status}")
        except Exception as e:
            logger.warning(f"Failed to delete webhook: {e}")
        
        # Chat IDs теперь берутся из EnergeticObject.telegram_chat_ids для каждого объекта
        logger.info("✅ Telegram monitor initialized. Chat IDs will be loaded from EnergeticObject.telegram_chat_ids")
        
        # Пытаемся отправить тестовое сообщение (если есть дефолтные чаты для команд бота)
        # Игнорируем результат теста — инициализация считается успешной в любом случае
        try:
            await monitor.send_test_message()
        except Exception as e:
            pass
            # logger.debug(f"Test message failed (expected if no default chats): {e}")
        
        return True
    
    except Exception as e:
        logger.error(f"Failed to initialize Telegram monitor: {e}", exc_info=True)
        return False


# ==================== КОМАНДЫ БОТА ====================

# Хранилище данных объектов для команд
_objects_data: Dict[str, Dict] = {}
_objects_battery_cache: Dict[str, Dict] = {}
_objects_status_cache: Dict[str, Dict] = {}
_objects_power_cache: Dict[str, Dict] = {}

BATTERY_CACHE_REQUIRED_FIELDS = ("soc", "general_battery_power")
STATUS_CACHE_REQUIRED_FIELDS = (
    "soc",
    "general_battery_power",
    "solar_total_pv_power",
    "ess_total_input_power",
)
POWER_CACHE_REQUIRED_FIELDS = (
    "solar_total_pv_power",
    "inverter_total_ac_output",
    "ess_total_input_power",
    "general_battery_power",
)


def _is_complete_snapshot(data: Dict, required_fields: tuple[str, ...]) -> bool:
    return all(data.get(field) is not None for field in required_fields)


def update_object_data(object_id: str, data: dict):
    """
    Обновляет данные об объекте для использования в командах
    
    Args:
        object_id: ID объекта
        data: Данные объекта (battery_soc, power, voltage, etc.)
    """
    global _objects_data
    previous = _objects_data.get(object_id, {})
    merged = {**previous}
    for key, value in data.items():
        if value is not None:
            merged[key] = value
    merged['last_update'] = datetime.now()
    _objects_data[object_id] = merged

    if _is_complete_snapshot(merged, BATTERY_CACHE_REQUIRED_FIELDS):
        _objects_battery_cache[object_id] = {**merged}

    if _is_complete_snapshot(merged, STATUS_CACHE_REQUIRED_FIELDS):
        _objects_status_cache[object_id] = {**merged}

    if _is_complete_snapshot(merged, POWER_CACHE_REQUIRED_FIELDS):
        _objects_power_cache[object_id] = {**merged}
    # logger.debug(f"📊 Updated object data for {object_id}: soc={data.get('soc')}%, power={data.get('general_battery_power')}W")


async def _load_object(object_id: str) -> Optional[EnergeticObject]:
    from backend.database.db import async_session_maker
    from sqlalchemy import select

    async with async_session_maker() as db:
        result = await db.execute(
            select(EnergeticObject).where(EnergeticObject.id == object_id)
        )
        return result.scalar_one_or_none()


async def _write_generator_relay(
    obj: EnergeticObject,
    relay: int,
    state: bool
) -> Dict[str, Optional[str]]:
    """Записывает состояние реле генератора через Modbus (coil write) с retry."""
    if not obj or not obj.protocol or not obj.ip_address or not obj.port:
        return {"ok": False, "error": "Missing Modbus connection settings"}

    slave_id = GENERATOR_SLAVE_ID
    logger.info(f"🔧 Writing generator relay: slave_id={slave_id}, relay={relay}, state={state}, host={obj.ip_address}:{obj.port}")
    
    # Для modbus_over_tcp создаем временный клиент с увеличенным таймаутом (10 сек)
    if obj.protocol == "modbus_over_tcp":
        # Retry логика для обработки "Нет соединения" ошибок
        for attempt in range(1, GENERATOR_COMMAND_RETRY_ATTEMPTS + 1):
            # Создаем временный клиент с таймаутом 10 секунд
            client = ModbusTCP(host=obj.ip_address, port=obj.port, timeout=10)
            
            try:
                logger.info(f"📡 Attempt {attempt}/{GENERATOR_COMMAND_RETRY_ATTEMPTS}: Executing write_coils...")
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None,
                    lambda: client.write_coils(slave_id=slave_id, relay=relay, state=state)
                )
                logger.info(f"✅ write_coils result: {result}")
                
                if result.get("ok"):
                    return {"ok": True, "error": None}
                
                # Если ошибка "Нет соединения" и есть еще попытки - повторяем
                if "соединения" in result.get("error", "").lower() and attempt < GENERATOR_COMMAND_RETRY_ATTEMPTS:
                    logger.warning(f"⚠️ Connection error on attempt {attempt}, retrying...")
                    await asyncio.sleep(1)  # Небольшая пауза перед повтором
                    continue
                
                return {"ok": False, "error": result.get("error") or "write_coils failed"}
                
            except TimeoutError:
                logger.error(f"⏱️ Modbus write timeout after 10s: slave_id={slave_id}, relay={relay}")
                if attempt < GENERATOR_COMMAND_RETRY_ATTEMPTS:
                    await asyncio.sleep(1)
                    continue
                return {"ok": False, "error": "Modbus timeout - устройство не ответило за 10 секунд"}
            except Exception as e:
                logger.error(f"❌ Modbus write error on attempt {attempt}: {e}", exc_info=True)
                if attempt < GENERATOR_COMMAND_RETRY_ATTEMPTS:
                    await asyncio.sleep(1)
                    continue
                return {"ok": False, "error": f"Modbus error: {str(e)}"}
            finally:
                # Закрываем временное соединение
                client.close()
    
    # Для modbus_tcp используем pymodbus async
    client = await get_or_create_modbus_client(
        protocol=obj.protocol,
        ip_address=obj.ip_address,
        port=obj.port,
        object_id=str(obj.id),
        slave_id=slave_id,
    )
    
    if not client:
        return {"ok": False, "error": "Modbus client not available"}
    
    value = 0xFF00 if state else 0x0000
    resp = await client.write_coil(relay, value, slave=slave_id)
    if resp.isError():
        return {"ok": False, "error": str(resp)}

    return {"ok": True, "error": None}


async def _read_generator_inputs(
    obj: EnergeticObject,
    start: int = 0,
    count: int = 8
) -> Dict[str, Optional[object]]:
    """Читает дискретные входы (DI) генератора через Modbus."""
    if not obj or not obj.protocol or not obj.ip_address or not obj.port:
        return {"ok": False, "error": "Missing Modbus connection settings", "inputs": None}


    slave_id = GENERATOR_SLAVE_ID
    logger.info(
        "🔍 Reading generator inputs: "
        f"slave_id={slave_id}, start={start}, count={count}, host={obj.ip_address}:{obj.port}"
    )
    
    # Для modbus_over_tcp используем общий клиент, как в API read_discrete_inputs
    if obj.protocol == "modbus_over_tcp":
        client = await get_or_create_modbus_client(
            protocol=obj.protocol,
            ip_address=obj.ip_address,
            port=obj.port,
            object_id=str(obj.id),
            slave_id=slave_id,
        )
        if not client:
            return {"ok": False, "error": "Modbus client not available", "inputs": None}

        last_error = None
        # Retry логика для обработки "Нет соединения" ошибок
        for attempt in range(1, GENERATOR_COMMAND_RETRY_ATTEMPTS + 1):
            try:
                logger.info(
                    f"📡 Attempt {attempt}/{GENERATOR_COMMAND_RETRY_ATTEMPTS}: "
                    f"Reading inputs (slave_id={slave_id})..."
                )
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None,
                    lambda: client.read_inputs(slave_id=slave_id, start=start, count=count)
                )
                logger.info(f"✅ read_inputs result: {result}")
                
                if result.get("ok"):
                    return {"ok": True, "error": None, "inputs": result.get("data", [])}
                
                last_error = result.get("error") or "read_inputs failed"
                if "соединения" in last_error.lower() and attempt < GENERATOR_COMMAND_RETRY_ATTEMPTS:
                    logger.warning(f"⚠️ Connection error on attempt {attempt}, retrying...")
                    await asyncio.sleep(1)
                    continue
            except TimeoutError:
                last_error = "Modbus timeout - устройство не ответило за 10 секунд"
                logger.error(f"⏱️ Modbus read timeout after 10s: slave_id={slave_id}, start={start}")
                if attempt < GENERATOR_COMMAND_RETRY_ATTEMPTS:
                    await asyncio.sleep(1)
                    continue
            except Exception as e:
                last_error = f"Modbus error: {str(e)}"
                logger.error(f"❌ Modbus read error on attempt {attempt}: {e}", exc_info=True)
                if attempt < GENERATOR_COMMAND_RETRY_ATTEMPTS:
                    await asyncio.sleep(1)
                    continue

        return {"ok": False, "error": last_error or "read_inputs failed", "inputs": None}
    
    # Для modbus_tcp используем pymodbus async
    client = await get_or_create_modbus_client(
        protocol=obj.protocol,
        ip_address=obj.ip_address,
        port=obj.port,
        object_id=str(obj.id),
        slave_id=slave_id,
    )
    
    if not client:
        return {"ok": False, "error": "Modbus client not available", "inputs": None}

    resp = await client.read_discrete_inputs(start, count, slave=slave_id)
    if resp.isError():
        return {"ok": False, "error": str(resp), "inputs": None}

    return {"ok": True, "error": None, "inputs": resp.bits[:count]}


async def _wait_for_generator_feedback(
    monitor: TelegramBatteryMonitor,
    chat_id: str,
    obj: EnergeticObject,
    expect_running: bool,
    timeout_seconds: int = GENERATOR_COMMAND_TIMEOUT_SECONDS,
    poll_seconds: float = GENERATOR_COMMAND_POLL_SECONDS,
):
    start_time = datetime.now()
    manual_notified = False

    while (datetime.now() - start_time).total_seconds() < timeout_seconds:
        result = await _read_generator_inputs(obj)
        if not result.get("ok"):
            # Не прерываем ожидание при ошибке чтения - просто логируем и продолжаем
            logger.warning(f"⚠️ Failed to read generator inputs: {result.get('error')}, will retry in {poll_seconds}s")
            await asyncio.sleep(poll_seconds)
            continue

        inputs = result.get("inputs") or []
        input_running = bool(inputs[GENERATOR_INPUT_RUNNING]) if len(inputs) > GENERATOR_INPUT_RUNNING else False
        input_manual = bool(inputs[GENERATOR_INPUT_MANUAL_START]) if len(inputs) > GENERATOR_INPUT_MANUAL_START else False

        if input_manual and not manual_notified:
            await monitor.send_message(
                "⚠️ Обнаружен запуск генератора вручную (вход 1 активен).",
                chat_id
            )
            manual_notified = True

        if input_running == expect_running:
            status_text = "вышел на режим" if expect_running else "остановлен"
            await monitor.send_message(
                f"✅ Генератор {status_text}.",
                chat_id
            )
            return

        await asyncio.sleep(poll_seconds)

    await monitor.send_message(
        "⏱️ Нет подтверждения за отведенное время. Проверьте генератор вручную.",
        chat_id
    )


async def _handle_generator_command_for_object(
    monitor: TelegramBatteryMonitor,
    chat_id: str,
    object_id: str,
    start: bool
):
    obj = await _load_object(object_id)
    if not obj:
        await monitor.send_message("❌ Объект не найден", chat_id)
        return

    action_text = "запуск" if start else "остановка"
    await monitor.send_message(
        f"⚙️ Выполняю {action_text} генератора...",
        chat_id
    )

    try:
        write_result = await _write_generator_relay(obj, GENERATOR_RELAY_START, start)
        if not write_result.get("ok"):
            await monitor.send_message(
                f"❌ Не удалось выполнить команду: {write_result.get('error')}",
                chat_id
            )
            return

        await monitor.send_message(
            "✅ Команда отправлена. Ожидаю подтверждение...",
            chat_id
        )

        await _wait_for_generator_feedback(
            monitor=monitor,
            chat_id=chat_id,
            obj=obj,
            expect_running=start,
        )
    except Exception as e:
        logger.error(f"Error in _handle_generator_command_for_object: {e}", exc_info=True)
        await monitor.send_message(
            f"❌ Ошибка управления генератором: {str(e)}",
            chat_id
        )


async def _handle_generator_block_command_for_object(
    monitor: TelegramBatteryMonitor,
    chat_id: str,
    object_id: str,
    block: bool
):
    """Управление блокировкой генератора."""
    obj = await _load_object(object_id)
    if not obj:
        await monitor.send_message("❌ Объект не найден", chat_id)
        return

    action_text = "блокировку" if block else "разблокировку"
    await monitor.send_message(
        f"🔒 Выполняю {action_text} генератора...",
        chat_id
    )

    try:
        write_result = await _write_generator_relay(obj, GENERATOR_RELAY_BLOCK, block)
        if not write_result.get("ok"):
            await monitor.send_message(
                f"❌ Не удалось выполнить команду: {write_result.get('error')}",
                chat_id
            )
            return

        status_emoji = "🚫" if block else "✅"
        status_text = "заблокирован" if block else "разблокирован"
        await monitor.send_message(
            f"{status_emoji} Генератор {status_text}.",
            chat_id
        )
    except Exception as e:
        logger.error(f"Error in _handle_generator_block_command_for_object: {e}", exc_info=True)
        await monitor.send_message(
            f"❌ Ошибка управления блокировкой: {str(e)}",
            chat_id
        )


async def handle_telegram_command_for_object(
    command: str,
    chat_id: str,
    object_id: str,
    monitor: TelegramBatteryMonitor
):
    """
    Обрабатывает команды от пользователей для конкретного объекта
    (используется когда каждый объект имеет свой бот)
    
    Args:
        command: Текст команды (например: '/status')
        chat_id: ID чата откуда пришла команда
        object_id: ID энергетического объекта
        monitor: Экземпляр TelegramBatteryMonitor для этого объекта
    """
    try:
        # Разбираем команду
        parts = command.strip().split()
        cmd = parts[0].lower()
        
        # Убираем @botname если есть (для групповых чатов)
        if '@' in cmd:
            cmd = cmd.split('@')[0]
        
        if cmd == '/start' or cmd == '/help':
            await send_help_message_for_object(monitor, chat_id, object_id)
        
        elif cmd == '/status':
            await send_status_for_object(monitor, chat_id, object_id)
        
        elif cmd == '/battery':
            await send_battery_for_object(monitor, chat_id, object_id)
        
        elif cmd == '/power':
            await send_power_for_object(monitor, chat_id, object_id)

        elif cmd == '/grid':
            await send_grid_for_object(monitor, chat_id, object_id)
        
        elif cmd == '/generator':
            await send_generator_for_object(monitor, chat_id, object_id)

        elif cmd == '/faults':
            await send_faults_for_object(monitor, chat_id, object_id)

        elif cmd == '/gen_start':
            await _handle_generator_command_for_object(monitor, chat_id, object_id, start=True)

        elif cmd == '/gen_stop':
            await _handle_generator_command_for_object(monitor, chat_id, object_id, start=False)

        elif cmd == '/gen_block':
            await _handle_generator_block_command_for_object(monitor, chat_id, object_id, block=True)

        elif cmd == '/gen_unblock':
            await _handle_generator_block_command_for_object(monitor, chat_id, object_id, block=False)
        
        elif cmd == '/debug':
            await send_debug_for_object(monitor, chat_id, object_id)
        
        else:
            await monitor.send_message(
                f"❓ Неизвестная команда: {cmd}\nИспользуйте /help для списка команд",
                chat_id
            )
    
    except Exception as e:
        logger.error(f"Error handling command '{command}' for object {object_id}: {e}", exc_info=True)


async def handle_telegram_command(command: str, chat_id: str, message_id: int):
    """
    Обрабатывает команды от пользователей
    
    Args:
        command: Текст команды (например: '/status')
        chat_id: ID чата откуда пришла команда
        message_id: ID сообщения
    """
    try:
        monitor = get_telegram_monitor()
        
        # Разбираем команду
        parts = command.strip().split()
        cmd = parts[0].lower()
        
        # Убираем @botname если есть (для групповых чатов)
        if '@' in cmd:
            cmd = cmd.split('@')[0]
        
        if cmd == '/start' or cmd == '/help':
            await send_help_message(monitor, chat_id)
        
        elif cmd == '/status':
            await send_status_message(monitor, chat_id)
        
        elif cmd == '/battery':
            await send_battery_message(monitor, chat_id)
        
        elif cmd == '/power':
            await send_power_message(monitor, chat_id)
        
        elif cmd == '/schedule':
            object_id = parts[1] if len(parts) > 1 else None
            await send_schedule_message(monitor, chat_id, object_id)
        
        elif cmd == '/debug':
            # Отладочная команда для проверки данных
            await send_debug_message(monitor, chat_id)
        
        elif cmd == '/generator':
            await send_generator_message(monitor, chat_id)

        elif cmd == '/faults':
            await send_faults_message(monitor, chat_id)
        
        else:
            await monitor.send_message(
                f"❓ Неизвестная команда: {cmd}\nИспользуйте /help для списка команд",
                chat_id
            )
    
    except Exception as e:
        logger.error(f"Error handling command '{command}': {e}", exc_info=True)




# ==================== КОМАНДЫ ДЛЯ КОНКРЕТНОГО ОБЪЕКТА ====================

async def send_help_message_for_object(monitor: TelegramBatteryMonitor, chat_id: str, object_id: str):
    """Отправляет справку по командам для конкретного объекта"""
    # Получаем имя объекта
    from backend.database.db import async_session_maker
    from sqlalchemy import select
    
    try:
        async with async_session_maker() as db:
            result = await db.execute(
                select(EnergeticObject).where(EnergeticObject.id == object_id)
            )
            obj = result.scalar_one_or_none()
            object_name = obj.name if obj else f"Объект {object_id[:8]}"
    except:
        object_name = f"Объект {object_id[:8]}"
    
    help_text = f"""
🤖 <b>Команды бота объекта "{object_name}"</b>

<b>Основные команды:</b>
/status - Статус объекта
/battery - Информация о батарее
/power - Текущая мощность
/grid - Статус сети
/generator - Статус генератора
/faults - Актуальные ошибки инвертора

<b>Управление генератором:</b>
/gen_start - Запуск генератора
/gen_stop - Остановка генератора
/gen_block - Блокировка генератора
/gen_unblock - Разблокировка генератора

/debug - Отладочная информация
/help - Эта справка

<b>Автоматические уведомления:</b>
• ⚠️ Низкий заряд батареи
• ⚡ Запуск/остановка генератора
• ⚠️ Потеря/восстановление питания
• 📅 Смена расписания

<b>Мониторинг работает 24/7</b>
"""
    
    await monitor.send_message(help_text, chat_id)


async def send_status_for_object(monitor: TelegramBatteryMonitor, chat_id: str, object_id: str):
    """Отправляет статус конкретного объекта"""
    from backend.database.db import async_session_maker
    from sqlalchemy import select
    
    try:
        # Получаем объект из БД
        async with async_session_maker() as db:
            result = await db.execute(
                select(EnergeticObject).where(EnergeticObject.id == object_id)
            )
            obj = result.scalar_one_or_none()
            
            if not obj:
                await monitor.send_message("❌ Объект не найден", chat_id)
                return
        
        # Получаем только полный кэш для /status
        data = _objects_status_cache.get(object_id)
        
        if not data:
            await monitor.send_message(
                f"📊 Полные данные статуса объекта <b>{obj.name}</b> еще не готовы\n\n"
                f"Возможные причины:\n"
                f"• Инициализация опроса\n"
                f"• Временный таймаут одной из групп\n"
                f"• Нет связи с устройством",
                chat_id
            )
            return
        
        # Формируем сообщение
        now = datetime.now(monitor.timezone)
        now_naive = datetime.now()
        last_update = data.get('last_update', now_naive)
        age_seconds = (now_naive - last_update).total_seconds()
        
        # Статус подключения
        if age_seconds < 10:
            status_icon = "🟢"
            status_text = "Онлайн"
        elif age_seconds < 60:
            status_icon = "🟡"
            status_text = f"Обновлено {int(age_seconds)}с назад"
        else:
            status_icon = "🔴"
            status_text = f"Нет связи {int(age_seconds/60)}м"
        
        soc = _as_number(data.get('soc', 0))
        battery_power = _as_number(data.get('general_battery_power', 0)) / 1000  # W -> kW
        solar = _as_number(data.get('solar_total_pv_power', 0)) / 1000
        grid_in = _as_number(data.get('ess_total_input_power', 0)) / 1000
        
        # Иконка батареи
        if soc >= 80:
            battery_icon = "🔋"
        elif soc >= 50:
            battery_icon = "🔋"
        elif soc >= 20:
            battery_icon = "🪫"
        else:
            battery_icon = "⚠️"
        
        message = (
            f"📊 <b>СТАТУС: {obj.name}</b>\n\n"
            f"{status_icon} <b>Состояние:</b> {status_text}\n"
            f"{battery_icon} <b>Батарея:</b> {soc:.1f}% ({battery_power:+.2f} kW)\n"
            f"☀️ <b>Солнце:</b> {solar:.2f} kW\n"
            f"🔌 <b>Сеть:</b> {grid_in:.2f} kW\n\n"
            f"🕐 Обновлено: {now.strftime('%H:%M:%S')}"
        )
        
        await monitor.send_message(message, chat_id)
    
    except Exception as e:
        logger.error(f"Error in send_status_for_object: {e}", exc_info=True)
        await monitor.send_message("❌ Ошибка получения статуса", chat_id)


async def send_battery_for_object(monitor: TelegramBatteryMonitor, chat_id: str, object_id: str):
    """Отправляет информацию о батарее конкретного объекта"""
    from backend.database.db import async_session_maker
    from sqlalchemy import select
    
    try:
        async with async_session_maker() as db:
            result = await db.execute(
                select(EnergeticObject).where(EnergeticObject.id == object_id)
            )
            obj = result.scalar_one_or_none()
            
            if not obj:
                await monitor.send_message("❌ Объект не найден", chat_id)
                return
        
        data = _objects_battery_cache.get(object_id)
        
        if not data:
            await monitor.send_message(
                f"📊 Полные данные о батарее объекта <b>{obj.name}</b> еще не готовы\n\n"
                f"Причина: выполняется инициализация опроса или временный таймаут группы battery",
                chat_id
            )
            return
        
        soc = _as_number(data.get('soc', 0))
        battery_power = _as_number(data.get('general_battery_power', 0))
        
        # Направление потока
        if battery_power > 50:
            direction = "⚡ Заряд"
        elif battery_power < -50:
            direction = "🔌 Разряд"
        else:
            direction = "⏸️ Покой"
        
        message = (
            f"🔋 <b>БАТАРЕЯ: {obj.name}</b>\n\n"
            f"📊 Заряд: <b>{soc:.1f}%</b>\n"
            f"{direction}: {abs(battery_power/1000):.2f} kW\n"
            f"Сервер: cor-monitoring"
        )
        
        await monitor.send_message(message, chat_id)
    
    except Exception as e:
        logger.error(f"Error in send_battery_for_object: {e}", exc_info=True)
        await monitor.send_message("❌ Ошибка получения данных о батарее", chat_id)


async def send_power_for_object(monitor: TelegramBatteryMonitor, chat_id: str, object_id: str):
    """Отправляет информацию о мощности конкретного объекта"""
    from backend.database.db import async_session_maker
    from sqlalchemy import select
    
    try:
        async with async_session_maker() as db:
            result = await db.execute(
                select(EnergeticObject).where(EnergeticObject.id == object_id)
            )
            obj = result.scalar_one_or_none()
            
            if not obj:
                await monitor.send_message("❌ Объект не найден", chat_id)
                return
        
        data = _objects_power_cache.get(object_id)
        
        if not data:
            await monitor.send_message(
                f"📊 Полные данные мощности объекта <b>{obj.name}</b> еще не готовы\n\n"
                f"Причина: выполняется инициализация опроса или временный таймаут одной из групп",
                chat_id
            )
            return
        
        solar = _as_number(data.get('solar_total_pv_power', 0)) / 1000
        inverter_out = _as_number(data.get('inverter_total_ac_output', 0)) / 1000
        grid_in = _as_number(data.get('ess_total_input_power', 0)) / 1000
        battery = _as_number(data.get('general_battery_power', 0)) / 1000
        
        message = (
            f"⚡ <b>МОЩНОСТЬ: {obj.name}</b>\n\n"
            f"☀️ Солнечные панели: {solar:.2f} kW\n"
            f"🏠 Потребление: {inverter_out:.2f} kW\n"
            f"🔌 Сеть (вход): {grid_in:.2f} kW\n"
            f"🔋 Батарея: {battery:+.2f} kW"
        )
        
        await monitor.send_message(message, chat_id)
    
    except Exception as e:
        logger.error(f"Error in send_power_for_object: {e}", exc_info=True)
        await monitor.send_message("❌ Ошибка получения данных о мощности", chat_id)


async def send_grid_for_object(monitor: TelegramBatteryMonitor, chat_id: str, object_id: str):
    """Отправляет статус сети конкретного объекта (актуально для Deye и Cerbo)."""
    from backend.database.db import async_session_maker
    from sqlalchemy import select

    try:
        async with async_session_maker() as db:
            result = await db.execute(
                select(EnergeticObject).where(EnergeticObject.id == object_id)
            )
            obj = result.scalar_one_or_none()

            if not obj:
                await monitor.send_message("❌ Объект не найден", chat_id)
                return

        data = _objects_data.get(object_id)

        if not data:
            await monitor.send_message(f"📊 Нет данных о сети объекта <b>{obj.name}</b>", chat_id)
            return

        voltage_l1 = data.get('phase_voltage_a')
        voltage_l2 = data.get('phase_voltage_b')
        voltage_l3 = data.get('phase_voltage_c')

        grid_power_w = data.get('grid_total_power')
        if grid_power_w is None:
            ess_input_w = data.get('ess_total_input_power')
            if ess_input_w is not None:
                grid_power_w = ess_input_w

        has_power = any(
            value is not None and value >= 100.0
            for value in [voltage_l1, voltage_l2, voltage_l3]
        )

        status_text = "✅ Сеть присутствует" if has_power else "❌ Сеть отсутствует"

        phase_lines = []
        if voltage_l1 is not None:
            phase_lines.append(f"L1: {voltage_l1:.1f} V")
        if voltage_l2 is not None:
            phase_lines.append(f"L2: {voltage_l2:.1f} V")
        if voltage_l3 is not None:
            phase_lines.append(f"L3: {voltage_l3:.1f} V")

        power_line = "—"
        if isinstance(grid_power_w, (int, float)):
            power_line = f"{grid_power_w / 1000:.2f} kW"

        message = (
            f"🔌 <b>СЕТЬ: {obj.name}</b>\n\n"
            f"📍 Статус: <b>{status_text}</b>\n"
            f"⚡ Мощность сети: {power_line}\n"
            f"📊 Фазы: {', '.join(phase_lines) if phase_lines else 'нет данных'}"
        )

        await monitor.send_message(message, chat_id)

    except Exception as e:
        logger.error(f"Error in send_grid_for_object: {e}", exc_info=True)
        await monitor.send_message("❌ Ошибка получения данных о сети", chat_id)


def _format_generator_status_details(
    gen_relay: int,
    input_running: Optional[bool],
    input_manual: Optional[bool]
) -> Dict[str, str]:
    resolved = _resolve_generator_status(gen_relay, input_running, input_manual)
    start_reason = resolved["start_reason"]
    status_icon = str(resolved["status_icon"])
    status_text = str(resolved["status_text"])

    details = []
    if start_reason:
        details.append(f"   🧭 Способ: <b>{start_reason}</b>")

    input_parts = []
    if input_running is not None:
        input_parts.append(f"DI0={'1' if input_running else '0'}")
    if input_manual is not None:
        input_parts.append(f"DI1={'1' if input_manual else '0'}")
    if input_parts:
        details.append(f"   🧩 Входы: {', '.join(input_parts)}")

    return {
        "status_icon": status_icon,
        "status_text": status_text,
        "details": "\n".join(details),
    }


async def send_generator_for_object(monitor: TelegramBatteryMonitor, chat_id: str, object_id: str):
    """Отправляет информацию о генераторе конкретного объекта"""
    from backend.database.db import async_session_maker
    from sqlalchemy import select
    from backend.repository.energy.modbus_broker import get_broker, RequestPriority
    
    try:
        async with async_session_maker() as db:
            result = await db.execute(
                select(EnergeticObject).where(EnergeticObject.id == object_id)
            )
            obj = result.scalar_one_or_none()
            
            if not obj or obj.modbus_config_file != 'deye_inverter.json':
                await monitor.send_message(
                    "ℹ️ У этого объекта нет подключенного генератора",
                    chat_id
                )
                return
        
        # Читаем статус генератора
        broker = get_broker()
        slave_id = obj.slave_id if hasattr(obj, 'slave_id') and obj.slave_id else 1
        
        # Читаем регистр 552 (gen_relay)
        gen_relay_result = await broker.submit_request(
            protocol=obj.protocol,
            host=obj.ip_address,
            port=obj.port,
            operation="read",
            params={"start": 552, "count": 1, "func_code": 3},
            slave_id=slave_id,
            object_id=str(obj.id),
            priority=RequestPriority.USER_READ,
            timeout=5.0,
            request_id=f"telegram_gen_check_{obj.id}",
        )
        
        raw_data = gen_relay_result.get("data", [])
        if not raw_data:
            await monitor.send_message(f"❌ Нет данных от устройства <b>{obj.name}</b>", chat_id)
            return
        
        register_value = raw_data[0]
        gen_relay = (register_value >> 3) & 1
        input_running = None
        input_manual = None
        inputs_result = await _read_generator_inputs(obj)
        if inputs_result.get("ok"):
            inputs = inputs_result.get("inputs") or []
            input_running = bool(inputs[GENERATOR_INPUT_RUNNING]) if len(inputs) > GENERATOR_INPUT_RUNNING else None
            input_manual = bool(inputs[GENERATOR_INPUT_MANUAL_START]) if len(inputs) > GENERATOR_INPUT_MANUAL_START else None
        else:
            logger.warning(
                f"Could not read generator inputs for {obj.name}: {inputs_result.get('error')}"
            )

        status_info = _format_generator_status_details(gen_relay, input_running, input_manual)
        status_icon = status_info["status_icon"]
        status_text = status_info["status_text"]
        status_details = status_info["details"]
        
        now = datetime.now(monitor.timezone)
        timestamp = now.strftime("%Y-%m-%d %H:%M:%S")

        details_block = f"\n{status_details}" if status_details else ""
        
        message = (
            f"⚡ <b>ГЕНЕРАТОР: {obj.name}</b>\n\n"
            f"{status_icon} Статус: {status_text}{details_block}\n"
            f"🕐 Время: {timestamp}"
        )
        
        await monitor.send_message(message, chat_id)
    
    except Exception as e:
        logger.error(f"Error in send_generator_for_object: {e}", exc_info=True)
        await monitor.send_message("❌ Ошибка получения статуса генератора", chat_id)


def _format_deye_issues_list(issues: List[dict], empty_text: str) -> str:
    if not issues:
        return empty_text

    lines = []
    for issue in issues[:10]:
        code = issue.get("name") or issue.get("code") or "N/A"
        description = issue.get("description_ru") or issue.get("description") or issue.get("name") or ""
        solution = issue.get("solution_ru") or issue.get("solution") or ""
        lines.append(f"• <b>{code}</b> — {description}")
        if solution:
            lines.append(f"  Решение: {solution}")

    remaining = len(issues) - 10
    if remaining > 0:
        lines.append(f"• ... и еще {remaining}")

    return "\n".join(lines)


async def send_faults_for_object(monitor: TelegramBatteryMonitor, chat_id: str, object_id: str):
    """Отправляет актуальные ошибки/предупреждения инвертора для конкретного объекта."""
    from backend.database.db import async_session_maker
    from sqlalchemy import select

    try:
        async with async_session_maker() as db:
            obj_result = await db.execute(
                select(EnergeticObject).where(EnergeticObject.id == object_id)
            )
            obj = obj_result.scalar_one_or_none()

            if not obj:
                await monitor.send_message("❌ Объект не найден", chat_id)
                return

            event_result = await db.execute(
                select(DeyeAlarmEvent)
                .where(DeyeAlarmEvent.energetic_object_id == object_id)
                .order_by(DeyeAlarmEvent.measured_at.desc(), DeyeAlarmEvent.created_at.desc())
                .limit(1)
            )
            latest_event = event_result.scalar_one_or_none()

        if not latest_event:
            await monitor.send_message(
                f"ℹ️ <b>ОШИБКИ ИНВЕРТОРА: {obj.name}</b>\n\n"
                "Нет данных по ошибкам/предупреждениям (опрос еще не собрал snapshot).",
                chat_id
            )
            return

        faults = latest_event.faults or []
        warnings = latest_event.warnings or []
        measured_at = latest_event.measured_at.astimezone(monitor.timezone).strftime("%Y-%m-%d %H:%M:%S")

        if not faults and not warnings:
            message = (
                f"✅ <b>ОШИБКИ ИНВЕРТОРА: {obj.name}</b>\n\n"
                "Активных ошибок и предупреждений нет.\n"
                f"🕐 Актуальность: {measured_at}"
            )
            await monitor.send_message(message, chat_id)
            return

        faults_block = _format_deye_issues_list(faults, "Нет активных ошибок")
        warnings_block = _format_deye_issues_list(warnings, "Нет активных предупреждений")

        message = (
            f"🚨 <b>ОШИБКИ ИНВЕРТОРА: {obj.name}</b>\n\n"
            f"<b>🔴 Ошибки:</b>\n{faults_block}\n\n"
            f"<b>⚠️ Предупреждения:</b>\n{warnings_block}\n\n"
            f"🕐 Актуальность: {measured_at}"
        )
        await monitor.send_message(message, chat_id)
    except Exception as e:
        logger.error(f"Error in send_faults_for_object: {e}", exc_info=True)
        await monitor.send_message("❌ Ошибка получения ошибок инвертора", chat_id)


async def send_debug_for_object(monitor: TelegramBatteryMonitor, chat_id: str, object_id: str):
    """Отправляет отладочную информацию для объекта"""
    global _objects_data
    
    try:
        from backend.database.db import async_session_maker
        from sqlalchemy import select
        
        async with async_session_maker() as db:
            result = await db.execute(
                select(EnergeticObject).where(EnergeticObject.id == object_id)
            )
            obj = result.scalar_one_or_none()
            object_name = obj.name if obj else f"Объект {object_id[:8]}"
        
        data = _objects_data.get(object_id)
        
        message_parts = [f"🐛 <b>DEBUG: {object_name}</b>\n"]
        
        if data:
            last_update = data.get('last_update', 'Never')
            if isinstance(last_update, datetime):
                age = (datetime.now() - last_update).total_seconds()
                last_update_str = f"{age:.1f}s ago"
            else:
                last_update_str = str(last_update)
            
            message_parts.append(f"📊 <b>Data available:</b> Yes")
            message_parts.append(f"🕐 Last update: {last_update_str}")
            message_parts.append(f"🔋 SoC: {data.get('soc', 'N/A')}%")
            message_parts.append(f"⚡ Power: {data.get('general_battery_power', 'N/A')} W")
        else:
            message_parts.append("⚠️ <b>No data available</b>")
            message_parts.append("\nCheck if polling task is running")
        
        await monitor.send_message("\n".join(message_parts), chat_id)
    
    except Exception as e:
        logger.error(f"Error in send_debug_for_object: {e}", exc_info=True)
        await monitor.send_message("❌ Ошибка получения отладочной информации", chat_id)


# ==================== ГЛОБАЛЬНЫЕ КОМАНДЫ (ДЛЯ ВСЕХ ОБЪЕКТОВ) ====================

async def send_help_message(monitor: TelegramBatteryMonitor, chat_id: str):
    """Отправляет справку по командам"""
    help_text = """
🤖 <b>Команды бота мониторинга энергосистемы</b>

<b>Основные команды:</b>
/status - Общий статус всех объектов
/battery - Информация о батареях
/power - Текущая мощность (ввод/вывод)
/generator - Статус генератора
/faults - Актуальные ошибки инвертора
/schedule [object_id] - Активное расписание
/debug - Отладочная информация
/help - Эта справка

<b>Автоматические уведомления:</b>
• ⚠️ Низкий заряд батареи (< {}%)
• 📅 Смена расписания
• ⚡ Потеря/восстановление питания

<b>Мониторинг работает 24/7</b>
Данные обновляются каждые 2 секунды
""".format(monitor.alert_threshold)
    
    await monitor.send_message(help_text, chat_id)


async def send_status_message(monitor: TelegramBatteryMonitor, chat_id: str):
    """Отправляет общий статус всех объектов"""
    # logger.info(f"📞 send_status_message called, chat_id={chat_id}, _objects_data has {len(_objects_data)} objects")
    # logger.debug(f"Objects data keys: {list(_objects_data.keys())}")
    
    if not _objects_data:
        await monitor.send_message(
            "📊 Нет данных об объектах.\n\n<b>Возможные причины:</b>\n• Фоновая задача сбора данных не запущена\n• Данные еще не собраны (подождите 2-3 секунды)\n• Проверьте, создана ли задача CERBO_COLLECTION в БД\n\nИспользуйте /help для справки",
            chat_id
        )
        return
    
    # Используем часовой пояс монитора для отображения времени
    now = datetime.now(monitor.timezone)
    now_naive = datetime.now()  # Для расчета age_seconds
    message_parts = ["📊 <b>ОБЩИЙ СТАТУС ОБЪЕКТОВ</b>\n"]
    
    for object_id, data in _objects_data.items():
        object_name = data.get('object_name', f'Объект {object_id}')

        status_data = _objects_status_cache.get(object_id)
        if not status_data:
            message_parts.append(
                f"\n⚪ <b>{object_name}</b>\n"
                f"   📊 Полные данные статуса еще не готовы"
            )
            continue

        data = status_data

        last_update = data.get('last_update', now_naive)
        age_seconds = (now_naive - last_update).total_seconds()
        
        # Статус подключения
        if age_seconds < 10:
            status_icon = "🟢"
            status_text = "Онлайн"
        elif age_seconds < 60:
            status_icon = "🟡"
            status_text = f"Обновлено {int(age_seconds)}с назад"
        else:
            status_icon = "🔴"
            status_text = f"Нет связи {int(age_seconds/60)}м"
        
        soc = _as_number(data.get('soc', 0))
        battery_power = _as_number(data.get('general_battery_power', 0)) / 1000  # W -> kW
        
        # Иконка батареи
        if soc >= 80:
            battery_icon = "🔋"
        elif soc >= 50:
            battery_icon = "🔋"
        elif soc >= 20:
            battery_icon = "🪫"
        else:
            battery_icon = "⚠️"
        
        message_parts.append(
            f"\n{status_icon} <b>{object_name}</b>\n"
            f"   {battery_icon} Батарея: {soc:.1f}% ({battery_power:+.2f} kW)\n"
            f"   📡 Статус: {status_text}"
        )
    
    message_parts.append(f"\n🕐 Обновлено: {now.strftime('%H:%M:%S')}")
    
    await monitor.send_message("\n".join(message_parts), chat_id)


async def send_battery_message(monitor: TelegramBatteryMonitor, chat_id: str):
    """Отправляет детальную информацию о батареях"""
    # logger.info(f"📞 send_battery_message called, chat_id={chat_id}, _objects_data has {len(_objects_data)} objects")
    
    if not _objects_data:
        await monitor.send_message("📊 Нет данных о батареях.\n\nВозможные причины:\n• Задача сбора данных не запущена\n• Данные еще не собраны (подождите 2-3 секунды)\n\nПроверьте статус через /status", chat_id)
        return
    
    message_parts = ["🔋 <b>СОСТОЯНИЕ БАТАРЕЙ</b>\n"]
    
    for object_id, data in _objects_data.items():
        object_name = data.get('object_name', f'Объект {object_id}')

        battery_data = _objects_battery_cache.get(object_id)
        if not battery_data:
            message_parts.append(
                f"\n<b>{object_name}</b>\n"
                f"   📊 Полные данные батареи еще не готовы"
            )
            continue

        data = battery_data

        soc = _as_number(data.get('soc', 0))
        battery_power = _as_number(data.get('general_battery_power', 0))
        # battery_voltage = data.get('battery_voltage')
        
        # Направление потока
        if battery_power > 50:
            direction = "⚡ Заряд"
        elif battery_power < -50:
            direction = "🔌 Разряд"
        else:
            direction = "⏸️ Покой"
        
        message_parts.append(
            f"\n<b>{object_name}</b>\n"
            f"   📊 Заряд: <b>{soc:.1f}%</b>\n"
            f"   {direction}: {abs(battery_power/1000):.2f} kW"
        )
        
        # if battery_voltage:
        #     message_parts.append(f"   🔌 Напряжение: {battery_voltage:.1f} V")
    
    await monitor.send_message("\n".join(message_parts), chat_id)


async def send_power_message(monitor: TelegramBatteryMonitor, chat_id: str):
    """Отправляет информацию о мощности"""
    if not _objects_data:
        await monitor.send_message("📊 Нет данных о мощности", chat_id)
        return
    
    message_parts = ["⚡ <b>МОЩНОСТЬ СИСТЕМЫ</b>\n"]
    
    for object_id, data in _objects_data.items():
        object_name = data.get('object_name', f'Объект {object_id}')

        power_data = _objects_power_cache.get(object_id)
        if not power_data:
            message_parts.append(
                f"\n<b>{object_name}</b>\n"
                f"   📊 Полные данные мощности еще не готовы"
            )
            continue

        data = power_data
        
        solar = _as_number(data.get('solar_total_pv_power', 0)) / 1000
        inverter_out = _as_number(data.get('inverter_total_ac_output', 0)) / 1000
        grid_in = _as_number(data.get('ess_total_input_power', 0)) / 1000
        battery = _as_number(data.get('general_battery_power', 0)) / 1000
        
        message_parts.append(
            f"\n<b>{object_name}</b>\n"
            f"   ☀️ Солнечные панели: {solar:.2f} kW\n"
            f"   🏠 Потребление: {inverter_out:.2f} kW\n"
            f"   🔌 Сеть (вход): {grid_in:.2f} kW\n"
            f"   🔋 Батарея: {battery:+.2f} kW"
        )
    
    await monitor.send_message("\n".join(message_parts), chat_id)


async def send_schedule_message(monitor: TelegramBatteryMonitor, chat_id: str, object_id: Optional[str] = None):
    """Отправляет информацию об активном расписании"""
    # TODO: Нужен доступ к базе данных для получения расписаний
    # Пока отправляем заглушку
    message = """
📅 <b>АКТИВНЫЕ РАСПИСАНИЯ</b>

В процессе разработки...

Используйте /status для общей информации
"""
    await monitor.send_message(message, chat_id)


async def send_debug_message(monitor: TelegramBatteryMonitor, chat_id: str):
    """Отправляет отладочную информацию"""
    global _objects_data
    
    message_parts = ["🐛 <b>DEBUG INFO</b>\n"]
    message_parts.append(f"📊 Objects in memory: {len(_objects_data)}")
    
    if _objects_data:
        message_parts.append("\n<b>Object IDs:</b>")
        for obj_id, data in _objects_data.items():
            last_update = data.get('last_update', 'Never')
            if isinstance(last_update, datetime):
                age = (datetime.now() - last_update).total_seconds()
                last_update = f"{age:.1f}s ago"
            
            message_parts.append(
                f"\n• {obj_id[:8]}..."
                f"\n  Name: {data.get('object_name', 'N/A')}"
                f"\n  SoC: {data.get('soc', 'N/A')}%"
                f"\n  Updated: {last_update}"
            )
    else:
        message_parts.append("\n⚠️ No objects data available")
        message_parts.append("\nCheck if CERBO_COLLECTION task is running")
    
    await monitor.send_message("\n".join(message_parts), chat_id)


async def send_generator_message(monitor: TelegramBatteryMonitor, chat_id: str):
    """
    Отправляет текущий статус генератора для объектов с Deye инвертором.
    Читает регистр 552 (gen_relay) напрямую из устройства.
    """
    from sqlalchemy import select
    from backend.database.db import async_session_maker
    from backend.database.models import EnergeticObject
    
    try:
        # Получаем все объекты, к которым привязан этот чат
        async with async_session_maker() as session:
            result = await session.execute(
                select(EnergeticObject).where(
                    EnergeticObject.is_active == True
                )
            )
            all_objects = result.scalars().all()
            
            # Фильтруем по chat_id (telegram_chat_ids это строка через запятую)
            energetic_objects = [
                obj for obj in all_objects
                if obj.telegram_chat_ids and chat_id in [
                    cid.strip() for cid in str(obj.telegram_chat_ids).split(',')
                ]
            ]
        
        if not energetic_objects:
            await monitor.send_message(
                "❌ <b>Доступ запрещен</b>\n\n"
                "Этот чат не привязан ни к одному энергообъекту.\n"
                "Обратитесь к администратору.",
                chat_id
            )
            return
        
        # Фильтруем объекты с генератором (Deye инвертор)
        deye_objects = [
            obj for obj in energetic_objects 
            if obj.modbus_config_file == 'deye_inverter.json'
        ]
        
        if not deye_objects:
            await monitor.send_message(
                "ℹ️ <b>Генератор не найден</b>\n\n"
                "Ни один из доступных объектов не имеет подключенного генератора.",
                chat_id
            )
            return
        
        # Получаем broker для чтения регистров
        broker = get_broker()
        
        # Формируем сообщение
        now_local = datetime.now(monitor.timezone)
        timestamp = now_local.strftime("%Y-%m-%d %H:%M:%S")
        
        message_parts = [
            "⚡ <b>СТАТУС ГЕНЕРАТОРА</b>\n",
            f"🕐 Время запроса: {timestamp}\n"
        ]
        
        # Опрашиваем каждый объект с генератором
        for obj in deye_objects:
            try:
                # Определяем slave_id (может быть в объекте или в конфигурации)
                slave_id = obj.slave_id if hasattr(obj, 'slave_id') and obj.slave_id else 1
                
                # Читаем регистр 552 (gen_relay) и регистры батарей (182-183)
                gen_relay_result = await broker.submit_request(
                    protocol=obj.protocol,
                    host=obj.ip_address,
                    port=obj.port,
                    operation="read",
                    params={"start": 552, "count": 1, "func_code": 3},
                    slave_id=slave_id,
                    object_id=str(obj.id),
                    priority=RequestPriority.USER_READ,
                    timeout=5.0,
                    request_id=f"telegram_gen_check_{obj.id}",
                )
                
                raw_data = gen_relay_result.get("data", [])
                if not raw_data:
                    message_parts.append(
                        f"\n📍 <b>{obj.name}</b>\n"
                        f"   ❌ Нет данных от устройства"
                    )
                    continue
                
                # Получаем полное значение регистра и извлекаем третий бит (индекс 3)
                register_value = raw_data[0]
                # logger.debug(f"/generator: raw 552={register_value}, bit3={(register_value >> 3) & 1}")
                gen_relay = (register_value >> 3) & 1
                input_running = None
                input_manual = None
                inputs_result = await _read_generator_inputs(obj)
                if inputs_result.get("ok"):
                    inputs = inputs_result.get("inputs") or []
                    input_running = bool(inputs[GENERATOR_INPUT_RUNNING]) if len(inputs) > GENERATOR_INPUT_RUNNING else None
                    input_manual = bool(inputs[GENERATOR_INPUT_MANUAL_START]) if len(inputs) > GENERATOR_INPUT_MANUAL_START else None
                else:
                    logger.warning(
                        f"Could not read generator inputs for {obj.name}: {inputs_result.get('error')}"
                    )
                
                # Читаем напряжение батарей 
                battery1_voltage = None
                battery2_voltage = None
                try:
                    battery_result = await broker.submit_request(
                        protocol=obj.protocol,
                        host=obj.ip_address,
                        port=obj.port,
                        operation="read",
                        params={"start": 586, "count": 11, "func_code": 3},
                        slave_id=slave_id,
                        object_id=str(obj.id),
                        priority=RequestPriority.USER_READ,
                        timeout=5.0,
                        request_id=f"telegram_battery_check_{obj.id}",
                    )
                    
                    battery_data = battery_result.get("data", [])
                    if len(battery_data) >= 8:
                        battery1_voltage = battery_data[1] * 0.1  # offset 1, scale 0.1
                        battery2_voltage = battery_data[7] * 0.1  # offset 7, scale 0.1
                        # logger.debug(f"/generator: battery1={battery1_voltage}V, battery2={battery2_voltage}V")
                except Exception as e:
                    logger.warning(f"Could not read battery voltages: {e}")
                
                status_info = _format_generator_status_details(gen_relay, input_running, input_manual)
                status_icon = status_info["status_icon"]
                status_text = status_info["status_text"]
                status_details = status_info["details"]
                
                # Формируем информацию о батареях
                battery_info = ""
                battery_parts = []
                if battery1_voltage is not None:
                    battery_parts.append(f"Батарея 1: {battery1_voltage:.1f}V")
                if battery2_voltage is not None:
                    battery_parts.append(f"Батарея 2: {battery2_voltage:.1f}V")
                
                if battery_parts:
                    battery_info = "\n   🔋 <b>Напряжение батарей:</b>\n      " + "\n      ".join(battery_parts)
                
                # logger.debug(f"/generator: object={obj.name}, is_on={is_on}, gen_relay_bit={gen_relay}")
                details_block = f"\n{status_details}" if status_details else ""
                message_parts.append(
                    f"\n📍 <b>{obj.name}</b>\n"
                    f"   {status_icon} Статус: {status_text}{details_block}{battery_info}\n"
                )
                
            except Exception as e:
                logger.error(f"Error reading generator status for object {obj.id}: {e}", exc_info=True)
                message_parts.append(
                    f"\n📍 <b>{obj.name}</b>\n"
                    f"   ⚠️ Ошибка связи: {str(e)[:50]}"
                )
        
        await monitor.send_message("\n".join(message_parts), chat_id)
        
    except Exception as e:
        logger.error(f"Error in send_generator_message: {e}", exc_info=True)
        await monitor.send_message(
            "❌ <b>Ошибка выполнения команды</b>\n\n"
            f"Произошла внутренняя ошибка при проверке статуса генератора.\n"
            f"Попробуйте позже или обратитесь к администратору.",
            chat_id
        )


async def send_faults_message(monitor: TelegramBatteryMonitor, chat_id: str):
    """Отправляет актуальные ошибки/предупреждения инвертора по всем доступным объектам чата."""
    from sqlalchemy import select
    from backend.database.db import async_session_maker

    try:
        async with async_session_maker() as session:
            result = await session.execute(
                select(EnergeticObject).where(EnergeticObject.is_active == True)
            )
            all_objects = result.scalars().all()

            energetic_objects = [
                obj for obj in all_objects
                if obj.telegram_chat_ids and chat_id in [
                    cid.strip() for cid in str(obj.telegram_chat_ids).split(',')
                ]
            ]

            if not energetic_objects:
                await monitor.send_message(
                    "❌ <b>Доступ запрещен</b>\n\n"
                    "Этот чат не привязан ни к одному энергообъекту.",
                    chat_id
                )
                return

            message_parts = ["🚨 <b>АКТУАЛЬНЫЕ ОШИБКИ ИНВЕРТОРОВ</b>\n"]

            for obj in energetic_objects:
                event_result = await session.execute(
                    select(DeyeAlarmEvent)
                    .where(DeyeAlarmEvent.energetic_object_id == str(obj.id))
                    .order_by(DeyeAlarmEvent.measured_at.desc(), DeyeAlarmEvent.created_at.desc())
                    .limit(1)
                )
                latest_event = event_result.scalar_one_or_none()

                if not latest_event:
                    message_parts.append(
                        f"\n<b>{obj.name}</b>\n"
                        "ℹ️ Нет данных по ошибкам"
                    )
                    continue

                faults = latest_event.faults or []
                warnings = latest_event.warnings or []
                measured_at = latest_event.measured_at.astimezone(monitor.timezone).strftime("%H:%M:%S")

                if not faults and not warnings:
                    message_parts.append(
                        f"\n<b>{obj.name}</b>\n"
                        f"✅ Активных ошибок нет (срез {measured_at})"
                    )
                    continue

                faults_block = _format_deye_issues_list(faults[:3], "Нет активных ошибок")
                warnings_block = _format_deye_issues_list(warnings[:3], "Нет активных предупреждений")

                message_parts.append(
                    f"\n<b>{obj.name}</b>\n"
                    f"🔴 Ошибки:\n{faults_block}\n"
                    f"⚠️ Предупреждения:\n{warnings_block}\n"
                    f"🕐 {measured_at}"
                )

        await monitor.send_message("\n".join(message_parts), chat_id)
    except Exception as e:
        logger.error(f"Error in send_faults_message: {e}", exc_info=True)
        await monitor.send_message("❌ Ошибка получения ошибок инвертора", chat_id)


# Polling loop для обработки команд
_last_update_id = 0
_commands_task: Optional[asyncio.Task] = None


async def load_all_chat_ids_from_db() -> List[str]:
    """
    Загружает все chat_ids из всех активных энергетических объектов
    
    Returns:
        Список уникальных chat_ids
    """
    try:
        from backend.database.db import async_session_maker
        from sqlalchemy import select
        
        all_chat_ids = []
        
        async with async_session_maker() as db:
            result = await db.execute(
                select(EnergeticObject).where(EnergeticObject.is_active == True)
            )
            objects = result.scalars().all()
            
            for obj in objects:
                if obj.telegram_chat_ids:
                    chat_ids = [cid.strip() for cid in str(obj.telegram_chat_ids).split(',') if cid.strip()]
                    all_chat_ids.extend(chat_ids)
        
        # Убираем дубликаты
        unique_chat_ids = list(set(all_chat_ids))
        logger.info(f"Loaded {len(unique_chat_ids)} unique chat IDs from {len(objects)} energetic objects")
        return unique_chat_ids
        
    except Exception as e:
        logger.error(f"Error loading chat IDs from DB: {e}", exc_info=True)
        return []


async def start_telegram_commands_handler():
    """
    Запускает обработчик команд (long polling)
    Работает в фоновом режиме
    """
    global _last_update_id, _commands_task
    
    monitor = get_telegram_monitor()
    logger.info("🤖 Starting Telegram commands handler...")
    
    # Загружаем chat_ids из БД при старте
    allowed_chat_ids = await load_all_chat_ids_from_db()
    logger.info(f"Telegram commands handler loaded {len(allowed_chat_ids)} allowed chat IDs")
    if not allowed_chat_ids:
        logger.warning("No allowed chat IDs loaded; commands will be ignored until list is refreshed")
    
    # Периодически обновляем список разрешенных чатов
    last_reload = datetime.now()
    RELOAD_INTERVAL_MINUTES = 10
    
    while True:
        try:
            # Перезагружаем список чатов каждые 10 минут
            if (datetime.now() - last_reload).total_seconds() > RELOAD_INTERVAL_MINUTES * 60:
                allowed_chat_ids = await load_all_chat_ids_from_db()
                last_reload = datetime.now()
            
            async with aiohttp.ClientSession() as session:
                url = f"{monitor.api_url}/getUpdates"
                params = {
                    'offset': _last_update_id + 1,
                    'timeout': 30,  # Long polling
                    'allowed_updates': ['message']
                }
                
                try:
                    # logger.debug(
                    #     f"Telegram polling getUpdates offset={_last_update_id + 1}, allowed_chat_ids={len(allowed_chat_ids)}"
                    # )
                    async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=40)) as response:
                        if response.status != 200:
                            logger.warning(f"Telegram API error: {response.status}")
                            await asyncio.sleep(2)
                            continue
                        
                        data = await response.json()
                        
                        if not data.get('ok'):
                            logger.error(f"Telegram API returned error: {data}")
                            await asyncio.sleep(2)
                            continue
                        
                        updates = data.get('result', [])
                        # logger.debug(f"Telegram polling got {len(updates)} updates")
                        
                        for update in updates:
                            _last_update_id = update['update_id']
                            
                            message = update.get('message')
                            if not message:
                                continue
                            
                            text = message.get('text', '')
                            if not text.startswith('/'):
                                continue  # Игнорируем не-команды
                            
                            chat_id = str(message['chat']['id'])
                            message_id = message['message_id']
                            
                            # Проверяем что это один из чатов наших объектов
                            if chat_id not in allowed_chat_ids:
                                sample = allowed_chat_ids[:5]
                                logger.warning(
                                    f"Ignoring command from unknown chat {chat_id}; "
                                    f"allowed_chat_ids_count={len(allowed_chat_ids)}, sample={sample}"
                                )
                                continue
                            
                            logger.info(f"📥 Received command: {text} from chat {chat_id}")
                            # Обрабатываем команду в отдельной задаче чтобы не блокировать polling
                            asyncio.create_task(handle_telegram_command(text, chat_id, message_id))
                    
                except asyncio.TimeoutError:
                    # logger.debug("Telegram long polling timeout, retrying...")
                    continue
                except aiohttp.ClientError as e:
                    logger.warning(f"Network error in Telegram polling: {e}")
                    await asyncio.sleep(3)
                    continue
        
        except asyncio.CancelledError:
            logger.info("Telegram commands handler cancelled")
            break
        except Exception as e:
            logger.error(f"Error in Telegram commands handler: {e}", exc_info=True)
            await asyncio.sleep(5)


def start_commands_handler_task():
    """Запускает фоновую задачу обработки команд"""
    global _commands_task
    
    logger.info("🤖 start_commands_handler_task() called")
    
    if _commands_task is None or _commands_task.done():
        logger.info("🤖 Creating new commands handler task...")
        _commands_task = asyncio.create_task(start_telegram_commands_handler())
        logger.info(f"✅ Telegram commands handler task created: {_commands_task}")
    else:
        logger.info(f"ℹ️ Commands handler task already running: {_commands_task}")
    
    return _commands_task
