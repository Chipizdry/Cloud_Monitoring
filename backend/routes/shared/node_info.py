"""
Public endpoint для отображения информации о текущей ноде
Используется на главной странице для определения на каком сервере работает приложение
"""

from typing import Optional
from fastapi import APIRouter, Request
from pydantic import BaseModel
from loguru import logger
import aiohttp
from backend.services.shared.device_info import get_client_ip as shared_get_client_ip

router = APIRouter(prefix="/node-info", tags=["Node Info"])


class NodeInfoResponse(BaseModel):
    """Информация о текущей ноде/сервере"""

    public_ip: Optional[str] = None
    client_ip: Optional[str] = None
    environment: str


# Кэш для публичного IP (обновляется раз в час)
_public_ip_cache: Optional[str] = None
_cache_timestamp: float = 0
CACHE_TTL = 3600  # 1 час


async def get_public_ip() -> Optional[str]:
    """
    Получает публичный IP адрес сервера

    Пробует несколько сервисов:
    1. ipify.org
    2. icanhazip.com
    3. ifconfig.me
    """
    global _public_ip_cache, _cache_timestamp

    import time

    current_time = time.time()

    # Возвращаем из кэша если актуален
    if _public_ip_cache and (current_time - _cache_timestamp) < CACHE_TTL:
        return _public_ip_cache

    services = [
        "https://api.ipify.org",
        "https://icanhazip.com",
        "https://ifconfig.me/ip",
    ]

    for service_url in services:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    service_url, timeout=aiohttp.ClientTimeout(total=3)
                ) as response:
                    if response.status == 200:
                        ip = (await response.text()).strip()
                        # Обновляем кэш
                        _public_ip_cache = ip
                        _cache_timestamp = current_time
                        logger.debug(f"Public IP obtained from {service_url}: {ip}")
                        return ip
        except Exception as e:
            logger.debug(f"Failed to get public IP from {service_url}: {e}")
            continue

    logger.warning("Could not determine public IP from any service")
    return None


def get_client_ip(request: Request) -> str:
    """Делегирует определение IP в общий модуль backend.services.shared.device_info."""
    return shared_get_client_ip(request)


@router.get(
    "",
    response_model=NodeInfoResponse,
    summary="Получить информацию о текущей ноде",
    description="""
    Возвращает информацию о сервере/ноде на которой работает приложение.
    
    **Используется для:**
    - Отображения на главной странице (до авторизации)
    - Определения на каком сервере (dev/prod) находится пользователь
    - Отладки при работе с несколькими нодами/серверами
    
    **Не требует авторизации** - публичный endpoint
    """,
)
async def get_node_info(request: Request):
    """
    Получает информацию о текущей ноде/сервере

    Returns:
        NodeInfoResponse с данными:
        - public_ip: публичный IP адрес сервера (если доступен)
        - client_ip: IP адрес клиента (с учетом прокси)
        - environment: окружение (development/production)
    """
    from backend.config.config import settings

    try:
        # Получаем публичный IP (с кэшированием)
        public_ip = await get_public_ip()

        # Получаем IP клиента
        client_ip = get_client_ip(request)

        # Окружение из настроек
        environment = settings.app_env

        logger.info(
            f"Node info requested: public_ip={public_ip}, client_ip={client_ip}, env={environment}"
        )

        return NodeInfoResponse(
            public_ip=public_ip,
            client_ip=client_ip,
            environment=environment,
        )

    except Exception as e:
        logger.error(f"Error getting node info: {e}", exc_info=True)
        # Возвращаем базовую информацию даже при ошибке
        return NodeInfoResponse(
            public_ip=None,
            client_ip=get_client_ip(request),
            environment=settings.app_env,
        )
