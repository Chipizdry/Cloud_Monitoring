


"""
Роуты для работы с Modbus устройствами.
"""

import asyncio
from fastapi import APIRouter, HTTPException, Depends, Query, status
from typing import List, Optional, Tuple
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select, or_, desc

from backend.database.db import get_db
from backend.repository.energy.cerbo_service import get_energetic_object
from backend.database.models.energy import DeyeAlarmEvent
from backend.services.energy.modbus_client import (
    get_or_create_modbus_client,
    close_modbus_client
)
from backend.services.energy.modbus_cache import (
    get_modbus_cache,
    get_modbus_register_cache,
    make_coils_cache_key,
    make_discrete_inputs_cache_key,
    set_modbus_register_cache,
    set_modbus_cache,
)

router = APIRouter(prefix="/modbus_tcp", tags=["Modbus TCP"]) 

MODBUS_OVER_TCP_OPERATION_TIMEOUT = 10.0
MODBUS_TCP_OPERATION_TIMEOUT = 8.0
MODBUS_CACHE_MAX_AGE_DEFAULT = 20
MODBUS_OVER_TCP_DISCRETE_INPUTS_TIMEOUT = 20.0
MODBUS_OVER_TCP_DISCRETE_INPUTS_RETRIES = 1


async def _get_client_for_request(
    protocol: str,
    ip_address: str,
    port: int,
    object_id: Optional[str],
):
    # HTTP requests must fail promptly when a Deye device is unreachable.
    # Background polling retries on later cycles.
    return await get_or_create_modbus_client(
        protocol=protocol,
        ip_address=ip_address,
        port=port,
        object_id=object_id,
        connect_retries=0 if protocol == "modbus_over_tcp" else None,
    )


async def _perform_modbus_read(
    protocol: str,
    host: str,
    port: int,
    slave_id: int,
    object_id: Optional[str],
    start: int,
    count: int,
    func_code: int,
):
    client = await _get_client_for_request(
        protocol=protocol,
        ip_address=host,
        port=port,
        object_id=object_id,
    )

    if not client:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"ok": False, "error": "Не удалось подключиться к устройству"},
        )

    if protocol == "modbus_over_tcp":
        result = await _run_modbus_over_tcp_call(
            "read",
            lambda: client.read(
                slave_id=slave_id,
                start=start,
                count=count,
                func=func_code,
            ),
        )
        return result

    if func_code == 3:
        response = await asyncio.wait_for(
            client.read_holding_registers(start, count),
            timeout=MODBUS_TCP_OPERATION_TIMEOUT,
        )
    elif func_code == 4:
        response = await asyncio.wait_for(
            client.read_input_registers(start, count),
            timeout=MODBUS_TCP_OPERATION_TIMEOUT,
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"ok": False, "error": f"Неподдерживаемый func_code: {func_code}"},
        )

    if response.isError():
        return {"ok": False, "error": str(response)}

    return {"ok": True, "data": response.registers}


async def _perform_modbus_read_coils(
    protocol: str,
    host: str,
    port: int,
    slave_id: int,
    object_id: Optional[str],
    start: int,
    count: int,
):
    client = await _get_client_for_request(
        protocol=protocol,
        ip_address=host,
        port=port,
        object_id=object_id,
    )

    if not client:
        raise HTTPException(503, "Modbus client not available")

    if protocol == "modbus_over_tcp":
        return await _run_modbus_over_tcp_call(
            "read_coils",
            lambda: client.read_coils(
                slave_id=slave_id,
                start=start,
                count=count,
            ),
        )

    resp = await asyncio.wait_for(
        client.read_coils(start, count, slave=slave_id),
        timeout=MODBUS_TCP_OPERATION_TIMEOUT,
    )
    if resp.isError():
        return {"ok": False, "error": str(resp)}

    return {"ok": True, "data": resp.bits[:count]}


