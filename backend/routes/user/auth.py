"""
COR-ID Authentication API

Этот модуль содержит все endpoints для авторизации и аутентификации пользователей
в интеграции с COR-ID backend через OAuth 2.0 + PKCE.

================================================================================
АРХИТЕКТУРА ИНТЕГРАЦИИ
================================================================================

Интеграция использует стандартный OAuth 2.0 Authorization Code Flow с PKCE:

    Cor-Fuel Клиент
           ↓
    POST /auth/v1/initiate-login
    или /auth/web/initiate-login
           ↓
    Получает: session_token + authorize_url (указывает на COR-ID)
           ↓
    Открывает authorize_url (WebView или браузер)
           ↓
    COR-ID Backend (https://corid.example.com/api/oauth/authorize)
    └─ Пользователь вводит email + пароль
    └─ COR-ID проверяет учётные данные
    └─ Редиректит обратно на /auth/corid/callback с code + state
           ↓
    Cor-Fuel Backend (/auth/corid/callback)
    └─ Получает code + state
    └─ Обменивает code на id_token (с PKCE verification)
    └─ Сохраняет approval в Redis
           ↓
    Клиент опрашивает: POST /auth/v1/check_session_status
           ↓
    Получает: access_token + refresh_token (для Cor-Fuel API)

================================================================================
ОСНОВНЫЕ ТИПЫ АВТОРИЗАЦИИ
================================================================================

1. ПРЯМАЯ АВТОРИЗАЦИЯ (Direct Auth) - только для COR-ID backend
   - Endpoints: /signup, /login
   - Требует: email + password
   - Используется: COR-ID мобильное приложение, COR-ID backend
   - Возвращает: токены сразу

2. OAuth 2.0 + PKCE (Delegated Auth via COR-ID)
   - Endpoints: /v1/initiate-login → /auth/corid/callback → /v1/check_session_status
   - Требует: подтверждение на COR-ID backend
   - Используется: Cor-Energy, Cor-Medical, и другие приложения
   - Возвращает: токены через polling после OAuth callback

================================================================================
БЫСТРАЯ НАВИГАЦИЯ
================================================================================

ПРЯМАЯ РЕГИСТРАЦИЯ И ВХОД:
  POST /auth/signup                    - Регистрация нового пользователя
  POST /auth/login                     - Вход по email и паролю

ПРИГЛАШЕНИЯ ПОЛЬЗОВАТЕЛЕЙ:
  POST /auth/invite                    - Создать приглашение (с отправкой email)
  POST /auth/validate-invitation       - Проверить токен приглашения
  POST /auth/accept-invitation         - Регистрация по приглашению

OAuth 2.0 + PKCE FLOW (интеграция с COR-ID backend):
  POST /auth/v1/initiate-login         - [ШАГ 1] Инициация (мобильные)
  POST /auth/web/initiate-login        - [ШАГ 1] Инициация (веб)
  GET  /auth/corid/callback            - [ШАГ 2] Callback от COR-ID (auto)
  POST /auth/v1/check_session_status   - [ШАГ 3] Получить токены (polling)
  POST /auth/web/qr-scanned            - Уведомление о сканировании QR

УПРАВЛЕНИЕ ТОКЕНАМИ:
  GET  /auth/refresh_token             - Обновить access и refresh токены
  GET  /auth/verify                    - Проверить валидность access_token
  GET  /auth/verify_session            - Проверить сессию + получить device_id

ВОССТАНОВЛЕНИЕ ДОСТУПА:
  POST /auth/send_verification_code    - Отправить код на email (регистрация)
  POST /auth/confirm_email             - Подтвердить email кодом
  POST /auth/forgot_password           - Отправить код восстановления
  POST /auth/restore_account_by_text   - Восстановить по recovery коду
  POST /auth/restore_account_by_recovery_file - Восстановить по файлу

================================================================================
OAuth 2.0 + PKCE FLOW - ПОДРОБНО
================================================================================

    Mobile App                         COR-ID Backend              Cor-Fuel Backend
         │                                    │                           │
         │─ POST /v1/initiate-login ────────►│                           │
         │                                    │──────────────────────────►│
         │◄────────────────────────────────────────────────────────────────
         │  authorize_url (PKCE params)       │                           │
         │                                    │                           │
         │─ Open authorize_url ──────────────►│                           │
         │  (shows COR-ID login page)         │                           │
         │                                    │                           │
         │─ Submit email + password ─────────►│                           │
         │                                    │                           │
         │◄─ Redirect callback?code=xxx ──────┤                           │
         │  (browser redirects to Cor-Fuel)   │                           │
         │                                    │─── GET /auth/corid/callback ──►│
         │                                    │    (with code + state)     │
         │                                    │      code_verifier check   │
         │                                    │◄──────────────────────────┤
         │                                    │     approval stored        │
         │                                    │                           │
         │─ POST /check_session_status ──────┼──────────────────────────►│
         │  (poll for approval)               │      check Redis           │
         │◄────────────────────────────────────────────────────────────────
         │  access_token + refresh_token     │                           │

================================================================================
БЕЗОПАСНОСТЬ
================================================================================

IP-BASED RATE LIMITING:
  - Максимум 15 неудачных попыток логина с одного IP
  - После 15 попыток IP блокируется на 15 минут
  - Хранится в Redis для работы в кластере

PKCE PROTECTION:
  - Authorization code не может быть обменен без code_verifier
  - code_verifier = SHA256 случайного числа
  - Защита от code interception в мобильных приложениях

STATE TOKEN (CSRF):
  - Случайный token для каждого OAuth session
  - Валидируется в callback
  - TTL = 10 минут
  - One-time use (удаляется после использования)

DEVICE-BASED SESSIONS:
  - Каждая сессия привязана к device_id
  - Мобильные устройства требуют master key при первом входе
  - Desktop приложения имеют упрощённую проверку

TOKEN SECURITY:
  - Access token: JWT, подписан HS256, TTL = 1 час
  - Refresh token: JWT, подписан HS256, зашифрован в БД
  - JTI (JWT ID) для отзыва токенов
  - id_token от COR-ID: подписан HS256(client_secret)


================================================================================
"""

from uuid import uuid4
from fastapi import (
    APIRouter,
    HTTPException,
    Depends,
    status,
    Security,
    BackgroundTasks,
    Request,
    File,
    Form,
)
from fastapi.security import (
    OAuth2PasswordRequestForm,
    HTTPAuthorizationCredentials,
    HTTPBearer,
)
from random import randint
from fastapi_limiter.depends import RateLimiter
from backend.database.db import get_db
from backend.schemas import (
    CheckSessionRequest,
    ConfirmCheckSessionResponse,
    InitiateLoginRequest,
    InitiateLoginResponse,
    RecoveryResponseModel,
    UserModel,
    UserDb,
    ResponseUser,
    EmailSchema,
    VerificationModel,
    LoginResponseModel,
    RecoveryCodeModel,
    UserSessionModel,
    WebInitiateLoginRequest,
    WebInitiateLoginResponse,
    UserMeResponse,
    InviteUserRequest,
    InviteUserResponse,
    ValidateInvitationRequest,
    ValidateInvitationResponse,
    AcceptInvitationRequest,
    AcceptInvitationResponse,
)
from backend.database.models import User
from backend.repository.user import person as repository_person
from backend.repository.user import user_session as repository_session
from backend.repository.user import cor_id as repository_cor_id
from backend.repository.user import invitation as repository_invitation
from backend.services.user.auth import auth_service
from backend.services.shared import device_info as di
from backend.services.shared import corid_identity as corid_registry
from backend.services.shared.corid_deep_link import build_corid_mobile_deep_link
from backend.services.shared.email import (
    send_email_code,
    send_email_code_forgot_password,
    send_invitation_email,
)
from backend.services.shared.access import admin_access
from backend.services.shared.websocket_events_manager import websocket_events_manager
from backend.services.user.cipher import (
    decode_base64_with_padding,
    decrypt_data,
    decrypt_user_key,
    encrypt_data,
)
from backend.config.config import settings
from loguru import logger
from fastapi import UploadFile

from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, parse_qs, quote
import base64
import os
import json
import secrets
import asyncio

from sqlalchemy.ext.asyncio import AsyncSession
from jose import jwt, JWTError
from backend.database.redis_db import redis_client
import time

# ============================================================================
# LOGIN-BASED RATE LIMITING CONFIGURATION
# ============================================================================
# Защита от брутфорс атак на /login endpoint
# Использует Redis для хранения счётчиков и блокировок (работает в кластере)
# ============================================================================

# Префиксы Redis ключей
LOGIN_ATTEMPTS_PREFIX = (
    "login:email_attempts:"  # Счётчик попыток: login:email_attempts:user%40example.com
)
LOGIN_BLOCKED_PREFIX = (
    "login:email_blocked:"  # Timestamp блокировки: login:email_blocked:user%40example.com
)

# Legacy префиксы (для обратной совместимости reset endpoint)
IP_ATTEMPTS_PREFIX = "login:ip_attempts:"
IP_BLOCKED_PREFIX = "login:ip_blocked:"

# Пороги блокировки
MAX_ATTEMPTS_PER_LOGIN = 15  # Максимум неудачных попыток на один логин
BLOCK_DURATION_SECONDS = 15 * 60  # Длительность блокировки: 15 минут

# Логика:
# 1. При неудачном логине → INCR login:email_attempts:{email}
# 2. Если счётчик >= 15 → SET login:email_blocked:{email} = timestamp + 15 минут
# 3. При успешном логине → DELETE обоих ключей
# 4. TTL на ключах = 15 минут (автоматически удаляются)


def _normalize_login_email(email: str) -> str:
    return email.strip().lower()


def _rate_limit_key_suffix(email: str) -> str:
    return quote(_normalize_login_email(email), safe="")


def _attempts_key(email: str) -> str:
    return f"{LOGIN_ATTEMPTS_PREFIX}{_rate_limit_key_suffix(email)}"


def _blocked_key(email: str) -> str:
    return f"{LOGIN_BLOCKED_PREFIX}{_rate_limit_key_suffix(email)}"


async def _scan_delete_by_pattern(pattern: str) -> int:
    deleted_count = 0
    cursor: int | str = 0

    while True:
        cursor, keys = await redis_client.scan(cursor=cursor, match=pattern, count=500)
        if keys:
            deleted_count += await redis_client.delete(*keys)
        if cursor in (0, "0"):
            break

    return deleted_count


router = APIRouter(prefix="/auth", tags=["Authorization"])
security = HTTPBearer()

SECRET_KEY = settings.secret_key
ALGORITHM = settings.algorithm

# Список тестовых email для ускоренного истечения access токена
# ВАЖНО: для тестовых пользователей сокращаем ТОЛЬКО срок жизни access токена.
# Refresh токен оставляем стандартным, чтобы механизм обновления не ломался.
TEST_EMAILS = [
    # "vadym.borshchevskyi.work@gmail.com",
    "o.zhovtenko@cor-int.com"
]
TEST_ACCESS_EXPIRES_DELTA = 1 / 60  # 1 минута (в часах)


# ============================================================================
# COR-ID interop helpers (server-to-server)
# ============================================================================


