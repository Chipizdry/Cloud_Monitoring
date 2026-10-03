"""
RBAC management routes
Role and permission management endpoints (superadmin/admin only)
"""

from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database.db import get_db
from backend.database.models import User, Role, Permission, UserRole, RolePermission
from backend.schemas.rbac import (
    RoleCreate,
    RoleUpdate,
    RoleResponse,
    PermissionCreate,
    PermissionResponse,
    AssignRoleRequest,
    RemoveRoleRequest,
    AssignPermissionToRoleRequest,
    RemovePermissionFromRoleRequest,
    RoleWithPermissionsResponse,
    UserRolesResponse,
    UserPermissionsResponse,
)
from backend.services.user.auth import auth_service
from backend.config.config import settings

router = APIRouter(prefix="/rbac", tags=["RBAC Management"])


async def get_user_by_identifier(
    db: AsyncSession, cor_id: str = None, email: str = None
) -> User:
    """
    Получить пользователя по cor_id или email
    Приоритет у cor_id
    """
    if cor_id:
        query = (
            select(User)
            .options(selectinload(User.user_roles).selectinload(UserRole.role))
            .where(User.cor_id == cor_id)
        )
        user = await db.scalar(query)
        if user:
            return user

    if email:
        query = (
            select(User)
            .options(selectinload(User.user_roles).selectinload(UserRole.role))
            .where(User.email == email)
        )
        user = await db.scalar(query)
        if user:
            return user

    raise HTTPException(status_code=404, detail="User not found")


def require_superadmin(user: User = Depends(auth_service.get_current_user)):
    """Проверка superadmin прав"""
    if user.email not in settings.superadmin_emails:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Superadmin access required",
        )
    return user


def require_admin_or_superadmin(user: User = Depends(auth_service.get_current_user)):
    """Проверка admin или superadmin прав"""
    is_admin = user.has_role("admin") or user.email in settings.superadmin_emails
    if not is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin or superadmin access required",
        )
    return user


# ============= ROLE MANAGEMENT =============


@router.post("/roles", response_model=RoleResponse, status_code=status.HTTP_201_CREATED)
async def create_role(
    role_data: RoleCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin_or_superadmin),
):
    """Создать новую роль (admin/superadmin)"""
    # Проверка существования
    query = select(Role).where(Role.name == role_data.name)
    existing = await db.scalar(query)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Role '{role_data.name}' already exists",
        )

    role = Role(
        name=role_data.name,
        description=role_data.description,
        is_system=False,
    )
    db.add(role)
    await db.commit()
    await db.refresh(role)
    return role


@router.get("/roles", response_model=List[RoleResponse])
async def list_roles(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(auth_service.get_current_user),
):
    """Получить список всех ролей"""
    query = select(Role).order_by(Role.name)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/roles/{role_id}", response_model=RoleWithPermissionsResponse)
async def get_role(
    role_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(auth_service.get_current_user),
):
    """Получить роль с её разрешениями"""
    query = select(Role).where(Role.id == role_id)
    role = await db.scalar(query)
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")

    # Загрузить разрешения
    perms_query = (
        select(Permission)
        .join(RolePermission)
        .where(RolePermission.role_id == role_id)
    )
    perms_result = await db.execute(perms_query)
    permissions = perms_result.scalars().all()

    return {**role.__dict__, "permissions": permissions}


@router.patch("/roles/{role_id}", response_model=RoleResponse)
async def update_role(
    role_id: str,
    role_data: RoleUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin_or_superadmin),
):
    """Обновить роль (admin/superadmin)"""
    query = select(Role).where(Role.id == role_id)
    role = await db.scalar(query)
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")

    if role.is_system:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot modify system role",
        )

    if role_data.description is not None:
        role.description = role_data.description

    await db.commit()
    await db.refresh(role)
    return role


@router.delete("/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_role(
    role_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_superadmin),
):
    """Удалить роль (только superadmin, нельзя удалить system роли)"""
    query = select(Role).where(Role.id == role_id)
    role = await db.scalar(query)
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")

    if role.is_system:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot delete system role",
        )

    await db.delete(role)
    await db.commit()


# ============= PERMISSION MANAGEMENT =============


@router.post(
    "/permissions", response_model=PermissionResponse, status_code=status.HTTP_201_CREATED
)
async def create_permission(
    perm_data: PermissionCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_superadmin),
):
    """Создать новое разрешение (только superadmin)"""
    query = select(Permission).where(Permission.name == perm_data.name)
    existing = await db.scalar(query)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Permission '{perm_data.name}' already exists",
        )

    permission = Permission(
        name=perm_data.name,
        description=perm_data.description,
        resource=perm_data.resource,
        action=perm_data.action,
    )
    db.add(permission)
    await db.commit()
    await db.refresh(permission)
    return permission


