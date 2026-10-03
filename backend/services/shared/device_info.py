from fastapi import Header, HTTPException, Request, status
import ipaddress


def get_device_header(
    user_agent: str = Header(None, description="User-Agent header"),
    x_device_type: str = Header(None, description="X-Device-Type header"),
    x_device_os: str = Header(None, description="X-Device-OS header"),
    x_device_info: str = Header(None, description="X-Device-Info header"),
    x_app_id: str = Header(None, description="X-App-Id"),
    x_device_id: str = Header(None, description="X-Device-Id"),
) -> dict:
    """
    Получает информацию об устройстве из заголовков.
    """
    if not user_agent and not x_device_type:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing required headers: User-Agent or X-Device-Type",
        )

    return {
        "device_type": x_device_type or "Desktop",  # Тип устройства
        "device_info": x_device_info or user_agent,  # Информация об устройстве
        "device_os": x_device_os or "Unknown OS",  # Операционная система
        "app_id": x_app_id,
        "device_id": x_device_id,
    }


def get_device_info(request: Request) -> dict:
    """
    Получает информацию об устройстве из запроса.
    Поддерживает как веб-браузеры, так и мобильные приложения.
    """
    user_agent = request.headers.get("User-Agent", "Unknown device")
    ip_address = get_client_ip(request)

    device_type = "Desktop"
    device_os = "Unknown OS"
    app_id = None
    device_id = None

    is_mobile_app = request.headers.get("X-Device-Type") is not None

    if is_mobile_app:
        device_type = request.headers.get("X-Device-Type", "Mobile")
        device_os = request.headers.get("X-Device-OS", "Unknown OS")
        device_info = request.headers.get("X-Device-Info", "Unknown device")
        app_id = request.headers.get("X-App-Id")  # уникальный id приложения
        device_id = request.headers.get("X-Device-Id")  # уникальный id устройства
    else:
        device_info = user_agent

        if "Mobile" in user_agent or "iPhone" in user_agent or "Android" in user_agent:
            device_type = "Mobile"

        if "Windows" in user_agent:
            device_os = "Windows"
        elif "Mac OS" in user_agent:
            device_os = "Mac OS"
        elif "iPhone" in user_agent:
            device_os = "iOS"
        elif "Android" in user_agent:
            device_os = "Android"
        elif "Linux" in user_agent:
            device_os = "Linux"

    return {
        "device_type": device_type,  # Тип устройства (Mobile, Desktop и т.д.)
        "device_info": device_info,  # Информация об устройстве
        "ip_address": ip_address,  # IP-адрес
        "device_os": device_os,  # Операционная система
        "app_id": app_id,  # Уникальный ID приложения (только для мобилок)
        "device_id": device_id,  # Уникальный ID устройства (только для мобилок)
    }


def get_client_ip(request: Request):
    """Получение реального IP-адреса клиента."""
    client_host = request.client.host if request.client else None
    x_real_ip = request.headers.get("x-real-ip")
    x_forwarded_for = request.headers.get("x-forwarded-for")
    http_client_ip = request.headers.get("http_client_ip")

    return (
        _normalize_ip(client_host)
        or _normalize_ip(x_real_ip)
        or _extract_ip_from_forwarded_for(x_forwarded_for)
        or _normalize_ip(http_client_ip)
        or "unknown"
    )


def _normalize_ip(ip_value: str | None) -> str | None:
    """Нормализует IPv4/IPv6 и отбрасывает порт, если он присутствует."""
    if not ip_value:
        return None

    candidate = str(ip_value).strip().strip('"').strip("'")
    if not candidate:
        return None

    if candidate.startswith("[") and "]" in candidate:
        candidate = candidate[1 : candidate.index("]")]
    elif candidate.count(":") == 1 and "." in candidate:
        candidate = candidate.rsplit(":", 1)[0]

    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None


def _extract_ip_from_forwarded_for(header_value: str | None) -> str | None:
    """Извлекает первый валидный IP из X-Forwarded-For цепочки."""
    if not header_value:
        return None

    for raw_ip in header_value.split(","):
        normalized = _normalize_ip(raw_ip)
        if normalized:
            return normalized

    return None