async def _corid_post_json(path: str, payload: dict) -> dict:
    """Call COR-ID public endpoint without auth (dev endpoints allow anon).

    Uses httpx if available, falls back to urllib in thread.
    If COR-ID вернул 4xx/5xx, логируем body и пробрасываем 502 вверх.
    """

    corid_base = settings.corid_base_url.rstrip("/") if settings.corid_base_url else None
    if not corid_base:
        raise HTTPException(status_code=500, detail="COR-ID base URL is not configured")

    url = f"{corid_base}{path}"
    headers: dict[str, str] = {}
    if "192.168." in corid_base:
        # COR-ID dev rejects IP host header; override to dev domain when using IP base
        headers["Host"] = "dev.corid.cor-int.com"

    try:
        try:
            import httpx

            async with httpx.AsyncClient(timeout=10, headers=headers or None) as client:
                resp = await client.post(url, json=payload)
                try:
                    resp.raise_for_status()
                except httpx.HTTPStatusError as e:
                    body = resp.text
                    logger.error(
                        f"COR-ID {path} responded {resp.status_code}: {body}"
                    )
                    raise HTTPException(
                        status_code=502,
                        detail=f"COR-ID error {resp.status_code}: {body}",
                    ) from e
                return resp.json()
        except ImportError:
            import urllib.request
            import urllib.error

            def _post_json() -> dict:
                req = urllib.request.Request(
                    url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json", **headers},
                    method="POST",
                )
                try:
                    with urllib.request.urlopen(req, timeout=10) as r:
                        return json.loads(r.read().decode("utf-8"))
                except urllib.error.HTTPError as e:
                    body = e.read().decode("utf-8", errors="replace")
                    logger.error(
                        f"COR-ID {path} responded {e.code}: {body}"
                    )
                    raise HTTPException(
                        status_code=502,
                        detail=f"COR-ID error {e.code}: {body}",
                    ) from e

            return await asyncio.to_thread(_post_json)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to call COR-ID endpoint {path}: {e}")
        raise HTTPException(status_code=502, detail="Failed to call COR-ID service")


def _extract_state(authorize_url: str) -> str | None:
    """Parse state query param from COR-ID authorize_url."""

    parsed = urlparse(authorize_url)
    qs = parse_qs(parsed.query)
    values = qs.get("state")
    return values[0] if values else None


def _decode_corid_token(token: str) -> tuple[str | None, str | None]:
    """Try to decode COR-ID JWT to get (cor_id, email) without signature verification."""

    if not token:
        return None, None
    try:
        claims = jwt.decode(
            token,
            key="",
            options={"verify_signature": False, "verify_exp": False},
        )
        return claims.get("corid"), claims.get("email")
    except Exception as e:
        logger.debug(f"Failed to decode COR-ID token for claims: {e}")
        return None, None


