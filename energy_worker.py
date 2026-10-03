"""
Energy Modbus Worker
Фоновый процесс для опроса энергетических устройств через Modbus
"""
import asyncio
from loguru import logger

from backend.services.energy.polling_manager import PollingManager
from backend.config.config import settings
from backend.services.shared.logger import setup_logging
from backend.database.redis_db import check_and_clear_reload_signal

setup_logging()

CHECK_INTERVAL = 5
polling_manager = PollingManager()
_telegram_started = False


async def ensure_telegram_started():
    """Запускает менеджер Telegram ботов (один бот на каждый объект)"""
    global _telegram_started
    if _telegram_started:
        logger.info("Telegram manager already started, skipping")
        return
    
    logger.info("🤖 ensure_telegram_started() called - starting Telegram manager...")
    
    try:
        # Проверяем наличие хотя бы одного токена (глобального или в объектах)
        global_token = getattr(settings, 'telegram_bot_token', None)
        
        from backend.services.telegram.telegram_manager import telegram_manager
        
        # Загружаем мониторы из БД (будет использовать токены из объектов или fallback на global_token)
        await telegram_manager.load_monitors_from_db()
        
        if len(telegram_manager.monitors) > 0:
            _telegram_started = True
            logger.info(
                f"✅ Telegram manager started successfully with {len(telegram_manager.monitors)} bot(s)"
            )
        else:
            logger.warning(
                "⚠️ No Telegram bots configured. "
                "Add telegram_bot_token to EnergeticObject or set global TELEGRAM_BOT_TOKEN"
            )
    
    except Exception as e:
        logger.error(f"💥 Failed to start Telegram manager: {e}", exc_info=True)


async def main_worker_entrypoint():
    """
    Главный цикл воркера
    Периодически синхронизирует задачи опроса из БД
    """
    try:
        last_reload_token = None
        # Стартуем Telegram сервисы (если настроен токен)
        await ensure_telegram_started()

        logger.info(f"🚀 Energy Worker started. Polling interval: {CHECK_INTERVAL}s")
        
        while True:
            try:
                # Перезагружаем задачи из БД
                await polling_manager.reload_tasks_from_db()
                
            except Exception as e:
                logger.error(f"Error in main loop: {e}", exc_info=True)

            # Ждём CHECK_INTERVAL секунд, но прерываемся если пришёл сигнал перезагрузки
            for _ in range(CHECK_INTERVAL):
                await asyncio.sleep(1)
                try:
                    reload_requested, last_reload_token = await check_and_clear_reload_signal(last_reload_token)
                    if reload_requested:
                        logger.info("⚡ Reload signal received via Redis, reloading tasks immediately")
                        break
                except Exception:
                    pass  # не прерываем цикл если Redis недоступен
    finally:
        # Закрываем все Modbus клиенты при остановке
        from backend.services.energy.modbus_client import close_all_modbus_clients
        logger.info("🛑 Shutting down worker, closing all Modbus clients...")
        await close_all_modbus_clients()


if __name__ == "__main__":
    try:
        asyncio.run(main_worker_entrypoint())
    except KeyboardInterrupt:
        logger.info("Energy Worker stopped by user.")
    except Exception as e:
        logger.error(f"Energy Worker crashed: {e}", exc_info=True)