@router.get("/permissions", response_model=List[PermissionResponse])
async def list_permissions(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(auth_service.get_current_user),
):
    """Получить список всех разрешений"""
    query = select(Permission).order_by(Permission.name)
    result = await db.execute(query)
    return result.scalars().all()


# ============= USER-ROLE ASSIGNMENT =============


@router.post("/users/assign-role", response_model=dict)
async def assign_role_to_user(
    request: AssignRoleRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin_or_superadmin),
):
    """Назначить роль пользователю"""
    # Поиск пользователя по cor_id или email
    user = await get_user_by_identifier(db, request.user_cor_id, request.user_email)

    # Проверка существования роли
    role = await db.get(Role, request.role_id)
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")

    # Только superadmin может назначать роль admin
    if role.name == "admin" and current_user.email not in settings.superadmin_emails:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only superadmin can assign admin role",
        )

    # Проверка, что роль еще не назначена
    query = select(UserRole).where(
        UserRole.user_id == user.id, UserRole.role_id == request.role_id
    )
    existing = await db.scalar(query)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Role already assigned to user",
        )

    user_role = UserRole(
        user_id=user.id,
        role_id=request.role_id,
        assigned_by=current_user.id,
    )
    db.add(user_role)
    await db.commit()

    return {"message": f"Role '{role.name}' assigned to user {user.cor_id}"}


@router.post("/users/remove-role", response_model=dict)
async def remove_role_from_user(
    request: RemoveRoleRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin_or_superadmin),
):
    """Удалить роль у пользователя"""
    # Поиск пользователя
    user = await get_user_by_identifier(db, request.user_cor_id, request.user_email)

    query = select(UserRole).where(
        UserRole.user_id == user.id, UserRole.role_id == request.role_id
    )
    user_role = await db.scalar(query)
    if not user_role:
        raise HTTPException(status_code=404, detail="User role assignment not found")

    # Проверка роли для безопасности
    role = await db.get(Role, request.role_id)
    if role and role.name == "admin" and current_user.email not in settings.superadmin_emails:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only superadmin can remove admin role",
        )

    await db.delete(user_role)
    await db.commit()

    return {"message": f"Role removed from user {user.cor_id}"}


@router.get("/users/{user_cor_id}/roles", response_model=UserRolesResponse)
async def get_user_roles(
    user_cor_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth_service.get_current_user),
):
    """Получить все роли пользователя"""
    user = await get_user_by_identifier(db, cor_id=user_cor_id)

    return {"user_cor_id": user.cor_id, "roles": user.get_roles()}


@router.get("/users/{user_cor_id}/permissions", response_model=UserPermissionsResponse)
async def get_user_permissions(
    user_cor_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth_service.get_current_user),
):
    """Получить все разрешения пользователя"""
    user = await get_user_by_identifier(db, cor_id=user_cor_id)

    permissions = await user.get_permissions(db)
    return {"user_cor_id": user.cor_id, "permissions": permissions}


# ============= ROLE-PERMISSION ASSIGNMENT =============


# @router.post("/roles/assign-permission", response_model=dict)
# async def assign_permission_to_role(
#     request: AssignPermissionToRoleRequest,
#     db: AsyncSession = Depends(get_db),
#     user: User = Depends(require_superadmin),
# ):
#     """Назначить разрешение роли (только superadmin)"""
#     role = await db.get(Role, request.role_id)
#     if not role:
#         raise HTTPException(status_code=404, detail="Role not found")

#     permission = await db.get(Permission, request.permission_id)
#     if not permission:
#         raise HTTPException(status_code=404, detail="Permission not found")

#     # Проверка существующей связи
#     query = select(RolePermission).where(
#         RolePermission.role_id == request.role_id,
#         RolePermission.permission_id == request.permission_id,
#     )
#     existing = await db.scalar(query)
#     if existing:
#         raise HTTPException(
#             status_code=status.HTTP_400_BAD_REQUEST,
#             detail="Permission already assigned to role",
#         )

#     role_permission = RolePermission(
#         role_id=request.role_id, permission_id=request.permission_id
#     )
#     db.add(role_permission)
#     await db.commit()

#     return {"message": f"Permission '{permission.name}' assigned to role '{role.name}'"}


# @router.post("/roles/remove-permission", response_model=dict)
# async def remove_permission_from_role(
#     request: RemovePermissionFromRoleRequest,
#     db: AsyncSession = Depends(get_db),
#     user: User = Depends(require_superadmin),
# ):
#     """Удалить разрешение у роли (только superadmin)"""
#     query = select(RolePermission).where(
#         RolePermission.role_id == request.role_id,
#         RolePermission.permission_id == request.permission_id,
#     )
#     role_permission = await db.scalar(query)
#     if not role_permission:
#         raise HTTPException(status_code=404, detail="Role permission not found")

#     await db.delete(role_permission)
#     await db.commit()

#     return {"message": "Permission removed from role"}