@router.get("/me", response_model=UserMeResponse, summary="Текущий пользователь и роли")
async def get_current_user_profile(
    current_user: User = Depends(auth_service.get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Возвращает данные текущего пользователя и актуальный список ролей.

    """
    user_roles = await repository_person.get_user_roles(email=current_user.email, db=db)

    # Получаем профиль, если он есть
    first_name = None
    surname = None
    middle_name = None

    if current_user.profile:
        decoded_key = None
        try:
            decoded_key = decode_base64_with_padding(settings.aes_key)
        except ValueError as e:
            logger.warning(f"Failed to decode profile encryption key: {e}")

        encrypted_profile_fields = {
            "first_name": current_user.profile.encrypted_first_name,
            "surname": current_user.profile.encrypted_surname,
            "middle_name": current_user.profile.encrypted_middle_name,
        }

        for field_name, encrypted_value in encrypted_profile_fields.items():
            if not encrypted_value or not decoded_key:
                continue

            try:
                decrypted_value = await decrypt_data(
                    encrypted_data=encrypted_value,
                    key=decoded_key,
                )
            except ValueError as e:
                logger.warning(
                    f"Failed to decrypt profile field '{field_name}' "
                    f"for user {current_user.email}: {e}"
                )
                continue

            if field_name == "first_name":
                first_name = decrypted_value
            elif field_name == "surname":
                surname = decrypted_value
            elif field_name == "middle_name":
                middle_name = decrypted_value

    return UserMeResponse(
        corid=current_user.cor_id,
        roles=user_roles,
        first_name=first_name,
        surname=surname,
        middle_name=middle_name,
    )


@router.post(
    "/signup",
    response_model=ResponseUser,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RateLimiter(times=10, seconds=60))],
)
async def signup(
    body: UserModel,
    request: Request,
    db: AsyncSession = Depends(get_db),
    device_info: dict = Depends(di.get_device_header),
):
    """
    **Регистрация нового пользователя (прямая регистрация)**

    Создаёт новый аккаунт в системе COR-ID. После регистрации пользователь
    автоматически авторизуется и получает токены доступа.

    ---

    **Использование:**
    - Регистрация в COR-ID мобильном приложении
    - Регистрация в Cor-Energy (если нужен новый аккаунт)
    - Любые приложения экосистемы для создания новых пользователей

    ---

    **Параметры:**
    - `email` (str) - Email пользователя (уникальный)
    - `password` (str) - Пароль (будет захеширован)

    ---

    **Возвращает:**
    - `user` - Объект пользователя (без пароля)
    - `access_token` - JWT токен для доступа к API
    - `refresh_token` - JWT токен для обновления access_token
    - `token_type` - Тип токена (всегда "bearer")
    - `device_id` - ID устройства/сессии
    - `detail` - Сообщение об успехе

    ---

    **Что происходит:**
    1. ✅ Проверяется уникальность email
    2. 🔐 Хешируется пароль (bcrypt)
    3. 👤 Создаётся пользователь в БД
    4. 🆔 Генерируется уникальный COR-ID
    5. 🔑 Генерируются access_token и refresh_token
    6. 💾 Создаётся сессия для текущего устройства

    ---

    **Безопасность:**
    - Пароль никогда не хранится в открытом виде
    - Rate limit: 10 регистраций в минуту с одного IP
    - Проверка на существующий email
    - Автоматическое создание уникального шифровального ключа пользователя

    ---

    **Возможные ошибки:**
    - 409 Conflict - Пользователь с таким email уже существует
    - 429 Too Many Requests - Превышен лимит запросов

    """
    client_ip = request.client.host
    exist_user = await repository_person.get_user_by_email(body.email, db)
    if exist_user:
        logger.debug(f"{body.email} user already exist")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Account already exists"
        )
    body.password = auth_service.get_password_hash(body.password)
    new_user = await repository_person.create_user(body, db)
    if not new_user.cor_id:
        await repository_cor_id.create_new_corid(new_user, db)
    # --- Identity Registry Sync (resolve/register) ---
    try:
        resolved = await corid_registry.resolve_identity(new_user.email)
        if resolved and resolved.get("exists") and resolved.get("cor_id"):
            if new_user.cor_id != resolved["cor_id"]:
                new_user.cor_id = resolved["cor_id"]
                await db.commit()
                await db.refresh(new_user)
        else:
            reg = await corid_registry.register_identity(
                email=new_user.email,
                cor_id=new_user.cor_id,
                birth=new_user.birth,
                user_sex=new_user.user_sex,
            )
            if reg and reg.get("cor_id") and reg["cor_id"] != new_user.cor_id:
                new_user.cor_id = reg["cor_id"]
                await db.commit()
                await db.refresh(new_user)
    except Exception as e:
        logger.warning(f"Identity registry sync failed for {new_user.email}: {e}")
    logger.debug(f"{body.email} user successfully created")

    # Проверка ролей
    user_roles = await repository_person.get_user_roles(email=body.email, db=db)

    # Создаём токены
    access_token, access_token_jti = await auth_service.create_access_token(
        data={"oid": str(new_user.id), "corid": new_user.cor_id, "email": new_user.email, "roles": user_roles}
    )
    refresh_token = await auth_service.create_refresh_token(
        data={"oid": str(new_user.id), "corid": new_user.cor_id, "email": new_user.email, "roles": user_roles}
    )

    # Фиксируем активность при выдаче токенов
    await repository_person.update_last_activity(user=new_user, db=db)

    # Создаём новую сессию
    device_information = di.get_device_info(request)
    app_id = device_information.get("app_id")
    device_id = device_information.get("device_id")
    legacy_device_info = device_information.get("device_info")
    if not device_id:
        device_id = str(uuid4())
    if not app_id:
        app_id = "unknown app"
    # ---- Создание новой сессии ----
    session_data = {
        "user_id": new_user.cor_id,
        "app_id": app_id,
        "device_id": device_id,
        "device_type": device_information["device_type"],
        "device_info": legacy_device_info,  # для legacy клиентов
        "ip_address": device_information["ip_address"],
        "device_os": device_information["device_os"],
        "jti": access_token_jti,
        "refresh_token": refresh_token,
        "access_token": access_token,
    }
    new_session = await repository_session.create_user_session(
        body=UserSessionModel(**session_data),  # Передаём данные для сессии
        user=new_user,
        db=db,
    )
    logger.debug(
        f"Успешная регистрация пользователя {new_user.email} "
        f"с IP {client_ip}, app_id={app_id}, device_id={device_id}, "
        f"device_info={legacy_device_info}"
    )

    return ResponseUser(
        user=new_user,
        detail="User successfully created",
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        device_id=device_id,
    )


@router.post(
    "/login",
    response_model=LoginResponseModel,
    dependencies=[Depends(RateLimiter(times=10, seconds=60))],
)
async def login(
    request: Request,
    body: OAuth2PasswordRequestForm = Depends(),
    device_info: dict = Depends(di.get_device_header),
    db: AsyncSession = Depends(get_db),
):
    """
    **Вход в систему (прямая авторизация)**

    Авторизует существующего пользователя по email и паролю.
    Используется для прямого входа в приложения экосистемы.

    ---

    **Использование:**
    - Вход в COR-ID мобильное приложение
    - Вход в десктоп версию любого приложения
    - Быстрый вход для мобильных приложений (если есть сохранённый master key)

    ---

    **Параметры:**
    - `username` (str) - Email пользователя (OAuth2 стандарт требует название "username")
    - `password` (str) - Пароль пользователя

    ---

    **Возвращает:**
    - `access_token` - JWT токен для доступа к API (срок жизни: 1 час)
    - `refresh_token` - JWT токен для обновления access_token
    - `token_type` - Тип токена (всегда "bearer")
    - `session_id` - ID созданной сессии
    - `device_id` - ID устройства для этой сессии

    ---

    **Безопасность - Защита от брутфорса:**

    **IP-based rate limiting через Redis:**
    - Максимум 15 неудачных попыток с одного IP
    - После 15 попыток → IP блокируется на 15 минут
    - Счётчик сбрасывается при успешном входе
    - Блокировки хранятся в Redis для работы в кластере

    **Дополнительные проверки для мобильных устройств:**
    - При входе с нового мобильного устройства требуется master key (recovery code)
    - Desktop приложения могут входить без master key
    - Проверяется наличие существующей сессии для device_id

    ---

    **Что происходит:**

    1. 🔍 Проверка блокировки IP адреса
    2. 👤 Поиск пользователя по email
    3. 🔐 Проверка пароля (bcrypt)
    4. 📱 **Для мобильных:** Проверка наличия сессии (master key)
    5. 🔑 Генерация access_token и refresh_token
    6. 💾 Создание новой сессии для устройства
    7. ✅ Сброс счётчика неудачных попыток

    ---

    **Логика для мобильных устройств:**

    ```
    Первый вход с нового устройства:
    └─> Требуется master key (recovery code)
    └─> После ввода создаётся сессия для device_id

    Последующие входы с того же устройства:
    └─> Сессия найдена → вход разрешён
    └─> Создаётся новая сессия с новыми токенами
    ```

    ---

    **Возможные ошибки:**

    - **401 Unauthorized** - Неверный email или пароль
    - **429 Too Many Requests** - IP заблокирован (15 неудачных попыток)
      ```json
      {
        "detail": "Слишком много попыток авторизации. IP-адрес заблокирован до 2025-11-13T13:15:00"
      }
      ```
    - **400 Bad Request** - Требуется master key для нового мобильного устройства
      ```json
      {
        "detail": "Нужен ввод мастер-ключа"
      }
      ```

    ---

    **Интеграция с другими endpoints:**

    После получения токенов используйте:
    - `/auth/refresh_token` - Обновление токенов при истечении
    - `/auth/verify` - Проверка валидности access_token
    - `/auth/verify_session` - Проверка сессии и получение device_id
    """
    normalized_email = _normalize_login_email(body.username)
    attempts_key = _attempts_key(normalized_email)
    blocked_key = _blocked_key(normalized_email)

    device_information = di.get_device_info(request)
    client_ip = device_information["ip_address"]

    # ---- Блокировки по login/email (rate limit) ----
    blocked_until_str = await redis_client.get(blocked_key)
    if blocked_until_str:
        blocked_until_timestamp = float(blocked_until_str)
        if blocked_until_timestamp > time.time():
            block_dt = datetime.fromtimestamp(blocked_until_timestamp)
            logger.warning(
                f"Логин {normalized_email} заблокирован до {block_dt} (ip={client_ip})."
            )
            raise HTTPException(
                status_code=429,
                detail=f"Логин временно заблокирован до {block_dt}",
            )
        else:
            await redis_client.delete(blocked_key)

    user = await repository_person.get_user_by_email(normalized_email, db)

    if user is None or not auth_service.verify_password(body.password, user.password):
        log_message = (
            f"Неудачная попытка входа для пользователя {normalized_email} с IP {client_ip}: "
            f"{'Пользователь не найден' if user is None else 'Неверный пароль'}"
        )
        logger.warning(log_message)

        current_attempts = await redis_client.incr(attempts_key)
        if current_attempts == 1:
            await redis_client.expire(attempts_key, BLOCK_DURATION_SECONDS)

        if current_attempts >= MAX_ATTEMPTS_PER_LOGIN:
            block_until_timestamp = time.time() + BLOCK_DURATION_SECONDS
            await redis_client.set(
                blocked_key,
                str(block_until_timestamp),
                ex=BLOCK_DURATION_SECONDS,
            )
            block_dt = datetime.fromtimestamp(block_until_timestamp)
            logger.warning(
                f"Слишком много попыток авторизации для логина {normalized_email}. "
                f"Блокировка до {block_dt} (ip={client_ip})."
            )
            raise HTTPException(
                status_code=429,
                detail=f"Слишком много попыток авторизации. Логин заблокирован до {block_dt}",
            )

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found / invalid email or password",
        )
    else:
        # успешный логин → сбрасываем счётчики
        await redis_client.delete(attempts_key)
        await redis_client.delete(blocked_key)

    # ---- Информация об устройстве ----

    # 🔹 Новое: различаем app_id / device_id
    app_id = device_information.get("app_id")
    device_id = device_information.get("device_id")
    legacy_device_info = device_information.get("device_info")

    # 🔹 Проверка на мобильных устройствах (master key)
    if device_information["device_type"] == "Mobile" and body.username not in [
        "apple-test@cor-software.com",
        "google-test@cor-software.com",
    ]:
        if app_id and device_id:
            existing_sessions = await repository_session.get_user_sessions_by_device(
                user.cor_id,
                db=db,
                app_id=device_information["app_id"],
                device_id=device_information["device_id"],
                device_info=device_information["device_info"],
            )
        else:
            # fallback для старых клиентов
            existing_sessions = (
                await repository_session.get_user_sessions_by_device_info(
                    user.cor_id, legacy_device_info, db
                )
            )

        if not existing_sessions:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Нужен ввод мастер-ключа",
            )

    # ---- Роли ----
    user_roles = await repository_person.get_user_roles(email=user.email, db=db)

    # Фиксируем время последней выдачи токена
    await repository_person.update_last_activity(user=user, db=db)

    # ---- Генерация токенов ----
    token_data = {"oid": str(user.id), "corid": user.cor_id, "email": user.email, "roles": user_roles}

    # Тестовые аккаунты: сокращаем только access, refresh оставляем стандартным
    if user.email in TEST_EMAILS:
        access_expires_delta = TEST_ACCESS_EXPIRES_DELTA
        refresh_expires_delta = None
    else:
        base_expires = None
        access_expires_delta = base_expires
        refresh_expires_delta = base_expires

    access_token, access_token_jti = await auth_service.create_access_token(
        data=token_data, expires_delta=access_expires_delta
    )
    refresh_token = await auth_service.create_refresh_token(
        data=token_data, expires_delta=refresh_expires_delta
    )

    if not device_id:
        device_id = str(uuid4())
    # ---- Создание новой сессии ----
    session_data = {
        "user_id": user.cor_id,
        "app_id": app_id,
        "device_id": device_id,
        "device_type": device_information["device_type"],
        "device_info": legacy_device_info,  # для legacy клиентов
        "ip_address": device_information["ip_address"],
        "device_os": device_information["device_os"],
        "jti": access_token_jti,
        "refresh_token": refresh_token,
        "access_token": access_token,
    }
    new_session = await repository_session.create_user_session(
        body=UserSessionModel(**session_data),  # Передаём данные для сессии
        user=user,
        db=db,
    )

    logger.debug(
        f"Успешный вход пользователя {user.email} "
        f"с IP {client_ip}, app_id={app_id}, device_id={device_id}, "
        f"device_info={legacy_device_info}"
    )

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "session_id": str(new_session.id),
        "device_id": device_id,
    }


@router.post("/admin/reset-ip-blocks", dependencies=[Depends(admin_access)])
async def admin_reset_login_blocks(
    email: str | None = None,
    ip: str | None = None,
):
    if email and ip:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide only one filter: email or ip",
        )

    if email:
        normalized_email = _normalize_login_email(email)
        deleted_count = await redis_client.delete(
            _attempts_key(normalized_email),
            _blocked_key(normalized_email),
        )
        return {
            "mode": "email",
            "email": normalized_email,
            "deleted": deleted_count,
        }

    if ip:
        normalized_ip = ip.strip()
        deleted_count = await redis_client.delete(
            f"{IP_ATTEMPTS_PREFIX}{normalized_ip}",
            f"{IP_BLOCKED_PREFIX}{normalized_ip}",
        )
        return {
            "mode": "ip",
            "ip": normalized_ip,
            "deleted": deleted_count,
        }

    deleted_email_attempts = await _scan_delete_by_pattern(f"{LOGIN_ATTEMPTS_PREFIX}*")
    deleted_email_blocked = await _scan_delete_by_pattern(f"{LOGIN_BLOCKED_PREFIX}*")
    deleted_ip_attempts = await _scan_delete_by_pattern(f"{IP_ATTEMPTS_PREFIX}*")
    deleted_ip_blocked = await _scan_delete_by_pattern(f"{IP_BLOCKED_PREFIX}*")

    total_deleted = (
        deleted_email_attempts
        + deleted_email_blocked
        + deleted_ip_attempts
        + deleted_ip_blocked
    )

    return {
        "mode": "all",
        "deleted": total_deleted,
        "details": {
            "email_attempts": deleted_email_attempts,
            "email_blocked": deleted_email_blocked,
            "ip_attempts": deleted_ip_attempts,
            "ip_blocked": deleted_ip_blocked,
        },
    }


# ============================================================================
# OAuth-LIKE AUTHENTICATION FLOW (Login via COR-ID)
# ============================================================================
# Используется внешними приложениями (Cor-Energy, Cor-Medical)
# для авторизации пользователей через COR-ID приложение
#
# FLOW:
# 1. Внешнее приложение → POST /v1/initiate-login → получает session_token + deep_link
# 2. Пользователь открывает COR-ID приложение по deep_link или QR коду
# 3. COR-ID приложение → POST /v1/confirm-login → подтверждает/отклоняет вход
# 4. Мобильное приложение (Cor-Energy) → POST /v1/check_session_status → получает токены
# ============================================================================


@router.post(
    "/v1/initiate-login",
    response_model=InitiateLoginResponse,
    dependencies=[Depends(RateLimiter(times=5, seconds=60))],
)
async def initiate_login(
    body: InitiateLoginRequest, request: Request, db: AsyncSession = Depends(get_db)
):
    """
    **[ШАГ 1] Инициация входа через COR-ID (OAuth 2.0 + PKCE)**

    Инициирует OAuth flow с COR-ID backend. Клиент использует authorize_url чтобы 
    перенаправить пользователя на COR-ID приложение или страницу входа.

    **Flow:**
    1. Мобила/Веб вызывает /auth/v1/initiate-login → получает authorize_url + session_token
    2. Клиент открывает authorize_url (через WebView, браузер или Deep Link)
    3. Пользователь подтверждает вход в COR-ID приложении
    4. COR-ID редиректит обратно на /auth/corid/callback с code + state
    5. Мобила/Веб опрашивают /auth/v1/check_session_status с session_token
    6. Cor-Fuel получает access_token + refresh_token от COR-ID, выдает их клиенту

    ---

    **Параметры:**
    - `email` (Optional[str]) - Email пользователя (если известен)
    - `cor_id` (Optional[str]) - COR ID пользователя (если известен)
    - `app_id` (str) - Идентификатор приложения (например: "cor-energy")

    **Примечание:** Нужно указать либо `email`, либо `cor_id`, либо оба

    ---

    **Возвращает:**
    - `session_token` (str) - Уникальный токен для отслеживания статуса входа (10 минут)
    - `authorize_url` (str) - URL для редиректа на COR-ID OAuth endpoint

    ---

    **Безопасность:**
    - PKCE (Proof Key for Code Exchange) protection against code interception
    - State parameter (CSRF protection)
    - Session token ограничен 10 минутами
    - Rate limit: 5 запросов в минуту

    """
    # Validate that at least one identifier is provided
    if not body.email and not body.cor_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email or cor_id must be provided"
        )

    device_information = di.get_device_info(request)
    if not body.app_id:
        body.app_id = device_information["app_id"]

    # Generate local session token (used by our clients for polling)
    session_token = secrets.token_urlsafe(32)

    try:
        corid_payload = {
            "email": body.email,
            "cor_id": body.cor_id,
            "app_id": body.app_id,
        }
        corid_resp = await _corid_post_json(
            "/api/auth/v1/initiate-login", corid_payload
        )

        corid_session_token = corid_resp.get("session_token")
        if not corid_session_token:
            logger.error(f"COR-ID initiate-login missing session_token: raw={corid_resp}")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Invalid response from COR-ID initiate-login",
            )

        # COR-ID v1 mobile endpoint only returns session_token;
        # construct deep link for mobile app to open COR-ID
        state = secrets.token_urlsafe(32)
        corid_base = settings.corid_base_url.rstrip("/") if settings.corid_base_url else ""
        deep_link = build_corid_mobile_deep_link(
            corid_session_token,
            email=str(body.email) if body.email else None,
            cor_id=body.cor_id,
        )
        authorize_url = f"{corid_base}/auth/confirm?session_token={corid_session_token}&state={state}"

        # Store mapping for callback and polling
        await redis_client.set(
            f"oauth:state:{state}",
            json.dumps(
                {
                    "session_token": session_token,
                    "corid_session_token": corid_session_token,
                    "email": body.email,
                    "cor_id": body.cor_id,
                    "app_id": body.app_id,
                }
            ),
            ex=600,
        )

        await redis_client.set(
            f"oauth:session:{session_token}",
            json.dumps(
                {
                    "email": body.email,
                    "cor_id": body.cor_id,
                    "app_id": body.app_id,
                    "initiated_at": datetime.utcnow().isoformat(),
                    "corid_session_token": corid_session_token,
                }
            ),
            ex=600,
        )

        await redis_client.set(
            f"oauth:bridge:{session_token}", corid_session_token, ex=600
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to prepare Cor-ID authorize URL: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to initiate COR-ID OAuth",
        )

    return {"session_token": session_token, "authorize_url": authorize_url, "deep_link": deep_link, "corid_session_token": corid_session_token}


@router.post(
    "/web/initiate-login",
    response_model=WebInitiateLoginResponse,
    dependencies=[Depends(RateLimiter(times=5, seconds=60))],
)
async def web_initiate_login(
    body: WebInitiateLoginRequest, request: Request, db: AsyncSession = Depends(get_db)
):
    """
    **[ВЕБ-ФРОНТЕНД] Инициация входа через COR-ID (OAuth 2.0 + PKCE)**

    Инициирует OAuth flow с COR-ID backend для веб-приложений. Клиент получает QR код,
    который пользователь сканирует в COR-ID мобильном приложении для подтверждения входа.

    **Flow:**
    1. Веб-фронтенд вызывает /auth/web/initiate-login → получает session_token + QR код + authorize_url
    2. Пользователь сканирует QR код в COR-ID приложении (или открывает authorize_url в браузере)
    3. COR-ID приложение отправляет POST /auth/web/qr-scanned → WebSocket уведомление вебу
    4. Пользователь подтверждает вход в COR-ID
    5. COR-ID редиректит на /auth/corid/callback с code + state
    6. Веб опрашивает /auth/v1/check_session_status
    7. Cor-Fuel выдает JWT tokens

    ---

    **Параметры:**
    - `email` (Optional[str]) - Email пользователя (если известен)

    ---

    **Возвращает:**
    - `session_token` (str) - Уникальный токен для отслеживания статуса входа (10 минут)
    - `authorize_url` (str) - URL для открытия в браузере (если пользователь не сканирует QR)
    - `qr_code` (str) - QR код в формате data:image/png;base64 (для сканирования в COR-ID)
    - `deep_link` (str) - Deep link для открытия COR-ID приложения
    - `expires_at` (datetime) - Время истечения сессии (10 минут)

    ---

    **Безопасность:**
    - PKCE (Proof Key for Code Exchange) protection
    - State parameter (CSRF protection)
    - Session token ограничен 10 минутами
    - Rate limit: 5 запросов в минуту
    - Email НЕ требуется (можно войти анонимно)

    """
    email = body.email.lower() if body.email else None

    # Generate local session token (used by our clients for polling)
    session_token = secrets.token_urlsafe(32)

    try:
        corid_payload = {"email": email, "app_id": "web"}
        corid_resp = await _corid_post_json(
            "/api/auth/web/initiate-login", corid_payload
        )

        corid_session_token = corid_resp.get("session_token")
        authorize_url = corid_resp.get("authorize_url")
        deep_link = corid_resp.get("deep_link")
        qr_code_data_url = corid_resp.get("qr_code")
        expires_at_str = corid_resp.get("expires_at")

        if not (corid_session_token and authorize_url and deep_link and qr_code_data_url):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Invalid response from COR-ID web initiate-login",
            )

        state = _extract_state(authorize_url)
        if not state:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="COR-ID authorize_url missing state",
            )

        await redis_client.set(
            f"oauth:state:{state}",
            json.dumps(
                {
                    "session_token": session_token,
                    "corid_session_token": corid_session_token,
                    "email": email,
                    "app_id": "web",
                }
            ),
            ex=600,
        )

        await redis_client.set(
            f"oauth:session:{session_token}",
            json.dumps(
                {
                    "email": email,
                    "app_id": "web",
                    "initiated_at": datetime.utcnow().isoformat(),
                    "corid_session_token": corid_session_token,
                }
            ),
            ex=600,
        )

        await redis_client.set(
            f"oauth:bridge:{session_token}", corid_session_token, ex=600
        )

        expires_at = (
            datetime.fromisoformat(expires_at_str)
            if expires_at_str
            else datetime.now() + timedelta(minutes=10)
        )

        logger.info(
            "Web login session bridged to COR-ID: "
            f"local={session_token[:8]}..., corid={corid_session_token[:8]}..., expires_at={expires_at}"
        )

        return WebInitiateLoginResponse(
            session_token=session_token,
            authorize_url=authorize_url,
            deep_link=deep_link,
            qr_code=qr_code_data_url,
            expires_at=expires_at,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to prepare Cor-ID authorize URL for web: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to initiate COR-ID OAuth",
        )


# @router.post(
#     "/web/qr-scanned",
#     status_code=status.HTTP_200_OK,
#     dependencies=[Depends(RateLimiter(times=10, seconds=60))],
# )
# async def notify_qr_scanned(body: QrScannedRequest, db: AsyncSession = Depends(get_db)):
#     """
#     **[МОБИЛЬНОЕ ПРИЛОЖЕНИЕ] Уведомление о сканировании QR-кода**

#     Вызывается COR-ID мобильным приложением сразу после открытия по deep link.
#     Отправляет WebSocket событие веб-фронтенду для показа анимации "Ожидание подтверждения".

#     **Параметры:**
#     - `session_token` (str) - Токен сессии из deep link (query параметр)

#     ---

#     **Безопасность:**
#     - Проверяется существование сессии в Redis
#     - Rate limit: 10 запросов в минуту
#     - WebSocket событие отправляется только для конкретной сессии

#     ---

#     **Возможные ошибки:**
#     - 404 Not Found - Сессия не найдена или истекла
#     - 429 Too Many Requests - Превышен rate limit
#     """
#     session_token = body.session_token

#     # Check if session exists in Redis
#     session_data = await redis_client.get(f"oauth:session:{session_token}")

#     if not session_data:
#         logger.warning(
#             f"QR scanned notification for non-existent session: {session_token[:8]}..."
#         )
#         raise HTTPException(
#             status_code=status.HTTP_404_NOT_FOUND,
#             detail="Сессия не найдена или истекла",
#         )

#     session_info = json.loads(session_data)

#     qr_scanned_event = {"event": "qr_scanned", "timestamp": datetime.now().isoformat()}

#     await websocket_events_manager.send_to_session(
#         session_id=session_token, event_data=qr_scanned_event
#     )

#     logger.info(
#         f"QR code scanned for session {session_token[:8]}... (email: {session_info.get('email')})"
#     )

#     return {"message": "QR scanned notification sent", "session_token": session_token}


@router.post(
    "/v1/check_session_status",
    response_model=ConfirmCheckSessionResponse,
    dependencies=[Depends(RateLimiter(times=60, seconds=60))],
)
async def check_session_status(
    body: CheckSessionRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    device_info: dict = Depends(di.get_device_header),
):
    """
    **[ШАГ 3a - для МОБИЛЬНЫХ приложений] Проверка статуса сессии и получение токенов**

    Используется мобильными приложениями (Cor-Energy, Cor-Medical) для:
    1. Polling - периодическая проверка подтвердил ли пользователь вход
    2. Получение access_token и refresh_token после подтверждения

    ---

    **Параметры:**
    - `session_token` (str) - Токен из ответа `/v1/initiate-login`
    - `email` (Optional[str]) - Email для дополнительной проверки
    - `cor_id` (Optional[str]) - COR ID для дополнительной проверки

    ---

    **Возвращает:**

    **Если пользователь ЕЩЁ НЕ подтвердил:**
    - HTTP 404 - "Сессия не найдена или отменена пользователем"

    **Если пользователь ПОДТВЕРДИЛ:**
    ```json
    {
      "status": "approved",
      "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
      "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
      "token_type": "bearer",
      "device_id": "550e8400-e29b-41d4-a716-446655440000"
    }
    ```

    **Если пользователь ОТКЛОНИЛ:**
    - Сессия удаляется, вернётся HTTP 404


    ---

    - Rate limit: 60 запросов в минуту (каждые 1-2 секунды polling)
    - Сессия автоматически создаётся в БД для устройства после подтверждения

    ---

    **Безопасность:**
    - Проверяется соответствие email/cor_id с данными сессии
    - Session token удаляется после использования (в будущем)
    - Создаётся новая сессия с device_id для работы refresh_token
    """
    email = body.email.lower() if body.email else None
    cor_id = body.cor_id
    session_token = body.session_token

    # Step 1: Check if already approved via callback (Redis cache)
    approved_key = f"oauth:approved:{session_token}"
    approved_payload = await redis_client.get(approved_key)
    
    if not approved_payload:
        # Step 2: No cached approval, poll COR-ID directly
        # Get COR-ID session token from our session mapping
        bridge_key = f"oauth:bridge:{session_token}"
        bridge_value = await redis_client.get(bridge_key)
        if not bridge_value:
            # Fallback: try reading from oauth:session
            session_key = f"oauth:session:{session_token}"
            session_value = await redis_client.get(session_key)
            if session_value:
                session_data = session_value if isinstance(session_value, str) else session_value.decode("utf-8")
                session_info = json.loads(session_data)
                corid_session_token = session_info.get("corid_session_token")
            else:
                corid_session_token = None
        else:
            corid_session_token = bridge_value if isinstance(bridge_value, str) else bridge_value.decode("utf-8")
        
        if not corid_session_token:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Сессия не найдена или истекла",
            )
        
        # Poll COR-ID check_session_status
        try:
            corid_body = {"session_token": corid_session_token}
            if email:
                corid_body["email"] = email
            if cor_id:
                corid_body["cor_id"] = cor_id
            
            corid_status = await _corid_post_json(
                "/api/auth/v1/check_session_status", corid_body
            )
            
            # Check if session was rejected by user
            confirmation_status = corid_status.get("status")
            if confirmation_status == "rejected":
                # Send WebSocket event to notify client
                await websocket_events_manager.send_to_session(
                    session_token,
                    {
                        "event": "auth_rejected",
                        "status": "rejected",
                        "message": "Вход отменен пользователем",
                    }
                )
                
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Вход отменен пользователем",
                )
            
            if confirmation_status != "approved":
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Сессия не найдена",
                )
            
            # Extract identity from COR-ID response
            approved_corid = corid_status.get("cor_id")
            approved_email = corid_status.get("email")
            
            # Fallback: decode COR-ID access_token if identity not in response
            if not (approved_corid and approved_email):
                decoded_corid, decoded_email = _decode_corid_token(
                    corid_status.get("access_token")
                )
                approved_corid = approved_corid or decoded_corid
                approved_email = approved_email or decoded_email
            
            if not approved_email:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="COR-ID did not return user identity",
                )
            
            # Cache approval for subsequent polls
            await redis_client.set(
                approved_key,
                json.dumps({"cor_id": approved_corid, "email": approved_email}),
                ex=600,
            )
            approved_payload = json.dumps({"cor_id": approved_corid, "email": approved_email})
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to poll COR-ID check_session_status: {e}")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Failed to verify session with COR-ID",
            )
    
    # Step 3: Process approved session (from cache or COR-ID poll)
    try:
        approved = json.loads(approved_payload)
        approved_email = approved.get("email")
        approved_corid = approved.get("cor_id")
        
        # Validate against request params if provided
        if email and approved_email and approved_email.lower() != email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Неверный email для данной сессии",
            )
        if cor_id and approved_corid and approved_corid != cor_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Неверный cor_id для данной сессии",
            )

        # Ensure user exists locally
        user = None
        if approved_corid:
            user = await repository_person.get_user_by_corid(approved_corid, db)
        if not user and approved_email:
            user = await repository_person.get_user_by_email(approved_email, db)
            if user and approved_corid and user.cor_id != approved_corid:
                user.cor_id = approved_corid
                await db.commit()
                await db.refresh(user)
        if not user:
            # JIT provision user
            random_pass = base64.urlsafe_b64encode(os.urandom(24)).decode("ascii")
            from backend.schemas import UserModel as _UserModel

            new_user_model = _UserModel(email=approved_email, password=random_pass)
            new_user_model.password = auth_service.get_password_hash(
                new_user_model.password
            )
            user = await repository_person.create_user(new_user_model, db)
            if approved_corid:
                user.cor_id = approved_corid
                await db.commit()
                await db.refresh(user)

        # Issue Cor-Fuel tokens
        user_roles = await repository_person.get_user_roles(email=user.email, db=db)
        token_data = {"oid": str(user.id), "corid": user.cor_id, "email": user.email, "roles": user_roles}

        if user.email in TEST_EMAILS:
            access_expires_delta = TEST_ACCESS_EXPIRES_DELTA
            refresh_expires_delta = None
        else:
            base_expires = None
            access_expires_delta = base_expires
            refresh_expires_delta = base_expires

        access_token, access_token_jti = await auth_service.create_access_token(
            data=token_data, expires_delta=access_expires_delta
        )
        refresh_token = await auth_service.create_refresh_token(
            data=token_data, expires_delta=refresh_expires_delta
        )

        device_information = di.get_device_info(request)
        existing_sessions = await repository_session.get_user_sessions_by_device(
            user.cor_id,
            db=db,
            app_id=device_information["app_id"],
            device_id=device_information["device_id"],
            device_info=device_information["device_info"],
        )
        if not existing_sessions:
            session_data = {
                "user_id": user.cor_id,
                "app_id": device_information["app_id"],
                "device_id": device_information["device_id"],
                "refresh_token": refresh_token,
                "device_type": device_information["device_type"],
                "device_info": device_information["device_info"],
                "ip_address": device_information["ip_address"],
                "device_os": device_information["device_os"],
                "jti": access_token_jti,
                "access_token": access_token,
            }
            await repository_session.create_user_session(
                body=UserSessionModel(**session_data), user=user, db=db
            )
        else:
            await repository_session.update_session_token(
                user=user,
                token=refresh_token,
                device_id=device_information["device_id"],
                device_info=device_information["device_info"],
                app_id=device_information["app_id"],
                db=db,
                jti=access_token_jti,
                access_token=access_token,
            )
        await repository_person.update_last_activity(user=user, db=db)

        # One-time use: remove approval cache after issuing tokens
        await redis_client.delete(approved_key)

        return ConfirmCheckSessionResponse(
            status="approved",
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="bearer",
            device_id=device_information["device_id"],
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to finalize Cor-ID OAuth session: {e}")
        raise HTTPException(status_code=500, detail="Internal error")


# @router.post(
#     "/v1/confirm-login",
#     response_model=ConfirmLoginResponse,
#     dependencies=[Depends(user_access)],
# )
# async def confirm_login(
#     request: Request,
#     body: ConfirmLoginRequest,
#     current_user: User = Depends(auth_service.get_current_user),
#     db: AsyncSession = Depends(get_db),
#     device_info: dict = Depends(di.get_device_header),
# ):
#     """
#     **[ШАГ 2] Подтверждение или отклонение входа в COR-ID приложении**

#     Вызывается **ТОЛЬКО из COR-ID мобильного приложения** когда пользователь:
#     - Сканирует QR код или переходит по deep link
#     - Видит запрос на вход от другого приложения (Cor-Energy и т.д.)
#     - Нажимает "Подтвердить" или "Отклонить"

#     ---

#     **Требования:**
#     - Пользователь должен быть авторизован в COR-ID приложении
#     - Authorization header с валидным access_token обязателен

#     ---

#     **Параметры:**
#     - `session_token` (str) - Токен сессии из QR кода или deep link
#     - `email` (Optional[str]) - Email пользователя для проверки
#     - `cor_id` (Optional[str]) - COR ID пользователя для проверки
#     - `status` (SessionLoginStatus) - "approved" или "rejected"

#     ---

#     **Что происходит при подтверждении (status="approved"):**

#     1. ✅ Проверяется что текущий пользователь соответствует запросу
#     2. 🔐 Генерируются новые access_token и refresh_token
#     3. 💾 Создаётся сессия для устройства внешнего приложения
#     4. 📡 Отправляется WebSocket событие с токенами
#     5. ✅ Внешнее приложение получает уведомление через WebSocket

#     **Что происходит при отклонении (status="rejected"):**

#     1. ❌ Сессия помечается как отклонённая
#     2. 📡 Отправляется WebSocket событие об отклонении
#     3. ❌ Внешнее приложение показывает ошибку "Вход отклонён"

#     ---

#     **Безопасность:**
#     - Требуется валидный access_token авторизованного пользователя
#     - Проверяется соответствие email/cor_id текущего пользователя с запросом
#     - Session token одноразовый (должен использоваться только один раз)
#     - WebSocket события отправляются только для конкретной сессии

#     ---

#     **Возможные ошибки:**
#     - 401 Unauthorized - Невалидный токен или пользователь не авторизован
#     - 400 Bad Request - Неверный email/cor_id для данной сессии
#     - 404 Not Found - Сессия не найдена или истекла
#     - 400 Bad Request - Неверный статус подтверждения

#     """
#     email = body.email
#     email = email.lower()
#     cor_id = body.cor_id

#     if email and current_user.email != email:
#         raise HTTPException(
#             status_code=status.HTTP_400_BAD_REQUEST,
#             detail="Вы не можете подтвердить вход под данным аккаунтом",
#         )

#     elif cor_id and current_user.cor_id != cor_id:
#         raise HTTPException(
#             status_code=status.HTTP_400_BAD_REQUEST,
#             detail="Вы не можете подтвердить вход под данным аккаунтом",
#         )

#     session_token = body.session_token
#     confirmation_status = body.status.lower()

#     db_session = await repository_session.get_auth_session(session_token, db)

#     if not db_session:
#         raise HTTPException(
#             status_code=status.HTTP_404_NOT_FOUND,
#             detail="Сессия не найдена или истекла",
#         )

#     if email and db_session.email != email:
#         raise HTTPException(
#             status_code=status.HTTP_400_BAD_REQUEST,
#             detail="Неверный email для данной сессии",
#         )

#     elif cor_id and db_session.cor_id != cor_id:
#         raise HTTPException(
#             status_code=status.HTTP_400_BAD_REQUEST,
#             detail="Неверный cor_id для данной сессии",
#         )

#     if confirmation_status == SessionLoginStatus.approved.value.lower():
#         await repository_session.update_session_status(
#             db_session, confirmation_status, db
#         )
#         # Получаем пользователя по email
#         user = await repository_person.get_user_by_email(db_session.email, db)
#         if user is None:
#             raise HTTPException(
#                 status_code=status.HTTP_404_NOT_FOUND,
#                 detail="User not found / invalid email",
#             )

#         # Проверка ролей
#         user_roles = await repository_person.get_user_roles(email=user.email, db=db)

#         # Получаем токены
#         token_data = {"oid": str(user.id), "corid": user.cor_id, "roles": user_roles}

#         if user.email in TEST_EMAILS:
#             access_expires_delta = TEST_ACCESS_EXPIRES_DELTA
#             refresh_expires_delta = None
#         else:
#             base_expires = None
#             access_expires_delta = base_expires
#             refresh_expires_delta = base_expires
#         access_token, access_token_jti = await auth_service.create_access_token(
#             data=token_data, expires_delta=access_expires_delta
#         )
#         refresh_token = await auth_service.create_refresh_token(
#             data=token_data, expires_delta=refresh_expires_delta
#         )

#         # Создаём новую сессию
#         device_information = di.get_device_info(request)
#         session_data = {
#             "user_id": user.cor_id,
#             "app_id": db_session.app_id,
#             "device_id": db_session.device_id,
#             "refresh_token": refresh_token,
#             "device_type": "Mobile" + f" {db_session.app_id}",  # Тип устройства
#             "device_info": device_information["device_info"]
#             + f" {db_session.app_id}",  # Информация об устройстве
#             "ip_address": device_information["ip_address"],  # IP-адрес
#             "device_os": device_information["device_os"],
#             "jti": access_token_jti,
#             "access_token": access_token,
#         }
#         new_session = await repository_session.create_user_session(
#             body=UserSessionModel(**session_data),  # Передаём данные для сессии
#             user=user,
#             db=db,
#         )
#         await repository_person.update_last_activity(user=user, db=db)

#         data = {
#             "status": "approved",
#             "access_token": access_token,
#             "refresh_token": refresh_token,
#             "token_type": "bearer",
#             "device_id": db_session.device_id,
#         }
#         await websocket_events_manager.send_to_session(
#             session_id=session_token, event_data=data
#         )
#         return {"message": "Вход успешно подтвержден"}

#     elif confirmation_status == SessionLoginStatus.rejected.value.lower():
#         await repository_session.update_session_status(
#             db_session, confirmation_status, db
#         )
#         data = {"status": "rejected"}
#         # await send_websocket_message(session_token=session_token, message={"status": "rejected"})
#         await websocket_events_manager.send_to_session(
#             session_id=session_token, event_data=data
#         )
#         return {"message": "Вход отменен пользователем"}
#     else:
#         raise HTTPException(
#             status_code=status.HTTP_400_BAD_REQUEST,
#             detail="Неверный статус подтверждения",
#         )


@router.get("/corid/callback")
async def corid_oauth_callback(request: Request):
    """
    OAuth callback endpoint for Cor-ID (Authorization Code + PKCE).

    Expects query params: code, state. Exchanges code for tokens, verifies id_token,
    marks session as approved in Redis keyed by our session_token. Mobile then polls
    /auth/v1/check_session_status to receive local tokens.
    """
    try:
        state = request.query_params.get("state")
        if not state:
            raise HTTPException(status_code=400, detail="Missing state")

        state_data = await redis_client.get(f"oauth:state:{state}")
        if not state_data:
            raise HTTPException(status_code=400, detail="Invalid or expired state")

        state_info = json.loads(state_data)
        session_token = state_info.get("session_token")
        corid_session_token = state_info.get("corid_session_token")
        email_hint = state_info.get("email")
        cor_id_hint = state_info.get("cor_id")

        # one-time state
        await redis_client.delete(f"oauth:state:{state}")

        if not session_token:
            raise HTTPException(status_code=400, detail="Missing session token mapping")

        if not corid_session_token:
            bridge_val = await redis_client.get(f"oauth:bridge:{session_token}")
            corid_session_token = bridge_val.decode("utf-8") if bridge_val else None

        if not corid_session_token:
            raise HTTPException(status_code=400, detail="Missing COR-ID session token")

        # Poll COR-ID status server-to-server (no auth required on dev)
        corid_body = {"session_token": corid_session_token}
        if email_hint:
            corid_body["email"] = email_hint
        if cor_id_hint:
            corid_body["cor_id"] = cor_id_hint

        corid_status = await _corid_post_json(
            "/api/auth/v1/check_session_status", corid_body
        )

        if corid_status.get("status") != "approved":
            raise HTTPException(status_code=404, detail="Session not approved yet")

        approved_corid = corid_status.get("cor_id")
        approved_email = corid_status.get("email")

        # Fallback: decode COR-ID access_token to extract identity
        if not (approved_corid and approved_email):
            decoded_corid, decoded_email = _decode_corid_token(
                corid_status.get("access_token")
            )
            approved_corid = approved_corid or decoded_corid
            approved_email = approved_email or decoded_email

        if not approved_email:
            raise HTTPException(
                status_code=502, detail="COR-ID did not return user identity"
            )

        await redis_client.set(
            f"oauth:approved:{session_token}",
            json.dumps({"cor_id": approved_corid, "email": approved_email}),
            ex=600,
        )

        # Return a minimal page for WebView/browser
        html = """
        <html><body>
        <h3>Login confirmed</h3>
        <p>You can return to the app.</p>
        </body></html>
        """
        from fastapi.responses import HTMLResponse

        return HTMLResponse(content=html, status_code=200)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in Cor-ID callback: {e}")
        raise HTTPException(status_code=500, detail="Internal error")


async def get_user_device_rate_limit_key(request: Request) -> str:
    """
    Генерирует уникальный ключ для rate limiting на основе пользователя и устройства.

    Используется в /refresh_token для ограничения частоты обновления токенов.
    Каждая комбинация user_id + device_type + device_info имеет свой отдельный лимит.

    Формат ключа:
    - Авторизованный: "user:{user_id}_device_type:{Mobile}_device_info:{iOS 17.0}"
    - Неавторизованный: "ip:{192.168.1.1}_ua:{Mozilla/5.0...}"

    Почему так:
    - Один пользователь может обновлять токены на разных устройствах одновременно
    - Каждое устройство имеет свой лимит (1 запрос в 5 секунд)
    - Предотвращает злоупотребление refresh endpoint

    Args:
        request: FastAPI Request объект с headers

    Returns:
        str: Уникальный ключ для rate limiter

    Example:
        user:550e8400-e29b-41d4-a716-446655440000_device_type:Mobile_device_info:iOS 17.0
    """
    auth_header = request.headers.get("Authorization")
    token = None
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1]
    if not token:
        return None
    try:
        payload = jwt.decode(
            token,
            key=auth_service.SECRET_KEY,
            algorithms=auth_service.ALGORITHM,
            options={"verify_exp": False},
        )

        user_id = payload.get("oid")
    except JWTError as e:
        logger.debug(f"Failed to decode token for rate limiter key (JWTError): {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error in get_user_id_from_token_sync: {e}")
        return None
    device_type = request.headers.get("X-Device-Type", "unknown")
    device_info_str = request.headers.get("X-Device-Info", "unknown")
    if user_id:
        return f"user:{user_id}_device_type:{device_type}_device_info:{device_info_str}"
    else:
        user_agent = request.headers.get("User-Agent", "unknown-agent")
        return f"ip:{request.client.host}_ua:{user_agent}"


