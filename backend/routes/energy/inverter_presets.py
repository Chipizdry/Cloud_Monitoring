"""
Inverter Bridge Presets API
Просмотр JSON-пресетов фоновых Modbus-команд для инверторов, опрашиваемых через COR Bridge.

Структура аналогична /Vendor_schemas — вендор / модель / пресет.
"""

from fastapi import APIRouter, HTTPException
from loguru import logger

from backend.services.energy.inverter_preset_loader import (
    PRESETS_BASE_PATH,
    describe_preset,
    has_preset,
    list_preset_models,
    list_preset_vendors,
)
from backend.services.shared.access import user_access
from fastapi import Depends

router = APIRouter(
    prefix="/inverter-presets",
    tags=["Inverter Bridge Presets"],
    dependencies=[Depends(user_access)],
)


@router.get(
    "/vendors",
    summary="Список вендоров инверторов",
    description="Возвращает список вендоров, для которых существуют пресеты фоновых Modbus-команд через COR Bridge.",
)
async def get_preset_vendors() -> list[str]:
    return list_preset_vendors()


@router.get(
    "/vendors/{vendor}",
    summary="Список моделей вендора",
    description="Возвращает список конкретных моделей инверторов, для которых есть пресеты. Если модели нет в списке, но есть 'default' пресет вендора — он будет применён автоматически.",
)
async def get_preset_models(vendor: str) -> dict:
    models = list_preset_models(vendor)
    vendors = list_preset_vendors()

    if vendor not in vendors:
        raise HTTPException(status_code=404, detail=f"Vendor '{vendor}' has no presets")

    return {
        "vendor": vendor,
        "has_default": (PRESETS_BASE_PATH / vendor / "default.json").exists(),
        "models": models,
    }


@router.get(
    "/vendors/{vendor}/{model}",
    summary="Получить пресет для модели инвертора",
    description=(
        "Возвращает детальное описание пресета со списком команд и примерами (slave_id=1). "
        "Если файл модели не найден — используется default.json вендора."
    ),
)
async def get_preset_for_model(vendor: str, model: str) -> dict:
    preset = describe_preset(vendor, model)
    if preset is None:
        raise HTTPException(
            status_code=404,
            detail=f"No inverter bridge preset found for vendor='{vendor}' model='{model}'",
        )
    return preset


@router.get(
    "/meta",
    summary="Все вендоры и модели",
    description="Возвращает сводку всех доступных вендоров и моделей с флагом наличия default-пресета.",
)
async def get_preset_meta() -> dict:
    vendors = list_preset_vendors()
    result: dict[str, dict] = {}
    for vendor in vendors:
        models = list_preset_models(vendor)
        has_default = (PRESETS_BASE_PATH / vendor / "default.json").exists()
        result[vendor] = {
            "has_default": has_default,
            "models": models,
        }
    return result


@router.get(
    "/check/{vendor}/{model}",
    summary="Проверить наличие пресета",
    description="Быстрая проверка: существует ли пресет для данного вендора/модели.",
)
async def check_preset_exists(vendor: str, model: str) -> dict:
    return {
        "vendor": vendor,
        "model": model,
        "has_preset": has_preset(vendor, model),
    }
