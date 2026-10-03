


from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from backend.repository.energy.cerbo_service import get_energetic_object
from backend.services.vendors.victron_service import VictronService
# from backend.repository.energetic_object_repository import get_energetic_object
from backend.database.db import get_db

router = APIRouter(
    prefix="/api/vendors/victron",
    tags=["Victron"]
)


def get_service(obj):
    return VictronService(
        host=obj.modbus_host,
        port=obj.modbus_port
    )


# 🔥 Универсальный poll (замена 10 старых endpoint)
@router.get("/poll")
async def poll_victron(
    energetic_object_id: str = Query(...),
    db: AsyncSession = Depends(get_db)
):
    obj = await get_energetic_object(db, energetic_object_id)

    if not obj:
        raise HTTPException(status_code=404, detail="Object not found")

    service = get_service(obj)

    return await service.full_poll(slave=obj.modbus_slave)


# 🔋 Батарея
@router.get("/battery")
async def get_battery(
    energetic_object_id: str,
    db: AsyncSession = Depends(get_db)
):
    obj = await get_energetic_object(db, energetic_object_id)

    if not obj:
        raise HTTPException(status_code=404, detail="Object not found")

    service = get_service(obj)

    return await service.read_battery(slave=obj.modbus_slave)


# ⚡ ESS AC
@router.get("/ess-ac")
async def get_ess_ac(
    energetic_object_id: str,
    db: AsyncSession = Depends(get_db)
):
    obj = await get_energetic_object(db, energetic_object_id)

    if not obj:
        raise HTTPException(status_code=404, detail="Object not found")

    service = get_service(obj)

    return await service.read_ess_ac(slave=obj.modbus_slave)


# 🔌 VE.Bus
@router.get("/vebus")
async def get_vebus(
    energetic_object_id: str,
    db: AsyncSession = Depends(get_db)
):
    obj = await get_energetic_object(db, energetic_object_id)

    if not obj:
        raise HTTPException(status_code=404, detail="Object not found")

    service = get_service(obj)

    return await service.read_vebus(slave=obj.modbus_slave)