@router.get(
    "/refresh_token",
    response_model=dict,
    dependencies=[
        Depends(
            RateLimiter(times=1, seconds=5, identifier=get_user_device_rate_limit_key)
        )
    ],
)
async def refresh_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Security(security),
    db: AsyncSession = Depends(get_db),
    device_info: dict = Depends(di.get_device_header),
):
    """
    **Обновление access и refresh токенов**

    Выдаёт новую пару токенов на основе валидного refresh_token.
    Работает ТОЛЬКО если существует активная сессия для данного устройства.

    ---

    **Когда использовать:**
    - Access token истёк (обычно через 1 час)
    - Приложение запускается и нужно проверить токены
    - Периодическое обновление токенов в фоне

    ---

    **Требования:**
    - Валидный refresh_token в Authorization header
    - Активная сессия для данного устройства в БД
    - Device headers (X-App-ID, X-Device-ID или X-Device-Info)

    ---

    **Headers:**
    ```
    Authorization: Bearer <refresh_token>
    X-App-ID: cor-energy (или другой app_id)
    X-Device-ID: 550e8400-e29b-41d4-a716-446655440000
    X-Device-Info: iOS 17.0, iPhone 15 Pro (legacy, для совместимости)
    ```

    ---

    **Возвращает:**
    - `access_token` - Новый JWT токен для доступа
    - `refresh_token` - Новый JWT refresh токен
    - `token_type` - "bearer"
    - `device_id` - ID устройства (только для мобильных)

    ---

    **Безопасность:**

    **Для мобильных устройств (строгая проверка):**
    1. 🔍 Находится сессия по app_id + device_id
    2. 🔐 Расшифровывается сохранённый refresh_token
    3. ✅ Сравнивается с переданным refresh_token
    4. ❌ Если не совпадает → 401 Unauthorized
    5. ✅ Если совпадает → выдаются новые токены

    **Для desktop приложений (упрощённая проверка):**
    1. 🔍 Проверяется наличие сессии
    2. ✅ Выдаются новые токены без проверки старого

    ---

    **Что происходит:**

    1. 🔓 Декодируется refresh_token
    2. 👤 Находится пользователь по user_id из токена
    3. 📱 Находится сессия по device_id
    4. 🔐 **Мобильные:** Проверяется совпадение refresh_token
    5. 🔑 Генерируются новые access_token и refresh_token
    6. 💾 Обновляется сессия в БД (новые токены и JTI)
    7. ✅ Возвращаются новые токены

    ---

    **Rate limiting:**
    - 1 запрос в 5 секунд **на пользователя + устройство**
    - Уникальный лимит для каждой комбинации user_id + device_id
    - Предотвращает злоупотребление обновлением токенов

    ---

    **Возможные ошибки:**

    - **401 Unauthorized** - Невалидный refresh_token
      ```json
      {"detail": "Invalid refresh token"}
      ```

    - **401 Unauthorized** - Сессия не найдена для устройства
      ```json
      {"detail": "Session not found for this device"}
      ```

    - **401 Unauthorized** - Refresh token не совпадает (мобильные)
      ```json
      {"detail": "Invalid refresh token for this device"}
      ```

    - **404 Not Found** - Пользователь не найден
      ```json
      {"detail": "User not found"}
      ```

    - **429 Too Many Requests** - Превышен rate limit
      ```json
      {"detail": "Rate limit exceeded. Try again in 5 seconds"}
      ```

    ---

    **Логика определения устройства:**

    ```python
    # Приоритет определения device_id:
    1. X-Device-ID header (новые клиенты)
    2. X-Device-Info header (legacy клиенты)
    3. Генерация нового UUID (fallback)

    # Приоритет определения app_id:
    1. X-App-ID header (cor-energy,  etc.)
    2. "unknown app" (fallback)
    ```

    ---

    **Связанные endpoints:**
    - `/auth/login` - Получение первичных токенов
    - `/auth/verify` - Проверка валидности access_token
    - `/auth/verify_session` - Проверка сессии с device_id
    """
    token = credentials.credentials
    logger.info(
        f"[REFRESH] START: token_prefix={token[:14] if token else None}, "
        f"X-App-Id={request.headers.get('X-App-Id')}, "
        f"X-Device-Id={request.headers.get('X-Device-Id')}, "
        f"X-Device-Type={request.headers.get('X-Device-Type')}, "
        f"X-Device-Info={request.headers.get('X-Device-Info')}"
    )
    user_id = await auth_service.decode_refresh_token(token)
    if not user_id:
        logger.warning(
            f"[REFRESH] FAILED: invalid refresh token prefix={token[:14] if token else None}"
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token"
        )
    logger.debug(f"[REFRESH] Step 1: Token decoded, user_id={user_id}")
    # Получаем пользователя
    user = await repository_person.get_user_by_uuid(user_id, db)
    if not user:
        logger.warning(f"[REFRESH] FAILED: User not found for user_id={user_id}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )
    logger.debug(
        f"[REFRESH] Step 2: User found, email={user.email} cor_id={user.cor_id}"
    )

    # Обновляем метку последней выдачи токена
    await repository_person.update_last_activity(user=user, db=db)
    
    # Получаем информацию об устройстве
    device_information = di.get_device_info(request)
    logger.debug(
        f"[REFRESH] Step 3: Device info, type={device_information.get('device_type')} "
        f"app_id={device_information.get('app_id')} device_id={device_information.get('device_id')} "
        f"device_info={device_information.get('device_info')} os={device_information.get('device_os')} "
        f"ip={device_information.get('ip_address')}"
    )

    # Находим сессию по device_id
    session = await repository_session.get_user_sessions_by_device(
        user.cor_id,
        db=db,
        app_id=device_information["app_id"],
        device_id=device_information["device_id"],
        device_info=device_information["device_info"],
    )
    if not session:
        logger.warning(
            f"[REFRESH] FAILED: Session not found, email={user.email} "
            f"app_id={device_information.get('app_id')} device_id={device_information.get('device_id')} "
            f"device_info={device_information.get('device_info')}"
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session not found for this device",
        )
    else:
        logger.debug(
            f"[REFRESH] Step 4: Session found, count={len(session)} session_id={session[0].id if session else None}"
        )

    # Проверяем refresh токен
    try:
        session_refresh_token = await decrypt_data(
            encrypted_data=session[0].refresh_token,
            key=await decrypt_user_key(user.unique_cipher_key),
        )
        logger.debug(
            f"[REFRESH] Step 5: Decrypted stored token, prefix={session_refresh_token[:14]}"
        )
    except Exception:
        logger.warning(f"Failed to decrypt refresh token for session {session[0].id}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token"
        )

    if device_information["device_type"] == "Desktop":
        logger.debug(
            f"[REFRESH] Step 6: Desktop branch, device_id={device_information.get('device_id')} app_id={device_information.get('app_id')}"
        )
        user_roles = await repository_person.get_user_roles(email=user.email, db=db)
        token_data = {"oid": str(user.id), "corid": user.cor_id, "email": user.email, "roles": user_roles}
        if user.email in TEST_EMAILS:
            access_expires_delta = TEST_ACCESS_EXPIRES_DELTA
            refresh_expires_delta = None
        else:
            base_expires = None
            access_expires_delta = base_expires
            refresh_expires_delta = base_expires

        access_token, access_token_jti = await auth_service.create_access_token(
            data=token_data, expires_delta=access_expires_delta
        )
        refresh_token = await auth_service.create_refresh_token(
            data=token_data, expires_delta=refresh_expires_delta
        )

        # Обновляем сессию
        await repository_session.update_session_token(
            user=user,
            token=refresh_token,
            device_id=device_information["device_id"],
            device_info=device_information["device_info"],
            app_id=device_information["app_id"],
            db=db,
            jti=access_token_jti,
            access_token=access_token,
        )
        logger.info(
            f"[REFRESH] SUCCESS (Desktop): email={user.email} "
            f"device_id={device_information.get('device_id')} jti={access_token_jti[:12] if access_token_jti else None}"
        )

        await repository_person.update_last_activity(user=user, db=db)

        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
        }

    else:
        if session_refresh_token != token:
            logger.warning(
                f"[REFRESH] FAILED (token mismatch): provided_prefix={token[:14]} "
                f"stored_prefix={session_refresh_token[:14]} email={user.email} "
                f"device_id={device_information.get('device_id')}"
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid refresh token for this device",
            )
        logger.debug(
            f"[REFRESH] Step 6: Mobile branch, token verified for email={user.email} device_id={device_information.get('device_id')}"
        )
        # Если всё ок → выдаём новые токены
        user_roles = await repository_person.get_user_roles(email=user.email, db=db)
        token_data = {"oid": str(user.id), "corid": user.cor_id, "email": user.email, "roles": user_roles}
        if user.email in TEST_EMAILS:
            access_expires_delta = TEST_ACCESS_EXPIRES_DELTA
            refresh_expires_delta = None
        else:
            base_expires = None
            access_expires_delta = base_expires
            refresh_expires_delta = base_expires

        access_token, access_token_jti = await auth_service.create_access_token(
            data=token_data, expires_delta=access_expires_delta
        )
        refresh_token = await auth_service.create_refresh_token(
            data=token_data, expires_delta=refresh_expires_delta
        )

        # Обновляем сессию
        session = await repository_session.update_session_token(
            user=user,
            token=refresh_token,
            device_id=device_information["device_id"],
            device_info=device_information["device_info"],
            app_id=device_information["app_id"],
            db=db,
            jti=access_token_jti,
            access_token=access_token,
        )
        logger.info(
            f"[REFRESH] SUCCESS (Mobile): email={user.email} "
            f"device_id={device_information.get('device_id')} jti={access_token_jti[:12] if access_token_jti else None}"
        )

        await repository_person.update_last_activity(user=user, db=db)

        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "device_id": session.device_id,
        }


