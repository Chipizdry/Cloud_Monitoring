from itertools import count
from typing import Optional, Dict, Union
import asyncio
import os
from loguru import logger
from pymodbus.client import AsyncModbusTcpClient
from datetime import datetime
import socket
import struct
import time
from threading import Lock

error_count = 0
_error_stats: Dict[str, Dict] = {}

# Пул Modbus TCP клиентов (Victron) - один клиент на каждый IP-адрес
# Modbus TCP protocol с БД 
_modbus_tcp_clients: Dict[str, AsyncModbusTcpClient] = {}

# Пул Modbus OVER_TCP клиентов (Deye) - один клиент на каждый объект
# Modbus OVER_TCP protocol - строго 1 клиент на энергообъект
_modbus_over_tcp_clients: Dict[str, 'ModbusTCP'] = {}
_modbus_over_tcp_connect_locks: Dict[str, asyncio.Lock] = {}
_modbus_over_tcp_retry_after: Dict[str, float] = {}

# Конфигурация Modbus (используется как дефолтный порт если не указан в объекте)
DEFAULT_MODBUS_PORT = 502
DEFAULT_MODBUS_OVER_TCP_SOCKET_TIMEOUT = 8
BATTERY_ID = 225
INVERTER_ID = 100
ESS_UNIT_ID = 227
SOLAR_CHARGER_SLAVE_IDS = list(range(1, 14)) + [100]

# Определение регистров Modbus 
REGISTERS = {
    "soc": 266,
    "voltage": 259,
    "current": 261,
    "temperature": 262,
    "power": 258,
    "soh": 304,
}

