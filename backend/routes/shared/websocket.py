import asyncio
import json
import base64
import os
from datetime import datetime, timezone
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, HTTPException
from loguru import logger

from backend.database.models import AuthSessionStatus, User
from backend.repository.user.user_session import get_auth_session_by_token, update_session_status
from backend.repository.user import person as repository_person
from backend.repository.user import user_session as repository_session
from backend.services.shared import websocket as ws
from backend.services.shared.websocket_events_manager import websocket_events_manager
from backend.services.user.auth import auth_service
from backend.schemas import UserModel as _UserModel
from backend.schemas import UserSessionModel
from backend.config.config import settings
from backend.database.redis_db import redis_client
from sqlalchemy.ext.asyncio import AsyncSession
from backend.database.db import get_db, async_session_maker


router = APIRouter(prefix="/websockets", tags=["Websockets"])

# Test emails with extended token expiry
TEST_EMAILS = getattr(settings, "test_emails", [])
TEST_ACCESS_EXPIRES_DELTA = None


async def _corid_post_json(path: str, payload: dict) -> dict:
    """Call COR-ID endpoint for WebSocket polling."""
    corid_base = settings.corid_base_url.rstrip("/") if settings.corid_base_url else None
    if not corid_base:
        raise HTTPException(status_code=500, detail="COR-ID base URL is not configured")

    url = f"{corid_base}{path}"
    headers: dict[str, str] = {}
    if "192.168." in corid_base:
        headers["Host"] = "dev.corid.cor-int.com"

    try:
        import httpx
        async with httpx.AsyncClient(timeout=10, headers=headers or None) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.error(f"COR-ID {path} error: {e}")
        raise