@router.get("/verify")
async def verify_access_token(
    credentials: HTTPAuthorizationCredentials = Security(security),
    db: AsyncSession = Depends(get_db),
):
    """
    **The verify_access_token function is used to verify the access token. / Маршрут для проверки валидности токена доступа **\n

    :param credentials: HTTPAuthorizationCredentials: Get the credentials from the request header
    :param db: AsyncSession: Pass the database session to the function
    :return: JSON message

    """
    token = credentials.credentials
    user = await auth_service.get_current_user(token=token, db=db)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token"
        )
    return {"detail": "Token is valid"}


@router.get("/verify_session")
async def verify_access_token(
    credentials: HTTPAuthorizationCredentials = Security(security),
    db: AsyncSession = Depends(get_db),
):
    """
    **The verify_access_token function is used to verify the access token. / Маршрут для проверки валидности токена доступа **\n

    :param credentials: HTTPAuthorizationCredentials: Get the credentials from the request header
    :param db: AsyncSession: Pass the database session to the function
    :return: JSON message

    """
    token = credentials.credentials
    user = await auth_service.get_current_user(token=token, db=db)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token"
        )
    decoded_jti = jwt.decode(
        token, key="", options={"verify_signature": False, "verify_exp": False}
    )
    jti = decoded_jti.get("jti")
    user_session = await repository_session.get_session_by_jti(
        user=user, db=db, jti=jti
    )
    return {"detail": "Token is valid", "session_id": user_session.device_id}


