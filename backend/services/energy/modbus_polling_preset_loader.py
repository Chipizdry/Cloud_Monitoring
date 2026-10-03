"""
Modbus Polling Preset Loader
Загружает JSON-пресеты для прямого Modbus-опроса устройств по вендору/модели инвертора.

Структура файлов:
    backend/ModbusPollingPresets/{Vendor}/{Model}.json
    backend/ModbusPollingPresets/{Vendor}/default.json  ← fallback

Формат JSON-пресета:
{
    "title": "...",
    "description": "...",
    "modbus_config_file": "deye_inverter.json",
    "tasks": [
        {
            "task_type": "modbus_registers",
            "command_config": {"register_groups": ["group1", "group2"]},
            "interval_ms": 15000
        },
        ...
    ]
}
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

from loguru import logger

PRESETS_BASE_PATH = Path(__file__).parent.parent.parent / "ModbusPollingPresets"


def has_polling_preset(vendor: str, model: str) -> bool:
    """Проверить, существует ли пресет (model-specific или default) для вендора/модели."""
    vendor_path = PRESETS_BASE_PATH / vendor
    return (
        (vendor_path / f"{model}.json").exists()
        or (vendor_path / "default.json").exists()
    )


@lru_cache(maxsize=128)
def _load_preset_file(path: str) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.error(f"Failed to load modbus polling preset file '{path}': {exc}")
        return None


def get_polling_preset(vendor: str, model: str) -> Optional[dict]:
    """
    Загрузить пресет прямого опроса для вендора/модели с fallback на default.json.

    Returns:
        dict с полями title, description, modbus_config_file, tasks — или None если не найден.
    """
    vendor_path = PRESETS_BASE_PATH / vendor
    model_path = vendor_path / f"{model}.json"
    default_path = vendor_path / "default.json"

    for path in (model_path, default_path):
        if path.exists():
            preset = _load_preset_file(str(path))
            if preset is not None:
                return preset

    logger.warning(f"No modbus polling preset found for vendor='{vendor}' model='{model}'")
    return None


def build_polling_task_records(preset: dict) -> list[dict]:
    """
    Собрать список словарей для создания DevicePollingTask записей из пресета.

    Returns:
        Список dict — каждый соответствует одной DevicePollingTask строке в БД.
    """
    records = []
    for task in preset.get("tasks", []):
        records.append(
            {
                "task_type": task["task_type"],
                "command_config": task.get("command_config", {}),
                "interval_ms": float(task.get("interval_ms", 15000)),
                "is_active": True,
            }
        )
    return records
