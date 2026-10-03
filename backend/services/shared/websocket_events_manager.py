import asyncio
from datetime import datetime, timezone
import os
import socket
from typing import List, Dict, Optional
import uuid
from fastapi import WebSocket, status
import json
from sqlalchemy import or_, select
from backend.database.db import async_session_maker
from backend.database.models import EnergeticDevice
from backend.database.redis_db import redis_client
from fastapi.websockets import WebSocketState
from backend.services.shared.device_info import (
    _extract_ip_from_forwarded_for,
    _normalize_ip,
)

from loguru import logger


def get_websocket_client_ip(websocket: WebSocket) -> str:
    """
    Получение реального IP-адреса клиента WebSocket из scope.
    Аналогично get_client_ip для HTTP-запросов, но адаптировано под WebSocket.
    """
    scope = websocket.scope
    headers = {k.decode("utf-8"): v.decode("utf-8") for k, v in scope["headers"]}
    client = scope.get("client")
    client_host = client[0] if client else None

    return (
        _normalize_ip(client_host)
        or _normalize_ip(headers.get("x-real-ip"))
        or _extract_ip_from_forwarded_for(headers.get("x-forwarded-for"))
        or _normalize_ip(headers.get("http_client_ip"))
        or "unknown"
    )


class WebSocketEventsManager:
    """Менеджер WebSocket с Redis для многоворкеров."""

    def __init__(self, worker_id: str):
        self.worker_id = worker_id
        self.active_connections: Dict[str, Dict[str, any]] = (
            {}
        )  # {"websocket": WebSocket, "session_id": str | None}
        self.frontend_subscribers: Dict[str, Dict] = (
            {}
        )  # {connection_id: {"websocket": ws, "device_id": str|None, "connected_at": datetime}}
        logger.info(f"WebSocketEventsManager initialized for worker {worker_id}")

    async def init_redis_listener(self):
        """Запуск подписки на глобальный и персональный Redis каналы."""
        asyncio.create_task(self._listen_pubsub())

    async def _listen_pubsub(self):
        pubsub = redis_client.pubsub()
        await pubsub.subscribe("ws:broadcast", f"ws:worker:{self.worker_id}")
        # logger.info(f"Subscribed to Redis channels: ws:broadcast, ws:worker:{self.worker_id}")

        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            try:
                channel = message["channel"]
                data = json.loads(message["data"])
                # logger.debug(f"Received message from Redis channel {channel}: {data}")

                if channel == "ws:broadcast":
                    await self._broadcast_to_local(data)
                else:
                    # Targeted message
                    connection_id = data["connection_id"]
                    event = data["event"]
                    # logger.debug(f"Processing targeted message for connection {connection_id}")
                    await self._send_to_connection(connection_id, event)
            except Exception as e:
                logger.error(f"Failed to handle pubsub message: {e}", exc_info=True)

    async def connect(
        self,
        websocket: WebSocket,
        session_id: Optional[str] = None,
        accept_connection: bool = True,
    ) -> str:
        """
        Подключение WebSocket клиента с опциональным session_id.

        Args:
            websocket: WebSocket соединение
            session_id: Опциональный идентификатор сессии
            accept_connection: Если True, вызовет websocket.accept(). Если False, предполагается что соединение уже принято.
        """
        if accept_connection:
            await websocket.accept()

        connection_id = str(uuid.uuid4())
        connected_at = datetime.now(timezone.utc).isoformat()
        client_ip = get_websocket_client_ip(websocket)

        self.active_connections[connection_id] = {
            "websocket": websocket,
            "session_id": session_id,
        }

        mapping = {
            "worker_id": self.worker_id,
            "connected_at": connected_at,
            "client_ip": client_ip,
        }
        if session_id:
            mapping["session_id"] = session_id
            await redis_client.set(f"ws:session:{session_id}", connection_id)

        await redis_client.hset(f"ws:connection:{connection_id}", mapping=mapping)
        await redis_client.sadd("ws:connections", connection_id)

        # logger.info(f"WS connected {connection_id} from {client_ip} with session_id={session_id}")
        return connection_id

    async def disconnect(self, connection_id: str):
        """Отключение WebSocket клиента."""
        conn = self.active_connections.pop(connection_id, None)
        if conn and conn["websocket"].client_state == WebSocketState.CONNECTED:
            await conn["websocket"].close(code=status.WS_1000_NORMAL_CLOSURE)

        conn_key = f"ws:connection:{connection_id}"
        connection_hash = await redis_client.hgetall(conn_key)

        aliases_to_delete = set()
        session_id = connection_hash.get("session_id")
        if session_id:
            aliases_to_delete.add(session_id)

        aliases_raw = connection_hash.get("session_aliases")
        if aliases_raw:
            try:
                aliases_to_delete.update(str(alias) for alias in json.loads(aliases_raw) if alias)
            except Exception:
                logger.warning(f"Failed to decode session aliases for {connection_id}: {aliases_raw}")

        for alias in aliases_to_delete:
            await redis_client.delete(f"ws:session:{alias}")

        await redis_client.delete(conn_key)
        await redis_client.srem("ws:connections", connection_id)

        logger.info(f"WS disconnected {connection_id}")

    async def broadcast_event(self, event_data: Dict):
        """Глобальная рассылка события всем воркерам через Redis (только для клиентов без session_id)."""
        message = json.dumps(event_data)
        await redis_client.publish("ws:broadcast", message)
        # logger.debug("Event published to Redis channel ws:broadcast")

    async def _resolve_legacy_connection_id(self, session_id: str):
        """Пытается восстановить connection_id для старых объектов, где мог сохраниться UUID записи устройства."""
        try:
            async with async_session_maker() as db:
                result = await db.execute(
                    select(EnergeticDevice).where(
                        or_(EnergeticDevice.id == session_id, EnergeticDevice.device_id == session_id)
                    )
                )
                device = result.scalars().first()
        except Exception as exc:
            logger.debug(f"Legacy session lookup failed for {session_id}: {exc}")
            return None

        if not device:
            return None

        for alias in {session_id, str(device.id), device.device_id}:
            if not alias:
                continue
            connection_id_bytes = await redis_client.get(f"ws:session:{alias}")
            if connection_id_bytes:
                await redis_client.set(f"ws:session:{session_id}", connection_id_bytes)
                return connection_id_bytes

        return None

    async def send_to_session(self, session_id: str, event_data: Dict) -> bool:
        """Точечная рассылка события по session_id."""
        connection_id_bytes = await redis_client.get(f"ws:session:{session_id}")
        if not connection_id_bytes:
            connection_id_bytes = await self._resolve_legacy_connection_id(session_id)

        if not connection_id_bytes:
            # Восстанавливаем потерянный Redis ключ, если соединение есть в памяти.
            for conn_id, conn_data in self.active_connections.items():
                if conn_data.get("session_id") == session_id:
                    logger.warning(
                        f"Redis key lost for session_id {session_id}, connection_id {conn_id}. Recovering..."
                    )
                    worker_id_bytes = await redis_client.hget(
                        f"ws:connection:{conn_id}", "worker_id"
                    )
                    if worker_id_bytes:
                        await redis_client.set(f"ws:session:{session_id}", conn_id)
                        connection_id_bytes = (
                            conn_id.encode("utf-8") if isinstance(conn_id, str) else conn_id
                        )
                        break

        if not connection_id_bytes:
            logger.warning(f"No connection found for session_id {session_id}")
            return False

        connection_id = (
            connection_id_bytes.decode("utf-8")
            if isinstance(connection_id_bytes, bytes)
            else connection_id_bytes
        )
        worker_id_bytes = await redis_client.hget(
            f"ws:connection:{connection_id}", "worker_id"
        )
        if not worker_id_bytes:
            logger.warning(
                f"No worker found for connection_id {connection_id}; "
                f"deleting stale mapping for session_id {session_id}"
            )
            await redis_client.delete(f"ws:session:{session_id}")
            connection_id_bytes = await self._resolve_legacy_connection_id(session_id)
            if not connection_id_bytes:
                return False

            connection_id = (
                connection_id_bytes.decode("utf-8")
                if isinstance(connection_id_bytes, bytes)
                else connection_id_bytes
            )
            worker_id_bytes = await redis_client.hget(
                f"ws:connection:{connection_id}", "worker_id"
            )
            if not worker_id_bytes:
                logger.warning(f"No worker found after stale mapping recovery for connection_id {connection_id}")
                return False

        worker_id = (
            worker_id_bytes.decode("utf-8")
            if isinstance(worker_id_bytes, bytes)
            else worker_id_bytes
        )
        message = json.dumps({"connection_id": connection_id, "event": event_data})
        await redis_client.publish(f"ws:worker:{worker_id}", message)
        # logger.debug(
        #     f"Targeted event published to worker {worker_id} for session_id {session_id}"
        # )
        return True

    async def _broadcast_to_local(self, event: Dict):
        """Отправка broadcast события только локальным клиентам без session_id."""
        dead_ids = []

        for connection_id, conn in self.active_connections.items():
            if conn["session_id"] is not None:
                continue  # Пропускаем клиентов с session_id
            websocket = conn["websocket"]
            if websocket.client_state != WebSocketState.CONNECTED:
                dead_ids.append(connection_id)
                continue
            try:
                await websocket.send_text(json.dumps(event))
            except Exception as e:
                logger.warning(f"Error sending to {connection_id}: {e}")
                dead_ids.append(connection_id)

        await self._cleanup_dead_connections(dead_ids)
        # logger.info(f"Local broadcast complete. Active connections: {len(self.active_connections)}")

    async def _send_to_connection(self, connection_id: str, event: Dict):
        """Отправка targeted события конкретному локальному соединению."""
        # logger.debug(f"Attempting to send targeted event to connection {connection_id}")

        conn = self.active_connections.get(connection_id)
        if not conn:
            logger.warning(
                f"Connection {connection_id} not found in active_connections"
            )
            return

        websocket = conn["websocket"]
        if websocket.client_state != WebSocketState.CONNECTED:
            logger.warning(f"Connection {connection_id} is not in CONNECTED state")
            await self.disconnect(connection_id)
            return

        defer_until = conn.get("defer_outbound_until_monotonic")
        if defer_until is not None:
            try:
                remaining_seconds = float(defer_until) - asyncio.get_event_loop().time()
            except Exception:
                remaining_seconds = 0

            if remaining_seconds > 0:
                logger.warning(
                    "Skipping outbound message during device grace period: "
                    f"connection_id={connection_id}, session_id={conn.get('session_id')}, "
                    f"remaining_seconds={remaining_seconds:.2f}, event={event}"
                )
                return

        try:
            logger.info(
                "Sending targeted websocket message: "
                f"connection_id={connection_id}, session_id={conn.get('session_id')}, event={event}"
            )
            await websocket.send_text(json.dumps(event))
            # logger.info(f"✅ Targeted event successfully sent to connection {connection_id}")
        except Exception as e:
            logger.warning(f"Error sending targeted to {connection_id}: {e}")
            await self.disconnect(connection_id)

    async def _cleanup_dead_connections(self, dead_ids: List[str]):
        """Очистка мёртвых соединений."""
        for cid in dead_ids:
            await self.disconnect(cid)


worker_id = f"{socket.gethostname()}-{os.getpid()}"
websocket_events_manager = WebSocketEventsManager(worker_id=worker_id)
