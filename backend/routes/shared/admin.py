from typing import Dict, List
from fastapi import APIRouter, HTTPException, Depends, Response, status
from backend.database.db import get_db
from backend.services.shared.websocket_events_manager import websocket_events_manager
from backend.services.user.auth import auth_service
from backend.database.models import User
from backend.services.shared.access import admin_access
from backend.schemas import (
    FullUserInfoResponse,
    NewUserRegistration,
    ProfileResponse,
    UserDataResponse,
    UserDb,
    UserProfileResponseForAdmin,
    UserRolesResponseForAdmin,
    UserDbResponse,
)
from backend.repository.user import person
from pydantic import EmailStr
from backend.database.redis_db import redis_client
from sqlalchemy.ext.asyncio import AsyncSession

from backend.services.user.cipher import decode_base64_with_padding, decrypt_data
from loguru import logger
from backend.config.config import settings

router = APIRouter(prefix="/admin", tags=["Admin"])


@router.get(
    "/get_all",
    response_model=List[UserDbResponse],
    dependencies=[Depends(admin_access)],
)
async def get_all_users(
    skip: int = 0,
    limit: int = 10,
    current_user: User = Depends(auth_service.get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    **Get a list of users. / Получение списка всех пользователей**\n
    This route allows to get a list of pagination-aware users.
    Level of Access:
    - Admin
    :param skip: int: Number of users to skip.
    :param limit: int: Maximum number of users to return.
    :param current_user: User: Current authenticated user (for dependency injection).
    :param db: AsyncSession: Database session.
    :return: List of users with their last activity.
    :rtype: List[UserDb]
    """

    list_users = await person.get_users(skip, limit, db)
    users_list_with_activity = []

    for user in list_users:
        user_response = UserDbResponse(
            id=user.id,
            cor_id=user.cor_id,
            email=user.email,
            is_active=user.is_active,
            last_password_change=user.last_password_change,
            user_sex=user.user_sex,
            birth=user.birth,
            user_index=user.user_index,
            created_at=user.created_at,
            last_active=user.last_activity,
        )

        users_list_with_activity.append(user_response)

    return users_list_with_activity


async def _create_profile_response(
    db_profile, current_user, router_instance
) -> ProfileResponse:
    """Формирует ProfileResponse, дешифруя поля и добавляя данные из User."""
    response_data = db_profile.__dict__.copy()
    decoded_key = None
    try:
        decoded_key = decode_base64_with_padding(settings.aes_key)
    except ValueError as e:
        logger.warning(f"Failed to decode profile encryption key: {e}")

    for field_name in ["surname", "first_name", "middle_name"]:
        encrypted_field = f"encrypted_{field_name}"
        encrypted_value = response_data.get(encrypted_field)
        if encrypted_value and decoded_key:
            try:
                response_data[field_name] = await decrypt_data(encrypted_value, decoded_key)
            except ValueError as e:
                logger.warning(
                    f"Failed to decrypt profile field '{field_name}' "
                    f"for user {current_user.id}: {e}"
                )
                response_data[field_name] = None
        else:
            response_data[field_name] = None

        response_data.pop(encrypted_field, None)

    response_data["email"] = current_user.email
    response_data["sex"] = current_user.user_sex


    return ProfileResponse.model_validate(response_data)


@router.get(
    "/get_user_info/{user_cor_id}",
    response_model=FullUserInfoResponse,
    dependencies=[Depends(admin_access)],
)
async def get_all_user_info(
    user_cor_id: str,
    current_user: User = Depends(auth_service.get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    **Get a list of user's info. / Получение всей информации по пользователю**\n
    Level of Access:
    - Admin
    """
    full_user_data = {}

    user = await person.get_user_by_corid(db=db, cor_id=user_cor_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")

    last_active = None
    if await redis_client.exists(str(user.id)):
        users_last_activity_str = await redis_client.get(str(user.id))
        try:
            last_active = users_last_activity_str
        except (ValueError, TypeError) as e:
            logger.error(f"Error parsing last_active from Redis: {e}")
            last_active = None

    full_user_data["user_info"] = UserDb(
        id=user.id,
        cor_id=user.cor_id,
        email=user.email,
        is_active=user.is_active,
        last_password_change=user.last_password_change,
        user_sex=user.user_sex,
        birth=user.birth,
        user_index=user.user_index,
        created_at=user.created_at,
        last_active=last_active,
    )

    user_roles = await person.get_user_roles(email=user.email, db=db)
    if user_roles:
        full_user_data["user_roles"] = user_roles

    profile = await person.get_profile_by_user_id(db=db, user_id=user.id)
    if profile:
        profile_response = await _create_profile_response(profile, user, router)
        full_user_data["profile"] = profile_response

    return full_user_data


@router.get(
    "/get_user_info/{user_cor_id}/user-data",
    response_model=UserDataResponse,
    dependencies=[Depends(admin_access)],
)
async def get_user_data_info(
    user_cor_id: str,
    current_user: User = Depends(auth_service.get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    **Get a list of user's info. / Получение всей информации по пользователю**\n
    Level of Access:
    - Admin
    """
    user_data = {}

    user = await person.get_user_by_corid(db=db, cor_id=user_cor_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")

    last_active = None
    if await redis_client.exists(str(user.id)):
        users_last_activity_str = await redis_client.get(str(user.id))
        try:
            last_active = users_last_activity_str
        except (ValueError, TypeError) as e:
            logger.error(f"Error parsing last_active from Redis: {e}")
            last_active = None

    user_data["user_info"] = UserDb(
        id=user.id,
        cor_id=user.cor_id,
        email=user.email,
        is_active=user.is_active,
        last_password_change=user.last_password_change,
        user_sex=user.user_sex,
        birth=user.birth,
        user_index=user.user_index,
        created_at=user.created_at,
        last_active=last_active,
    )

    return user_data


@router.get(
    "/get_user_info/{user_cor_id}/user-roles",
    response_model=UserRolesResponseForAdmin,
    dependencies=[Depends(admin_access)],
)
async def get_user_roles_info(
    user_cor_id: str,
    current_user: User = Depends(auth_service.get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    **Get a list of user's info. / Получение всей информации по пользователю**\n
    Level of Access:
    - Admin
    """
    user_data = {}

    user = await person.get_user_by_corid(db=db, cor_id=user_cor_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")

    user_roles = await person.get_user_roles(email=user.email, db=db)
    if user_roles:
        user_data["user_roles"] = user_roles

    return user_data


@router.get(
    "/get_user_info/{user_cor_id}/profile-data",
    response_model=UserProfileResponseForAdmin,
    dependencies=[Depends(admin_access)],
)
async def get_user_profile_info(
    user_cor_id: str,
    current_user: User = Depends(auth_service.get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    **Get a list of user's info. / Получение всей информации по пользователю**\n
    Level of Access:
    - Admin
    """
    user_data = {}

    user = await person.get_user_by_corid(db=db, cor_id=user_cor_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")

    profile = await person.get_profile_by_user_id(db=db, user_id=user.id)
    if profile:
        profile_response = await _create_profile_response(profile, user, router)
        user_data["profile"] = profile_response

    return user_data


@router.delete("/{email}", dependencies=[Depends(admin_access)])
async def delete_user(email: EmailStr, db: AsyncSession = Depends(get_db)):
    """
        **Delete user by email. / Удаление пользователя по имейлу**\n

    =
    """
    user = await person.get_user_by_email(email, db)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )
    else:
        await person.delete_user_by_email(db=db, email=email)
        return {"message": f" user {email} - was deleted"}


@router.patch("/deactivate/{email}", dependencies=[Depends(admin_access)])
async def deactivate_user(email: EmailStr, db: AsyncSession = Depends(get_db)):
    """
    **Deactivate user by email. / Деактивация аккаунта пользователя**\n

    This route allows to deactivate a user account by their email.

    :param email: EmailStr: Email of the user to deactivate.

    :param db: AsyncSession: Database Session.

    :return: Message about successful deactivation.

    :rtype: dict
    """
    user = await person.get_user_by_email(email, db)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )
    if not user.is_active:
        return {"message": f"The {user.email} account is already deactivated"}
    else:
        await person.deactivate_user(email, db)
        return {"message": f"{user.email} - account is deactivated"}


@router.patch("/activate/{email}", dependencies=[Depends(admin_access)])
async def activate_user(email: EmailStr, db: AsyncSession = Depends(get_db)):
    """
    **Activate user by email. / Активация аккаунта пользователя**\n

    This route allows to activate a user account by their email.

    :param email: EmailStr: Email of the user to activate.

    :param db: AsyncSession: Database Session.

    :return: Message about successful activation.

    :rtype: dict
    """
    user = await person.get_user_by_email(email, db)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )
    if user.is_active:
        return {"message": f"The {user.email} account is already active"}
    else:
        await person.activate_user(email, db)
        return {"message": f"{user.email} - account is activated"}


@router.patch("/kickout/{email}", dependencies=[Depends(admin_access)])
async def kickout_user(email: EmailStr, db: AsyncSession = Depends(get_db)):
    """
    **Kickout user by email (delete refresh token). / Удаление рефреш токена пользователя**\n

    """
    user = await person.get_user_by_email(email, db)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )
    else:
        new_token = None
        await person.update_token(user=user, token=new_token, db=db)
        return {"message": f"{email} - delete refresh token"}


@router.post(
    "/register_new_user",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(admin_access)],
)
async def register_new_user(
    body: NewUserRegistration, db: AsyncSession = Depends(get_db)
):
    """
    Создает нового пользователя с временным паролем
    """

    if body:
        new_user_info = body
        exist_user = await person.get_user_by_email(new_user_info.email, db)
        if exist_user:
            logger.debug(f"{new_user_info.email} user already exists")
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Account already exists"
            )
        new_user = await person.register_new_user(db=db, body=new_user_info)
        return {"message": f"Новый пользователь {body.email} успешно зарегистрирован."}
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Некорректные данные регистрации пользователя.",
        )
