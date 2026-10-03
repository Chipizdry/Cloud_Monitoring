# from redis import Redis
from redis.asyncio import Redis
from backend.config.config import settings


redis_client = Redis(
    host=settings.redis_host,
    port=settings.redis_port,
    db=settings.redis_db,
    decode_responses=True,
)

_WORKER_RELOAD_KEY = "polling_worker:reload_requested"
_BROADCAST_RELOAD_KEY = "broadcast_worker:reload_requested"


async def _signal_reload(key: str) -> None:
    await redis_client.incr(key)


async def _check_reload_signal_changed(key: str, last_seen_token: str | None) -> tuple[bool, str | None]:
    result = await redis_client.get(key)
    if result is None:
        return False, last_seen_token
    if result != last_seen_token:
        return True, result
    return False, last_seen_token


async def signal_worker_reload() -> None:
    """Выставляет флаг в Redis, чтобы воркер перезагрузил задачи из БД немедленно."""
    await _signal_reload(_WORKER_RELOAD_KEY)


async def check_and_clear_reload_signal(last_seen_token: str | None = None) -> tuple[bool, str | None]:
    """Проверяет изменение версии сигнала перезагрузки и возвращает новый токен."""
    return await _check_reload_signal_changed(_WORKER_RELOAD_KEY, last_seen_token)


async def signal_broadcast_reload() -> None:
    """Выставляет флаг в Redis, чтобы WebSocket воркер перезагрузил broadcast-задачи."""
    await _signal_reload(_BROADCAST_RELOAD_KEY)


async def check_and_clear_broadcast_reload_signal(last_seen_token: str | None = None) -> tuple[bool, str | None]:
    """Проверяет изменение версии сигнала перезагрузки broadcast-задач и возвращает новый токен."""
    return await _check_reload_signal_changed(_BROADCAST_RELOAD_KEY, last_seen_token)
