"""
RBAC initialization script
Creates default system roles and optionally permissions
Run this after database migration to set up initial RBAC structure
"""

import asyncio
import sys
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database.db import async_session_maker
from backend.database.models import Role, Permission, RolePermission
from loguru import logger


SYSTEM_ROLES = [
    {
        "name": "superadmin",
        "description": "Суперадминистратор с полными правами (назначается через SUPERADMIN_EMAILS в ENV)",
        "is_system": True,
    },
    {
        "name": "admin",
        "description": "Администратор системы, может управлять ролями и пользователями",
        "is_system": True,
    },
    {
        "name": "lawyer",
        "description": "Юрист, доступ к юридическим функциям",
        "is_system": False,
    },
    {
        "name": "financier",
        "description": "Финансист, доступ к финансовым операциям",
        "is_system": False,
    },
    {
        "name": "energy_manager",
        "description": "Менеджер по энергетике, доступ к энергетическим операциям",
        "is_system": False,
    },
]

# Примерные разрешения
EXAMPLE_PERMISSIONS = [
    {
        "name": "manage_users",
        "description": "Управление пользователями",
        "resource": "user",
        "action": "manage",
    },
    {
        "name": "approve_doctor",
        "description": "Одобрение врачей",
        "resource": "doctor",
        "action": "approve",
    },
    {
        "name": "manage_roles",
        "description": "Управление ролями и разрешениями",
        "resource": "role",
        "action": "manage",
    },
]


async def create_roles(db: AsyncSession, create_permissions: bool = False):
    """Создание системных ролей"""
    created_roles = []

    for role_data in SYSTEM_ROLES:
        # Проверка существования
        query = select(Role).where(Role.name == role_data["name"])
        existing_role = await db.scalar(query)

        if existing_role:
            logger.info(f"Role '{role_data['name']}' already exists, skipping")
            created_roles.append(existing_role)
            continue

        # Создание роли
        role = Role(
            name=role_data["name"],
            description=role_data["description"],
            is_system=role_data["is_system"],
        )
        db.add(role)
        await db.flush()
        created_roles.append(role)
        logger.success(f"Created role: {role_data['name']}")

    await db.commit()

    # Создание примерных разрешений (опционально)
    if create_permissions:
        await create_example_permissions(db, created_roles)

    return created_roles


async def create_example_permissions(db: AsyncSession, roles: list[Role]):
    """Создание примерных разрешений и назначение админу"""
    created_permissions = []

    for perm_data in EXAMPLE_PERMISSIONS:
        # Проверка существования
        query = select(Permission).where(Permission.name == perm_data["name"])
        existing_perm = await db.scalar(query)

        if existing_perm:
            logger.info(f"Permission '{perm_data['name']}' already exists, skipping")
            created_permissions.append(existing_perm)
            continue

        # Создание разрешения
        permission = Permission(
            name=perm_data["name"],
            description=perm_data["description"],
            resource=perm_data["resource"],
            action=perm_data["action"],
        )
        db.add(permission)
        await db.flush()
        created_permissions.append(permission)
        logger.success(f"Created permission: {perm_data['name']}")

    await db.commit()

    # Назначить все разрешения роли admin
    admin_role = next((r for r in roles if r.name == "admin"), None)
    if admin_role:
        for permission in created_permissions:
            # Проверка существования связи
            query = select(RolePermission).where(
                RolePermission.role_id == admin_role.id,
                RolePermission.permission_id == permission.id,
            )
            existing = await db.scalar(query)

            if not existing:
                role_perm = RolePermission(
                    role_id=admin_role.id, permission_id=permission.id
                )
                db.add(role_perm)

        await db.commit()
        logger.success(f"Assigned all permissions to 'admin' role")


async def init_rbac(create_permissions: bool = False):
    """Главная функция инициализации RBAC"""
    logger.info("Starting RBAC initialization...")

    async with async_session_maker() as db:
        try:
            roles = await create_roles(db, create_permissions)
            logger.success(
                f"✅ RBAC initialization complete! Created {len(roles)} roles"
            )

            if create_permissions:
                logger.info("Example permissions created and assigned to admin role")

            return True
        except Exception as e:
            logger.error(f"❌ RBAC initialization failed: {e}")
            await db.rollback()
            return False


async def main():
    """Entry point"""
    # Можно передать аргумент для создания примерных разрешений
    create_perms = "--with-permissions" in sys.argv or "-p" in sys.argv

    success = await init_rbac(create_permissions=create_perms)

    if success:
        logger.info("🎉 You can now:")
        logger.info("  1. Set SUPERADMIN_EMAILS in .env")
        logger.info("  2. Use /api/rbac endpoints to manage roles")
        logger.info("  3. Assign roles to users via API")
    else:
        logger.error("Initialization failed. Check logs above.")
        sys.exit(1)


if __name__ == "__main__":
    print("=" * 60)
    print("RBAC Initialization Script")
    print("=" * 60)
    print("Usage:")
    print("  python -m backend.services.shared.init_rbac")
    print("  python -m backend.services.shared.init_rbac --with-permissions")
    print("=" * 60)

    asyncio.run(main())
