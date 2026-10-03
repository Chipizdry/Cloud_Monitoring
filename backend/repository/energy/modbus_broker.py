"""
Modbus Request Broker - централизованный обработчик запросов к Modbus устройствам.

Паттерн: Command Queue с приоритизацией.
Обеспечивает строгую очерёдность и сериализацию всех операций с Modbus устройствами.
"""
import asyncio
from typing import Optional, Dict, Any, Union, Callable, Awaitable
from dataclasses import dataclass, field
from enum import IntEnum
from datetime import datetime
from loguru import logger
from pymodbus.client import AsyncModbusTcpClient

from backend.database.models.enums import RequestPriority

from ...services.energy.modbus_client import (
    ModbusTCP,
    get_or_create_modbus_client
)




@dataclass(order=True)
class ModbusRequest:
    """
    Запрос к Modbus устройству.
    
    Поля для сортировки в PriorityQueue:
    - priority: приоритет (меньше = выше)
    - timestamp: время создания (раньше = выше приоритет при равных priority)
    """
    priority: int
    # Данные запроса (не участвуют в сортировке) - ОБЯЗАТЕЛЬНЫЕ ПОЛЯ
    protocol: str = field(compare=False)
    host: str = field(compare=False)
    port: int = field(compare=False)
    operation: str = field(compare=False)  # "read", "write_single", "write_multiple"
    
    # ОПЦИОНАЛЬНЫЕ ПОЛЯ (с defaults)
    timestamp: float = field(default_factory=lambda: datetime.now().timestamp())
    slave_id: int = field(compare=False, default=1)
    object_id: Optional[str] = field(compare=False, default=None)
    params: Dict[str, Any] = field(compare=False, default_factory=dict)
    
    # Callback для результата
    future: asyncio.Future = field(compare=False, default_factory=asyncio.Future)
    
    # Метаданные
    request_id: str = field(compare=False, default="")
    timeout: float = field(compare=False, default=10.0)


