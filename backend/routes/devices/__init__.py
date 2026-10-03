"""
Devices Domain Routes

"""
from fastapi import APIRouter

from .energetic_device import router as device_proxy_router
from .websocket import router as websocket_router


router = APIRouter()

router.include_router(device_proxy_router)
router.include_router(websocket_router)

