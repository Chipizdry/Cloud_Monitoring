"""
Energy Domain Routes
"""
from fastapi import APIRouter

from .cerbo_routes import router as cerbo_router
from .modbus_tcp import router as modbus_tcp_router
from .modbus_routes import router as modbus_router
from .polling_tasks import router as polling_tasks_router
from .energetic_objects import router as energetic_objects_router
from .energetic_schedules import router as energetic_schedules_router
from .inverter_presets import router as inverter_presets_router
from .flywheel_data import router as flywheel_data_router
from .demo_solar_battery import router as demo_solar_battery_router
from .demo_solar_battery_v2 import router as demo_solar_battery_v2_router


router = APIRouter()

router.include_router(cerbo_router)
router.include_router(modbus_tcp_router)
router.include_router(modbus_router)
router.include_router(polling_tasks_router)
router.include_router(energetic_objects_router)
router.include_router(energetic_schedules_router)
router.include_router(inverter_presets_router)
router.include_router(flywheel_data_router)
router.include_router(demo_solar_battery_router)
router.include_router(demo_solar_battery_v2_router)
