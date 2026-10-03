"""
Legacy role checkers - now use access.py classes instead
"""

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import db
from backend.database.models import User
from backend.services.user.auth import get_current_user
from backend.config.config import settings


class UserRoleChecker:
    """Проверка, активен ли пользователь"""

    async def is_active(self, user: User = Depends(get_current_user)):
        return user.is_active


class AdminRoleChecker:
    """
    Проверка прав админа
    """

    async def is_admin(
        self,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(db.get_db),
    ):
        if user.email in settings.superadmin_emails:
            return True

        if user.has_role("admin") or user.has_role("superadmin"):
            return True

        return False


class LawyerRoleChecker:
    """
    Проверка прав юриста
    """

    async def is_lawyer(
        self,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(db.get_db),
    ):
        if user.has_role("lawyer"):
            return True

        return False


class CorIntRoleChecker:
    """Проверка принадлежности к компании COR-INT"""

    async def is_cor_int(self, user: User = Depends(get_current_user)):
        return user.email.endswith("@cor-int.com")


class FinancierRoleChecker:
    """
    Проверка прав финансиста
    """

    async def is_financier(
        self,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(db.get_db),
    ):

        if user.has_role("financier"):
            return True

        return False


class EnergyManagerRoleChecker:
    """
    Проверка прав энергетического менеджера
    """

    async def is_energy_manager(
        self,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(db.get_db),
    ):

        if user.has_role("energy_manager"):
            return True

        return False


# Singleton instances для использования в зависимостях
user_role_checker = UserRoleChecker()
admin_role_checker = AdminRoleChecker()
lawyer_role_checker = LawyerRoleChecker()
cor_int_role_checker = CorIntRoleChecker()
financier_role_checker = FinancierRoleChecker()
energy_manager_role_checker = EnergyManagerRoleChecker()
