"""
Shared/Common Domain Routes
"""

from fastapi import APIRouter

from .admin import router as admin_router
from .websocket import router as websocket_router
from .websocket_events import router as websocket_events_router
from .support import router as support_router
from .node_info import router as node_info_router

router = APIRouter()

router.include_router(admin_router)
router.include_router(websocket_router)
router.include_router(websocket_events_router)
router.include_router(support_router)
router.include_router(node_info_router)
