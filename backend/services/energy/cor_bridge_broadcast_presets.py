from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from backend.schemas.websocket import WebSocketBroadcastTaskCreate


def calculate_modbus_rtu_crc(frame: bytes) -> bytes:
    """Returns Modbus RTU CRC16 in low-byte/high-byte order."""
    crc = 0xFFFF
    for byte in frame:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return bytes((crc & 0xFF, (crc >> 8) & 0xFF))


def build_modbus_read_command_hex(
    *,
    slave_id: int,
    start_address: int,
    count: int,
    function_code: int = 3,
) -> str:
    if not 1 <= slave_id <= 247:
        raise HTTPException(status_code=400, detail="slave_id must be in range 1..247")

    frame = bytes(
        [slave_id, function_code]
        + list(start_address.to_bytes(2, byteorder="big"))
        + list(count.to_bytes(2, byteorder="big"))
    )
    return (frame + calculate_modbus_rtu_crc(frame)).hex(" ").upper()


COR_BRIDGE_POLLING_PRESETS: dict[str, dict[str, Any]] = {
    "cor_bridge_status_polling": {
        "title": "COR bridge status polling",
        "description": "Grid, battery, PV 1-4, PV 5-8 and load Modbus polling for COR bridges.",
        "commands": [
            {"suffix": "grid", "label": "Grid", "start_address": 0x0256, "count": 0x0017},
            {"suffix": "battery", "label": "Battery", "start_address": 0x024A, "count": 0x000B},
            {"suffix": "pv_1_4", "label": "PV panels 1-4", "start_address": 0x02A0, "count": 0x000C},
            {"suffix": "pv_5_8", "label": "PV panels 5-8", "start_address": 0x02CE, "count": 0x000D},
            {"suffix": "load", "label": "Load", "start_address": 0x0284, "count": 0x0011},
        ],
    },
    "cor_70b8f66247dc_default": {
        "title": "COR-70B8F66247DC default polling",
        "description": "Alias of cor_bridge_status_polling for bridge COR-70B8F66247DC.",
        "commands": [
            {"suffix": "grid", "label": "Grid", "start_address": 0x0256, "count": 0x0017},
            {"suffix": "battery", "label": "Battery", "start_address": 0x024A, "count": 0x000B},
            {"suffix": "pv_1_4", "label": "PV panels 1-4", "start_address": 0x02A0, "count": 0x000C},
            {"suffix": "pv_5_8", "label": "PV panels 5-8", "start_address": 0x02CE, "count": 0x000D},
            {"suffix": "load", "label": "Load", "start_address": 0x0284, "count": 0x0011},
        ],
    },
}


def list_cor_bridge_broadcast_presets() -> list[dict[str, Any]]:
    presets: list[dict[str, Any]] = []
    for preset_name, preset in COR_BRIDGE_POLLING_PRESETS.items():
        commands = [
            {
                "label": command["label"],
                "suffix": command["suffix"],
                "start_address": command["start_address"],
                "count": command["count"],
                "function_code": 3,
                "example_hex_slave_1": build_modbus_read_command_hex(
                    slave_id=1,
                    start_address=command["start_address"],
                    count=command["count"],
                ),
            }
            for command in preset["commands"]
        ]
        presets.append(
            {
                "preset_name": preset_name,
                "title": preset["title"],
                "description": preset["description"],
                "commands": commands,
            }
        )
    return presets


def build_cor_bridge_preset_tasks(
    *,
    preset_name: str,
    task_name_prefix: str,
    session_id: str,
    slave_id: int,
    interval_ms: float,
    is_active: bool,
    created_by: str | None,
) -> list[WebSocketBroadcastTaskCreate]:
    preset = COR_BRIDGE_POLLING_PRESETS.get(preset_name)
    if not preset:
        available = ", ".join(sorted(COR_BRIDGE_POLLING_PRESETS.keys()))
        raise HTTPException(status_code=400, detail=f"Unknown preset '{preset_name}'. Available: {available}")

    tasks: list[WebSocketBroadcastTaskCreate] = []
    for command in preset["commands"]:
        tasks.append(
            WebSocketBroadcastTaskCreate(
                task_name=f"{task_name_prefix}:{command['suffix']}",
                session_id=session_id,
                command_type="modbus_read",
                command_name=command["suffix"],
                hex_data=build_modbus_read_command_hex(
                    slave_id=slave_id,
                    start_address=command["start_address"],
                    count=command["count"],
                ),
                interval_ms=interval_ms,
                is_active=is_active,
                created_by=created_by,
            )
        )
    return tasks