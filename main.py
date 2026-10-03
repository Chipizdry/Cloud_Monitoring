import asyncio
import time
import uvicorn
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from fastapi import FastAPI, Request, Depends, HTTPException, status, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from prometheus_client import Counter, Histogram, CONTENT_TYPE_LATEST, generate_latest

from starlette.middleware.trustedhost import TrustedHostMiddleware

from fastapi_limiter import FastAPILimiter

from backend.services.shared.init_rbac import init_rbac
from backend.version import __version__
from backend.database.db import get_db, async_session_maker
from backend.database.redis_db import redis_client
from backend.services.shared.websocket_events_manager import websocket_events_manager

from backend.routes import (
    user,
    shared,
    roles, 
    energy, 
    devices,

)
from backend.routes.devices import vendors_router
from backend.config.config import settings
from backend.services.shared.ip2_location import initialize_ip2location
from loguru import logger
from fastapi.responses import JSONResponse
from collections import defaultdict

from backend.services.shared.websocket import (
    check_session_timeouts,
    cleanup_auth_sessions,
    register_signature_expirer,
)

from backend.services.shared.logger import setup_logging

setup_logging()


all_licenses_info = [
    {
        "name": "IP2Location LITE Database License",
        "url": "https://lite.ip2location.com/",
        "description": "Используется для IP-геолокации. Требуется указание ссылки как часть условий лицензии.",
    },
    {
        "name": "OpenSlide (LGPL v2.1)",
        "url": "https://openslide.org/license/",
        "description": "Библиотека для чтения изображений с микроскопа. Распространяется под LGPL v2.1.",
    },
    {
        "name": "Psycopg (LGPL 3.0 / Modified BSD)",
        "url": "https://www.psycopg.org/docs/license.html",
        "description": "PostgreSQL адаптер для Python. Распространяется под двойной лицензией LGPL 3.0 или Modified BSD.",
    },
    {
        "name": "MIT License",
        "url": "https://opensource.org/licenses/MIT",
        "description": "Многие компоненты API распространяются под разрешительной лицензией MIT.",
    },
    {
        "name": "Apache License 2.0",
        "url": "https://www.apache.org/licenses/LICENSE-2.0",
        "description": "Некоторые компоненты API распространяются под разрешительной лицензией Apache 2.0.",
    },
    {
        "name": "BSD 3-Clause License",
        "url": "https://opensource.org/licenses/BSD-3-Clause",
        "description": "Некоторые компоненты API распространяются под разрешительной лицензией BSD 3-Clause.",
    },
    {
        "name": "BSD 2-Clause License",
        "url": "https://opensource.org/licenses/BSD-2-Clause",
        "description": "Некоторые компоненты API распространяются под разрешительной лицензией BSD 2-Clause.",
    },
]

api_description = """
**COR-ID API** - это основной сервис идентификации и аутентификации пользователей в системе COR

---

### Используемые лицензии:
"""
for lic in all_licenses_info:
    api_description += f"- **{lic['name']}**: [Подробнее]({lic['url']})"
    if "description" in lic:
        api_description += f" - {lic['description']}"
    api_description += "\n"

api_description += """
---
*Все торговые марки являются собственностью их соответствующих владельцев.*
"""


app = FastAPI(
    title="COR-ID API",
    description=api_description,
    version="1.0.1",
    openapi_url=(
        "/openapi.json" if settings.docs_enabled else None
    ),
    docs_url="/docs" if settings.docs_enabled else None,
    redoc_url="/redoc" if settings.docs_enabled else None
)

app.mount("/static", StaticFiles(directory="frontend"), name="static")
app.mount("/backend/DevicesSchemas", StaticFiles(directory="backend/DevicesSchemas"), name="devices_schemas")

origins = settings.allowed_cors_origins

HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total", "Total HTTP requests", ["method", "endpoint", "status_code"]
)

HTTP_REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "endpoint", "status_code"],
    buckets=(
        0.005,
        0.01,
        0.025,
        0.05,
        0.075,
        0.1,
        0.25,
        0.5,
        0.75,
        1.0,
        2.5,
        5.0,
        7.5,
        10.0,
        float("inf"),
    ),
)


# Кастомный middleware для сбора метрик
@app.middleware("http")
async def prometheus_metrics_middleware(request: Request, call_next):
    # Получаем route для endpoint (не path с параметрами)
    endpoint = request.url.path
    for route in app.routes:
        match = route.matches(request.scope)
        if match[0] == 2:  # Match.FULL
            endpoint = route.path
            break

    method = request.method

    # Засекаем время
    start_time = time.time()

    try:
        response = await call_next(request)
        status_code = response.status_code
    except Exception as e:
        status_code = 500
        raise
    finally:
        # Записываем метрики
        duration = time.time() - start_time
        HTTP_REQUESTS_TOTAL.labels(
            method=method, endpoint=endpoint, status_code=status_code
        ).inc()
        HTTP_REQUEST_DURATION.labels(
            method=method, endpoint=endpoint, status_code=status_code
        ).observe(duration)

    return response


