
# COR Monitoring API

Unified API для системы мониторинга COR, включающая:
- **COR-ID**: аутентификация и управление пользователями
- **Energy System**: мониторинг энергетических устройств (Victron Cerbo GX, Deye Inverters)
- **Devices**: управление WebSocket устройствами (ESP32, Raspberry Pi)

---

## Быстрый старт (локально)

1) Python 3.12, virtualenv: `python -m venv .venv && source .venv/bin/activate`
2) Зависимости: `pip install -r requirements.txt`
3) Переменные окружения: настройте `.env` и заполните секреты (DB, Redis, SMTP, JWT, Telegram и т.д.)
4) Миграции: `alembic upgrade head`
5) Запуск API: `uvicorn main:app --reload`

При старте автоматически запускаются:
- Main API (REST endpoints на порту 8000)
- Energy Worker (опрос Modbus устройств)
- WebSocket Server 
- Telegram Monitor Manager

## Автогенерация миграций

При запущенном контейнере:
```bash
docker exec dev-fastapi-monitoring alembic revision --autogenerate -m "описание_миграции"
```

Локально:
```bash
alembic revision --autogenerate -m "описание_миграции"
alembic upgrade head
```

## Запуск в Docker

```bash
# Установите переменные окружения
export CORMONITORING_ENV=dev

# Запустите все сервисы
docker-compose --env-file dev-cormonitoring.cor-medical.ua.env up --build
```


## Тесты и качество

- Тесты: `pytest`
- Форматирование: `black .`

---


**Инфраструктура:**
- PostgreSQL: основное хранилище
- Redis: кэш, rate limiting, pub/sub
- Prometheus/Grafana/Loki: мониторинг и логи


## Лицензии

Список лицензий сторонних зависимостей: [LICENSES.md](LICENSES.md)

