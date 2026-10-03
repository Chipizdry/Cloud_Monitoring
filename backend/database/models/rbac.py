"""
RBAC (Role-Based Access Control) models
Dynamic roles and permissions system
"""

from sqlalchemy import Column, String, Boolean, ForeignKey, DateTime, Text
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

from .base import Base


class Role(Base):
    """
    Динамическая роль в системе.
    Встроенные роли (is_system=True) защищены от удаления.
    """

    __tablename__ = "roles"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(50), unique=True, nullable=False, index=True)
    description = Column(Text, nullable=True)
    is_system = Column(
        Boolean, default=False, nullable=False
    )  # Встроенные роли (superadmin, admin)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # Relationships
    user_roles = relationship("UserRole", back_populates="role", cascade="all, delete")
    role_permissions = relationship(
        "RolePermission", back_populates="role", cascade="all, delete"
    )


class Permission(Base):
    """
    Разрешение (permission) - атомарное право на действие.
    Например: create_doctor, delete_case, view_reports, manage_roles
    """

    __tablename__ = "permissions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(100), unique=True, nullable=False, index=True)
    description = Column(Text, nullable=True)
    resource = Column(String(50), nullable=True)  # doctor, case, report, role...
    action = Column(String(50), nullable=True)  # create, read, update, delete, manage
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    role_permissions = relationship(
        "RolePermission", back_populates="permission", cascade="all, delete"
    )


class UserRole(Base):
    """
    Связь пользователей с ролями (many-to-many).
    """

    __tablename__ = "user_roles"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    role_id = Column(String(36), ForeignKey("roles.id"), nullable=False)
    assigned_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    assigned_by = Column(String(36), ForeignKey("users.id"), nullable=True)

    # Relationships
    user = relationship("User", foreign_keys=[user_id], back_populates="user_roles")
    role = relationship("Role", back_populates="user_roles")
    assigner = relationship("User", foreign_keys=[assigned_by])


class RolePermission(Base):
    """
    Связь ролей с разрешениями (many-to-many).
    Определяет, какие права имеет каждая роль.
    """

    __tablename__ = "role_permissions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    role_id = Column(String(36), ForeignKey("roles.id"), nullable=False)
    permission_id = Column(String(36), ForeignKey("permissions.id"), nullable=False)
    granted_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    role = relationship("Role", back_populates="role_permissions")
    permission = relationship("Permission", back_populates="role_permissions")
