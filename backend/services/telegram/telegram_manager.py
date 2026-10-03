"""
Telegram Monitor Manager
Управляет несколькими Telegram ботами (по одному на энергетический объект)
"""
import asyncio
import aiohttp
import os
import socket
from typing import Any, Dict, Optional, List, Set
from datetime import datetime
from loguru import logger
from sqlalchemy import select

from backend.database.db import async_session_maker
from backend.database.models.energy import EnergeticObject
from backend.services.telegram.telegram_bot import TelegramBatteryMonitor
from backend.config.config import settings


class TelegramMonitorManager:
    """
    Управляет несколькими Telegram мониторами
    Каждый энергетический объект может иметь свой собственный Telegram бот
    """
    
    def __init__(self):
        # Мониторы по object_id
        self.monitors: Dict[str, TelegramBatteryMonitor] = {}
        
        # Polling задачи для каждого бота
        self.polling_tasks: Dict[str, asyncio.Task] = {}
        
        # Маппинг chat_id -> object_id для быстрого поиска
        self.chat_to_object: Dict[str, str] = {}
        
        # Последние update_id для каждого бота
        self.last_update_ids: Dict[str, int] = {}

        # Боты, для которых уже залогировали конфликт 409 (чтобы не спамить)
        self._token_conflict_warned: Set[str] = set()

        # Очереди команд Telegram по объектам (serial execution)
        self.command_queues: Dict[str, asyncio.Queue] = {}
        self.command_workers: Dict[str, asyncio.Task] = {}
        self.command_queue_max_size: int = 500
        self.command_exec_timeout_sec: int = 120

        # Идентификатор текущего инстанса для диагностики конфликтов polling
        self.instance_id = f"{socket.gethostname()}:{os.getpid()}"

        # Диагностические счётчики Telegram polling/commands
        self.token_stats: Dict[str, Dict[str, Any]] = {}
        self.ignored_reasons_total: Dict[str, int] = {
            "no_message": 0,
            "not_command": 0,
            "unknown_chat": 0,
            "queue_overflow": 0,
            "command_timeout": 0,
        }
        self.recent_events: List[Dict[str, Any]] = []
        self.max_recent_events: int = 100
        
        # Глобальный токен (fallback)
        self.global_token = getattr(settings, 'telegram_bot_token', None)

    @staticmethod
    def _token_suffix(bot_token: str) -> str:
        return bot_token[-6:] if bot_token else "unknown"

    def _ensure_token_stats(self, bot_token: str) -> Dict[str, Any]:
        token_suffix = self._token_suffix(bot_token)
        if token_suffix not in self.token_stats:
            self.token_stats[token_suffix] = {
                "updates_received": 0,
                "commands_received": 0,
                "ignored_no_message": 0,
                "ignored_not_command": 0,
                "ignored_unknown_chat": 0,
                "queue_overflow": 0,
                "command_timeout": 0,
                "last_poll_ok_at": None,
                "last_poll_error_at": None,
                "last_poll_error": None,
            }
        return self.token_stats[token_suffix]

    def _mark_poll_ok(self, bot_token: str):
        stats = self._ensure_token_stats(bot_token)
        stats["last_poll_ok_at"] = datetime.now().isoformat()
        stats["last_poll_error"] = None

    def _mark_poll_error(self, bot_token: str, error: str):
        stats = self._ensure_token_stats(bot_token)
        stats["last_poll_error_at"] = datetime.now().isoformat()
        stats["last_poll_error"] = error

    def _push_event(self, event: Dict[str, Any]):
        payload = {
            "at": datetime.now().isoformat(),
            **event,
        }
        self.recent_events.append(payload)
        if len(self.recent_events) > self.max_recent_events:
            self.recent_events = self.recent_events[-self.max_recent_events:]

    def get_diagnostics(self) -> Dict[str, Any]:
        """Возвращает диагностику Telegram polling/command pipeline."""
        queue_sizes = {
            object_id: queue.qsize()
            for object_id, queue in self.command_queues.items()
        }

        workers = {
            object_id: {
                "exists": worker is not None,
                "done": worker.done() if worker else True,
                "cancelled": worker.cancelled() if worker else False,
            }
            for object_id, worker in self.command_workers.items()
        }

        return {
            "instance_id": self.instance_id,
            "started_at": datetime.now().isoformat(),
            "monitors_count": len(self.monitors),
            "polling_tokens_count": len(self.polling_tasks),
            "known_chats_count": len(self.chat_to_object),
            "last_update_ids": {
                self._token_suffix(token): update_id
                for token, update_id in self.last_update_ids.items()
            },
            "ignored_reasons_total": self.ignored_reasons_total.copy(),
            "token_stats": {k: v.copy() for k, v in self.token_stats.items()},
            "command_queue_max_size": self.command_queue_max_size,
            "command_exec_timeout_sec": self.command_exec_timeout_sec,
            "command_queue_sizes": queue_sizes,
            "command_workers": workers,
            "recent_events": self.recent_events[-30:],
        }
    
    async def load_monitors_from_db(self):
        """
        Загружает объекты с токенами из БД и создаёт мониторы
        Также поддерживает fallback на глобальный токен
        """
        try:
            async with async_session_maker() as db:
                result = await db.execute(
                    select(EnergeticObject).where(
                        EnergeticObject.is_active == True
                    )
                )
                objects = result.scalars().all()
                
                logger.info(f"Loading Telegram monitors for {len(objects)} active objects")
                token_monitors: Dict[str, TelegramBatteryMonitor] = {}
                
                for obj in objects:
                    # Определяем токен: приоритет объектному, fallback на глобальный
                    bot_token = obj.telegram_bot_token or self.global_token
                    
                    if not bot_token:
                        # logger.debug(f"Object {obj.name} has no bot token, skipping")
                        continue
                    
                    # Парсим chat_ids
                    chat_ids = []
                    if obj.telegram_chat_ids:
                        chat_ids = [cid.strip() for cid in str(obj.telegram_chat_ids).split(',') if cid.strip()]
                    
                    if not chat_ids:
                        # logger.debug(f"Object {obj.name} has no chat IDs, skipping")
                        continue
                    
                    # Создаём монитор для объекта
                    if obj.id not in self.monitors:
                        monitor = TelegramBatteryMonitor(
                            bot_token=bot_token,
                            chat_ids=chat_ids,
                            alert_threshold=obj.telegram_battery_threshold or 70,
                            cooldown_minutes=obj.telegram_cooldown_minutes or 60,
                            timezone=obj.timezone or 'Europe/Kyiv'
                        )
                        self.monitors[obj.id] = monitor
                        
                        # Маппинг chat_id -> object_id
                        for chat_id in chat_ids:
                            self.chat_to_object[chat_id] = obj.id

                        # Один monitor-источник на токен для getUpdates
                        if bot_token not in token_monitors:
                            token_monitors[bot_token] = monitor
                        
                        logger.info(
                            f"✅ Started Telegram monitor for object '{obj.name}' "
                            f"(chats: {len(chat_ids)}, threshold: {obj.telegram_battery_threshold or 70}%)"
                        )

                # Запускаем ровно один polling loop на каждый bot token
                for bot_token, monitor in token_monitors.items():
                    if bot_token not in self.polling_tasks:
                        task = asyncio.create_task(self._start_polling(bot_token, monitor))
                        self.polling_tasks[bot_token] = task
                        token_suffix = bot_token[-6:] if bot_token else "unknown"
                        logger.info(
                            f"🤖 Started Telegram polling loop for token *{token_suffix} "
                            f"on instance {self.instance_id}"
                        )
                
                logger.info(f"✅ Total Telegram monitors started: {len(self.monitors)}")
        
        except Exception as e:
            logger.error(f"Error loading Telegram monitors: {e}", exc_info=True)
    
    async def _start_polling(self, bot_token: str, monitor: TelegramBatteryMonitor):
        """
        Long polling для конкретного бота
        
        Args:
            bot_token: Telegram bot token
            monitor: Экземпляр TelegramBatteryMonitor
        """
        last_update_id = self.last_update_ids.get(bot_token, 0)
        token_suffix = bot_token[-6:] if bot_token else "unknown"
        logger.info(
            f"🤖 Starting Telegram polling loop for token *{token_suffix} "
            f"on instance {self.instance_id}"
        )
        
        while True:
            try:
                # Получаем обновления от Telegram API
                updates = await self._get_updates(monitor, offset=last_update_id + 1)
                token_stats = self._ensure_token_stats(bot_token)
                self._mark_poll_ok(bot_token)
                token_stats["updates_received"] += len(updates)
                
                for update in updates:
                    last_update_id = update['update_id']

                    message = update.get('message') or update.get('edited_message')
                    if not message:
                        self._push_event({
                            "type": "update_ignored",
                            "token": self._token_suffix(bot_token),
                            "update_id": last_update_id,
                            "reason": "no_message",
                        })
                        token_stats["ignored_no_message"] += 1
                        self.ignored_reasons_total["no_message"] += 1
                        continue
                    
                    text = message.get('text', '')
                    if not text.startswith('/'):
                        self._push_event({
                            "type": "update_ignored",
                            "token": self._token_suffix(bot_token),
                            "update_id": last_update_id,
                            "chat_id": str(message.get("chat", {}).get("id", "")),
                            "text": text,
                            "reason": "not_command",
                        })
                        token_stats["ignored_not_command"] += 1
                        self.ignored_reasons_total["not_command"] += 1
                        continue  # Игнорируем не-команды

                    token_stats["commands_received"] += 1
                    
                    chat_id = str(message['chat']['id'])
                    
                    # Определяем объект по chat_id
                    object_id = self.chat_to_object.get(chat_id)
                    if not object_id:
                        object_id = await self._resolve_object_by_chat_id(chat_id)
                    if not object_id:
                        self._push_event({
                            "type": "update_ignored",
                            "token": self._token_suffix(bot_token),
                            "update_id": last_update_id,
                            "chat_id": chat_id,
                            "text": text,
                            "reason": "unknown_chat",
                        })
                        token_stats["ignored_unknown_chat"] += 1
                        self.ignored_reasons_total["unknown_chat"] += 1
                        logger.warning(
                            f"Ignoring command from unknown chat {chat_id}"
                        )
                        continue

                    logger.info(f"📥 Received command '{text}' for object '{object_id}' from chat {chat_id}")
                    self._push_event({
                        "type": "command_received",
                        "token": self._token_suffix(bot_token),
                        "update_id": last_update_id,
                        "chat_id": chat_id,
                        "object_id": object_id,
                        "command": text,
                    })

                    target_monitor = self.monitors.get(object_id, monitor)
                    
                    # Кладем команду в очередь объекта (гарантированно serial)
                    await self._enqueue_command(
                        command=text,
                        chat_id=chat_id,
                        object_id=object_id,
                        monitor=target_monitor
                    )

                self.last_update_ids[bot_token] = last_update_id
            
            except asyncio.CancelledError:
                logger.info("Telegram polling cancelled")
                break
            except Exception as e:
                self._mark_poll_error(bot_token, str(e))
                self._push_event({
                    "type": "poll_error",
                    "token": self._token_suffix(bot_token),
                    "error": str(e),
                })
                logger.error(f"Error in Telegram polling loop: {e}", exc_info=True)
                await asyncio.sleep(5)
    
    async def _get_updates(self, monitor: TelegramBatteryMonitor, offset: int, timeout: int = 30) -> List[dict]:
        """
        Получает обновления от Telegram API
        
        Args:
            monitor: Монитор с токеном
            offset: Offset для getUpdates
            timeout: Timeout для long polling
            
        Returns:
            Список обновлений
        """
        try:
            url = f"{monitor.api_url}/getUpdates"
            params = {
                'offset': offset,
                'timeout': timeout,
                'allowed_updates': ['message']
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=timeout + 10)) as response:
                    if response.status != 200:
                        if response.status == 409:
                            token_suffix = monitor.bot_token[-6:] if monitor.bot_token else "unknown"
                            if token_suffix not in self._token_conflict_warned:
                                logger.warning(
                                    f"Telegram API conflict 409 for token *{token_suffix}. "
                                    f"Another getUpdates poller is active. "
                                    f"Current instance: {self.instance_id}"
                                )
                                self._token_conflict_warned.add(token_suffix)
                            await asyncio.sleep(2)
                            return []

                        logger.warning(f"Telegram API error: {response.status}")
                        return []

                    token_suffix = monitor.bot_token[-6:] if monitor.bot_token else "unknown"
                    self._token_conflict_warned.discard(token_suffix)
                    
                    data = await response.json()
                    
                    if not data.get('ok'):
                        logger.error(f"Telegram API returned error: {data}")
                        return []
                    
                    return data.get('result', [])
        
        except asyncio.TimeoutError:
            # Это нормально для long polling
            return []
        except Exception as e:
            logger.warning(f"Error getting updates: {e}")
            return []
    
    async def _handle_command(
        self,
        command: str,
        chat_id: str,
        object_id: str,
        monitor: TelegramBatteryMonitor
    ):
        """
        Обрабатывает команду от пользователя для конкретного объекта
        
        Args:
            command: Текст команды
            chat_id: Chat ID
            object_id: ID энергетического объекта
            monitor: Монитор для отправки ответов
        """
        from backend.services.telegram.telegram_bot import handle_telegram_command_for_object
        
        try:
            await handle_telegram_command_for_object(
                command=command,
                chat_id=chat_id,
                object_id=object_id,
                monitor=monitor
            )
        except Exception as e:
            logger.error(f"Error handling command '{command}': {e}", exc_info=True)
            await monitor.send_message(
                "❌ Ошибка обработки команды. Попробуйте позже.",
                chat_id=chat_id
            )

    async def _ensure_command_worker(self, object_id: str):
        """Гарантирует наличие воркера обработки команд для объекта."""
        worker = self.command_workers.get(object_id)
        if worker and not worker.done():
            return

        if object_id not in self.command_queues:
            self.command_queues[object_id] = asyncio.Queue(maxsize=self.command_queue_max_size)

        async def _worker():
            queue = self.command_queues[object_id]
            while True:
                try:
                    item: Dict[str, Any] = await queue.get()
                    try:
                        await asyncio.wait_for(
                            self._handle_command(
                                command=item["command"],
                                chat_id=item["chat_id"],
                                object_id=item["object_id"],
                                monitor=item["monitor"],
                            ),
                            timeout=self.command_exec_timeout_sec,
                        )
                        self._push_event({
                            "type": "command_completed",
                            "token": self._token_suffix(item.get("monitor").bot_token),
                            "chat_id": item.get("chat_id"),
                            "object_id": item.get("object_id"),
                            "command": item.get("command"),
                        })
                    except asyncio.TimeoutError:
                        token_stats = self._ensure_token_stats(item.get("monitor").bot_token)
                        token_stats["command_timeout"] += 1
                        self.ignored_reasons_total["command_timeout"] += 1
                        self._push_event({
                            "type": "command_timeout",
                            "token": self._token_suffix(item.get("monitor").bot_token),
                            "chat_id": item.get("chat_id"),
                            "object_id": item.get("object_id"),
                            "command": item.get("command"),
                        })
                        logger.warning(
                            f"Telegram command timeout for object {object_id}: {item.get('command')}"
                        )
                        await item["monitor"].send_message(
                            "⏱️ Команда выполняется слишком долго. Попробуйте снова.",
                            chat_id=item["chat_id"],
                        )
                    finally:
                        queue.task_done()
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"Telegram command worker error for object {object_id}: {e}", exc_info=True)

        self.command_workers[object_id] = asyncio.create_task(_worker())

    async def _enqueue_command(
        self,
        command: str,
        chat_id: str,
        object_id: str,
        monitor: TelegramBatteryMonitor,
    ):
        """Ставит команду в очередь обработки для конкретного объекта."""
        await self._ensure_command_worker(object_id)
        queue = self.command_queues[object_id]

        item = {
            "command": command,
            "chat_id": chat_id,
            "object_id": object_id,
            "monitor": monitor,
        }

        if queue.full():
            logger.warning(
                f"Telegram command queue near overflow for object {object_id}, waiting for free slot"
            )
            try:
                await asyncio.wait_for(queue.put(item), timeout=3)
                self._push_event({
                    "type": "command_enqueued",
                    "token": self._token_suffix(monitor.bot_token),
                    "chat_id": chat_id,
                    "object_id": object_id,
                    "command": command,
                    "queue_size": queue.qsize(),
                })
                return
            except asyncio.TimeoutError:
                token_stats = self._ensure_token_stats(monitor.bot_token)
                token_stats["queue_overflow"] += 1
                self.ignored_reasons_total["queue_overflow"] += 1
                self._push_event({
                    "type": "command_queue_overflow",
                    "token": self._token_suffix(monitor.bot_token),
                    "chat_id": chat_id,
                    "object_id": object_id,
                    "command": command,
                })
                logger.warning(f"Telegram command queue overflow for object {object_id}")
                await monitor.send_message(
                    "⚠️ Очередь команд перегружена. Повторите команду через пару секунд.",
                    chat_id=chat_id,
                )
                return

        await queue.put(item)
        self._push_event({
            "type": "command_enqueued",
            "token": self._token_suffix(monitor.bot_token),
            "chat_id": chat_id,
            "object_id": object_id,
            "command": command,
            "queue_size": queue.qsize(),
        })

    async def _resolve_object_by_chat_id(self, chat_id: str) -> Optional[str]:
        """Ищет object_id для chat_id в БД, если локальный кэш chat_to_object не содержит запись."""
        try:
            async with async_session_maker() as db:
                result = await db.execute(
                    select(EnergeticObject).where(EnergeticObject.is_active == True)
                )
                objects = result.scalars().all()

                for obj in objects:
                    raw_chat_ids = getattr(obj, "telegram_chat_ids", None)
                    if not raw_chat_ids:
                        continue

                    parsed = [cid.strip() for cid in str(raw_chat_ids).split(',') if cid.strip()]
                    if chat_id in parsed:
                        self.chat_to_object[chat_id] = obj.id
                        return obj.id
        except Exception as e:
            logger.error(f"Error resolving object by chat_id {chat_id}: {e}", exc_info=True)

        return None
    
    def get_monitor(self, object_id: str) -> Optional[TelegramBatteryMonitor]:
        """
        Получить монитор для объекта
        
        Args:
            object_id: ID энергетического объекта
            
        Returns:
            TelegramBatteryMonitor или None
        """
        return self.monitors.get(object_id)
    
    async def reload_monitors(self):
        """
        Перезагружает мониторы из БД (для горячей перезагрузки конфигурации)
        """
        # Останавливаем старые polling задачи
        for task in self.polling_tasks.values():
            task.cancel()

        for task in self.command_workers.values():
            task.cancel()
        
        # Очищаем состояние
        self.monitors.clear()
        self.polling_tasks.clear()
        self.chat_to_object.clear()
        self.last_update_ids.clear()
        self._token_conflict_warned.clear()
        self.command_workers.clear()
        self.command_queues.clear()
        
        # Загружаем заново
        await self.load_monitors_from_db()


# Глобальный экземпляр менеджера
telegram_manager = TelegramMonitorManager()