async def _poll_corid_and_push_events(session_token: str, db_session_id: str):
    """
    Background task that polls COR-ID check_session_status and pushes WebSocket events.
    Follows the pattern from CORID_FINAL_LOGIN_PORTING_GUIDE.md section 8.
    """
    qr_scanned_sent = False
    poll_count = 0
    
    try:
        # Get COR-ID session token from Redis bridge
        bridge_key = f"oauth:bridge:{session_token}"
        corid_session_token = await redis_client.get(bridge_key)
        
        if not corid_session_token:
            # Fallback: try oauth:session
            session_key = f"oauth:session:{session_token}"
            session_data = await redis_client.get(session_key)
            if session_data:
                session_info = json.loads(session_data if isinstance(session_data, str) else session_data.decode("utf-8"))
                corid_session_token = session_info.get("corid_session_token")
        
        if isinstance(corid_session_token, bytes):
            corid_session_token = corid_session_token.decode("utf-8")
            
        if not corid_session_token:
            await websocket_events_manager.send_to_session(
                session_token,
                {
                    "event": "auth_error",
                    "error": "Session bridge not found",
                }
            )
            return
        
        # Poll COR-ID every 1.5 seconds
        while poll_count < 400:  # Max 10 minutes
            poll_count += 1
            await asyncio.sleep(1.5)
            
            try:
                corid_status = await _corid_post_json(
                    "/api/auth/v1/check_session_status",
                    {"session_token": corid_session_token}
                )
                
                # Check for qr_scanned flag (only send once)
                if not qr_scanned_sent:
                    qr_scanned = corid_status.get("qr_scanned") or corid_status.get("is_qr_scanned") or corid_status.get("scanned")
                    if qr_scanned:
                        await websocket_events_manager.send_to_session(
                            session_token,
                            {
                                "event": "qr_scanned",
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            }
                        )
                        qr_scanned_sent = True
                        logger.info(f"QR scanned for session {session_token[:8]}...")
                
                confirmation_status = (corid_status.get("status") or "").lower()
                
                # Handle rejected status
                if confirmation_status == "rejected":
                    # Update database session status
                    async with async_session_maker() as db:
                        auth_session = await get_auth_session_by_token(session_token, db)
                        if auth_session:
                            await update_session_status(auth_session, "rejected", db)
                    
                    # Send WebSocket event
                    await websocket_events_manager.send_to_session(
                        session_token,
                        {
                            "event": "auth_rejected",
                            "status": "rejected",
                            "message": "Вход отменен пользователем",
                        }
                    )
                    logger.info(f"Auth rejected for session {session_token[:8]}...")
                    break
                
                # Handle approved status
                if confirmation_status == "approved":
                    try:
                        # Extract identity
                        approved_corid = corid_status.get("cor_id")
                        approved_email = corid_status.get("email")
                        
                        if not approved_email:
                            raise ValueError("COR-ID did not return user email")
                        
                        # Create tokens and user session
                        async with async_session_maker() as db:
                            # Find or create user
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
                                new_user_model = _UserModel(email=approved_email, password=random_pass)
                                new_user_model.password = auth_service.get_password_hash(new_user_model.password)
                                user = await repository_person.create_user(new_user_model, db)
                                if approved_corid:
                                    user.cor_id = approved_corid
                                    await db.commit()
                                    await db.refresh(user)
                            
                            # Issue tokens
                            user_roles = await repository_person.get_user_roles(email=user.email, db=db)
                            token_data = {
                                "oid": str(user.id),
                                "corid": user.cor_id,
                                "email": user.email,
                                "roles": user_roles
                            }
                            
                            access_expires_delta = None if user.email not in TEST_EMAILS else TEST_ACCESS_EXPIRES_DELTA
                            refresh_expires_delta = None
                            
                            access_token, access_token_jti = await auth_service.create_access_token(
                                data=token_data, expires_delta=access_expires_delta
                            )
                            refresh_token = await auth_service.create_refresh_token(
                                data=token_data, expires_delta=refresh_expires_delta
                            )
                            
                            # Create WebSocket device session
                            device_id = f"ws-{session_token[:8]}"
                            session_data = {
                                "user_id": user.cor_id,
                                "app_id": "cor-energy",  # WebSocket sessions default to cor-energy
                                "device_id": device_id,
                                "refresh_token": refresh_token,
                                "device_type": "websocket",
                                "device_info": "WebSocket Auth",
                                "ip_address": "0.0.0.0",
                                "device_os": "unknown",
                                "jti": access_token_jti,
                                "access_token": access_token,
                            }
                            
                            existing = await repository_session.get_user_sessions_by_device(
                                user.cor_id, db=db, app_id="cor-energy", device_id=device_id, device_info="WebSocket Auth"
                            )
                            
                            if not existing:
                                await repository_session.create_user_session(
                                    body=UserSessionModel(**session_data), user=user, db=db
                                )
                            else:
                                await repository_session.update_session_token(
                                    user=user, token=refresh_token, device_id=device_id,
                                    device_info="WebSocket Auth", app_id="cor-energy", db=db,
                                    jti=access_token_jti, access_token=access_token
                                )
                            
                            # Update auth session status
                            auth_session = await get_auth_session_by_token(session_token, db)
                            if auth_session:
                                await update_session_status(auth_session, "approved", db)
                        
                        # Send WebSocket event with tokens
                        await websocket_events_manager.send_to_session(
                            session_token,
                            {
                                "event": "auth_approved",
                                "status": "approved",
                                "access_token": access_token,
                                "refresh_token": refresh_token,
                                "token_type": "bearer",
                                "device_id": device_id,
                            }
                        )
                        logger.info(f"Auth approved for session {session_token[:8]}...")
                        
                        # Cache approval in Redis for HTTP polling fallback
                        await redis_client.set(
                            f"oauth:approved:{session_token}",
                            json.dumps({"cor_id": approved_corid, "email": approved_email}),
                            ex=600,
                        )
                        break
                        
                    except Exception as e:
                        logger.error(f"Error processing approved auth: {e}", exc_info=True)
                        await websocket_events_manager.send_to_session(
                            session_token,
                            {
                                "event": "auth_error",
                                "error": str(e),
                            }
                        )
                        break
                        
            except Exception as e:
                # Check if error indicates rejection
                error_str = str(e).lower()
                if any(keyword in error_str for keyword in ["rejected", "cancelled", "declined"]):
                    async with async_session_maker() as db:
                        auth_session = await get_auth_session_by_token(session_token, db)
                        if auth_session:
                            await update_session_status(auth_session, "rejected", db)
                    
                    await websocket_events_manager.send_to_session(
                        session_token,
                        {
                            "event": "auth_rejected",
                            "status": "rejected",
                            "message": "Вход отменен пользователем",
                        }
                    )
                    logger.info(f"Auth rejected (error branch) for session {session_token[:8]}...")
                    break
                
                # Continue polling on transient errors
                if poll_count < 5:
                    logger.debug(f"COR-ID poll error (attempt {poll_count}): {e}")
                continue
                
    except asyncio.CancelledError:
        logger.debug(f"Polling cancelled for session {session_token[:8]}...")
        raise
    except Exception as e:
        logger.error(f"Fatal error in WebSocket polling: {e}", exc_info=True)
        try:
            await websocket_events_manager.send_to_session(
                session_token,
                {
                    "event": "auth_error",
                    "error": "Internal server error",
                }
            )
        except:
            pass


@router.websocket("/auth/{session_token}")
async def websocket_endpoint(
    websocket: WebSocket,
    session_token: str,
    db: AsyncSession = Depends(get_db),
):
    """
    WebSocket endpoint for COR-ID auth status updates.
    Follows CORID_FINAL_LOGIN_PORTING_GUIDE.md section 8.
    """
    db_session = await get_auth_session_by_token(session_token, db)
    if not db_session:
        await websocket.accept()
        await websocket.close(code=1008, reason="Сессия не найдена")
        return

    if db_session.status != AuthSessionStatus.PENDING:
        await websocket.accept()
        await websocket.close(
            code=1008, reason=f"Неверный статус сессии: {db_session.status}"
        )
        return

    await websocket.accept()
    connection_id = await websocket_events_manager.connect(
        websocket=websocket,
        session_id=session_token,
        accept_connection=False,
    )
    
    # Start background polling task
    poll_task = asyncio.create_task(
        _poll_corid_and_push_events(session_token, str(db_session.id))
    )
    
    try:
        while True:
            # Keep-alive from client
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        poll_task.cancel()
        await websocket_events_manager.disconnect(connection_id)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        poll_task.cancel()
        await websocket_events_manager.disconnect(connection_id)
