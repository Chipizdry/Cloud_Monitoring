


from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pathlib import Path
from functools import lru_cache
import json

router = APIRouter(prefix="/Vendor_schemas", tags=["Vendor_Schemas"])

# 🔥 БАЗОВЫЙ ПУТЬ (относительный!)
BASE_PATH = Path(__file__).resolve().parents[2] / "DevicesSchemas"

# =========================================================
# 🧠 ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =========================================================

def get_vendor_path(vendor: str) -> Path:
    return BASE_PATH / vendor


def get_model_path(vendor: str, model: str) -> Path:
    return BASE_PATH / vendor / f"{model}.json"


# =========================================================
# 📌 1. СПИСОК ВЕНДОРОВ
# =========================================================

@lru_cache
def _list_vendors():
    if not BASE_PATH.exists():
        return []

    return sorted([
        p.name for p in BASE_PATH.iterdir()
        if p.is_dir()
    ])


@router.get("/vendors")
async def get_vendors():
    return _list_vendors()


# =========================================================
# 📌 2. СПИСОК МОДЕЛЕЙ ВЕНДОРА
# =========================================================

@lru_cache
def _list_models(vendor: str):
    vendor_path = get_vendor_path(vendor)

    if not vendor_path.exists():
        return []

    return sorted([
        f.stem for f in vendor_path.glob("*.json")
        if f.is_file() and f.name != "default.json"
    ])


@router.get("/models")
async def get_models(vendor: str):
    return _list_models(vendor)


# =========================================================
# 📌 3. ПОЛУЧИТЬ СХЕМУ МОДЕЛИ
# =========================================================

@router.get("/{vendor}/{model}")
async def get_schema(vendor: str, model: str):
    file_path = get_model_path(vendor, model)

    # 🔁 fallback на default.json
    if not file_path.exists():
        file_path = get_vendor_path(vendor) / "default.json"

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Schema not found")

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =========================================================
# 📌 4. (ОПЦИОНАЛЬНО) META: всё сразу
# =========================================================

@router.get("/meta")
async def get_meta():
    result = {}

    for vendor in _list_vendors():
        result[vendor] = _list_models(vendor)

    return result