INVERTER_REGISTERS = {
    "inverter_power": 870,
    "output_power_l1": 878,
    "output_power_l2": 880,
    "output_power_l3": 882,
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
    "ess_power_setpoint_l1": 96,
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

# ===============================
# Backoff настройки подключения
# ===============================
CONNECT_MAX_RETRIES = 3
CONNECT_BASE_DELAY = 1.0
CONNECT_MAX_DELAY = 8.0
MODBUS_OVER_TCP_CONNECT_COOLDOWN_SECONDS = 5.0


# ============================================================================
# ModbusTCP - Modbus OVER TCP protocol (для инверторов Deye)
# ============================================================================

class ModbusTCP:
    """
    Modbus OVER TCP клиент для инверторов Deye.
    Каждый энергообъект должен иметь свой экземпляр класса.
    """
    
    def __init__(self, host: str, port: int = 502, timeout: float = DEFAULT_MODBUS_OVER_TCP_SOCKET_TIMEOUT):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.sock = None
        self.lock = Lock()
        self.last_connect_error = None

    @staticmethod
    def modbus_crc16(data: bytes) -> int:
        """Вычисляет CRC16 для Modbus."""
        crc = 0xFFFF
        for b in data:
            crc ^= b
            for _ in range(8):
                crc = (crc >> 1) ^ 0xA001 if (crc & 1) else crc >> 1
        return crc

    def connect(self) -> bool:
        """Подключается к Modbus серверу."""
        self.close()
        self.last_connect_error = None
        try:
            s = socket.create_connection((self.host, self.port), timeout=self.timeout)
            s.settimeout(self.timeout)
            self.sock = s
            # logger.info(f"[ModbusTCP] ✅ Подключено к {self.host}:{self.port}")
            return True
        except Exception as e:
            # logger.error(f"[ModbusTCP] ❌ Ошибка подключения к {self.host}:{self.port}: {e}")
            self.last_connect_error = str(e)
            self.sock = None
            return False

    def close(self):
        """Закрывает соединение."""
        if self.sock:
            try:
                self.sock.close()
                # logger.debug(f"[ModbusTCP] 🔌 Соединение закрыто для {self.host}:{self.port}")
            except Exception as e:
                logger.warning(f"[ModbusTCP] ⚠️ Ошибка при закрытии соединения: {e}")
        self.sock = None

    def read(self, slave_id: int, start: int, count: int, func: int = 3) -> Dict:
        """
        Читает регистры Modbus.
        
        Args:
            start: Начальный адрес регистра
            count: Количество регистров
            func: Функция Modbus (3 - read holding registers)
            
        Returns:
            Dict с результатом: {"ok": bool, "data": list или "error": str}
        """
        with self.lock:
            if not self.sock:
                if not self.connect():
                    return {"ok": False, "error": "Ошибка подключения"}

            frame = struct.pack(">B B H H", slave_id, func, start, count)
            crc = self.modbus_crc16(frame)
            frame += struct.pack("<H", crc)

            try:
                self.sock.sendall(frame)
            except Exception as e:
                self.close()
                return {"ok": False, "error": f"Ошибка отправки запроса: {str(e)}"}

            try:
                response = self.sock.recv(256)
            except socket.timeout:
                self.close()
                return {"ok": False, "error": "Таймаут - устройство не ответило"}
            except Exception as e:
                self.close()
                return {"ok": False, "error": f"Ошибка при получении ответа: {str(e)}"}

            if not response:
                self.close()
                return {"ok": False, "error": "Ответ пустой - устройство не ответило"}

            # Проверяем CRC
            data = response[:-2]
            recv_crc = (response[-1] << 8) | response[-2]
            calc_crc = self.modbus_crc16(data)

            if calc_crc != recv_crc:
                return {"ok": False, "error": f"Ошибка CRC: ожидалось {hex(calc_crc)}, получено {hex(recv_crc)}"}

            # Парсим ответ
            try:
                byte_count = response[2]
                values = []
                for i in range(0, byte_count, 2):
                    v = (response[3 + i] << 8) | response[4 + i]
                    values.append(v)
                return {"ok": True, "data": values}
            except Exception as e:
                return {"ok": False, "error": f"Некорректный формат ответа: {str(e)}"}

    def write_single(self, slave_id: int, address: int, value: int) -> Dict:
        """
        Записывает одиночный регистр.
        
        Args:
            address: Адрес регистра
            value: Значение для записи
            
        Returns:
            Dict с результатом
        """
        with self.lock:
            if not self.sock:
                if not self.connect():
                    return {"ok": False, "error": "Ошибка подключения"}

            frame = struct.pack(">B B H H", slave_id, 6, address, value)
            crc = self.modbus_crc16(frame)
            frame += struct.pack("<H", crc)

            try:
                self.sock.sendall(frame)
                resp = self.sock.recv(256)
                return {"ok": True, "data": resp.hex()}
            except Exception as e:
                self.close()
                return {"ok": False, "error": f"Ошибка записи регистра: {str(e)}"}

    def write_multiple(self, slave_id: int, address: int, values: list) -> Dict:
        """
        Записывает несколько регистров.
        
        Args:
            address: Начальный адрес
            values: Список значений для записи
            
        Returns:
            Dict с результатом
        """
        with self.lock:
            if not self.sock:
                if not self.connect():
                    return {"ok": False, "error": "Ошибка подключения"}

            count = len(values)
            byte_count = count * 2
            frame = struct.pack(">B B H H B",slave_id, 16, address, count, byte_count)

            for v in values:
                frame += struct.pack(">H", v)

            crc = self.modbus_crc16(frame)
            frame += struct.pack("<H", crc)

            try:
                self.sock.sendall(frame)
                resp = self.sock.recv(256)
                return {"ok": True, "data": resp.hex()}
            except Exception as e:
                self.close()
                return {"ok": False, "error": f"Ошибка записи: {str(e)}"}
            

            

    def read_inputs(self, slave_id: int, start: int = 0, count: int = 8) -> Dict:
        """
        Чтение логических входов (Function 0x02)
        """
        with self.lock:
            if not self.sock and not self.connect():
                return {"ok": False, "error": "Нет соединения"}

            frame = struct.pack(">B B H H", slave_id, 0x02, start, count)
            crc = self.modbus_crc16(frame)
            frame += struct.pack("<H", crc)

            self.sock.sendall(frame)
            resp = self.sock.recv(256)

            if not resp:
                return {"ok": False, "error": "Нет ответа"}

            if self.modbus_crc16(resp[:-2]) != (resp[-1] << 8 | resp[-2]):
                return {"ok": False, "error": "CRC error"}

            byte_count = resp[2]
            bits = []
            for i in range(byte_count):
                b = resp[3 + i]
                for bit in range(8):
                    bits.append(bool(b & (1 << bit)))

            return {"ok": True, "data": bits[:count]}
        

        

    def read_coils(self, slave_id: int, start: int = 0, count: int = 8) -> Dict:
        """
        Чтение состояния реле (Function 0x01)
        """
        with self.lock:
            if not self.sock and not self.connect():
                return {"ok": False, "error": "Нет соединения"}

            frame = struct.pack(">B B H H", slave_id, 0x01, start, count)
            crc = self.modbus_crc16(frame)
            frame += struct.pack("<H", crc)

            self.sock.sendall(frame)
            resp = self.sock.recv(256)

            if not resp:
                return {"ok": False, "error": "Нет ответа"}

            if self.modbus_crc16(resp[:-2]) != (resp[-1] << 8 | resp[-2]):
                return {"ok": False, "error": "CRC error"}

            byte_count = resp[2]
            states = []
            for i in range(byte_count):
                b = resp[3 + i]
                for bit in range(8):
                    states.append(bool(b & (1 << bit)))

            return {"ok": True, "data": states[:count]}
        


    def write_coils(self, slave_id: int, relay: int, state: bool) -> Dict:
        """
        Вкл/выкл одно реле (Function 0x05)
        """
        value = 0xFF00 if state else 0x0000

        with self.lock:
            if not self.sock and not self.connect():
                return {"ok": False, "error": "Нет соединения"}

            frame = struct.pack(">B B H H", slave_id, 0x05, relay, value)
            crc = self.modbus_crc16(frame)
            frame += struct.pack("<H", crc)

            self.sock.sendall(frame)
            resp = self.sock.recv(256)

            if not resp:
                return {"ok": False, "error": "Нет ответа"}

            if resp != frame:
                return {"ok": False, "error": "Ответ не совпадает с запросом"}

            return {"ok": True}


async def get_or_create_modbus_client(
    protocol: str,
    ip_address: str,
    port: int = None,
    object_id: str = None,
    slave_id: int = 1,
    connect_retries: Optional[int] = None,
) -> Optional[Union[AsyncModbusTcpClient, ModbusTCP]]:
    """
    Получает или создаёт Modbus клиент для указанного протокола и объекта.
    
    Args:
        protocol: Тип протокола - "modbus_tcp" (Victron) или "modbus_over_tcp" (Deye)
        ip_address: IP-адрес Modbus сервера
        port: Порт Modbus сервера (по умолчанию DEFAULT_MODBUS_PORT)
        object_id: ID энергетического объекта (для логирования)
        slave_id: Slave ID устройства (для modbus_over_tcp)
        connect_retries: Число повторов подключения Deye; None — одна попытка на цикл
    
    Returns:
        AsyncModbusTcpClient или ModbusTCP или None в случае ошибки
    """
    global _modbus_tcp_clients, _modbus_over_tcp_clients
    
    if not ip_address:
        logger.error(f"[{object_id or 'unknown'}] IP-адрес не указан")
        return None
    
    port = port or DEFAULT_MODBUS_PORT
    
    # Modbus TCP (Victron) - пул по IP:port
    if protocol == "modbus_tcp":
        client_key = f"{ip_address}:{port}"
        # logger.info(f"[{object_id or client_key}] 🔧 Creating/getting modbus_tcp client (AsyncModbusTcpClient) for Victron")
        
        try:
            # Проверяем существующий клиент
            if client_key in _modbus_tcp_clients:
                client = _modbus_tcp_clients[client_key]
                if client.connected:
                    # logger.debug(f"[{object_id or client_key}] 🔌 Переиспользование Modbus TCP клиента {client_key}")
                    return client
                else:
                    # Клиент существует но не подключен - удаляем
                    logger.warning(f"[{object_id or client_key}] 🔄 Клиент {client_key} не подключен, переподключение...")
                    try:
                        await client.close()
                    except Exception as e:
                        logger.warning(f"[{object_id or client_key}] ⚠️ Ошибка при закрытии клиента: {e}")
                    del _modbus_tcp_clients[client_key]
            
            # Создаём новый клиент с backoff-повторами
            # logger.info(f"[{object_id or client_key}] 🔄 Создание Modbus TCP клиента для {client_key} (retries={CONNECT_MAX_RETRIES})...")
            attempt = 0
            delay = CONNECT_BASE_DELAY
            last_error = None
            while attempt <= CONNECT_MAX_RETRIES:
                new_client = AsyncModbusTcpClient(host=ip_address, port=port, timeout=5)
                try:
                    await new_client.connect()
                    if new_client.connected:
                        # logger.info(f"[{object_id or client_key}] ✅ Modbus TCP клиент {client_key} подключен (attempt {attempt+1})")
                        _modbus_tcp_clients[client_key] = new_client
                        return new_client
                    else:
                        last_error = f"not connected after connect()"
                        try:
                            await new_client.close()
                        except Exception:
                            pass
                except Exception as e:
                    last_error = str(e)
                    try:
                        await new_client.close()
                    except Exception:
                        pass

                attempt += 1
                if attempt <= CONNECT_MAX_RETRIES:
                    logger.warning(f"[{object_id or client_key}] ⚠️ Подключение к {client_key} не удалось (attempt {attempt}/{CONNECT_MAX_RETRIES}), retry in {delay:.1f}s: {last_error}")
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, CONNECT_MAX_DELAY)

            logger.error(f"[{object_id or client_key}] ❌ Не удалось подключиться к {client_key} после {CONNECT_MAX_RETRIES} попыток: {last_error}")
            return None
            
        except Exception as e:
            logger.exception(f"[{object_id or client_key}] ❗ Ошибка при создании Modbus TCP клиента для {client_key}", exc_info=e)
            return None
    
    # Modbus OVER TCP (Deye) - один клиент на объект
    elif protocol == "modbus_over_tcp":
        object_key = object_id or f"{ip_address}:{port}"
        retries = 0 if connect_retries is None else max(0, connect_retries)
        # logger.info(f"[{object_key}] 🔧 Creating/getting modbus_over_tcp client (ModbusTCP) for Deye")
        
        try:
            lock = _modbus_over_tcp_connect_locks.setdefault(object_key, asyncio.Lock())
            async with lock:
                client = _modbus_over_tcp_clients.get(object_key)
                if client is not None:
                    if client.sock is not None:
                        return client
                    _modbus_over_tcp_clients.pop(object_key, None)

                if time.monotonic() < _modbus_over_tcp_retry_after.get(object_key, 0):
                    return None

                attempt = 0
                delay = CONNECT_BASE_DELAY
                last_error = None
                while attempt <= retries:
                    new_client = ModbusTCP(
                        host=ip_address,
                        port=port,
                        timeout=DEFAULT_MODBUS_OVER_TCP_SOCKET_TIMEOUT,
                    )
                    connected = False
                    try:
                        connected = await asyncio.to_thread(new_client.connect)
                        if connected:
                            _modbus_over_tcp_clients[object_key] = new_client
                            _modbus_over_tcp_retry_after.pop(object_key, None)
                            return new_client
                        last_error = new_client.last_connect_error or "connect() returned False"
                    except Exception as e:
                        last_error = str(e)
                    finally:
                        if not connected:
                            new_client.close()

                    attempt += 1
                    if attempt <= retries:
                        logger.warning(
                            f"[{object_key}] ⚠️ Подключение к {ip_address}:{port} не удалось "
                            f"(attempt {attempt}/{retries}), retry in {delay:.1f}s: {last_error}"
                        )
                        await asyncio.sleep(delay)
                        delay = min(delay * 2, CONNECT_MAX_DELAY)

                _modbus_over_tcp_retry_after[object_key] = (
                    time.monotonic() + MODBUS_OVER_TCP_CONNECT_COOLDOWN_SECONDS
                )
                logger.error(
                    f"[{object_key}] ❌ Не удалось подключиться к {ip_address}:{port} "
                    f"после {attempt} попыток: {last_error}"
                )
                return None
            
        except Exception as e:
            logger.exception(f"[{object_key}] ❗ Ошибка при создании Modbus OVER TCP клиента для {object_key}", exc_info=e)
            return None
    
    else:
        logger.error(f"[{object_id or 'unknown'}] ❌ Неизвестный протокол: {protocol}")
        return None


