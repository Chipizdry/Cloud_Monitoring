import json
import asyncio
import ssl
from typing import Optional
from urllib import request, parse, error
from loguru import logger
from backend.config.config import settings

INTERNAL_RESOLVE_PATH = "/api/internal/identity/resolve"
INTERNAL_REGISTER_PATH = "/api/internal/identity/register"


def _build_url(base: str, path: str) -> str:
    base = base.rstrip("/")
    if not path.startswith("/"):
        path = "/" + path
    return f"{base}{path}"


def _create_ssl_context() -> ssl.SSLContext:
    """Create SSL context that doesn't verify certificates for internal services."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


async def _http_get_json(url: str, headers: dict) -> dict:
    def _do() -> dict:
        req = request.Request(url, headers=headers, method="GET")
        context = _create_ssl_context()
        with request.urlopen(req, timeout=10, context=context) as resp:
            return json.loads(resp.read().decode("utf-8"))

    return await asyncio.to_thread(_do)


async def _http_post_json(url: str, payload: dict, headers: dict) -> dict:
    def _do() -> dict:
        data = json.dumps(payload).encode("utf-8")
        hdrs = {"Content-Type": "application/json"}
        hdrs.update(headers)
        req = request.Request(url, data=data, headers=hdrs, method="POST")
        context = _create_ssl_context()
        with request.urlopen(req, timeout=10, context=context) as resp:
            return json.loads(resp.read().decode("utf-8"))

    return await asyncio.to_thread(_do)


def _auth_headers() -> dict:
    headers = {}
    api_key = settings.corid_internal_api_key
    if api_key:
        headers["X-API-Key"] = api_key
    # Add correct Host header when using IP address
    if settings.corid_base_url and "192.168." in settings.corid_base_url:
        headers["Host"] = "dev.corid.cor-int.com"
    logger.debug(f"Auth headers: X-API-Key={'***' + api_key[-4:] if api_key else 'NOT SET'}")
    return headers


async def resolve_identity(email: str) -> Optional[dict]:
    """Resolve cor_id by email from Cor-ID internal registry.

    Returns dict {"exists": bool, "cor_id": str, "email": str} or None on error.
    """
    try:
        base = settings.corid_base_url
        if not base:
            logger.warning("CORID_BASE_URL is not set for internal resolve")
            return None
        
        url = _build_url(base, INTERNAL_RESOLVE_PATH) + "?" + parse.urlencode({"email": email})
        logger.debug(f"resolve_identity: attempting to resolve email={email}")
        logger.debug(f"resolve_identity: target URL={url}")
        logger.debug(f"resolve_identity: CORID_BASE_URL={base}")
        
        data = await _http_get_json(url, headers=_auth_headers())
        logger.info(f"resolve_identity: successfully resolved email={email}, exists={data.get('exists')}")
        return data
    except error.HTTPError as e:
        try:
            error_body = e.read().decode('utf-8')
            logger.warning(f"resolve_identity HTTPError for email={email}: {e.code} {e.reason} | Response: {error_body} | URL: {_build_url(settings.corid_base_url or '', INTERNAL_RESOLVE_PATH)}")
        except:
            logger.warning(f"resolve_identity HTTPError for email={email}: {e.code} {e.reason} | URL: {_build_url(settings.corid_base_url or '', INTERNAL_RESOLVE_PATH)}")
        return None
    except error.URLError as e:
        logger.warning(f"resolve_identity URLError for email={email}: {e.reason} | URL: {_build_url(settings.corid_base_url or '', INTERNAL_RESOLVE_PATH)} | Cor-ID service unavailable")
        return None
    except Exception as e:
        logger.error(f"resolve_identity failed for email={email}: {type(e).__name__}: {e} | URL: {_build_url(settings.corid_base_url or '', INTERNAL_RESOLVE_PATH)}")
        return None


async def register_identity(email: str, cor_id: str, birth: Optional[int] = None, user_sex: Optional[str] = None) -> Optional[dict]:
    """Register email->cor_id mapping in Cor-ID internal registry (idempotent).

    Returns dict like {"cor_id": str, "created": bool, ...} or None on error.
    """
    try:
        base = settings.corid_base_url
        if not base:
            logger.warning("CORID_BASE_URL is not set for internal register")
            return None
        
        url = _build_url(base, INTERNAL_REGISTER_PATH)
        payload = {"email": email, "cor_id": cor_id}
        if birth is not None:
            payload["birth"] = birth
        if user_sex is not None:
            payload["user_sex"] = user_sex
        
        logger.debug(f"register_identity: attempting to register email={email}, cor_id={cor_id}")
        logger.debug(f"register_identity: target URL={url}")
        logger.debug(f"register_identity: CORID_BASE_URL={base}")
        logger.debug(f"register_identity: payload={payload}")
        
        data = await _http_post_json(url, payload, headers=_auth_headers())
        logger.info(f"register_identity: successfully registered email={email}, cor_id={cor_id}, created={data.get('created')}")
        return data
    except error.HTTPError as e:
        try:
            error_body = e.read().decode('utf-8')
            logger.warning(f"register_identity HTTPError for email={email}, cor_id={cor_id}: {e.code} {e.reason} | Response: {error_body} | URL: {_build_url(settings.corid_base_url or '', INTERNAL_REGISTER_PATH)}")
        except:
            logger.warning(f"register_identity HTTPError for email={email}, cor_id={cor_id}: {e.code} {e.reason} | URL: {_build_url(settings.corid_base_url or '', INTERNAL_REGISTER_PATH)}")
        return None
    except error.URLError as e:
        logger.warning(f"register_identity URLError for email={email}, cor_id={cor_id}: {e.reason} | URL: {_build_url(settings.corid_base_url or '', INTERNAL_REGISTER_PATH)} | Cor-ID service unavailable")
        return None
    except Exception as e:
        logger.error(f"register_identity failed for email={email}, cor_id={cor_id}: {type(e).__name__}: {e} | URL: {_build_url(settings.corid_base_url or '', INTERNAL_REGISTER_PATH)}")
        return None