async def _perform_modbus_read_discrete_inputs(
    protocol: str,
    host: str,
    port: int,
    slave_id: int,
    object_id: Optional[str],
    start: int,
    count: int,
):
    client = await _get_client_for_request(
        protocol=protocol,
        ip_address=host,
        port=port,
        object_id=object_id,
    )

    if not client:
        raise HTTPException(503, "Modbus client not available")

    if protocol == "modbus_over_tcp":
        return await _run_modbus_over_tcp_call(
            "read_discrete_inputs",
            lambda: client.read_inputs(
                slave_id=slave_id,
                start=start,
                count=count,
            ),
            timeout_seconds=MODBUS_OVER_TCP_DISCRETE_INPUTS_TIMEOUT,
            retry_count=MODBUS_OVER_TCP_DISCRETE_INPUTS_RETRIES,
        )

    resp = await asyncio.wait_for(
        client.read_discrete_inputs(start, count, slave=slave_id),
        timeout=MODBUS_TCP_OPERATION_TIMEOUT,
    )
    if resp.isError():
        return {"ok": False, "error": str(resp)}

    return {"ok": True, "data": resp.bits[:count]}


async def _run_modbus_over_tcp_call(
    operation_name: str,
    call,
    timeout_seconds: float = MODBUS_OVER_TCP_OPERATION_TIMEOUT,
    retry_count: int = 0,
):
    """Выполняет sync Modbus OVER TCP вызов в отдельном потоке с timeout."""
    attempts = max(1, int(retry_count) + 1)
    last_timeout: Optional[asyncio.TimeoutError] = None

    for attempt in range(1, attempts + 1):
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(call),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError as e:
            last_timeout = e
            logger.warning(
                f"Modbus OVER TCP operation '{operation_name}' timed out after "
                f"{timeout_seconds}s (attempt {attempt}/{attempts})"
            )

    raise HTTPException(
        status_code=status.HTTP_504_GATEWAY_TIMEOUT,
        detail={
            "ok": False,
            "error": (
                f"Modbus OVER TCP timeout ({timeout_seconds}s) during {operation_name} "
                f"after {attempts} attempt(s)"
            ),
        },
    ) from last_timeout


async def _resolve_connection_params(
    db: AsyncSession,
    object_id: Optional[str],
    protocol: Optional[str],
    host: Optional[str],
    port: Optional[int],
) -> Tuple[str, str, int]:
    """Дополняет/проверяет параметры подключения. Если передан object_id — берём protocol/host/port из БД.
    Если после подстановки нет необходимых значений — бросаем 400.
    """

    if object_id:
        obj = await get_energetic_object(db, object_id)
        if not obj:
            raise HTTPException(status_code=404, detail=f"Энергообъект {object_id} не найден")
        protocol = protocol or obj.protocol
        host = host or obj.ip_address
        port = port or obj.port

    if not protocol or not host or not port:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Нужно указать protocol/host/port явно или object_id с заполненными данными"
        )

    if protocol == "modbus_over_tcp" and not object_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Для modbus_over_tcp требуется object_id (уникальный объект/клиент)"
        )

    return protocol, host, int(port)


