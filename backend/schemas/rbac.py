"""
RBAC schemas for roles and permissions management
"""

from typing import List, Optional
from datetime import datetime
from pydantic import Field

from backend.schemas.base import BaseSchema


# Role schemas
class RoleCreate(BaseSchema):
    """Создание новой роли"""

    name: str = Field(
        ..., min_length=1, max_length=50, description="Уникальное имя роли"
    )
    description: Optional[str] = Field(None, description="Описание роли")


class RoleUpdate(BaseSchema):
    """Обновление роли"""

    description: Optional[str] = Field(None, description="Описание роли")


class RoleResponse(BaseSchema):
    """Ответ с данными роли"""

    id: str
    name: str
    description: Optional[str]
    is_system: bool
    created_at: datetime
    updated_at: datetime


# Permission schemas
class PermissionCreate(BaseSchema):
    """Создание нового разрешения"""

    name: str = Field(..., min_length=1, max_length=100, description="Имя разрешения")
    description: Optional[str] = Field(None, description="Описание")
    resource: Optional[str] = Field(
        None, max_length=50, description="Ресурс (doctor, case, report...)"
    )
    action: Optional[str] = Field(
        None, max_length=50, description="Действие (create, read, update, delete...)"
    )


class PermissionResponse(BaseSchema):
    """Ответ с данными разрешения"""

    id: str
    name: str
    description: Optional[str]
    resource: Optional[str]
    action: Optional[str]
    created_at: datetime


# User-Role assignment schemas
class AssignRoleRequest(BaseSchema):
    """Назначение роли пользователю"""

    user_cor_id: Optional[str] = Field(None, description="COR ID пользователя")
    user_email: Optional[str] = Field(None, description="Email пользователя")
    role_id: str = Field(..., description="ID роли")

    def model_post_init(self, __context):
        """Проверка, что хотя бы один идентификатор указан"""
        if not self.user_cor_id and not self.user_email:
            raise ValueError("Either user_cor_id or user_email must be provided")


class RemoveRoleRequest(BaseSchema):
    """Удаление роли у пользователя"""

    user_cor_id: Optional[str] = Field(None, description="COR ID пользователя")
    user_email: Optional[str] = Field(None, description="Email пользователя")
    role_id: str = Field(..., description="ID роли")

    def model_post_init(self, __context):
        """Проверка, что хотя бы один идентификатор указан"""
        if not self.user_cor_id and not self.user_email:
            raise ValueError("Either user_cor_id or user_email must be provided")


class UserRoleResponse(BaseSchema):
    """Ответ с информацией о назначенной роли"""

    id: str
    user_id: str
    role_id: str
    assigned_at: datetime
    role: RoleResponse


# Role-Permission assignment schemas
class AssignPermissionToRoleRequest(BaseSchema):
    """Назначение разрешения роли"""

    role_id: str = Field(..., description="ID роли")
    permission_id: str = Field(..., description="ID разрешения")


class RemovePermissionFromRoleRequest(BaseSchema):
    """Удаление разрешения у роли"""

    role_id: str = Field(..., description="ID роли")
    permission_id: str = Field(..., description="ID разрешения")


class RoleWithPermissionsResponse(RoleResponse):
    """Роль с её разрешениями"""

    permissions: List[PermissionResponse] = []


class UserRolesResponse(BaseSchema):
    """Список ролей пользователя"""

    user_cor_id: str
    roles: List[str] = Field(description="Список имён ролей")


class UserPermissionsResponse(BaseSchema):
    """Список разрешений пользователя"""

    user_cor_id: str
    permissions: List[str] = Field(description="Список имён разрешений")
