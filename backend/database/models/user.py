"""
User Domain Models
User accounts, authentication, sessions, records, and settings
"""

import uuid
from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    func,
)
from sqlalchemy.orm import relationship

from .base import Base
from .enums import AuthSessionStatus


class User(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    cor_id = Column(
        String(250), nullable=True, index=True
    )  # Unique constraint реализован через partial index в миграции
    email = Column(String(250), unique=True, nullable=False)
    backup_email = Column(String(250), unique=True, nullable=True)
    password = Column(String(250), nullable=False)
    last_password_change = Column(DateTime, server_default=func.now())
    access_token = Column(String(500), nullable=True)
    refresh_token = Column(String(500), nullable=True)
    recovery_code = Column(
        LargeBinary, nullable=True
    )  # Уникальный код восстановление пользователя
    unique_cipher_key = Column(String(250), nullable=False)
    is_active = Column(Boolean, default=True)
    user_sex = Column(String(10), nullable=True)
    birth = Column(Integer, nullable=True)
    user_index = Column(
        Integer, unique=True, nullable=True
    )  # индекс пользователя, используется в создании cor_id
    created_at = Column(DateTime, nullable=False, default=func.now())
    last_activity = Column(DateTime, nullable=True)

    user_sessions = relationship(
        "UserSession", back_populates="user", cascade="all, delete-orphan"
    )

    profile = relationship(
        "Profile", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )

    # RBAC relationships
    user_roles = relationship(
        "UserRole",
        back_populates="user",
        foreign_keys="[UserRole.user_id]",
        cascade="all, delete-orphan",
    )
    # Энергетические устройства
    energetic_devices = relationship("EnergeticDevice", back_populates="owner")
    energetic_objects = relationship("EnergeticObject", back_populates="owner")
    energetic_granted_accesses = relationship(
        "EnergeticDeviceAccess",
        foreign_keys="[EnergeticDeviceAccess.granting_user_cor_id]",
        back_populates="granting_user",
    )
    energetic_received_accesses = relationship(
        "EnergeticDeviceAccess",
        foreign_keys="[EnergeticDeviceAccess.accessing_user_cor_id]",
        back_populates="accessing_user",
    )
    energetic_object_granted_accesses = relationship(
        "EnergeticObjectAccess",
        foreign_keys="[EnergeticObjectAccess.granting_user_cor_id]",
        back_populates="granting_user",
    )
    energetic_object_received_accesses = relationship(
        "EnergeticObjectAccess",
        foreign_keys="[EnergeticObjectAccess.accessing_user_cor_id]",
        back_populates="accessing_user",
    )

    # Индексы
    __table_args__ = (
        Index("idx_users_email", "email"),
        Index("idx_users_cor_id", "cor_id"),
        Index("users_cor_id_key", "cor_id", unique=True),
    )

    def has_role(self, role_name: str) -> bool:
        """Проверка наличия роли у пользователя"""
        return any(ur.role.name == role_name for ur in self.user_roles)

    def get_roles(self) -> list[str]:
        """Получить список всех ролей пользователя"""
        return [ur.role.name for ur in self.user_roles]

    async def has_permission(self, permission_name: str, db) -> bool:
        """Проверка наличия разрешения у пользователя через его роли"""
        from sqlalchemy import select
        from .rbac import RolePermission, Permission

        for user_role in self.user_roles:
            query = (
                select(Permission)
                .join(RolePermission)
                .where(
                    RolePermission.role_id == user_role.role_id,
                    Permission.name == permission_name,
                )
            )
            result = await db.execute(query)
            if result.scalar_one_or_none():
                return True
        return False

    async def get_permissions(self, db) -> list[str]:
        """Получить список всех разрешений пользователя"""
        from sqlalchemy import select
        from .rbac import RolePermission, Permission

        permissions = set()
        for user_role in self.user_roles:
            query = (
                select(Permission.name)
                .join(RolePermission)
                .where(RolePermission.role_id == user_role.role_id)
            )
            result = await db.execute(query)
            permissions.update(result.scalars().all())
        return list(permissions)


class UserSession(Base):
    __tablename__ = "user_sessions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.cor_id"), nullable=False)
    device_type = Column(String(250), nullable=True)
    device_info = Column(String(250), nullable=True)
    app_id = Column(String(250), nullable=True)  # Идентификатор апки
    device_id = Column(String(250), nullable=True)  # айди устройства
    ip_address = Column(String(250), nullable=True)
    device_os = Column(String(250), nullable=True)
    jti = Column(
        String,
        unique=True,
        nullable=True,
        comment="JTI последнего Access токена, выданного для этой сессии",
    )
    refresh_token = Column(LargeBinary, nullable=True)
    access_token = Column(LargeBinary, nullable=True)
    created_at = Column(DateTime, nullable=False, default=func.now())
    updated_at = Column(
        DateTime, nullable=False, default=func.now(), onupdate=func.now()
    )

    # Связи
    user = relationship("User", back_populates="user_sessions")

    # Индексы
    __table_args__ = (Index("idx_user_sessions_user_id", "user_id"),)


class CorIdAuthSession(Base):
    __tablename__ = "cor_id_auth_sessions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email = Column(String(255), index=True, nullable=True)
    cor_id = Column(String(250), index=True, nullable=True)
    session_token = Column(String(36), unique=True, index=True, nullable=False)
    app_id = Column(String(250), nullable=True)
    device_id = Column(String(250), nullable=True)
    status = Column(
        Enum(AuthSessionStatus), default=AuthSessionStatus.PENDING, nullable=False
    )
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, nullable=False, default=func.now())

    __table_args__ = (Index("idx_cor_id_auth_sessions_token", "session_token"),)


class Verification(Base):
    __tablename__ = "verification"
    id = Column(Integer, primary_key=True)
    email = Column(String(250), unique=True, nullable=False)
    verification_code = Column(Integer, default=None)
    email_confirmation = Column(Boolean, default=False)

    # Индексы
    __table_args__ = (Index("idx_verification_email", "email"),)


class Profile(Base):
    """
    Профиль пользователя - расширенная информация о пользователе
    """

    __tablename__ = "profiles"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id"), unique=True, nullable=False)

    # Зашифрованные персональные данные
    encrypted_surname = Column(LargeBinary, nullable=True)
    encrypted_first_name = Column(LargeBinary, nullable=True)
    encrypted_middle_name = Column(LargeBinary, nullable=True)

    # Основная информация
    birth_date = Column(Date, nullable=True)
    phone_number = Column(String(20), nullable=True)
    city = Column(String(100), nullable=True)

    # Информация об автомобиле
    car_brand = Column(String(100), nullable=True)
    engine_type = Column(String(50), nullable=True)
    fuel_tank_volume = Column(Integer, nullable=True)

    # Фото профиля
    photo_data = Column(LargeBinary, nullable=True)
    photo_file_type = Column(String, nullable=True)

    # Временные метки
    change_date = Column(DateTime, default=func.now(), onupdate=func.now())
    create_date = Column(DateTime, default=func.now())

    # Relationships
    user = relationship("User", back_populates="profile")

    def __repr__(self):
        return f"<Profile(id='{self.id}', user_id='{self.user_id}')>"
