from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import db
from backend.database.models import User
from backend.services.user.auth import auth_service
from backend.config.config import settings


class UserAccess:
    """Базовый доступ - только активные пользователи"""

    def __init__(self, active_user=None):
        self.active_user = active_user

    async def __call__(self, user: User = Depends(auth_service.get_current_user)):
        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Account is not active"
            )
        return user


class SuperadminAccess:
    """Доступ только для superadmin из ENV"""

    def __init__(self, email=None):
        self.email = email

    async def __call__(self, user: User = Depends(auth_service.get_current_user)):
        if user.email not in settings.superadmin_emails:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Superadmin access required",
            )
        return user


class AdminAccess:
    """Доступ для admin роли или superadmin"""

    def __init__(self, email=None):
        self.email = email

    async def __call__(
        self,
        user: User = Depends(auth_service.get_current_user),
        db_session: AsyncSession = Depends(db.get_db),
    ):
        # Superadmin всегда имеет доступ
        if user.email in settings.superadmin_emails:
            return user

        # Проверка роли admin через RBAC
        if user.has_role("admin"):
            return user

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required"
        )


class RoleAccess:
    """Проверка наличия определенной роли у пользователя"""

    def __init__(self, required_role: str):
        self.required_role = required_role

    async def __call__(
        self,
        user: User = Depends(auth_service.get_current_user)):
        # Superadmin имеет все роли
        if user.email in settings.superadmin_emails:
            return user

        if not user.has_role(self.required_role):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{self.required_role}' required",
            )
        return user


class PermissionAccess:
    """Проверка наличия определенного разрешения у пользователя"""

    def __init__(self, required_permission: str):
        self.required_permission = required_permission

    async def __call__(
        self,
        user: User = Depends(auth_service.get_current_user),
        db_session: AsyncSession = Depends(db.get_db),
    ):
        # Superadmin имеет все разрешения
        if user.email in settings.superadmin_emails:
            return user

        has_perm = await user.has_permission(self.required_permission, db_session)
        if not has_perm:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission '{self.required_permission}' required",
            )
        return user


class LawyerAccess:
    """Доступ для юристов"""

    def __init__(self, email=None):
        self.email = email

    async def __call__(
        self,
        user: User = Depends(auth_service.get_current_user)
    ):
        # Superadmin/admin всегда имеют доступ
        if user.email in settings.superadmin_emails:
            return user
        if user.has_role("admin"):
            return user

        # Проверка роли lawyer
        if user.has_role("lawyer"):
            return user

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Lawyer access required",
        )


class FinancierAccess:
    """Доступ для финансистов (через роль financier)"""

    def __init__(self, email=None):
        self.email = email

    async def __call__(
        self,
        user: User = Depends(auth_service.get_current_user),
        db_session: AsyncSession = Depends(db.get_db),
    ):
        # Superadmin/admin всегда имеют доступ
        if user.email in settings.superadmin_emails:
            return user
        if user.has_role("admin"):
            return user

        # Проверка роли financier
        if user.has_role("financier"):
            return user

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Financier access required",
        )


class EnergyManagerAccess:
    """Доступ для энергетических менеджеров (через роль energy_manager)"""

    def __init__(self, email=None):
        self.email = email

    async def __call__(
        self,
        user: User = Depends(auth_service.get_current_user)):
        # Superadmin/admin всегда имеют доступ
        if user.email in settings.superadmin_emails:
            return user
        if user.has_role("admin"):
            return user

        # Проверка роли energy_manager
        if user.has_role("energy_manager"):
            return user

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Energy manager access required",
        )


# Экземпляры для удобного использования
user_access = UserAccess()
superadmin_access = SuperadminAccess()
admin_access = AdminAccess()
lawyer_access = LawyerAccess()
financier_access = FinancierAccess()
energy_manager_access = EnergyManagerAccess()
