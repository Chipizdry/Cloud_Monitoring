"""
Inverter Bridge Preset Loader
Загружает JSON-пресеты для фоновых и вызываемых по запросу команд через COR Bridge.

Структура файлов:
    backend/InverterBridgePresets/{Vendor}/{Model}.json
    backend/InverterBridgePresets/{Vendor}/default.json  ← fallback

Формат JSON-пресета:
{
    "title": "...",
    "description": "...",
    "interval_ms": 5000,
    "commands": [  // фоновые команды
        {
            "suffix":        "grid",   // уникальный суффикс команды внутри пресета
            "label":         "Grid",   // человекочитаемое название
            "command_type":  "modbus_read", // modbus_read (по умолчанию), modbus_tcp или pi30
            "function_code": 3,        // Modbus function code
            "start_address": 598,      // начальный регистр (decimal)
            "count":         23        // количество регистров (для modbus_read)
        },
        {
            "suffix":       "dc_status",
            "label":        "DC status",
            "command_type": "pi30",
            "pi30_command": "FWDCSTATUS"
        },
        {
            "suffix":       "clear",
            "label":        "Clear buffer",
            "command_type": "pi30",
            "pi30_hex":     "0D" // raw PI30 hex payload, sent without CRC formatting
        },
        ...
    ],
    "on_demand_commands": [
        {
            "name": "QPIRI",  // имя для API
            "label": "Device rating information",
            "command_type": "pi30",
            "pi30_command": "QPIRI"
        },
        {
            "name": "PGR",
            "label": "Example setting command",
            "command_type": "pi30",
            "pi30_command": "PGR{value}"  // value обязательно только для команды с шаблоном
        }
    ]
}
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Optional

from loguru import logger

from backend.services.energy.cor_bridge_broadcast_presets import (
    build_modbus_read_command_hex,
)
from backend.services.energy.pi30_commands import format_pi30_command_with_crc_hex

PRESETS_BASE_PATH = Path(__file__).parent.parent.parent / "InverterBridgePresets"


def list_preset_vendors() -> list[str]:
    """Вернуть отсортированный список вендоров, для которых есть пресеты."""
    if not PRESETS_BASE_PATH.exists():
        return []
    return sorted(p.name for p in PRESETS_BASE_PATH.iterdir() if p.is_dir())


def list_preset_models(vendor: str) -> list[str]:
    """Вернуть отсортированный список моделей для вендора (без default)."""
    vendor_path = PRESETS_BASE_PATH / vendor
    if not vendor_path.exists():
        return []
    return sorted(
        f.stem
        for f in vendor_path.glob("*.json")
        if f.is_file() and f.name != "default.json"
    )


def has_preset(vendor: str, model: str) -> bool:
    """Проверить, существует ли пресет (model-specific или default) для вендора/модели."""
    vendor_path = PRESETS_BASE_PATH / vendor
    return (
        (vendor_path / f"{model}.json").exists()
        or (vendor_path / "default.json").exists()
    )

@lru_cache(maxsize=128)
def _load_preset_file(path: str) -> Optional[dict]:
    """Загрузить JSON-файл пресета. Аргумент — строка пути для совместимости с lru_cache."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.error(f"Failed to load inverter preset file '{path}': {exc}")
        return None


def get_preset(vendor: str, model: str) -> Optional[dict]:
    """
    Загрузить пресет для вендора/модели с fallback на default.json.

    Returns:
        dict с полями title, description, interval_ms, commands — или None если пресет не найден.
    """
    if (
        not vendor or not model
        or Path(vendor).name != vendor or Path(model).name != model
        or vendor in {".", ".."} or model in {".", ".."}
    ):
        return None
    vendor_path = PRESETS_BASE_PATH / vendor
    model_path = vendor_path / f"{model}.json"
    default_path = vendor_path / "default.json"

    for path in (model_path, default_path):
        if path.exists():
            preset = _load_preset_file(str(path))
            if preset is not None:
                return preset

    logger.warning(f"No inverter bridge preset found for vendor='{vendor}' model='{model}'")
    return None


def get_on_demand_command(vendor: str, model: str, name: str) -> Optional[dict]:
    """Find an explicitly allowed command by name in the model's preset."""
    preset = get_preset(vendor, model)
    if not preset:
        return None
    for command in preset.get("on_demand_commands", []):
        if command.get("name") == name:
            return command
    return None