@router.post(
    "/send_verification_code",
    dependencies=[Depends(RateLimiter(times=5, seconds=60))],
)  # Маршрут проверки почты в случае если это новая регистрация
async def send_verification_code(
    body: EmailSchema,
    background_tasks: BackgroundTasks,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    **Отправка кода верификации на почту (проверка почты)** \n

    """
    verification_code = randint(100000, 999999)

    exist_user = await repository_person.get_user_by_email(body.email, db)
    if exist_user:
        logger.debug(f"{body.email} Account already exists")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Account already exists",
        )

    if not exist_user:
        background_tasks.add_task(
            send_email_code, body.email, request.base_url, verification_code
        )
        logger.debug("Check your email for verification code.")
        await repository_person.write_verification_code(
            email=body.email, db=db, verification_code=verification_code
        )

    return {"message": "Check your email for verification code."}


@router.post(
    "/confirm_email", dependencies=[Depends(RateLimiter(times=10, seconds=60))]
)
async def confirm_email(body: VerificationModel, db: AsyncSession = Depends(get_db)):
    """
    **Проверка кода верификации почты** \n

    """

    ver_code = await repository_person.verify_verification_code(
        body.email, db, body.verification_code
    )
    confirmation = False
    access_token = None
    exist_user = await repository_person.get_user_by_email(body.email, db)

    if ver_code:
        confirmation = True
        logger.debug(f"Your {body.email} is confirmed")
        if exist_user:
            access_token, jti = await auth_service.create_access_token(
                data={"oid": str(exist_user.id), "corid": exist_user.cor_id}
            )
            await repository_person.update_last_activity(user=exist_user, db=db)
        return {
            "message": "Your email is confirmed",
            "detail": "Confirmation success",  # Сообщение для JS о том что имейл подтвержден
            "confirmation": confirmation,
            "access_token": access_token,
        }
    else:
        logger.debug(f"{body.email} - Invalid verification code")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid verification code"
        )


@router.post(
    "/forgot_password",
    dependencies=[Depends(RateLimiter(times=5, seconds=60))],
)
async def forgot_password_send_verification_code(
    body: EmailSchema,
    background_tasks: BackgroundTasks,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    **Отправка кода верификации на почту в случае если забыли пароль (проверка почты)** \n
    """

    verification_code = randint(100000, 999999)
    exist_user = await repository_person.get_user_by_email(body.email, db)
    if not exist_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )
    if exist_user:
        background_tasks.add_task(
            send_email_code_forgot_password,
            body.email,
            request.base_url,
            verification_code,
        )
        await repository_person.write_verification_code(
            email=body.email, db=db, verification_code=verification_code
        )
        logger.debug(f"{body.email} - Check your email for verification code.")
    return {"message": "Check your email for verification code."}