@router.get("/v1/read")
async def modbus_read(
    protocol: Optional[str] = None,
    host: Optional[str] = None,
    port: Optional[int] = 502,
    slave_id: int = 1,
    object_id: Optional[str] = None,
    start: int = 0,
    count: int = 1,
    func_code: int = 3,
    db: AsyncSession = Depends(get_db),
):
    """
    Читает регистры из Modbus устройства.
    Напрямую использует modbus_client.
    
    Args:
        protocol: Тип протокола - "modbus_tcp" (Victron) или "modbus_over_tcp" (Deye)
        host: IP-адрес устройства
        port: Порт (по умолчанию 502)
        slave_id: Slave ID устройства (для modbus_over_tcp)
        object_id: ID энергообъекта (для modbus_over_tcp)
        start: Начальный адрес регистра
        count: Количество регистров
        func_code: 3 (holding) или 4 (input)
        
    Returns:
        Dict с данными регистров или ошибкой
    """
    protocol, host, port = await _resolve_connection_params(db, object_id, protocol, host, port)

    try:
        client = await _get_client_for_request(
            protocol=protocol,
            ip_address=host,
            port=port,
            object_id=object_id
        )
        
        if not client:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"ok": False, "error": "Не удалось подключиться к устройству"}
            )
        
        # Для modbus_over_tcp используем метод read класса ModbusTCP
        if protocol == "modbus_over_tcp":
            result = await _run_modbus_over_tcp_call(
                "read",
                lambda: client.read(
                    slave_id=slave_id,
                    start=start,
                    count=count,
                    func=func_code,
                ),
            )
            return result
        # Для modbus_tcp используем AsyncModbusTcpClient
        else:
            if func_code == 3:
                response = await asyncio.wait_for(
                    client.read_holding_registers(start, count),
                    timeout=MODBUS_TCP_OPERATION_TIMEOUT,
                )
            elif func_code == 4:
                response = await asyncio.wait_for(
                    client.read_input_registers(start, count),
                    timeout=MODBUS_TCP_OPERATION_TIMEOUT,
                )
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={"ok": False, "error": f"Неподдерживаемый func_code: {func_code}"}
                )
            
            if response.isError():
                return {"ok": False, "error": str(response)}
            
            return {"ok": True, "data": response.registers}
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка при чтении Modbus регистров: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"ok": False, "error": f"Ошибка чтения: {str(e)}"}
        )


@router.get("/v1_cached/read")
async def modbus_read_cached(
    protocol: Optional[str] = None,
    host: Optional[str] = None,
    port: Optional[int] = 502,
    slave_id: int = 1,
    object_id: Optional[str] = None,
    start: int = 0,
    count: int = 1,
    func_code: int = 3,
    max_age_seconds: int = MODBUS_CACHE_MAX_AGE_DEFAULT,
    db: AsyncSession = Depends(get_db),
):
    protocol, host, port = await _resolve_connection_params(db, object_id, protocol, host, port)
    cached = await get_modbus_register_cache(
        protocol=protocol,
        host=host,
        port=port,
        slave_id=slave_id,
        object_id=object_id,
        start=start,
        count=count,
        func_code=func_code,
        max_age_seconds=max_age_seconds,
    )
    if cached:
        return {**cached, "source": "cache"}

    live = await _perform_modbus_read(
        protocol=protocol,
        host=host,
        port=port,
        slave_id=slave_id,
        object_id=object_id,
        start=start,
        count=count,
        func_code=func_code,
    )
    if live.get("ok"):
        await set_modbus_register_cache(
            protocol=protocol,
            host=host,
            port=port,
            slave_id=slave_id,
            object_id=object_id,
            start=start,
            count=count,
            func_code=func_code,
            data=live.get("data", []),
        )

    return {**live, "source": "live"}


@router.post("/v1/write_single")
async def modbus_write_single(
    protocol: Optional[str] = None,
    host: Optional[str] = None,
    port: Optional[int] = 502,
    slave_id: int = 1,
    object_id: Optional[str] = None,
    address: int = 0,
    value: int = 0,
    db: AsyncSession = Depends(get_db),
):
    """
    Записывает одиночный регистр.
    Напрямую использует modbus_client.
    
    Args:
        protocol: Тип протокола - "modbus_tcp" или "modbus_over_tcp"
        host: IP-адрес устройства
        port: Порт (по умолчанию 502)
        slave_id: Slave ID устройства
        object_id: ID энергообъекта (для modbus_over_tcp)
        address: Адрес регистра
        value: Значение для записи
        
    Returns:
        Dict с результатом
    """
    protocol, host, port = await _resolve_connection_params(db, object_id, protocol, host, port)

    try:
        client = await _get_client_for_request(
            protocol=protocol,
            ip_address=host,
            port=port,
            object_id=object_id
        )
        
        if not client:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"ok": False, "error": "Не удалось подключиться к устройству"}
            )
        
        # Для modbus_over_tcp используем метод write_single класса ModbusTCP
        if protocol == "modbus_over_tcp":
            result = await _run_modbus_over_tcp_call(
                "write_single",
                lambda: client.write_single(
                    slave_id=slave_id,
                    address=address,
                    value=value,
                ),
            )
            return result
        # Для modbus_tcp используем AsyncModbusTcpClient
        else:
            response = await asyncio.wait_for(
                client.write_register(address, value),
                timeout=MODBUS_TCP_OPERATION_TIMEOUT,
            )
            
            if response.isError():
                return {"ok": False, "error": str(response)}
            
            return {"ok": True, "data": "Register written successfully"}
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка при записи Modbus регистра: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"ok": False, "error": f"Ошибка записи: {str(e)}"}
        )