class OnDemandValueError(ValueError):
    """The caller did not provide a usable value for a preset template."""


def on_demand_command_uses_value(command: dict) -> bool:
    return command.get("pi30_hex") is None and "{value}" in str(command.get("pi30_command") or "")


def build_on_demand_payload(command: dict, value: str | int | float | None = None) -> dict:
    """Build the bridge wire payload for a named PI30 command."""
    if command.get("command_type") != "pi30":
        raise ValueError("Unsupported on-demand command type")
    pi30_command = command.get("pi30_command")
    pi30_hex = command.get("pi30_hex")
    if pi30_hex is not None:
        try:
            payload_bytes = bytes.fromhex(str(pi30_hex))
        except ValueError as exc:
            raise ValueError("Invalid PI30 hex in on-demand preset") from exc
        if not payload_bytes:
            raise ValueError("Empty PI30 hex in on-demand preset")
        payload = payload_bytes.hex(" ").upper()
    elif isinstance(pi30_command, str) and pi30_command:
        if on_demand_command_uses_value(command):
            if value is None or isinstance(value, bool):
                raise OnDemandValueError("Value is required for this command")
            if isinstance(value, float) and not math.isfinite(value):
                raise OnDemandValueError("Value must be finite")
            value_text = str(value)
            if not value_text.strip() or len(value_text) > 64 or any(ord(char) < 32 or ord(char) > 126 for char in value_text):
                raise OnDemandValueError("Value must be 1–64 printable ASCII characters")
            pi30_command = pi30_command.replace("{value}", value_text)
        if any(ord(char) < 32 or ord(char) > 126 for char in pi30_command):
            raise ValueError("PI30 command must contain printable ASCII only")
        if "{" in pi30_command or "}" in pi30_command:
            raise ValueError("Unknown PI30 command placeholder")
        payload = format_pi30_command_with_crc_hex(pi30_command)
    else:
        raise ValueError("Missing or invalid PI30 command in on-demand preset")
    return {"command_type": "pi30", "pi30": payload, "command_name": command["name"]}


def build_broadcast_task_records(
    *,
    preset: dict,
    session_id: str,
    slave_id: int,
    host: Optional[str],
    port: Optional[int],
    task_name_prefix: str,
    created_by: Optional[str],
    is_active: bool = True,
) -> list[dict]:
    """
    Собрать список словарей для создания WebSocketBroadcastTask записей в БД.

    Args:
        preset:           загруженный пресет (результат get_preset).
        session_id:       device_id / session_id COR Bridge.
        slave_id:         Modbus RTU slave ID (1..247).
        task_name_prefix: префикс для task_name — к нему добавляется суффикс команды.
        created_by:       cor_id владельца, для трассировки.
        is_active:        запускать ли задачу сразу.

    Returns:
        Список dict — каждый соответствует одной WebSocketBroadcastTask строке в БД.
    """
    commands: list[dict] = preset.get("commands", [])
    interval_ms: float = float(preset.get("interval_ms", 5000))
    records: list[dict] = []

    for cmd in commands:
        suffix = cmd.get("suffix", "cmd")
        command_type = str(cmd.get("command_type", "modbus_read"))

        if command_type == "pi30":
            pi30_hex = cmd.get("pi30_hex")
            pi30_command = str(cmd.get("pi30_command", "")).strip()
            if pi30_hex is None and not pi30_command:
                logger.warning(
                    f"Skipping pi30 preset command '{suffix}' because 'pi30_command' or 'pi30_hex' is missing"
                )
                continue

            formatted_payload = str(pi30_hex).strip() if pi30_hex is not None else format_pi30_command_with_crc_hex(pi30_command)
            if not formatted_payload:
                logger.warning(
                    f"Skipping pi30 preset command '{suffix}' because formatted PI30 payload is empty"
                )
                continue

            records.append(
                {
                    "task_name": f"{task_name_prefix}:{suffix}",
                    "session_id": session_id,
                    "command_type": "pi30",
                    "command_payload": {
                        "pi30": formatted_payload,
                        "command_name": suffix,
                    },
                    "interval_ms": interval_ms,
                    "is_active": is_active,
                    "created_by": created_by,
                }
            )
            continue

        function_code = int(cmd.get("function_code", 3))
        start_address = int(cmd["start_address"])

        if command_type == "modbus_tcp":
            quantity = int(cmd.get("quantity", cmd.get("count", 1)))
            value = int(cmd.get("value", 0))
            unit_id_raw = cmd.get("unit_id", None)
            if unit_id_raw in (None, ""):
                unit_id = int(slave_id)
            else:
                unit_id = int(unit_id_raw)
            target_port = int(port or 502)

            if not host:
                logger.warning(
                    f"Skipping modbus_tcp preset command '{suffix}' because energetic object has no ip_address"
                )
                continue

            records.append(
                {
                    "task_name": f"{task_name_prefix}:{suffix}",
                    "session_id": session_id,
                    "command_type": "modbus_tcp",
                    "command_payload": {
                        "ip": host,
                        "port": target_port,
                        "unit_id": unit_id,
                        "func": function_code,
                        "start_addr": start_address,
                        "quantity": quantity,
                        "value": value,
                        "command_name": suffix,
                    },
                    "interval_ms": interval_ms,
                    "is_active": is_active,
                    "created_by": created_by,
                }
            )
            continue

        count = int(cmd["count"])
        hex_data = build_modbus_read_command_hex(
            slave_id=slave_id,
            start_address=start_address,
            count=count,
            function_code=function_code,
        )

        records.append(
            {
                "task_name": f"{task_name_prefix}:{suffix}",
                "session_id": session_id,
                "command_type": "modbus_read",
                "command_payload": {
                    "hex_data": hex_data,
                    "command_name": suffix,
                },
                "interval_ms": interval_ms,
                "is_active": is_active,
                "created_by": created_by,
            }
        )

    return records