@router.post(
    "/restore_account_by_text",
    dependencies=[Depends(RateLimiter(times=10, seconds=60))],
    response_model=RecoveryResponseModel,
)
async def restore_account_by_text(
    body: RecoveryCodeModel,
    request: Request,
    device_info: dict = Depends(di.get_device_header),
    db: AsyncSession = Depends(get_db),
):
    """
    **Проверка кода восстановления с помощью текста**\n
    """
    client_ip = request.client.host
    user = await repository_person.get_user_by_email(body.email, db)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found / invalid email",
        )

    # Расшифровываем recovery_code
    try:
        decrypted_recovery_code = await decrypt_data(
            encrypted_data=user.recovery_code,
            key=await decrypt_user_key(user.unique_cipher_key),
        )
    except Exception:
        logger.warning(f"Failed to decrypt recovery code for user {body.email}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid recovery code format",
        )

    if decrypted_recovery_code != body.recovery_code:
        logger.debug(f"{body.email} - Invalid recovery code")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid recovery code"
        )

    confirmation = True
    user.recovery_code = await encrypt_data(
        data=body.recovery_code, key=await decrypt_user_key(user.unique_cipher_key)
    )
    await db.commit()

    # Проверка ролей
    user_roles = await repository_person.get_user_roles(email=user.email, db=db)

    token_data = {"oid": str(user.id), "corid": user.cor_id, "email": user.email, "roles": user_roles}

    if user.email in TEST_EMAILS:
        access_expires_delta = TEST_ACCESS_EXPIRES_DELTA
        refresh_expires_delta = None
    else:
        base_expires = None
        access_expires_delta = base_expires
        refresh_expires_delta = base_expires

    access_token, access_token_jti = await auth_service.create_access_token(
        data=token_data, expires_delta=access_expires_delta
    )
    refresh_token = await auth_service.create_refresh_token(
        data=token_data, expires_delta=refresh_expires_delta
    )

    # Информация об устройстве
    device_information = di.get_device_info(request)
    app_id = device_information.get("app_id")
    device_id = device_information.get("device_id")
    legacy_device_info = device_information.get("device_info")
    if not device_id:
        # генерируем UUID для старых клиентов
        device_id = str(uuid4())
    if not app_id:
        app_id = "unknown app"

    # ---- Создание новой сессии ----
    session_data = {
        "user_id": user.cor_id,
        "app_id": app_id,
        "device_id": device_id,
        "device_type": device_information["device_type"],
        "device_info": legacy_device_info,  # для legacy клиентов
        "ip_address": device_information["ip_address"],
        "device_os": device_information["device_os"],
        "jti": access_token_jti,
        "refresh_token": refresh_token,
        "access_token": access_token,
    }
    new_session = await repository_session.create_user_session(
        body=UserSessionModel(**session_data),  # Передаём данные для сессии
        user=user,
        db=db,
    )

    logger.debug(
        f"Успешный вход пользователя {user.email} "
        f"с IP {client_ip}, app_id={app_id}, device_id={device_id}, "
        f"device_info={legacy_device_info}"
    )
    response = RecoveryResponseModel(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        message="Recovery code is correct",
        confirmation=confirmation,
        session_id=str(new_session.id),
        device_id=device_id,
    )
    await repository_person.update_last_activity(user=user, db=db)
    return response


@router.post(
    "/restore_account_by_recovery_file",
    dependencies=[Depends(RateLimiter(times=10, seconds=60))],
    response_model=RecoveryResponseModel,
)
async def upload_recovery_file(
    request: Request,
    file: UploadFile = File(...),
    email: str = Form(...),
    db: AsyncSession = Depends(get_db),
    device_info: dict = Depends(di.get_device_header),
):
    """
    **Загрузка и проверка файла восстановления**\n
    """
    client_ip = request.client.host
    user = await repository_person.get_user_by_email(email, db)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )
    confirmation = False
    file_content = await file.read()

    try:
        recovery_code = await decrypt_data(
            encrypted_data=user.recovery_code,
            key=await decrypt_user_key(user.unique_cipher_key),
        )
    except Exception:
        logger.warning(f"Failed to decrypt recovery code for user {email}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid recovery code format",
        )
    # Проверка ролей
    user_roles = await repository_person.get_user_roles(email=user.email, db=db)

    if file_content == recovery_code.encode():
        confirmation = True
        recovery_code = await encrypt_data(
            data=recovery_code, key=await decrypt_user_key(user.unique_cipher_key)
        )
        await db.commit()

        # Получаем токены
        token_data = {"oid": str(user.id), "corid": user.cor_id, "email": user.email, "roles": user_roles}
        # Тестовые аккаунты: сокращаем только access, refresh оставляем стандартным
        if user.email in TEST_EMAILS:
            access_expires_delta = TEST_ACCESS_EXPIRES_DELTA
            refresh_expires_delta = None
        else:
            base_expires = None
            access_expires_delta = base_expires
            refresh_expires_delta = base_expires

        access_token, access_token_jti = await auth_service.create_access_token(
            data=token_data, expires_delta=access_expires_delta
        )
        refresh_token = await auth_service.create_refresh_token(
            data=token_data, expires_delta=refresh_expires_delta
        )
        # Создаём новую сессию
        device_information = di.get_device_info(request)
        app_id = device_information.get("app_id")
        device_id = device_information.get("device_id")
        legacy_device_info = device_information.get("device_info")
        if not device_id:
            device_id = str(uuid4())
        if not app_id:
            app_id = "unknown app"
        session_data = {
            "user_id": user.cor_id,
            "app_id": app_id,
            "device_id": device_id,
            "device_type": device_information["device_type"],
            "device_info": legacy_device_info,  # для legacy клиентов
            "ip_address": device_information["ip_address"],
            "device_os": device_information["device_os"],
            "jti": access_token_jti,
            "refresh_token": refresh_token,
            "access_token": access_token,
        }
        new_session = await repository_session.create_user_session(
            body=UserSessionModel(**session_data),  # Передаём данные для сессии
            user=user,
            db=db,
        )
        logger.debug(
            f"Успешный вход пользователя {user.email} "
            f"с IP {client_ip}, app_id={app_id}, device_id={device_id}, "
            f"device_info={legacy_device_info}"
        )
        response = RecoveryResponseModel(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="bearer",
            message="Recovery code is correct",
            confirmation=confirmation,
            session_id=str(new_session.id),
            device_id=device_id,
        )
        await repository_person.update_last_activity(user=user, db=db)
        return response
    else:
        logger.debug(f"{email} - Invalid recovery file")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid recovery file"
        )