@router.post("/v1/write_multiple")
async def modbus_write_multiple(
    protocol: Optional[str] = None,
    host: Optional[str] = None,
    port: Optional[int] = 502,
    slave_id: int = 1,
    object_id: Optional[str] = None,
    address: int = 0,
    values: List[int] = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Записывает несколько регистров.
    Напрямую использует modbus_client.
    
    Args:
        protocol: Тип протокола - "modbus_tcp" или "modbus_over_tcp"
        host: IP-адрес устройства
        port: Порт (по умолчанию 502)
        slave_id: Slave ID устройства
        object_id: ID энергообъекта (для modbus_over_tcp)
        address: Начальный адрес
        values: Список значений для записи
        
    Returns:
        Dict с результатом
    """
    if not values:
        values = []

    protocol, host, port = await _resolve_connection_params(db, object_id, protocol, host, port)

    try:
        client = await _get_client_for_request(
            protocol=protocol,
            ip_address=host,
            port=port,
            object_id=object_id
        )
        
        if not client:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"ok": False, "error": "Не удалось подключиться к устройству"}
            )
        
        # Для modbus_over_tcp используем метод write_multiple класса ModbusTCP
        if protocol == "modbus_over_tcp":
            result = await _run_modbus_over_tcp_call(
                "write_multiple",
                lambda: client.write_multiple(
                    slave_id=slave_id,
                    address=address,
                    values=values,
                ),
            )
            return result
        # Для modbus_tcp используем AsyncModbusTcpClient
        else:
            response = await asyncio.wait_for(
                client.write_registers(address, values),
                timeout=MODBUS_TCP_OPERATION_TIMEOUT,
            )
            
            if response.isError():
                return {"ok": False, "error": str(response)}
            
            return {"ok": True, "data": "Registers written successfully"}
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка при записи Modbus регистров: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"ok": False, "error": f"Ошибка записи: {str(e)}"}
        )



@router.get("/v1/read_coils")
async def modbus_read_coils(
    protocol: Optional[str] = None,
    host: Optional[str] = None,
    port: Optional[int] = 502,
    slave_id: int = 1,
    object_id: Optional[str] = None,
    start: int = 0,
    count: int = 8,
    db: AsyncSession = Depends(get_db),
):
    protocol, host, port = await _resolve_connection_params(
        db, object_id, protocol, host, port
    )

    client = await _get_client_for_request(
        protocol=protocol,
        ip_address=host,
        port=port,
        object_id=object_id,
    )

    if not client:
        raise HTTPException(503, "Modbus client not available")

    try:
        # ---- Modbus OVER TCP (Deye) ----
        if protocol == "modbus_over_tcp":
            result = await _run_modbus_over_tcp_call(
                "read_coils",
                lambda: client.read_coils(
                    slave_id=slave_id,
                    start=start,
                    count=count
                )
            )
            return result

        # ---- Modbus TCP (pymodbus) ----
        resp = await asyncio.wait_for(
            client.read_coils(start, count, slave=slave_id),
            timeout=MODBUS_TCP_OPERATION_TIMEOUT,
        )
        if resp.isError():
            return {"ok": False, "error": str(resp)}

        return {"ok": True, "data": resp.bits[:count]}

    except Exception as e:
        logger.exception("read_coils error")
        raise HTTPException(500, str(e))


@router.get("/v1_cached/read_coils")
async def modbus_read_coils_cached(
    protocol: Optional[str] = None,
    host: Optional[str] = None,
    port: Optional[int] = 502,
    slave_id: int = 1,
    object_id: Optional[str] = None,
    start: int = 0,
    count: int = 8,
    max_age_seconds: int = MODBUS_CACHE_MAX_AGE_DEFAULT,
    db: AsyncSession = Depends(get_db),
):
    protocol, host, port = await _resolve_connection_params(db, object_id, protocol, host, port)
    cache_key = make_coils_cache_key(
        protocol=protocol,
        host=host,
        port=port,
        slave_id=slave_id,
        object_id=object_id,
        start=start,
        count=count,
    )

    cached = await get_modbus_cache(cache_key, max_age_seconds=max_age_seconds)
    if cached:
        return {**cached, "source": "cache"}

    live = await _perform_modbus_read_coils(
        protocol=protocol,
        host=host,
        port=port,
        slave_id=slave_id,
        object_id=object_id,
        start=start,
        count=count,
    )
    if live.get("ok"):
        await set_modbus_cache(cache_key, live.get("data", []))

    return {**live, "source": "live"}




@router.get("/v1/read_discrete_inputs")
async def modbus_read_discrete_inputs(
    protocol: Optional[str] = None,
    host: Optional[str] = None,
    port: Optional[int] = 502,
    slave_id: int = 1,
    object_id: Optional[str] = None,
    start: int = 0,
    count: int = 8,
    db: AsyncSession = Depends(get_db),
):
    protocol, host, port = await _resolve_connection_params(
        db, object_id, protocol, host, port
    )

    try:
        return await _perform_modbus_read_discrete_inputs(
            protocol=protocol,
            host=host,
            port=port,
            slave_id=slave_id,
            object_id=object_id,
            start=start,
            count=count,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("read_discrete_inputs error")
        raise HTTPException(500, str(e))


@router.get("/v1_cached/read_discrete_inputs")
async def modbus_read_discrete_inputs_cached(
    protocol: Optional[str] = None,
    host: Optional[str] = None,
    port: Optional[int] = 502,
    slave_id: int = 1,
    object_id: Optional[str] = None,
    start: int = 0,
    count: int = 8,
    max_age_seconds: int = MODBUS_CACHE_MAX_AGE_DEFAULT,
    db: AsyncSession = Depends(get_db),
):
    protocol, host, port = await _resolve_connection_params(db, object_id, protocol, host, port)
    cache_key = make_discrete_inputs_cache_key(
        protocol=protocol,
        host=host,
        port=port,
        slave_id=slave_id,
        object_id=object_id,
        start=start,
        count=count,
    )

    cached = await get_modbus_cache(cache_key, max_age_seconds=max_age_seconds)
    if cached:
        return {**cached, "source": "cache"}

    live = await _perform_modbus_read_discrete_inputs(
        protocol=protocol,
        host=host,
        port=port,
        slave_id=slave_id,
        object_id=object_id,
        start=start,
        count=count,
    )
    if live.get("ok"):
        await set_modbus_cache(cache_key, live.get("data", []))

    return {**live, "source": "live"}





@router.post("/v1/write_coils")
async def modbus_write_coils(
    protocol: Optional[str] = None,
    host: Optional[str] = None,
    port: Optional[int] = 502,
    slave_id: int = 1,
    object_id: Optional[str] = None,
    relay: int = 0,
    state: bool = False,
    db: AsyncSession = Depends(get_db),
):
    protocol, host, port = await _resolve_connection_params(
        db, object_id, protocol, host, port
    )

    client = await _get_client_for_request(
        protocol=protocol,
        ip_address=host,
        port=port,
        object_id=object_id,
    )

    if not client:
        raise HTTPException(503, "Modbus client not available")

    try:
        # ---- Modbus OVER TCP (Deye) ----
        if protocol == "modbus_over_tcp":
            result = await _run_modbus_over_tcp_call(
                "write_coils",
                lambda: client.write_coils(
                    slave_id=slave_id,
                    relay=relay,
                    state=state
                )
            )
            return result

        # ---- Modbus TCP (pymodbus) ----
        value = 0xFF00 if state else 0x0000
        resp = await asyncio.wait_for(
            client.write_coil(relay, value, slave=slave_id),
            timeout=MODBUS_TCP_OPERATION_TIMEOUT,
        )

        if resp.isError():
            return {"ok": False, "error": str(resp)}

        return {"ok": True}

    except Exception as e:
        logger.exception("write_coils error")
        raise HTTPException(500, str(e))


@router.get("/deye/alarm-events")
async def get_deye_alarm_events(
    object_id: str,
    page: int = Query(1, ge=1, description="Номер страницы (начиная с 1)"),
    page_size: int = Query(100, ge=1, le=500, description="Количество событий на странице"),
    limit: Optional[int] = Query(
        None,
        ge=1,
        le=500,
        deprecated=True,
        description="Устаревший алиас для page_size",
    ),
    include_empty: bool = False,
    db: AsyncSession = Depends(get_db),
):
    """
    Возвращает историю snapshot-событий Deye faults/warnings по объекту.

    Args:
        object_id: ID энергетического объекта
        page: Номер страницы (начиная с 1)
        page_size: Количество записей на странице (1..500)
        limit: Устаревший алиас для page_size
        include_empty: Включать записи без активных ошибок/предупреждений (все нули)
    """
    # Keep supporting existing clients while exposing a conventional pagination API.
    effective_page_size = limit if limit is not None else page_size

    filters = [DeyeAlarmEvent.energetic_object_id == object_id]

    if not include_empty:
        filters.append(
            or_(
                DeyeAlarmEvent.fault_word_1 != 0,
                DeyeAlarmEvent.fault_word_2 != 0,
                DeyeAlarmEvent.warning_word_1 != 0,
                DeyeAlarmEvent.warning_word_2 != 0,
            )
        )

    total_count = (
        await db.execute(
            select(func.count()).select_from(DeyeAlarmEvent).where(*filters)
        )
    ).scalar_one()

    query = (
        select(DeyeAlarmEvent)
        .where(*filters)
        .order_by(desc(DeyeAlarmEvent.measured_at), desc(DeyeAlarmEvent.created_at))
        .offset((page - 1) * effective_page_size)
        .limit(effective_page_size)
    )

    rows = (await db.execute(query)).scalars().all()

    items = [
        {
            "id": row.id,
            "object_id": row.energetic_object_id,
            "object_name": row.object_name,
            "measured_at": row.measured_at,
            "source_task_id": row.source_task_id,
            "raw": {
                "fault_1": row.fault_word_1,
                "fault_2": row.fault_word_2,
                "warning_1": row.warning_word_1,
                "warning_2": row.warning_word_2,
            },
            "faults": row.faults or [],
            "warnings": row.warnings or [],
            "created_at": row.created_at,
        }
        for row in rows
    ]

    return {
        "ok": True,
        "object_id": object_id,
        "count": len(items),
        "total_count": total_count,
        "page": page,
        "page_size": effective_page_size,
        "total_pages": (total_count + effective_page_size - 1) // effective_page_size,
        "items": items,
    }







@router.delete("/v1/close")
async def modbus_close(
    protocol: Optional[str] = None,
    host: Optional[str] = None,
    port: Optional[int] = 502,
    object_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Закрывает соединение с Modbus устройством.
    Напрямую использует modbus_client.
    
    Args:
        protocol: Тип протокола - "modbus_tcp" или "modbus_over_tcp"
        host: IP-адрес устройства
        port: Порт (по умолчанию 502)
        object_id: ID энергообъекта (для modbus_over_tcp)
        
    Returns:
        Dict с результатом
    """
    protocol, host, port = await _resolve_connection_params(db, object_id, protocol, host, port)

    try:
        await close_modbus_client(
            protocol=protocol,
            ip_address=host,
            port=port,
            object_id=object_id
        )
        
        return {"ok": True, "message": "Соединение закрыто"}
        
    except Exception as e:
        logger.error(f"Ошибка при закрытии Modbus соединения: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"ok": False, "error": f"Ошибка закрытия: {str(e)}"}
        )