def describe_preset(vendor: str, model: str) -> Optional[dict]:
    """
    Вернуть описание пресета
    """
    preset = get_preset(vendor, model)
    if preset is None:
        return None

    commands_described = []
    for cmd in preset.get("commands", []):
        command_type = str(cmd.get("command_type", "modbus_read"))

        if command_type == "pi30":
            pi30_hex = cmd.get("pi30_hex")
            pi30_command = str(cmd.get("pi30_command", "")).strip()
            example_hex = str(pi30_hex).strip() if pi30_hex is not None else (
                format_pi30_command_with_crc_hex(pi30_command) if pi30_command else None
            )
            commands_described.append(
                {
                    "suffix": cmd.get("suffix"),
                    "label": cmd.get("label"),
                    "command_type": command_type,
                    "pi30_command": pi30_command,
                    "pi30_hex": str(pi30_hex).strip() if pi30_hex is not None else None,
                    "example_hex_with_crc": example_hex,
                }
            )
            continue

        function_code = int(cmd.get("function_code", 3))
        start_address = int(cmd["start_address"])
        if command_type == "modbus_tcp":
            quantity = int(cmd.get("quantity", cmd.get("count", 1)))
            commands_described.append(
                {
                    "suffix": cmd.get("suffix"),
                    "label": cmd.get("label"),
                    "command_type": command_type,
                    "function_code": function_code,
                    "start_address": start_address,
                    "quantity": quantity,
                    "unit_id": int(cmd.get("unit_id", 1)),
                    "value": int(cmd.get("value", 0)),
                }
            )
            continue

        count = int(cmd["count"])
        example_hex = build_modbus_read_command_hex(
            slave_id=1,
            start_address=start_address,
            count=count,
            function_code=function_code,
        )
        commands_described.append(
            {
                "suffix": cmd.get("suffix"),
                "label": cmd.get("label"),
                "command_type": command_type,
                "function_code": function_code,
                "start_address": start_address,
                "count": count,
                "example_hex_slave_1": example_hex,
            }
        )

    return {
        "vendor": vendor,
        "model": model,
        "title": preset.get("title"),
        "description": preset.get("description"),
        "interval_ms": preset.get("interval_ms", 5000),
        "commands": commands_described,
        "on_demand_commands": [
            {
                "name": cmd.get("name"),
                "label": cmd.get("label"),
                "command_type": cmd.get("command_type"),
                "requires_value": on_demand_command_uses_value(cmd),
            }
            for cmd in preset.get("on_demand_commands", [])
        ],
    }