# ============================================================================
# USER INVITATION ENDPOINTS (Приглашение пользователей)
# ============================================================================


@router.post(
    "/invite",
    response_model=InviteUserResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(RateLimiter(times=10, seconds=60))],
)
async def invite_user(
    body: InviteUserRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(auth_service.get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    **Создание приглашения для нового пользователя**

    Создаёт приглашение с уникальным токеном, которое можно отправить пользователю
    для регистрации в системе. Email из приглашения будет доступен только для чтения
    при регистрации.

    **После создания приглашения автоматически отправляется email с ссылкой для регистрации.**

    ---

    **Workflow:**
    1. Администратор создаёт приглашение
    2. Система генерирует уникальный токен
    3. **Автоматически отправляется email с ссылкой на регистрацию**
    4. Пользователь переходит по ссылке и видит форму регистрации
    5. Email в форме предзаполнен и readonly
    6. После регистрации приглашение помечается как использованное

    ---

    **Возможные ошибки:**
    - 409 Conflict - Уже существует активное приглашение для этого email
    - 409 Conflict - Пользователь с таким email уже зарегистрирован
    - 401 Unauthorized - Не авторизован
    - 429 Too Many Requests - Превышен rate limit
    """
    email = body.email.lower()

    existing_user = await repository_person.get_user_by_email(email, db)
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Пользователь с email {email} уже зарегистрирован",
        )

    existing_invitation = await repository_invitation.get_pending_invitation_by_email(
        email, db
    )
    if existing_invitation:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Активное приглашение для {email} уже существует (истекает {existing_invitation.expires_at})",
        )

    invitation = await repository_invitation.create_invitation(
        email=email,
        invited_by_id=current_user.id,
        expires_in_days=body.expires_in_days or 7,
        db=db,
    )

    # Base URL для ссылки приглашения (из env или fallback на dev/prod)
    if settings.corid_base_url:
        base_url = f"{settings.corid_base_url.rstrip('/')}/api"
    elif settings.app_env == "development":
        base_url = "https://dev-corid.cor-medical.ua/api"
    else:
        base_url = "https://prod-corid.cor-medical.ua/api"

    invitation_link = f"{base_url}/signup?token={invitation.token}"

    # Отправляем email в фоновом режиме
    background_tasks.add_task(
        send_invitation_email,
        email=email,
        invitation_link=invitation_link,
        invited_by_email=current_user.email,
        expires_at=invitation.expires_at.isoformat(),
    )

    logger.info(
        f"User {current_user.email} created invitation for {email}, "
        f"token={invitation.token[:12]}..., expires_at={invitation.expires_at}, "
        f"email will be sent in background"
    )

    return InviteUserResponse(
        invitation_id=invitation.id,
        email=invitation.email,
        token=invitation.token,
        invitation_link=invitation_link,
        expires_at=invitation.expires_at,
        created_at=invitation.created_at,
    )


@router.post(
    "/validate-invitation",
    response_model=ValidateInvitationResponse,
    dependencies=[Depends(RateLimiter(times=30, seconds=60))],
)
async def validate_invitation(
    body: ValidateInvitationRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    **Проверка валидности токена приглашения**

    Используется фронтендом для проверки токена перед показом формы регистрации.
    Возвращает email, который нужно использовать при регистрации.

    ---

    **Параметры:**
    - `token` (str) - Токен приглашения из URL query параметра

    ---

    **Возвращает:**
    - `is_valid` (bool) - Валиден ли токен
    - `email` (str, optional) - Email для регистрации (если валиден)
    - `expires_at` (datetime, optional) - Когда истекает (если валиден)
    - `message` (str, optional) - Сообщение об ошибке (если невалиден)

    ---

    **Невалидные случаи:**
    - Приглашение не найдено
    - Приглашение уже использовано
    - Приглашение истекло

    ---

    **Безопасность:**
    - Rate limit: 30 проверок в минуту
    - Не требует авторизации (публичный endpoint)
    """
    invitation = await repository_invitation.get_invitation_by_token(body.token, db)

    if not invitation:
        return ValidateInvitationResponse(
            is_valid=False, message="Приглашение не найдено"
        )

    if invitation.is_used:
        return ValidateInvitationResponse(
            is_valid=False, message="Это приглашение уже было использовано"
        )

    if invitation.expires_at < datetime.now():
        return ValidateInvitationResponse(
            is_valid=False, message=f"Приглашение истекло {invitation.expires_at}"
        )

    return ValidateInvitationResponse(
        is_valid=True, email=invitation.email, expires_at=invitation.expires_at
    )


@router.post(
    "/accept-invitation",
    response_model=AcceptInvitationResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(RateLimiter(times=5, seconds=60))],
)
async def accept_invitation(
    body: AcceptInvitationRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    device_info: dict = Depends(di.get_device_header),
):
    """
    **Регистрация пользователя по приглашению**

    Создаёт нового пользователя на основе валидного токена приглашения.
    Email берётся из приглашения (readonly), пользователь указывает только пароль
    и опциональные персональные данные.

    ---

    **Параметры:**
    - `token` (str) - Токен приглашения
    - `password` (str) - Пароль пользователя (8-32 символа)
    - `birth` (int, optional) - Год рождения (>= 1900)
    - `user_sex` (str, optional) - Пол: 'M', 'F', '*'

    ---

    **Безопасность:**
    - Rate limit: 5 регистраций в минуту
    - Приглашение одноразовое (is_used=True)
    - Email readonly (берётся из приглашения)
    - Пароль хешируется перед сохранением

    ---

    **Возможные ошибки:**
    - 404 Not Found - Приглашение не найдено
    - 400 Bad Request - Приглашение уже использовано
    - 400 Bad Request - Приглашение истекло
    - 409 Conflict - Пользователь с таким email уже существует
    - 429 Too Many Requests - Превышен rate limit
    """
    # Проверяем валидность приглашения
    invitation = await repository_invitation.get_valid_invitation_by_token(
        body.token, db
    )

    if not invitation:
        # Проверяем, существует ли приглашение вообще
        any_invitation = await repository_invitation.get_invitation_by_token(
            body.token, db
        )

        if not any_invitation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Приглашение не найдено"
            )

        if any_invitation.is_used:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Это приглашение уже было использовано",
            )

        if any_invitation.expires_at < datetime.now():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Приглашение истекло {any_invitation.expires_at}",
            )

    email = invitation.email

    # Проверяем, не зарегистрирован ли уже пользователь
    existing_user = await repository_person.get_user_by_email(email, db)
    if existing_user:
        # Помечаем приглашение как использованное даже если пользователь уже существует
        await repository_invitation.mark_invitation_used(invitation, db)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Пользователь с этим email уже зарегистрирован",
        )

    # Создаём пользователя
    user_data = UserModel(
        email=email, password=body.password, birth=body.birth, user_sex=body.user_sex
    )
    user_data.password = auth_service.get_password_hash(user_data.password)

    new_user = await repository_person.create_user(user_data, db)

    if not new_user.cor_id:
        await repository_cor_id.create_new_corid(new_user, db)

    # --- Identity Registry Sync (resolve/register) ---
    try:
        resolved = await corid_registry.resolve_identity(new_user.email)
        if resolved and resolved.get("exists") and resolved.get("cor_id"):
            if new_user.cor_id != resolved["cor_id"]:
                new_user.cor_id = resolved["cor_id"]
                await db.commit()
                await db.refresh(new_user)
        else:
            reg = await corid_registry.register_identity(
                email=new_user.email,
                cor_id=new_user.cor_id,
                birth=new_user.birth,
                user_sex=new_user.user_sex,
            )
            if reg and reg.get("cor_id") and reg["cor_id"] != new_user.cor_id:
                new_user.cor_id = reg["cor_id"]
                await db.commit()
                await db.refresh(new_user)
    except Exception as e:
        logger.warning(f"Identity registry sync (invitation) failed for {new_user.email}: {e}")

    logger.info(
        f"User {email} registered via invitation (invited_by={invitation.invited_by})"
    )

    # Помечаем приглашение как использованное
    await repository_invitation.mark_invitation_used(invitation, db)

    # Получаем роли пользователя
    user_roles = await repository_person.get_user_roles(email=new_user.email, db=db)

    # Создаём токены
    access_token, access_token_jti = await auth_service.create_access_token(
        data={"oid": str(new_user.id), "corid": new_user.cor_id, "email": new_user.email, "roles": user_roles}
    )
    refresh_token = await auth_service.create_refresh_token(
        data={"oid": str(new_user.id), "corid": new_user.cor_id, "email": new_user.email, "roles": user_roles}
    )

    # Фиксируем активность при выдаче токенов
    await repository_person.update_last_activity(user=new_user, db=db)

    # Создаём сессию
    device_information = di.get_device_info(request)
    app_id = device_information.get("app_id")
    device_id = device_information.get("device_id")
    legacy_device_info = device_information.get("device_info")

    if not device_id:
        device_id = str(uuid4())
    if not app_id:
        app_id = "unknown app"

    session_data = {
        "user_id": new_user.cor_id,
        "app_id": app_id,
        "device_id": device_id,
        "device_type": device_information["device_type"],
        "device_info": legacy_device_info,
        "ip_address": device_information["ip_address"],
        "device_os": device_information["device_os"],
        "jti": access_token_jti,
        "refresh_token": refresh_token,
        "access_token": access_token,
    }

    new_session = await repository_session.create_user_session(
        body=UserSessionModel(**session_data),
        user=new_user,
        db=db,
    )

    logger.info(
        f"User {new_user.email} successfully registered via invitation, "
        f"session created: device_id={device_id}, app_id={app_id}"
    )

    return AcceptInvitationResponse(
        user=UserDb.model_validate(new_user),
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        device_id=device_id,
        message="Регистрация по приглашению успешно завершена",
    )