class ModbusBroker:
    """
    Централизованный брокер запросов к Modbus устройствам.
    
    Особенности:
    - Очередь запросов с приоритетами (asyncio.PriorityQueue)
    - Один воркер на устройство (host:port)
    - Автоматическое управление воркерами (создание/остановка)
    - Таймауты на уровне запросов
    - Метрики и статистика
    """
    
    def __init__(self):
        # Очереди запросов: device_key -> PriorityQueue[ModbusRequest]
        self._queues: Dict[str, asyncio.PriorityQueue] = {}
        
        # Воркеры обработки очередей: device_key -> asyncio.Task
        self._workers: Dict[str, asyncio.Task] = {}
        
        # Флаги остановки воркеров
        self._stop_flags: Dict[str, asyncio.Event] = {}
        
        # Статистика
        self._stats: Dict[str, Dict[str, int]] = {}
        
        # Глобальная блокировка для управления воркерами
        self._management_lock = asyncio.Lock()
        
        logger.info("🚀 Modbus Broker initialized")
    
    def _make_device_key(self, protocol: str, host: str, port: int, object_id: Optional[str] = None) -> str:
        """Создаёт уникальный ключ устройства."""
        if protocol == "modbus_tcp":
            return f"tcp:{host}:{port}"
        else:  # modbus_over_tcp
            return f"over_tcp:{object_id or f'{host}:{port}'}"
    
    def _init_device_stats(self, device_key: str):
        """Инициализирует статистику для устройства."""
        if device_key not in self._stats:
            self._stats[device_key] = {
                "total_requests": 0,
                "completed_requests": 0,
                "failed_requests": 0,
                "timeout_requests": 0,
                "queue_size": 0,
            }
    
    async def _ensure_worker(self, device_key: str, protocol: str, host: str, port: int, slave_id: int = 1, object_id: Optional[str] = None):
        """Гарантирует что воркер для устройства запущен."""
        async with self._management_lock:
            if device_key not in self._workers or self._workers[device_key].done():
                # Создаём очередь если нет
                if device_key not in self._queues:
                    self._queues[device_key] = asyncio.PriorityQueue()
                
                # Создаём флаг остановки
                self._stop_flags[device_key] = asyncio.Event()
                
                # Инициализируем статистику
                self._init_device_stats(device_key)
                
                # Запускаем воркер
                worker = asyncio.create_task(
                    self._device_worker(device_key, protocol, host, port, slave_id, object_id)
                )
                self._workers[device_key] = worker
                
                logger.info(f"✅ [{device_key}] Modbus worker started")
    
    async def _device_worker(
        self,
        device_key: str,
        protocol: str,
        host: str,
        port: int,
        slave_id: int = 1,
        object_id: Optional[str] = None
    ):
        """
        Воркер обработки очереди запросов для конкретного устройства.
        Обрабатывает запросы последовательно, гарантируя что следующий запрос
        выполняется только после завершения предыдущего.
        """
        queue = self._queues[device_key]
        stop_flag = self._stop_flags[device_key]
        
        # logger.info(f"🔄 [{device_key}] Worker loop started")
        
        try:
            while not stop_flag.is_set():
                try:
                    # Ждём запрос с таймаутом 1 сек (чтобы проверять stop_flag)
                    request = await asyncio.wait_for(queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                
                # Обновляем статистику очереди
                self._stats[device_key]["queue_size"] = queue.qsize()
                self._stats[device_key]["total_requests"] += 1
                
                # logger.debug(
                #     f"📨 [{device_key}] Processing request {request.request_id} "
                #     f"(priority={request.priority}, queue_size={queue.qsize()})"
                # )
                
                # Обрабатываем запрос с таймаутом
                try:
                    result = await asyncio.wait_for(
                        self._execute_request(request),
                        timeout=request.timeout
                    )
                    
                    if not request.future.done():
                        request.future.set_result(result)
                    
                    self._stats[device_key]["completed_requests"] += 1
                    
                except asyncio.TimeoutError:
                    logger.warning(
                        f"⏱️ [{device_key}] Request {request.request_id} timeout "
                        f"({request.timeout}s)"
                    )
                    if not request.future.done():
                        request.future.set_exception(
                            TimeoutError(f"Modbus request timeout after {request.timeout}s")
                        )
                    self._stats[device_key]["timeout_requests"] += 1
                    
                except Exception as e:
                    logger.error(
                        f"❌ [{device_key}] Request {request.request_id} failed: {e}",
                        exc_info=True
                    )
                    if not request.future.done():
                        request.future.set_exception(e)
                    self._stats[device_key]["failed_requests"] += 1
                
                finally:
                    queue.task_done()
        
        except asyncio.CancelledError:
            logger.info(f"🛑 [{device_key}] Worker cancelled")
            raise
        except Exception as e:
            logger.error(f"❗ [{device_key}] Worker crashed: {e}", exc_info=True)
    
    async def _execute_request(self, request: ModbusRequest) -> Dict[str, Any]:
        """Выполняет Modbus запрос."""
        # Получаем клиент
        client = await get_or_create_modbus_client(
            protocol=request.protocol,
            ip_address=request.host,
            port=request.port,
            object_id=request.object_id,
            slave_id=request.slave_id,
        )
        
        if not client:
            raise ConnectionError(
                f"Failed to create Modbus client for {request.host}:{request.port}"
            )
        
        # Выполняем операцию
        if request.operation == "read":
            return await self._execute_read(client, request)
        elif request.operation == "write_single":
            return await self._execute_write_single(client, request)
        elif request.operation == "write_multiple":
            return await self._execute_write_multiple(client, request)
        else:
            raise ValueError(f"Unknown operation: {request.operation}")
    
    async def _execute_read(
        self,
        client: Union[AsyncModbusTcpClient, ModbusTCP],
        request: ModbusRequest
    ) -> Dict[str, Any]:
        """Выполняет операцию чтения."""
        start = request.params["start"]
        count = request.params["count"]
        func_code = request.params.get("func_code", 3)
        
        # Modbus OVER TCP
        if isinstance(client, ModbusTCP):
            if func_code == 1:
                result = await asyncio.to_thread(
                    client.read_coils,
                    request.slave_id,
                    start,
                    count,
                )
            elif func_code == 2:
                result = await asyncio.to_thread(
                    client.read_inputs,
                    request.slave_id,
                    start,
                    count,
                )
            else:
                result = await asyncio.to_thread(
                    client.read,
                    request.slave_id,
                    start,
                    count,
                    func_code,
                )
            
            if not result.get("ok"):
                error_message = result.get("error", "Unknown error")
                error_text = str(error_message).lower()
                if "таймаут" in error_text or "timeout" in error_text:
                    raise TimeoutError(error_message)
                raise RuntimeError(error_message)
            
            return {"ok": True, "data": result.get("data")}
        
        # Modbus TCP
        else:
            if func_code == 3:
                result = await client.read_holding_registers(
                    address=start,
                    count=count,
                    slave=request.slave_id,
                )
            elif func_code == 4:
                result = await client.read_input_registers(
                    address=start,
                    count=count,
                    slave=request.slave_id,
                )
            else:
                raise ValueError("func_code must be 3 (holding) or 4 (input)")
            
            if result.isError():
                raise RuntimeError(f"Modbus error: {result}")
            
            return {"ok": True, "data": result.registers}
    
    async def _execute_write_single(
        self,
        client: Union[AsyncModbusTcpClient, ModbusTCP],
        request: ModbusRequest
    ) -> Dict[str, Any]:
        """Выполняет запись одного регистра."""
        address = request.params["address"]
        value = request.params["value"]
        
        # Modbus OVER TCP
        if isinstance(client, ModbusTCP):
            result = await asyncio.to_thread(
                client.write_single,
                request.slave_id,
                address,
                value,
            )
            
            if not result.get("ok"):
                raise RuntimeError(result.get("error", "Unknown error"))
            
            return {"ok": True, "data": result.get("data")}
        
        # Modbus TCP
        else:
            result = await client.write_register(address, value, slave=request.slave_id)
            
            if result.isError():
                raise RuntimeError(f"Modbus error: {result}")
            
            return {"ok": True, "message": "Register written successfully"}
    
    async def _execute_write_multiple(
        self,
        client: Union[AsyncModbusTcpClient, ModbusTCP],
        request: ModbusRequest
    ) -> Dict[str, Any]:
        """Выполняет запись нескольких регистров."""
        address = request.params["address"]
        values = request.params["values"]
        
        # Modbus OVER TCP
        if isinstance(client, ModbusTCP):
            result = await asyncio.to_thread(
                client.write_multiple,
                request.slave_id,
                address,
                values,
            )
            
            if not result.get("ok"):
                raise RuntimeError(result.get("error", "Unknown error"))
            
            return {"ok": True, "data": result.get("data")}
        
        # Modbus TCP
        else:
            result = await client.write_registers(address, values, slave=request.slave_id)
            
            if result.isError():
                raise RuntimeError(f"Modbus error: {result}")
            
            return {"ok": True, "message": f"{len(values)} registers written successfully"}
    
    async def submit_request(
        self,
        protocol: str,
        host: str,
        port: int,
        operation: str,
        params: Dict[str, Any],
        slave_id: int = 1,
        object_id: Optional[str] = None,
        priority: RequestPriority = RequestPriority.USER_READ,
        timeout: float = 10.0,
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Отправляет запрос в очередь и ждёт результата.
        
        Args:
            protocol: "modbus_tcp" или "modbus_over_tcp"
            host: IP-адрес устройства
            port: Порт
            operation: "read", "write_single", "write_multiple"
            params: Параметры операции
            priority: Приоритет запроса
            slave_id: Slave ID (для modbus_over_tcp)
            object_id: ID объекта (для modbus_over_tcp)
            timeout: Таймаут запроса в секундах
            request_id: ID запроса для логирования
            
        Returns:
            Результат выполнения запроса
            
        Raises:
            TimeoutError: если запрос не выполнен за timeout
            Exception: другие ошибки выполнения
        """
        device_key = self._make_device_key(protocol, host, port, object_id)
        
        # Гарантируем что воркер запущен
        await self._ensure_worker(device_key, protocol, host, port, slave_id, object_id)
        
        # Создаём запрос
        request = ModbusRequest(
            priority=priority,
            protocol=protocol,
            host=host,
            port=port,
            slave_id=slave_id,
            object_id=object_id,
            operation=operation,
            params=params,
            timeout=timeout,
            request_id=request_id or f"{operation}_{datetime.now().timestamp()}",
        )
        
        # Добавляем в очередь
        queue = self._queues[device_key]
        await queue.put(request)
        
        # logger.debug(
        #     f"➕ [{device_key}] Request {request.request_id} queued "
        #     f"(priority={priority}, queue_size={queue.qsize()})"
        # )
        
        # Ждём результат
        result = await request.future
        return result
    
    async def stop_worker(self, protocol: str, host: str, port: int, object_id: Optional[str] = None):
        """Останавливает воркер для устройства."""
        device_key = self._make_device_key(protocol, host, port, object_id)
        
        async with self._management_lock:
            if device_key in self._stop_flags:
                self._stop_flags[device_key].set()
            
            if device_key in self._workers:
                worker = self._workers[device_key]
                worker.cancel()
                try:
                    await worker
                except asyncio.CancelledError:
                    pass
                
                del self._workers[device_key]
                logger.info(f"🛑 [{device_key}] Worker stopped")
    
    async def stop_all_workers(self):
        """Останавливает все воркеры."""
        logger.info("🛑 Stopping all Modbus workers...")
        
        device_keys = list(self._workers.keys())
        for device_key in device_keys:
            # Парсим device_key обратно
            parts = device_key.split(":", 2)
            if parts[0] == "tcp":
                protocol = "modbus_tcp"
                host = parts[1]
                port = int(parts[2])
                object_id = None
            else:
                protocol = "modbus_over_tcp"
                object_id = parts[1]
                host = ""
                port = 0
            
            await self.stop_worker(protocol, host, port, object_id)
        
        logger.info("✅ All Modbus workers stopped")
    
    def get_stats(self, protocol: str, host: str, port: int, object_id: Optional[str] = None) -> Dict[str, int]:
        """Возвращает статистику для устройства."""
        device_key = self._make_device_key(protocol, host, port, object_id)
        return self._stats.get(device_key, {}).copy()
    
    def get_all_stats(self) -> Dict[str, Dict[str, int]]:
        """Возвращает статистику для всех устройств."""
        return {k: v.copy() for k, v in self._stats.items()}


# Глобальный экземпляр брокера
_broker: Optional[ModbusBroker] = None


def get_broker() -> ModbusBroker:
    """Возвращает глобальный экземпляр брокера."""
    global _broker
    if _broker is None:
        _broker = ModbusBroker()
    return _broker