async def close_modbus_client(protocol: str, ip_address: str, port: int = None, object_id: str = None):
    """
    Закрывает и удаляет Modbus клиент.
    
    Args:
        protocol: Тип протокола - "modbus_tcp" или "modbus_over_tcp"
        ip_address: IP-адрес Modbus сервера
        port: Порт Modbus сервера
        object_id: ID энергетического объекта (для modbus_over_tcp)
    """
    global _modbus_tcp_clients, _modbus_over_tcp_clients
    
    port = port or DEFAULT_MODBUS_PORT
    
    if protocol == "modbus_tcp":
        client_key = f"{ip_address}:{port}"
        if client_key in _modbus_tcp_clients:
            try:
                await _modbus_tcp_clients[client_key].close()
                # logger.info(f"🔌 Modbus TCP клиент {client_key} закрыт")
            except Exception as e:
                logger.warning(f"⚠️ Ошибка при закрытии Modbus TCP клиента {client_key}: {e}")
            finally:
                del _modbus_tcp_clients[client_key]
    
    elif protocol == "modbus_over_tcp":
        object_key = object_id or f"{ip_address}:{port}"
        _modbus_over_tcp_retry_after.pop(object_key, None)
        if object_key in _modbus_over_tcp_clients:
            try:
                _modbus_over_tcp_clients[object_key].close()
                # logger.info(f"🔌 Modbus OVER TCP клиент {object_key} закрыт")
            except Exception as e:
                logger.warning(f"⚠️ Ошибка при закрытии Modbus OVER TCP клиента {object_key}: {e}")
            finally:
                del _modbus_over_tcp_clients[object_key]


