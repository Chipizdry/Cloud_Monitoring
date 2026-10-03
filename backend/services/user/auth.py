from typing import Optional
import uuid

from jose import JWTError, jwt
from fastapi import HTTPException, status, Depends
from fastapi.security import OAuth2PasswordBearer
from passlib.context import CryptContext
from datetime import timedelta, datetime, timezone
from sqlalchemy import select

from backend.database.db import get_db
from backend.repository.user import person as repository_users
from backend.config.config import settings
from backend.services.shared import redis_service
from loguru import logger
from backend.services.shared.websocket_events_manager import websocket_events_manager

from sqlalchemy.ext.asyncio import AsyncSession


class Auth:
    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
    SECRET_KEY = settings.secret_key
    ALGORITHM = settings.algorithm
    oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

    def verify_password(self, plain_password, hashed_password):
        """
        The verify_password function takes a plain-text password and the hashed version of that password,
            and returns True if they match, False otherwise. This is used to verify that the user's login
            credentials are correct.

        :param self: Represent the instance of the class
        :param plain_password: Pass the password that is entered by the user
        :param hashed_password: Compare the plain_password parameter to see if they match
        :return: True if the password is correct, and false otherwise
        """
        return self.pwd_context.verify(plain_password, hashed_password)

    def get_password_hash(self, password: str):
        """
        The get_password_hash function takes a password as input and returns the hash of that password.
            The function uses the pwd_context object to generate a hash from the given password.
        :param self: Represent the instance of the class
        :param password: str: Pass the password into the function
        :return: A hash of the password
        """
        return self.pwd_context.hash(password)

    async def create_access_token(
        self, data: dict, expires_delta: Optional[float] = None
    ):
        """
        The create_access_token function creates a new access token for the user.
        :param self: Represent the instance of the class
        :param data: dict: Pass the data to be encoded
        :param expires_delta: Optional[float]: Set the time limit for the token
        :return: A string
        """
        to_encode = data.copy()
        if expires_delta:
            expire = datetime.now(timezone.utc) + timedelta(hours=expires_delta)
        else:
            expire = datetime.now(timezone.utc) + timedelta(
                seconds=settings.access_token_expiration
            )
        jti = str(uuid.uuid4())
        to_encode.update(
            {
                "iat": datetime.now(timezone.utc),
                "exp": expire,
                "scp": "access_token",
                "jti": jti,
            }
        )

        encoded_access_token = jwt.encode(
            to_encode, key=self.SECRET_KEY, algorithm=self.ALGORITHM
        )
        logger.debug(f"Access token: {encoded_access_token}")
        return encoded_access_token, jti

    async def create_refresh_token(
        self, data: dict, expires_delta: Optional[float] = None
    ):
        """
        The create_refresh_token function creates a refresh token for the user.
            Args:
                data (dict): A dictionary containing the user's id and username.
                expires_delta (Optional[float]): The number of seconds until the refresh token expires. Defaults to None, which sets it to 7 days from now.

        :param self: Represent the instance of the class
        :param data: dict: Pass in the user data that we want to encode
        :param expires_delta: Optional[float]: Set the expiration time of the refresh token
        :return: A refresh token that is encoded with the user's id, username, email and scope
        """
        to_encode = data.copy()
        if expires_delta:
            expire = datetime.now(timezone.utc) + timedelta(hours=expires_delta)
        else:
            expire = datetime.now(timezone.utc) + timedelta(
                hours=settings.refresh_token_expiration
            )
        jti = str(uuid.uuid4())
        to_encode.update(
            {
                "iat": datetime.now(timezone.utc),
                "exp": expire,
                "scp": "refresh_token",
                "jti": jti,
            }
        )

        encoded_refresh_token = jwt.encode(
            to_encode, key=self.SECRET_KEY, algorithm=self.ALGORITHM
        )
        logger.debug(f"refresh token: {encoded_refresh_token}")
        return encoded_refresh_token

    async def decode_refresh_token(self, refresh_token: str):
        """
        The decode_refresh_token function takes a refresh token and decodes it.
            If the scope is 'refresh_token', then we return the email address of the user.
            Otherwise, we raise an HTTPException with status code 401 (UNAUTHORIZED) and detail message 'Invalid scope for token'.


        :param self: Represent the instance of the class
        :param refresh_token: str: Pass in the refresh token that was sent by the user
        :return: The email of the user who requested it
        """
        try:

            payload = jwt.decode(
                refresh_token, key=self.SECRET_KEY, algorithms=self.ALGORITHM
            )

            if payload["scp"] == "refresh_token":
                id = payload["oid"]
                return id
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid scope for token",
            )
        except JWTError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials",
            )

    async def get_current_user(
        self, token: str = Depends(oauth2_scheme), db: AsyncSession = Depends(get_db)
    ):
        """
        Проверяет валидность Access токена и возвращает объект пользователя.
        Включает проверку на:
        - Истечение срока действия токена
        - Наличие JTI в черном списке Redis (отзыв токена)
        - Корректность структуры токена
        - Существование пользователя в базе данных

        :param token: Access токен из заголовка "Authorization: Bearer".
        :param db: Асинхронная сессия базы данных.
        :return: Объект User, если токен валиден и пользователь существует.
        :raises HTTPException 401: Если токен невалиден, истёк, отозван или пользователь не найден.
        """
        try:
            payload = jwt.decode(
                token, key=self.SECRET_KEY, algorithms=[self.ALGORITHM]
            )

            exp = payload.get("exp")
            jti = payload.get("jti")
            if exp is None or jti is None:
                logger.warning(
                    f"Malformed token: missing 'exp' or 'jti'. Payload: {payload}"
                )
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Некорректный токен: отсутствует время истечения или идентификатор.",
                    headers={"WWW-Authenticate": "Bearer"},
                )

            if await redis_service.is_jti_blacklisted(jti):
                logger.warning(f"Revoked token detected with JTI: {jti}")
                event_data = {
                    "channel": "cor-erp-prod",
                    "event_type": "token_blacklisted",
                    "token": token,
                    "reason": "Token explicitly revoked or logged out.",
                    "timestamp": datetime.now(timezone.utc).timestamp(),
                }
                await websocket_events_manager.broadcast_event(event_data)
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Токен отозван. Используйте новый токен или авторизуйтесь заново.",
                    headers={"WWW-Authenticate": "Bearer"},
                )

            if datetime.fromtimestamp(exp, tz=timezone.utc) < datetime.now(
                timezone.utc
            ):
                logger.warning(f"Expired token detected for JTI: {jti}")
                event_data = {
                    "channel": "cor-erp-prod",
                    "event_type": "token_expired",
                    "token": token,
                    "reason": "Token has naturally expired.",
                    "timestamp": datetime.now(timezone.utc).timestamp(),
                }
                await websocket_events_manager.broadcast_event(event_data)
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Срок действия токена истёк. Пожалуйста, обновите токен.",
                    headers={"WWW-Authenticate": "Bearer"},
                )

            if payload.get("scp") != "access_token":
                logger.warning(
                    f"Invalid token scope. Expected 'access_token', got '{payload.get('scp')}'"
                )
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Некорректная область действия токена. Ожидается 'access_token'.",
                    headers={"WWW-Authenticate": "Bearer"},
                )

            oid = payload.get("oid")
            if oid is None:
                logger.warning(f"Token payload missing 'oid'. Payload: {payload}")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Некорректный токен: отсутствует идентификатор пользователя.",
                    headers={"WWW-Authenticate": "Bearer"},
                )

        except JWTError as e:
            error_message = str(e).lower()
            detail_for_user = (
                "Не удалось проверить учетные данные: неверный или поврежденный токен."
            )

            if "signature verification failed" in error_message:
                detail_for_user = "Недействительная подпись."
                logger.warning(f"JWT signature error: {error_message}")
            elif "malformed" in error_message:
                detail_for_user = "Некорректный формат токена."
                logger.warning(f"JWT malformed error: {error_message}")

            elif "not enough segments" in error_message:
                detail_for_user = "Неполный токен: не хватает сегментов."
                logger.warning(f"JWT malformed error: {error_message}")

            elif "signature has expired" in error_message:
                detail_for_user = "Срок действия токена истек."
                logger.warning(f"JWT expiry error: {error_message}")

            elif "invalid issuer" in error_message:
                detail_for_user = "Неверный издатель токена."
                logger.warning(f"JWT invalid issuer error: {error_message}")
            elif "invalid audience" in error_message:
                detail_for_user = "Токен предназначен для другой аудитории."
                logger.warning(f"JWT invalid audience error: {error_message}")
            else:
                logger.error(f"Unhandled JWT error: {error_message}", exc_info=True)

            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=detail_for_user,
                headers={"WWW-Authenticate": "Bearer"},
            )

        user = await repository_users.get_user_by_uuid(oid, db)
        if user is None:
            logger.warning(f"User with OID '{oid}' not found for valid token.")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Пользователь не найден.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        return user


auth_service = Auth()


# Standalone function wrapper for get_current_user to avoid circular imports
async def get_current_user(
    token: str = Depends(Auth.oauth2_scheme), 
    db: AsyncSession = Depends(get_db)
):
    """
    Wrapper function for auth_service.get_current_user to avoid circular imports.
    Can be imported directly without importing auth_service.
    """
    return await auth_service.get_current_user(token, db)
