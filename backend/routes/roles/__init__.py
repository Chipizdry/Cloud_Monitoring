"""
Roles Domain Routes
"""

from fastapi import APIRouter

from .rbac import router as rbac_router


router = APIRouter()

router.include_router(rbac_router)