async def close_all_modbus_clients():
    """
    Закрывает все Modbus клиенты (обоих типов).
    Используется при остановке воркера.
    """
    global _modbus_tcp_clients, _modbus_over_tcp_clients
    
    # Закрываем Modbus TCP клиентов
    for client_key, client in list(_modbus_tcp_clients.items()):
        try:
            await client.close()
            # logger.info(f"🔌 Modbus TCP клиент {client_key} закрыт")
        except Exception as e:
            logger.warning(f"⚠️ Ошибка при закрытии Modbus TCP клиента {client_key}: {e}")
    _modbus_tcp_clients.clear()
    
    # Закрываем Modbus OVER TCP клиентов
    for object_key, client in list(_modbus_over_tcp_clients.items()):
        try:
            client.close()
            # logger.info(f"🔌 Modbus OVER TCP клиент {object_key} закрыт")
        except Exception as e:
            logger.warning(f"⚠️ Ошибка при закрытии Modbus OVER TCP клиента {object_key}: {e}")
    _modbus_over_tcp_clients.clear()
    
    logger.info("✅ Все Modbus клиенты закрыты")


# Обратная совместимость (deprecated)
async def get_modbus_client_singleton() -> Optional[AsyncModbusTcpClient]:
    """
    DEPRECATED: Используйте get_or_create_modbus_client() вместо этого.
    Оставлено для обратной совместимости.
    """
    logger.warning("⚠️ get_modbus_client_singleton() устарел, используйте get_or_create_modbus_client()")
    return None