# Endpoint для метрик
@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint"""
    from prometheus_client import REGISTRY

    # Используем дефолтный registry со всеми метриками
    data = generate_latest(REGISTRY)
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)


# Дополнительные кастомные метрики
REQUEST_COUNT = Counter("app_requests_total", "Total number of requests")
REQUEST_LATENCY = Histogram("app_request_latency_seconds", "Request latency")

# Middleware для CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Обработчики исключений
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(Exception)
async def exception_handler(request: Request, exc: Exception):
    logger.error("An unhandled exception occurred", exc_info=exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal Server Error"},
    )


@app.exception_handler(ValueError)
async def validation_exception_handler(request: Request, exc: ValueError):
    return JSONResponse(
        # logger.error(f"Произошла ошибка валидации: {str(exc)}"),
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": "Произошла ошибка валидации", "error": str(exc)},
    )


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(
    request: Request, exc: RequestValidationError
):
    sanitized_errors = []
    for error in exc.errors():
        sanitized_errors.append(
            {
                "type": error.get("type"),
                "loc": error.get("loc"),
                "msg": error.get("msg"),
            }
        )

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": sanitized_errors},
    )


# Маршруты
@app.get("/config")
def read_config():
    return {"ENV": settings.app_env}


@app.get("/", name="Корень")
def read_root(request: Request):
    REQUEST_COUNT.inc()
    with REQUEST_LATENCY.time():
        return FileResponse("frontend/COR_ID/login.html")


@app.get("/version")
async def get_version():
    return {"version": __version__}


@app.get("/api/healthchecker")
async def healthchecker(db: AsyncSession = Depends(get_db)):
    REQUEST_COUNT.inc()
    try:
        result = await db.execute(text("SELECT 1"))
        if result is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Database is not configured correctly",
            )
        return {"message": "Welcome to FastApi, database work correctly"}
    except Exception as e:
        logger.error("Database connection error", exc_info=e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Error connecting to the database",
        )


app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)


# Middleware для добавления заголовка времени обработки
@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time
    response.headers["My-Process-Time"] = str(process_time)
    return response


async def custom_identifier(request: Request) -> str:
    return request.client.host


# Событие при старте приложения
@app.on_event("startup")
async def startup():
    logger.info("------------- STARTUP --------------")
    await FastAPILimiter.init(redis_client, identifier=custom_identifier)
    asyncio.create_task(check_session_timeouts())
    asyncio.create_task(cleanup_auth_sessions())
    register_signature_expirer(app, async_session_maker)
    initialize_ip2location()
    await websocket_events_manager.init_redis_listener()

    logger.info("Initializing RBAC system...")
    success = await init_rbac(create_permissions=False)
    if success:
        logger.success("✅ RBAC system initialized")
    else:
        logger.error("⚠️ RBAC initialization failed, continuing without RBAC")
    
    # Запуск Energy Services (Worker only - WebSocket сервер запускается отдельно через entrypoint.sh)
    logger.info("🚀 Starting Energy Services (Worker)...")
    from energy_worker import main_worker_entrypoint

    # Broadcast tasks already run in the dedicated WebSocket server on port 45762.
    # Keep this disabled to avoid launching the same tasks in both processes.
    # from backend.services.energy.websocket_broadcast import BroadcastTaskManager
    # broadcast_manager = BroadcastTaskManager()
    # await broadcast_manager.load_from_db()
    # logger.info("✅ Broadcast tasks loaded from database")

    # Запускаем Energy Worker
    asyncio.create_task(main_worker_entrypoint())
    logger.info("✅ Energy Worker started as background task")
    logger.info(f"ℹ️  WebSocket Server runs separately on port {settings.websocket_port} (started by entrypoint.sh)")


@app.on_event("shutdown")
async def shutdown_event():
    logger.info("------------- SHUTDOWN --------------")


# Domain-based router registration
app.include_router(user.router, prefix="/api")
app.include_router(shared.router, prefix="/api")
app.include_router(roles.router, prefix="/api")
app.include_router(energy.router, prefix="/api")
app.include_router(devices.router, prefix="/api")
app.include_router(vendors_router.router, prefix="/api")

if __name__ == "__main__":
    uvicorn.run(
        app="main:app",
        host=getattr(settings, "api_host", "0.0.0.0") or "0.0.0.0",
        port=getattr(settings, "api_port", 8000) or 8000,
        log_level="info",
        access_log=True,
        reload=settings.reload,
    )