def register_modbus_error(object_id: str = None):
    """
    Регистрирует ошибку Modbus и инкрементирует счётчик.
    
    Args:
        object_id: ID энергетического объекта (опционально)
    """
    global error_count, _error_stats
    error_count += 1
    logger.warning(f"❗ Modbus ошибка #{error_count}")
    
    # Если указан object_id, ведем статистику по объекту
    if object_id:
        if object_id not in _error_stats:
            _error_stats[object_id] = {
                'total_errors': 0,
                'consecutive_errors': 0,
                'last_error_time': None,
                'last_success_time': None,
            }
        
        _error_stats[object_id]['total_errors'] += 1
        _error_stats[object_id]['consecutive_errors'] += 1
        _error_stats[object_id]['last_error_time'] = datetime.now()


def register_modbus_success(object_id: str = None):
    """
    Регистрирует успешную операцию Modbus.
    
    Args:
        object_id: ID энергетического объекта (опционально)
    """
    global _error_stats
    
    if object_id and object_id in _error_stats:
        _error_stats[object_id]['consecutive_errors'] = 0
        _error_stats[object_id]['last_success_time'] = datetime.now()


def get_modbus_error_stats(object_id: str) -> Dict:
    """
    Получает статистику ошибок для объекта.
    
    Args:
        object_id: ID энергетического объекта
        
    Returns:
        Словарь со статистикой ошибок
    """
    global _error_stats
    
    if object_id not in _error_stats:
        return {
            'total_errors': 0,
            'consecutive_errors': 0,
            'last_error_time': None,
            'last_success_time': None,
        }
    
    return _error_stats[object_id].copy()


def reset_modbus_error_stats(object_id: str = None):
    """
    Сбрасывает статистику ошибок.
    
    Args:
        object_id: ID объекта для сброса. Если None - сбрасывает все.
    """
    global _error_stats, error_count
    
    if object_id:
        if object_id in _error_stats:
            _error_stats[object_id] = {
                'total_errors': 0,
                'consecutive_errors': 0,
                'last_error_time': None,
                'last_success_time': None,
            }
    else:
        _error_stats.clear()
        error_count = 0


def decode_signed_16(value: int) -> int:
    """Декодирует 16-битное знаковое целое число."""
    return value - 0x10000 if value >= 0x8000 else value


def decode_signed_32(high: int, low: int) -> int:
    """Декодирует 32-битное знаковое целое число из двух 16-битных регистров."""
    combined = (high << 16) | low
    return combined - 0x100000000 if combined >= 0x80000000 else combined
