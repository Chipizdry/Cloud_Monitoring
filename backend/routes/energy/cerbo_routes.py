from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from typing import List, Optional, Union
from backend.database.db import get_db
from backend.database.models import AccessLevel, EnergeticObject, User
from backend.repository.energy.cerbo_service import BATTERY_ID, ESS_UNIT_ID, INVERTER_ID, REGISTERS, decode_signed_16, decode_signed_32, get_modbus_client, get_energetic_object, register_modbus_error
from backend.repository.energy.energetic_object_access import ensure_object_permission
from backend.repository.energy.energy_meter_measurements import get_energy_meter_history_paginated
from backend.repository.energy.power_measurements import (
    get_averaged_power_history,
    get_energy_power_history,
    get_power_history_paginated,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from backend.schemas.device_measurement import CerboMeasurementResponse, EnergyMeterMeasurementResponse, PaginatedResponse
from backend.schemas.inverter_controls import DVCCMaxChargeCurrentRequest, EssAdvancedControl, GridLimitUpdate, InverterPowerPayload, RegisterWriteRequest, VebusSOCControl
from backend.services.shared.access import user_access
from backend.services.user.auth import auth_service
from backend.services.energy.modbus_cache import get_modbus_register_cache


ERROR_THRESHOLD = 9
error_count = 0
VICTRON_TEST_OBJECT_ID = "20ee15de-10e0-489e-a125-e786a49ff3ba"


router = APIRouter(prefix="/modbus", tags=["Modbus"])


async def _resolve_history_object_id(
    db: AsyncSession,
    *,
    energetic_object_id: Optional[str],
    object_name: Optional[str],
) -> str:
    if energetic_object_id:
        return energetic_object_id

    if not object_name:
        raise HTTPException(
            status_code=400,
            detail="energetic_object_id is required",
        )

    result = await db.execute(select(EnergeticObject).where(EnergeticObject.name == object_name))
    obj = result.scalar_one_or_none()
    if not obj:
        raise HTTPException(status_code=404, detail="Энергетический объект не найден")

    return obj.id


async def _get_object_measurements(
    db: AsyncSession,
    *,
    energetic_object: EnergeticObject,
    page: int,
    page_size: int,
    start_date: Optional[datetime],
    end_date: Optional[datetime],
):
    history = get_power_history_paginated
    if (
        str(energetic_object.vendor or "").strip().casefold() == "taiye"
        and str(energetic_object.model_name or "").strip().casefold() == "tac4300ct"
    ):
        history = get_energy_meter_history_paginated
    return await history(
        db=db,
        energetic_object_id=energetic_object.id,
        page=page,
        page_size=page_size,
        start_date=start_date,
        end_date=end_date,
    )


def _pick_cached_value(snapshot_data: dict, *keys):
    for key in keys:
        if key in snapshot_data and snapshot_data[key] is not None:
            return snapshot_data[key]
    return None


async def _get_cached_register_data(
    db: AsyncSession,
    energetic_object_id: str,
    slave_id: int,
    start: int,
    count: int,
    func_code: int,
    max_age_seconds: int,
) -> list:
    obj = await get_energetic_object(db, energetic_object_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Энергетический объект не найден")

    cached = await get_modbus_register_cache(
        protocol=obj.protocol,
        host=obj.ip_address,
        port=obj.port,
        slave_id=slave_id,
        start=start,
        count=count,
        func_code=func_code,
        object_id=energetic_object_id,
        max_age_seconds=max_age_seconds,
    )
    if not cached:
        raise HTTPException(status_code=503, detail="Кеш недоступен или устарел")

    data = cached.get("data")
    if not isinstance(data, list):
        raise HTTPException(status_code=503, detail="Кеш регистров не содержит data")

    return data


@router.get("/error_count")
async def get_error_count():
    """Возвращает текущее количество ошибок Modbus"""
    return {"error_count": error_count}

# Получение статуса батареи
@router.get("/battery_status")
async def get_battery_status(request: Request):
    try:
        client = await get_modbus_client(request.app)  
        addresses = [REGISTERS[key] for key in REGISTERS]
        start = min(addresses)
        count = max(addresses) - start + 1

        result = await client.read_input_registers(start, count=count, slave=BATTERY_ID)
        if result.isError():
            raise HTTPException(status_code=500, detail="Ошибка чтения регистров батареи")

        raw = result.registers

        def get_value(name: str) -> int:
            return raw[REGISTERS[name] - start]
        global error_count
        error_count = 0

        return {
            "soc": get_value("soc") / 10,
            "voltage": get_value("voltage") / 100,
            "current": decode_signed_16(get_value("current")) / 10,
            "temperature":get_value("temperature") / 10,
            "power": decode_signed_16(get_value("power")),
            "soh": get_value("soh") / 10
        }

    except Exception as e:
        register_modbus_error()
        logger.error("❗ Ошибка получения данных с батареи", exc_info=e)
        raise HTTPException(status_code=500, detail="Modbus ошибка")

# Сделать кешированный вариант
# Получение статуса батареи
@router.get("/victron_battery_status")
async def get_battery_status(request: Request):
    try:
        client = await get_modbus_client(request.app)

        addresses = [REGISTERS[key] for key in REGISTERS]
        start = min(addresses)
        count = max(addresses) - start + 2   # +2 чтобы захватить 32bit

        result = await client.read_input_registers(start, count=count, slave=BATTERY_ID)

        if result.isError():
            raise HTTPException(status_code=500, detail="Ошибка чтения регистров батареи")

        raw = result.registers

        def get_value(name: str) -> int:
            return raw[REGISTERS[name] - start]

        def get_value32(name: str) -> int:
            high = raw[REGISTERS[name] - start]
            low = raw[REGISTERS[name] - start + 1]
            return decode_signed_32(high, low)

        global error_count
        error_count = 0

        return {
            "soc": get_value("soc") / 10,
            "voltage": get_value("voltage") / 100,
            "current": -decode_signed_16(get_value("current")) / 10,
            "temperature": get_value("temperature") / 10,
            "power": -get_value32("power_int32"),
            "soh": get_value("soh") / 10
        }

    except Exception as e:
        register_modbus_error()
        logger.error("❗ Ошибка получения данных с батареи", exc_info=e)
        raise HTTPException(status_code=500, detail="Modbus ошибка")
    


# Сделать кешированный вариант
@router.get("/inverter_power_status")
async def get_inverter_power_status(request: Request):
    """
    Получает данные по мощности инвертора/зарядного устройства:
    - Общая мощность DC
    - Мощность на выходе по фазам (AC)
    Исключает чтение input_power_l1/l2/l3 (регистры 872, 874, 876)
    """
    try:
        client = await get_modbus_client(request.app)
        slave = INVERTER_ID
        reg_map = {
            "dc_power": 870,
            "ac_output_l1": 878,
            "ac_output_l2": 880,
            "ac_output_l3": 882,
        }

        result = {}

        # Чтение всех нужных регистров по отдельности
        for name, addr in reg_map.items():
            res = await client.read_holding_registers(address=addr, count=2, slave=slave)
            if res.isError():
                raise HTTPException(status_code=500, detail=f"Ошибка чтения регистров {addr}-{addr+1}")
            value = decode_signed_32(res.registers[0], res.registers[1])
            result[name] = value
           # logging.info(f"✅ {name}: {value} Вт")
        global error_count
        error_count = 0
        return {
            "dc_power": result["dc_power"],
            "ac_output": {
                "l1": result["ac_output_l1"],
                "l2": result["ac_output_l2"],
                "l3": result["ac_output_l3"],
                "total": result["ac_output_l1"] + result["ac_output_l2"] + result["ac_output_l3"]
            }
        }

    except Exception as e:
        register_modbus_error()
        logger.error("❗ Ошибка получения данных мощности инвертора", exc_info=e)
        raise HTTPException(status_code=500, detail="Modbus ошибка")


@router.get("/ess_ac_status")
async def get_ess_ac_status(request: Request):
    """
    Reads AC input and output parameters from the ESS unit:
    - Voltages (L1-L3)
    - Currents (L1-L3)
    - Frequencies (L1-L3)
    - Power (L1-L3)
    """
    try:
        client = await get_modbus_client(request.app) 
        slave = ESS_UNIT_ID
        
        # Define all registers to read (address: description)
        registers = {
            # Input parameters
            3: "input_voltage_l1",
            4: "input_voltage_l2",
            5: "input_voltage_l3",
            6: "input_current_l1",
            7: "input_current_l2",
            8: "input_current_l3",
            9: "input_frequency_l1",
            10: "input_frequency_l2",
            11: "input_frequency_l3",
            12: "input_power_l1",
            13: "input_power_l2",
            14: "input_power_l3",
            
            # Output parameters
            15: "output_voltage_l1",
            16: "output_voltage_l2",
            17: "output_voltage_l3",
            18: "output_current_l1",
            19: "output_current_l2",
            20: "output_current_l3",
            21: "output_frequency",
            22: "active_input_current_limit",
            23: "output_power_l1",
            24: "output_power_l2",
            25: "output_power_l3"
        }

        # Calculate read range
        start = min(registers.keys())
        count = max(registers.keys()) - start + 1

        # Read all registers in one operation
        result = await client.read_input_registers(start, count=count, slave=slave)
        if result.isError():
            raise HTTPException(status_code=500, detail="Ошибка чтения AC регистров ESS")

        raw = result.registers

        def get_value(reg_name: str):
            reg_address = next(k for k, v in registers.items() if v == reg_name)
            value = raw[reg_address - start]
            
            # Apply appropriate scaling and decoding
            if "voltage" in reg_name:
                return value / 10.0  # uint16 scaled by 10
            elif "current" in reg_name:
                return decode_signed_16(value) / 10.0  # int16 scaled by 10
            elif "frequency" in reg_name:
                return decode_signed_16(value) / 100.0  # int16 scaled by 100
            elif "power" in reg_name:
                return decode_signed_16(value) * 10  # int16 scaled by 0.1
            return value
        global error_count
        error_count = 0  
        # Build response structure
        response = {
            "input": {
                "voltages": {
                    "l1": get_value("input_voltage_l1"),
                    "l2": get_value("input_voltage_l2"),
                    "l3": get_value("input_voltage_l3")
                },
                "currents": {
                    "l1": get_value("input_current_l1"),
                    "l2": get_value("input_current_l2"),
                    "l3": get_value("input_current_l3")
                },
                "frequencies": {
                    "l1": get_value("input_frequency_l1"),
                    "l2": get_value("input_frequency_l2"),
                    "l3": get_value("input_frequency_l3")
                },
                "powers": {
                    "l1": get_value("input_power_l1"),
                    "l2": get_value("input_power_l2"),
                    "l3": get_value("input_power_l3"),
                    "inputPowerTotal": get_value("input_power_l1") + get_value("input_power_l2") + get_value("input_power_l3")
                }
            },
            "output": {
                "voltages": {
                    "l1": get_value("output_voltage_l1"),
                    "l2": get_value("output_voltage_l2"),
                    "l3": get_value("output_voltage_l3")
                },
                "currents": {
                    "l1": get_value("output_current_l1"),
                    "l2": get_value("output_current_l2"),
                    "l3": get_value("output_current_l3")
                },
                "frequency": get_value("output_frequency"),
                "active_input_current_limit": get_value("active_input_current_limit"),
                "powers": {
                    "l1": get_value("output_power_l1"),
                    "l2": get_value("output_power_l2"),
                    "l3": get_value("output_power_l3"),
                    "LoadTotalPower": get_value("output_power_l1") + get_value("output_power_l2") + get_value("output_power_l3")
                }


            }
        }

        # Логи отладки
      #  logging.info("ESS AC Status:")
      #  logging.info(f"Input Voltages: L1={response['input']['voltages']['l1']}V, L2={response['input']['voltages']['l2']}V, L3={response['input']['voltages']['l3']}V")
      #  logging.info(f"Input Currents: L1={response['input']['currents']['l1']}A, L2={response['input']['currents']['l2']}A, L3={response['input']['currents']['l3']}A")
      #  logging.info(f"Input Frequencies: L1={response['input']['frequencies']['l1']}Hz, L2={response['input']['frequencies']['l2']}Hz, L3={response['input']['frequencies']['l3']}Hz")
      #  logging.info(f"Input Power Total: {response['input']['powers']['total']}W")
      #  logging.info(f"Output Voltages: L1={response['output']['voltages']['l1']}V, L2={response['output']['voltages']['l2']}V, L3={response['output']['voltages']['l3']}V")
      #  logging.info(f"Output Currents: L1={response['output']['currents']['l1']}A, L2={response['output']['currents']['l2']}A, L3={response['output']['currents']['l3']}A")

        return response

    except Exception as e:
        register_modbus_error()
        logger.error("❗ Ошибка чтения AC параметров ESS", exc_info=e)
        raise HTTPException(status_code=500, detail="Modbus ошибка")


# Сделать кешированный вариант
@router.get("/victron_ac_status")
async def get_ess_ac_status(request: Request):
    """
    Reads AC input and output parameters from the ESS unit:
    - Voltages (L1-L3)
    - Currents (L1-L3)
    - Frequencies (L1-L3)
    - Power (L1-L3)
    """
    try:
        client = await get_modbus_client(request.app) 
        slave = ESS_UNIT_ID
        
        # Define all registers to read (address: description)
        registers = {
            # Input parameters
            3: "input_voltage_l1",
            4: "input_voltage_l2",
            5: "input_voltage_l3",
            6: "input_current_l1",
            7: "input_current_l2",
            8: "input_current_l3",
            9: "input_frequency_l1",
            10: "input_frequency_l2",
            11: "input_frequency_l3",
            12: "input_power_l1",
            13: "input_power_l2",
            14: "input_power_l3",
            
            # Output parameters
            15: "output_voltage_l1",
            16: "output_voltage_l2",
            17: "output_voltage_l3",
            18: "output_current_l1",
            19: "output_current_l2",
            20: "output_current_l3",
            21: "output_frequency",
            22: "active_input_current_limit",
            23: "output_power_l1",
            24: "output_power_l2",
            25: "output_power_l3"
        }

        # Calculate read range
        start = min(registers.keys())
        count = max(registers.keys()) - start + 1

        # Read all registers in one operation
        result = await client.read_input_registers(start, count=count, slave=slave)
        if result.isError():
            raise HTTPException(status_code=500, detail="Ошибка чтения AC регистров ESS")

        raw = result.registers

        def get_value(reg_name: str):
            reg_address = next(k for k, v in registers.items() if v == reg_name)
            value = raw[reg_address - start]
            
            # Apply appropriate scaling and decoding
            if "voltage" in reg_name:
                return value / 10.0  # uint16 scaled by 10
            elif "current" in reg_name:
                return decode_signed_16(value) / 10.0  # int16 scaled by 10
            elif "frequency" in reg_name:
                return decode_signed_16(value) / 100.0  # int16 scaled by 100
            elif "power" in reg_name:
                return decode_signed_16(value) * 10  # int16 scaled by 0.1
            return value
        global error_count
        error_count = 0  
        # Build response structure
        response = {
            # GRID (input)
            "inputVoltageL1": get_value("input_voltage_l1"),
            "inputVoltageL2": get_value("input_voltage_l2"),
            "inputVoltageL3": get_value("input_voltage_l3"),

            "inputCurrentL1": get_value("input_current_l1"),
            "inputCurrentL2": get_value("input_current_l2"),
            "inputCurrentL3": get_value("input_current_l3"),

            "inputPowerL1": get_value("input_power_l1"),
            "inputPowerL2": get_value("input_power_l2"),
            "inputPowerL3": get_value("input_power_l3"),

            "inputPowerTotal": ( get_value("input_power_l1") + get_value("input_power_l2") + get_value("input_power_l3")),

            "inputFrequency": get_value("input_frequency_l1"),

            # LOAD (output)
            "LoadPhaseVoltageA": get_value("output_voltage_l1"),
            "LoadPhaseVoltageB": get_value("output_voltage_l2"),
            "LoadPhaseVoltageC": get_value("output_voltage_l3"),

            "LoadPhaseCurrentA": get_value("output_current_l1"),
            "LoadPhaseCurrentB": get_value("output_current_l2"),
            "LoadPhaseCurrentC": get_value("output_current_l3"),

            "LoadPhasePowerA": get_value("output_power_l1"),
            "LoadPhasePowerB": get_value("output_power_l2"),
            "LoadPhasePowerC": get_value("output_power_l3"),

            "LoadTotalPower": ( get_value("output_power_l1") + get_value("output_power_l2") + get_value("output_power_l3") ),

            "LoadFrequency": get_value("output_frequency"),

            "activeInputCurrentLimit": get_value("active_input_current_limit")
        }

        return response

    except Exception as e:
        register_modbus_error()
        logger.error("❗ Ошибка чтения AC параметров ESS", exc_info=e)
        raise HTTPException(status_code=500, detail="Modbus ошибка")

# Сделать кешированный вариант
@router.get("/vebus_status")
async def get_vebus_status(request: Request):
    """
    Чтение VE.Bus регистров с 21 по 41 (устройство 227):
    - Частота, ограничения, мощность, напряжение/ток АКБ, тревоги, состояния, ESS настройки
    """
    try:
        client = request.app.state.modbus_client
        slave = ESS_UNIT_ID
        start = 21
        count = 21  # от 21 до 41 включительно

        result = await client.read_input_registers(start, count=count, slave=slave)
        if result.isError():
            raise HTTPException(status_code=500, detail="Ошибка чтения регистров VE.Bus")

        r = result.registers

        def val(idx): return r[idx - start]

        def s16(v): return decode_signed_16(v)
        global error_count
        error_count = 0
        return {
            "output_frequency_hz": s16(val(21)) / 100,
            "input_current_limit_a": s16(val(22)) / 10,
            "output_power": {
                "l1": s16(val(23)) * 10,
                "l2": s16(val(24)) * 10,
                "l3": s16(val(25)) * 10,
            },
            "battery_voltage_v": val(26) / 100,
            "battery_current_a": s16(val(27)) / 10,
            "phase_count": val(28),
            "active_input": val(29),
            "soc_percent": val(30) / 10,
            "vebus_state": val(31),
            "vebus_error": val(32),
            "switch_position": val(33),
            "alarms": {
                "temperature": val(34),
                "low_battery": val(35),
                "overload": val(36),
            },
            "ess": {
                "power_setpoint_l1": s16(val(37)),
                "disable_charge": val(38),
                "disable_feed": val(39),
                "power_setpoint_l2": s16(val(40)),
                "power_setpoint_l3": s16(val(41))
            }
        }

    except Exception as e:
        register_modbus_error()
        logger.error("❗ Ошибка чтения VE.Bus регистров", exc_info=e)
        raise HTTPException(status_code=500, detail="Modbus ошибка")

# статус зарядки проценты
@router.post("/vebus/soc")
async def set_vebus_soc(control: VebusSOCControl, request: Request):
    """
    Устанавливает VE.Bus SoC (state of charge threshold)
    """
    try:
        logger.debug(f"📤 Установка VE.Bus SoC: {control.soc_threshold}%")
        client = request.app.state.modbus_client

        # Значение с масштабированием x10 (как в описании)
        scaled_value = int(control.soc_threshold * 10)

        await client.write_register(
            address=2901,  # адрес регистра VE.Bus SoC
            value=scaled_value,
            slave=100
        )
        global error_count
        error_count = 0
        return {"status": "ok"}
    except Exception as e:
        register_modbus_error()
        logger.error("❗ Ошибка установки VE.Bus SoC", exc_info=e)
        raise HTTPException(status_code=500, detail="Modbus ошибка")

# отдача в сеть
@router.post("/ess_advanced_settings/setpoint_fine")
async def set_ess_advanced_setpoint_fine(control: EssAdvancedControl, request: Request):
    """
    Устанавливает точное значение AC Power Setpoint (регистр 2703)
    Диапазон: от -100000 до 100000
    """
    try:
        client = request.app.state.modbus_client
        slave = INVERTER_ID
        
        # Преобразуем значение для записи в регистр
        register_value = int(control.ac_power_setpoint_fine / 100)
        
        # Преобразование отрицательных чисел в формат Modbus (дополнительный код)
        if register_value < 0:
            register_value = (1 << 16) + register_value  # Преобразование в 16-битное представление
            
        # Проверяем, что значение вписывается в int16
        if register_value < 0 or register_value > 65535:
            raise HTTPException(status_code=400, detail="Значение выходит за допустимые пределы")
        
        # Записываем значение в регистр 2703
        await client.write_register(
            address=2703,
            value=register_value,
            slave=slave
        )
        global error_count
        error_count = 0  
        # logger.debug(f"✅ Установлено AC Power Setpoint Fine: {control.ac_power_setpoint_fine} W (регистр 2703 = {register_value})")
        return {"status": "ok", "value": control.ac_power_setpoint_fine}
        
    except Exception as e:
        register_modbus_error() 
        logger.error("❗ Ошибка записи AC Power Setpoint Fine", exc_info=e)
        raise HTTPException(status_code=500, detail="Modbus ошибка")



@router.post("/ess_advanced_settings/inverter_power")
async def set_inverter_power_setpoint(payload: InverterPowerPayload, request: Request):
    try:
        client = request.app.state.modbus_client
        slave = INVERTER_ID

        raw_value = payload.inverter_power
        if raw_value is None:
            raise HTTPException(status_code=400, detail="Не передано значение inverter_power")

        # Масштабируем и проверяем на допустимые границы int16
        scaled_value = int(float(raw_value/10))
        if not -32768 <= scaled_value <= 32767:
            raise HTTPException(status_code=400, detail="Значение выходит за пределы int16")

        # Преобразуем в формат Modbus (uint16, если отрицательное — в дополнительный код)
        if scaled_value < 0:
            register_value = (1 << 16) + scaled_value
        else:
            register_value = scaled_value

        await client.write_register(address=2704, value=register_value, slave=slave)

        # logger.debug(f"✅ Установлено значение инвертора: {raw_value} W (регистр 2704 = {register_value})")
        return {"status": "ok", "value": raw_value}

    except Exception as e:
        logger.error("❗ Ошибка записи регистра 2704", exc_info=e)
        raise HTTPException(status_code=500, detail="Modbus ошибка")


# Ток заряда 
@router.post("/ess_advanced_settings/dvcc_max_charge_current")
async def set_dvcc_max_charge_current(data: DVCCMaxChargeCurrentRequest, request: Request):
    """
    Устанавливает DVCC system max charge current (регистр 2705)
    """
    try:
        client = request.app.state.modbus_client
        slave = INVERTER_ID

        value = data.current_limit

        # Проверка границ значений int16
        if not -32768 <= value <= 32767:
            raise HTTPException(status_code=400, detail="Значение выходит за пределы int16")

        # Преобразуем в формат Modbus (uint16) для передачи
        if value < 0:
            register_value = (1 << 16) + value  # преобразуем -1 в 0xFFFF
        else:
            register_value = value

        # Запись в регистр
        await client.write_register(address=2705, value=register_value, slave=slave)

        # logger.debug(f"✅ Установлен DVCC max charge current: {value} A (регистр 2705 = {register_value})")
        return {"status": "ok", "value": value}

    except Exception as e:
        register_modbus_error()
        logger.error("❗ Ошибка установки DVCC max charge current", exc_info=e)
        raise HTTPException(status_code=500, detail="Modbus ошибка")



@router.post("/ess/grid_limiting_status")
async def set_grid_limiting_status(data: GridLimitUpdate, request: Request):
    """
    Переключение grid_limiting_status (регистр 2709): включение / отключение.
    """
    try:
        client = request.app.state.modbus_client
        slave = INVERTER_ID
        register = 2707
        value = 1 if data.enabled else 0

        # Запись значения
        result = await client.write_register(register, value, slave=slave)

        if result.isError():
            raise HTTPException(status_code=500, detail="Ошибка записи регистра 2709")
        global error_count
        error_count = 0
        return {"success": True, "grid_limiting_status": value}

    except Exception as e:
        register_modbus_error()
        logger.error("❗ Ошибка при записи grid_limiting_status", exc_info=e)
        raise HTTPException(status_code=500, detail="Ошибка записи Modbus")


# Сделать кешированный вариант
@router.get("/ess_settings")
async def get_ess_settings(request: Request):
    """
    Чтение настроек ESS:
    - BatteryLife State
    - Minimum SoC
    - ESS Mode
    - BatteryLife SoC limit (read-only)
    """
    try:
        client = request.app.state.modbus_client
        slave = 100

        start_address = 2900
        count = 4

        result = await client.read_holding_registers(start_address, count=count, slave=slave)
        if result.isError():
            raise HTTPException(status_code=500, detail="Ошибка чтения регистров ESS Settings")

        regs = result.registers
        global error_count
        error_count = 0
        return {
            "battery_life_state": regs[0],               # 2900
            "minimum_soc_limit": regs[1] / 10.0,         # 2901, scale x10
            "ess_mode": regs[2],                         # 2902
            "battery_life_soc_limit": regs[3] / 10.0     # 2903, scale x10
        }

    except Exception as e:
        register_modbus_error()
        logger.error("❗ Ошибка чтения ESS настроек", exc_info=e)
        raise HTTPException(status_code=500, detail="Modbus ошибка")


# Сделать кешированный вариант
@router.get("/ess_advanced_settings")
async def get_ess_advanced_settings(request: Request):
    """
    Чтение базовых ESS и системных настроек с адресов 2700–2712 + 2715 + 2716 (устройство 100).
    Также определяет активный режим ESS: 1, 2 или 3.
    """
    try:
        client = request.app.state.modbus_client
        slave = INVERTER_ID  # Обычно 100

        # 2700–2712 (13 регистров)
        start_main = 2700
        count_main = 13
        result_main = await client.read_input_registers(start_main, count=count_main, slave=slave)
        if result_main.isError() or not hasattr(result_main, "registers"):
            raise HTTPException(status_code=500, detail="Ошибка чтения регистров 2700–2712")

        r_main = result_main.registers
        def safe_main(idx): return r_main[idx - start_main] if (idx - start_main) < len(r_main) else None
        def s16(v): return decode_signed_16(v) if v is not None else None
        global error_count
        error_count = 0
        # Формируем результат
        result_data = {
            "ac_power_setpoint": safe_main(2700),  # Просто читаем значение регистра 2700
            "max_charge_percent": safe_main(2701),
            "max_discharge_percent": safe_main(2702),
            "ac_power_setpoint_fine": s16(safe_main(2703)) * 100 if safe_main(2703) is not None else None,
            "max_discharge_power": s16(safe_main(2704)) * 10 if safe_main(2704) is not None else None,
            "dvcc_max_charge_current": s16(safe_main(2705)),
            "max_feed_in_power": s16(safe_main(2706)) * 10 if safe_main(2706) is not None else None,
            "overvoltage_feed_in": safe_main(2707),
            "prevent_feedback": safe_main(2708),
            "grid_limiting_status": safe_main(2709),
            "max_charge_voltage": safe_main(2710) / 10.0 if safe_main(2710) is not None else None,
            "ac_input_1_source": safe_main(2711),
            "ac_input_2_source": safe_main(2712),
        }

        # logging.info("✅ ESS Advanced Settings:\n%s", json.dumps(result_data, indent=2, ensure_ascii=False))
        return result_data

    except Exception as e:
        register_modbus_error() 
        logger.error("❗ Ошибка при чтении ESS настроек", exc_info=e)
        raise HTTPException(status_code=500, detail="Modbus ошибка")

@router.get("/solarchargers_status")
async def get_solarchargers_status(request: Request):
    """
    Быстрое чтение PV-напряжения и тока с MPPT по Modbus + суммарная мощность
    """
    try:
        client = request.app.state.modbus_client
        slave_ids = list(range(1, 14)) + [100]

        results = {}
        total_pv_power = 0  # Инициализация переменной для суммарной мощности

        for slave in slave_ids:
            charger_data = {}

            try:
                # Читаем диапазон: 3700–3703 и 3724–3727 = 8 регистров
                addresses = [
                    ("pv_voltage_0", 3700, 100, False),
                    ("pv_voltage_1", 3701, 100, False),
                    ("pv_voltage_2", 3702, 100, False),
                    ("pv_voltage_3", 3703, 100, False),
                    ("pv_power_0", 3724, 1, False),
                    ("pv_power_1", 3725, 1, False),
                    ("pv_power_2", 3726, 1, False),
                    ("pv_power_3", 3727, 1, False),
                ]
 
                # Все нужные адреса
                needed_regs = [3700, 3701, 3702, 3703, 3724, 3725, 3726, 3727]
                min_reg = min(needed_regs)
                max_reg = max(needed_regs)
                count = max_reg - min_reg + 1

                # Один запрос
                res = await client.read_input_registers(address=min_reg, count=count, slave=slave)

                if res.isError() or not hasattr(res, "registers"):
                    for name, reg, scale, _ in addresses:
                        charger_data[name] = None
                    logger.warning(f"⚠️ Ошибка чтения диапазона у slave {slave}")
                else:
                    regs = res.registers  # список считанных значений
                    for name, reg, scale, is_signed in addresses:
                        idx = reg - min_reg
                        raw = regs[idx]
                        value = decode_signed_16(raw) if is_signed else raw
                        charger_data[name] = round(value / scale, 2)
                        
                        # Суммируем только мощности (pv_power_*)
                        if name.startswith("pv_power_"):
                            total_pv_power += charger_data[name]

            except Exception as e:
                charger_data["error"] = str(e)
                logger.warning(f"⚠️ Исключение при чтении slave {slave}: {e}")

            results[f"charger_{slave}"] = charger_data

        # Добавляем суммарную мощность в результаты
        results["total_pv_power"] = round(total_pv_power, 2)
        
        return results

    except Exception as e:
        register_modbus_error()
        logger.error("❗️ Общая ошибка при опросе MPPT", exc_info=e)
        raise HTTPException(status_code=500, detail="Modbus ошибка")
    



@router.get("/solarchargers_sum")
async def get_solarchargers_current_sum(request: Request):
    """
    Чтение регистров 3730 с MPPT для всех UID и суммирование их значений
    """
    try:
        client = request.app.state.modbus_client
        slave_ids = list(range(1, 14)) + [100]

        results = {}
        total_power = 0  # Суммарное значение регистров 3730

        for slave in slave_ids:
            try:
                res = await client.read_input_registers(address=3730, count=1, slave=slave)

                if res.isError() or not hasattr(res, "registers"):
                    results[f"charger_{slave}"] = None
                    logger.warning(f"⚠️ Ошибка чтения регистра 3730 у slave {slave}")
                else:
                    value = res.registers[0]
                    results[f"charger_{slave}"] = value
                    total_power += value

            except Exception as e:
                results[f"charger_{slave}"] = {"error": str(e)}
                logger.warning(f"⚠️ Исключение при чтении slave {slave}: {e}")

        # Добавляем суммарное значение
        results["total_PV_Power"] = total_power

        return results

    except Exception as e:
        register_modbus_error()
        logger.error("❗️ Общая ошибка при опросе регистров 3730", exc_info=e)
        raise HTTPException(status_code=500, detail="Modbus ошибка")


# Сделать кешированный вариант
@router.get("/victron_solarchargers_status")
async def get_solarchargers_status(request: Request):

    try:
        client = request.app.state.modbus_client
        slave_ids = list(range(1, 14)) + [100]

        start = 3700
        count = 31  # 3700..3730

        total_pv_power = 0

        results = {
            "chargers": [],
            "TotalPVPower": 0
        }

        for slave in slave_ids:

            try:
                res = await client.read_input_registers(
                    address=start,
                    count=count,
                    slave=slave
                )

                if res.isError():
                    raise Exception("modbus read error")

                r = res.registers

                def reg(addr):
                    return r[addr - start]

                # ---------- PV voltages ----------
                pv_voltage_0 = reg(3700) / 100
                pv_voltage_1 = reg(3701) / 100
                pv_voltage_2 = reg(3702) / 100
                pv_voltage_3 = reg(3703) / 100

                # ---------- PV power ----------
                pv_power_0 = reg(3724)
                pv_power_1 = reg(3725)
                pv_power_2 = reg(3726)
                pv_power_3 = reg(3727)

                # ---------- currents ----------
                pv_current_0 = round(pv_power_0 / pv_voltage_0, 2) if pv_voltage_0 > 0 else 0
                pv_current_1 = round(pv_power_1 / pv_voltage_1, 2) if pv_voltage_1 > 0 else 0
                pv_current_2 = round(pv_power_2 / pv_voltage_2, 2) if pv_voltage_2 > 0 else 0
                pv_current_3 = round(pv_power_3 / pv_voltage_3, 2) if pv_voltage_3 > 0 else 0

                power_sum = pv_power_0 + pv_power_1 + pv_power_2 + pv_power_3

                total_pv_power += power_sum

                charger_data = {
                    "slave": slave,
                    "power": power_sum,
                    "strings": [
                        {
                            "voltage": pv_voltage_0,
                            "current": pv_current_0,
                            "power": pv_power_0
                        },
                        {
                            "voltage": pv_voltage_1,
                            "current": pv_current_1,
                            "power": pv_power_1
                        },
                        {
                            "voltage": pv_voltage_2,
                            "current": pv_current_2,
                            "power": pv_power_2
                        },
                        {
                            "voltage": pv_voltage_3,
                            "current": pv_current_3,
                            "power": pv_power_3
                        }
                    ]
                }

                results["chargers"].append(charger_data)

            except Exception as e:

                logger.warning(f"MPPT {slave} read error: {e}")

                results["chargers"].append({
                    "slave": slave,
                    "error": str(e)
                })

        results["TotalPVPower"] = total_pv_power

        return results

    except Exception as e:

        register_modbus_error()

        logger.error("MPPT polling error", exc_info=e)

        raise HTTPException(
            status_code=500,
            detail="Modbus ошибка"
        )


# Сделать кешированный вариант
@router.get("/dynamic_ess_settings")
async def get_dynamic_ess_settings(request: Request):
    client = request.app.state.modbus_client
    try:
        unit_id = 100
        start_address = 5420
        count = 10  # 5430 не читается
        result = await client.read_holding_registers(start_address, count=count, slave=unit_id)

        if result.isError():
            raise HTTPException(status_code=500, detail=f"Modbus error: {result}")

        regs = result.registers

        if len(regs) != count:
            raise HTTPException(
                status_code=500,
                detail=f"Ожидалось {count} регистров, получено {len(regs)}: {regs}"
            )

        data = {
            "BatteryCapacity_kWh": regs[0] / 10.0,                    # 5420
            "FullChargeDuration_hr": regs[1],                         # 5421
            "FullChargeInterval_day": regs[2],                        # 5422
            "DynamicEssMode": regs[3],                                # 5423
            "Schedule_AllowGridFeedIn": regs[4],                      # 5424
            "Schedule_Duration_sec": regs[5],                         # 5425
            "Schedule_Restrictions": regs[6],                         # 5426
            "Schedule_TargetSoc_pct": regs[7],                        # 5427
            "Schedule_Start_unix": (regs[8] << 16) + regs[9],         # 5428 + 5429
            # Schedule_Strategy отсутствует — 5430 недоступен
        }

        return data

    except Exception as e:
        logger.error("🛑 Unexpected error", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка чтения Dynamic ESS: {e}")


@router.get("/victron_battery_status_cached")
async def get_victron_battery_status_cached(
    energetic_object_id: str = Query(VICTRON_TEST_OBJECT_ID, description="ID энергетического объекта"),
    max_age_seconds: int = Query(120, ge=1, le=3600, description="Максимальный возраст кеша в секундах"),
    db: AsyncSession = Depends(get_db),
):
    addresses = [REGISTERS[key] for key in REGISTERS]
    start = min(addresses)
    count = max(addresses) - start + 2
    data = await _get_cached_register_data(
        db=db,
        energetic_object_id=energetic_object_id,
        slave_id=BATTERY_ID,
        start=start,
        count=count,
        func_code=4,
        max_age_seconds=max_age_seconds,
    )

    def get_value(name: str) -> int:
        return data[REGISTERS[name] - start]

    def get_value32(name: str) -> int:
        high = data[REGISTERS[name] - start]
        low = data[REGISTERS[name] - start + 1]
        return decode_signed_32(high, low)

    return {
        "soc": get_value("soc") / 10,
        "voltage": get_value("voltage") / 100,
        "current": -decode_signed_16(get_value("current")) / 10,
        "temperature": get_value("temperature") / 10,
        "power": -get_value32("power_int32"),
        "soh": get_value("soh") / 10,
    }


@router.get("/inverter_power_status_cached")
async def get_inverter_power_status_cached(
    energetic_object_id: str = Query(VICTRON_TEST_OBJECT_ID, description="ID энергетического объекта"),
    max_age_seconds: int = Query(120, ge=1, le=3600, description="Максимальный возраст кеша в секундах"),
    db: AsyncSession = Depends(get_db),
):
    reg_map = {
        "dc_power": 870,
        "ac_output_l1": 878,
        "ac_output_l2": 880,
        "ac_output_l3": 882,
    }
    result = {}
    for name, addr in reg_map.items():
        regs = await _get_cached_register_data(
            db=db,
            energetic_object_id=energetic_object_id,
            slave_id=INVERTER_ID,
            start=addr,
            count=2,
            func_code=3,
            max_age_seconds=max_age_seconds,
        )
        result[name] = decode_signed_32(regs[0], regs[1])

    return {
        "dc_power": result["dc_power"],
        "ac_output": {
            "l1": result["ac_output_l1"],
            "l2": result["ac_output_l2"],
            "l3": result["ac_output_l3"],
            "total": result["ac_output_l1"] + result["ac_output_l2"] + result["ac_output_l3"],
        },
    }


@router.get("/victron_ac_status_cached")
async def get_victron_ac_status_cached(
    energetic_object_id: str = Query(VICTRON_TEST_OBJECT_ID, description="ID энергетического объекта"),
    max_age_seconds: int = Query(120, ge=1, le=3600, description="Максимальный возраст кеша в секундах"),
    db: AsyncSession = Depends(get_db),
):
    registers = {
        3: "input_voltage_l1",
        4: "input_voltage_l2",
        5: "input_voltage_l3",
        6: "input_current_l1",
        7: "input_current_l2",
        8: "input_current_l3",
        9: "input_frequency_l1",
        10: "input_frequency_l2",
        11: "input_frequency_l3",
        12: "input_power_l1",
        13: "input_power_l2",
        14: "input_power_l3",
        15: "output_voltage_l1",
        16: "output_voltage_l2",
        17: "output_voltage_l3",
        18: "output_current_l1",
        19: "output_current_l2",
        20: "output_current_l3",
        21: "output_frequency",
        22: "active_input_current_limit",
        23: "output_power_l1",
        24: "output_power_l2",
        25: "output_power_l3",
    }
    start = min(registers.keys())
    count = max(registers.keys()) - start + 1
    raw = await _get_cached_register_data(
        db=db,
        energetic_object_id=energetic_object_id,
        slave_id=ESS_UNIT_ID,
        start=start,
        count=count,
        func_code=4,
        max_age_seconds=max_age_seconds,
    )

    def get_value(reg_name: str):
        reg_address = next(k for k, v in registers.items() if v == reg_name)
        value = raw[reg_address - start]
        if "voltage" in reg_name:
            return value / 10.0
        if "current" in reg_name:
            return decode_signed_16(value) / 10.0
        if "frequency" in reg_name:
            return decode_signed_16(value) / 100.0
        if "power" in reg_name:
            return decode_signed_16(value) * 10
        return value

    input_power_l1 = get_value("input_power_l1")
    input_power_l2 = get_value("input_power_l2")
    input_power_l3 = get_value("input_power_l3")
    output_power_l1 = get_value("output_power_l1")
    output_power_l2 = get_value("output_power_l2")
    output_power_l3 = get_value("output_power_l3")

    return {
        "inputVoltageL1": get_value("input_voltage_l1"),
        "inputVoltageL2": get_value("input_voltage_l2"),
        "inputVoltageL3": get_value("input_voltage_l3"),
        "inputCurrentL1": get_value("input_current_l1"),
        "inputCurrentL2": get_value("input_current_l2"),
        "inputCurrentL3": get_value("input_current_l3"),
        "inputPowerL1": input_power_l1,
        "inputPowerL2": input_power_l2,
        "inputPowerL3": input_power_l3,
        "inputPowerTotal": input_power_l1 + input_power_l2 + input_power_l3,
        "inputFrequency": get_value("input_frequency_l1"),
        "LoadPhaseVoltageA": get_value("output_voltage_l1"),
        "LoadPhaseVoltageB": get_value("output_voltage_l2"),
        "LoadPhaseVoltageC": get_value("output_voltage_l3"),
        "LoadPhaseCurrentA": get_value("output_current_l1"),
        "LoadPhaseCurrentB": get_value("output_current_l2"),
        "LoadPhaseCurrentC": get_value("output_current_l3"),
        "LoadPhasePowerA": output_power_l1,
        "LoadPhasePowerB": output_power_l2,
        "LoadPhasePowerC": output_power_l3,
        "LoadTotalPower": output_power_l1 + output_power_l2 + output_power_l3,
        "LoadFrequency": get_value("output_frequency"),
        "activeInputCurrentLimit": get_value("active_input_current_limit"),
    }


@router.get("/vebus_status_cached")
async def get_vebus_status_cached(
    energetic_object_id: str = Query(VICTRON_TEST_OBJECT_ID, description="ID энергетического объекта"),
    max_age_seconds: int = Query(120, ge=1, le=3600, description="Максимальный возраст кеша в секундах"),
    db: AsyncSession = Depends(get_db),
):
    start = 21
    count = 21
    r = await _get_cached_register_data(
        db=db,
        energetic_object_id=energetic_object_id,
        slave_id=ESS_UNIT_ID,
        start=start,
        count=count,
        func_code=4,
        max_age_seconds=max_age_seconds,
    )

    def val(idx):
        return r[idx - start]

    def s16(v):
        return decode_signed_16(v)

    return {
        "output_frequency_hz": s16(val(21)) / 100,
        "input_current_limit_a": s16(val(22)) / 10,
        "output_power": {
            "l1": s16(val(23)) * 10,
            "l2": s16(val(24)) * 10,
            "l3": s16(val(25)) * 10,
        },
        "battery_voltage_v": val(26) / 100,
        "battery_current_a": s16(val(27)) / 10,
        "phase_count": val(28),
        "active_input": val(29),
        "soc_percent": val(30) / 10,
        "vebus_state": val(31),
        "vebus_error": val(32),
        "switch_position": val(33),
        "alarms": {
            "temperature": val(34),
            "low_battery": val(35),
            "overload": val(36),
        },
        "ess": {
            "power_setpoint_l1": s16(val(37)),
            "disable_charge": val(38),
            "disable_feed": val(39),
            "power_setpoint_l2": s16(val(40)),
            "power_setpoint_l3": s16(val(41)),
        },
    }


@router.get("/ess_settings_cached")
async def get_ess_settings_cached(
    energetic_object_id: str = Query(VICTRON_TEST_OBJECT_ID, description="ID энергетического объекта"),
    max_age_seconds: int = Query(120, ge=1, le=3600, description="Максимальный возраст кеша в секундах"),
    db: AsyncSession = Depends(get_db),
):
    regs = await _get_cached_register_data(
        db=db,
        energetic_object_id=energetic_object_id,
        slave_id=100,
        start=2900,
        count=4,
        func_code=3,
        max_age_seconds=max_age_seconds,
    )
    return {
        "battery_life_state": regs[0],
        "minimum_soc_limit": regs[1] / 10.0,
        "ess_mode": regs[2],
        "battery_life_soc_limit": regs[3] / 10.0,
    }


@router.get("/ess_advanced_settings_cached")
async def get_ess_advanced_settings_cached(
    energetic_object_id: str = Query(VICTRON_TEST_OBJECT_ID, description="ID энергетического объекта"),
    max_age_seconds: int = Query(120, ge=1, le=3600, description="Максимальный возраст кеша в секундах"),
    db: AsyncSession = Depends(get_db),
):
    r_main = await _get_cached_register_data(
        db=db,
        energetic_object_id=energetic_object_id,
        slave_id=INVERTER_ID,
        start=2700,
        count=13,
        func_code=4,
        max_age_seconds=max_age_seconds,
    )

    def safe_main(idx):
        start_main = 2700
        return r_main[idx - start_main] if (idx - start_main) < len(r_main) else None

    def s16(v):
        return decode_signed_16(v) if v is not None else None

    return {
        "ac_power_setpoint": safe_main(2700),
        "max_charge_percent": safe_main(2701),
        "max_discharge_percent": safe_main(2702),
        "ac_power_setpoint_fine": s16(safe_main(2703)) * 100 if safe_main(2703) is not None else None,
        "max_discharge_power": s16(safe_main(2704)) * 10 if safe_main(2704) is not None else None,
        "dvcc_max_charge_current": s16(safe_main(2705)),
        "max_feed_in_power": s16(safe_main(2706)) * 10 if safe_main(2706) is not None else None,
        "overvoltage_feed_in": safe_main(2707),
        "prevent_feedback": safe_main(2708),
        "grid_limiting_status": safe_main(2709),
        "max_charge_voltage": safe_main(2710) / 10.0 if safe_main(2710) is not None else None,
        "ac_input_1_source": safe_main(2711),
        "ac_input_2_source": safe_main(2712),
    }


@router.get("/victron_solarchargers_status_cached")
async def get_victron_solarchargers_status_cached(
    energetic_object_id: str = Query(VICTRON_TEST_OBJECT_ID, description="ID энергетического объекта"),
    max_age_seconds: int = Query(120, ge=1, le=3600, description="Максимальный возраст кеша в секундах"),
    db: AsyncSession = Depends(get_db),
):
    slave_ids = list(range(1, 14)) + [100]
    start = 3700
    count = 31
    total_pv_power = 0
    chargers = []

    for slave in slave_ids:
        try:
            r = await _get_cached_register_data(
                db=db,
                energetic_object_id=energetic_object_id,
                slave_id=slave,
                start=start,
                count=count,
                func_code=4,
                max_age_seconds=max_age_seconds,
            )

            def reg(addr):
                return r[addr - start]

            pv_voltage_0 = reg(3700) / 100
            pv_voltage_1 = reg(3701) / 100
            pv_voltage_2 = reg(3702) / 100
            pv_voltage_3 = reg(3703) / 100

            pv_power_0 = reg(3724)
            pv_power_1 = reg(3725)
            pv_power_2 = reg(3726)
            pv_power_3 = reg(3727)

            pv_current_0 = round(pv_power_0 / pv_voltage_0, 2) if pv_voltage_0 > 0 else 0
            pv_current_1 = round(pv_power_1 / pv_voltage_1, 2) if pv_voltage_1 > 0 else 0
            pv_current_2 = round(pv_power_2 / pv_voltage_2, 2) if pv_voltage_2 > 0 else 0
            pv_current_3 = round(pv_power_3 / pv_voltage_3, 2) if pv_voltage_3 > 0 else 0

            power_sum = pv_power_0 + pv_power_1 + pv_power_2 + pv_power_3
            total_pv_power += power_sum

            chargers.append({
                "slave": slave,
                "power": power_sum,
                "strings": [
                    {"voltage": pv_voltage_0, "current": pv_current_0, "power": pv_power_0},
                    {"voltage": pv_voltage_1, "current": pv_current_1, "power": pv_power_1},
                    {"voltage": pv_voltage_2, "current": pv_current_2, "power": pv_power_2},
                    {"voltage": pv_voltage_3, "current": pv_current_3, "power": pv_power_3},
                ],
            })
        except HTTPException as e:
            chargers.append({"slave": slave, "error": e.detail})

    return {
        "chargers": chargers,
        "TotalPVPower": total_pv_power,
    }


@router.get("/dynamic_ess_settings_cached")
async def get_dynamic_ess_settings_cached(
    energetic_object_id: str = Query(VICTRON_TEST_OBJECT_ID, description="ID энергетического объекта"),
    max_age_seconds: int = Query(120, ge=1, le=3600, description="Максимальный возраст кеша в секундах"),
    db: AsyncSession = Depends(get_db),
):
    regs = await _get_cached_register_data(
        db=db,
        energetic_object_id=energetic_object_id,
        slave_id=100,
        start=5420,
        count=10,
        func_code=3,
        max_age_seconds=max_age_seconds,
    )
    return {
        "BatteryCapacity_kWh": regs[0] / 10.0,
        "FullChargeDuration_hr": regs[1],
        "FullChargeInterval_day": regs[2],
        "DynamicEssMode": regs[3],
        "Schedule_AllowGridFeedIn": regs[4],
        "Schedule_Duration_sec": regs[5],
        "Schedule_Restrictions": regs[6],
        "Schedule_TargetSoc_pct": regs[7],
        "Schedule_Start_unix": (regs[8] << 16) + regs[9],
    }


@router.get("/test_dynamic_ess_registers")
async def test_dynamic_ess_registers(
    request: Request,
    start: int = Query(..., description="Начальный регистр"),
    end: int = Query(..., description="Конечный регистр"),
    unit_id: int = Query(100, description="Slave UID устройства")
):
    client = request.app.state.modbus_client
    results = {}

    for reg in range(start, end + 1):
        try:
            res = await client.read_holding_registers(address=reg, count=1, slave=unit_id)
            if res.isError():
                results[str(reg)] = f"❌ Error: {res}"
            elif hasattr(res, "registers"):
                results[str(reg)] = f"✅ Value: {res.registers[0]}"
            else:
                results[str(reg)] = "❓ No 'registers' attribute"
        except Exception as e:
            results[str(reg)] = f"💥 Exception: {str(e)}"

    return results


@router.post("/write_register")
async def write_register(request_data: RegisterWriteRequest, request: Request):
    """
    Записывает значение в указанный регистр Modbus.
    """
    try:
        client = request.app.state.modbus_client
        
        # Записываем значение в регистр
        result = await client.write_register(
            address=request_data.register_number,
            value=request_data.value,
            slave=request_data.slave_id
        )
        
        if result.isError():
            raise HTTPException(status_code=500, detail="Ошибка записи регистра")
            
        return {"status": "success", "register": request_data.register_number, "value": request_data.value}
        
    except Exception as e:
        register_modbus_error()
        logger.error(f"❗ Ошибка записи регистра {request_data.register_number}", exc_info=e)
        raise HTTPException(status_code=500, detail="Modbus ошибка")


# Добавить energetic object id - done

@router.get(
    "/measurements/",
    response_model=PaginatedResponse[Union[EnergyMeterMeasurementResponse, CerboMeasurementResponse]],
    summary="Получить все измерения энергосистемы с пагинацией и фильтрацией",
    description="Получает список всех измерений с поддержкой пагинации, фильтрации по имени объекта и диапазону дат.",
    tags=["Measurements"]
)
async def read_legacy_measurements(
    page: int = Query(1, ge=1, description="Номер страницы (начиная с 1)"),
    page_size: int = Query(10, ge=1, le=1000, description="Количество элементов на странице (от 1 до 1000)"),
    energetic_object_id: Optional[str] = Query(None, description="Фильтр по ID объекта"),
    object_name: Optional[str] = Query(None, description="Фильтр по имени объекта"),
    start_date: Optional[datetime] = Query(None, description="Начальная дата измерения (ISO 8601, например '2023-01-01T00:00:00')"),
    end_date: Optional[datetime] = Query(None, description="Конечная дата измерения (ISO 8601, например '2023-12-31T23:59:59')"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth_service.get_current_user),
    _: User = Depends(user_access),
):
    resolved_object_id = await _resolve_history_object_id(
        db,
        energetic_object_id=energetic_object_id,
        object_name=object_name,
    )
    energetic_object = await ensure_object_permission(
        db,
        energetic_object_id=resolved_object_id,
        user=current_user,
        required_access=AccessLevel.READ,
    )

    return await _get_object_measurements(
        db=db,
        energetic_object=energetic_object,
        page=page,
        page_size=page_size,
        start_date=start_date,
        end_date=end_date,
    )

@router.get(
    "/v1/measurements/",
    response_model=PaginatedResponse[Union[EnergyMeterMeasurementResponse, CerboMeasurementResponse]],
    summary="Deprecated alias for /modbus/measurements/",
    description=(
        "Deprecated: use `/api/modbus/measurements/` with `energetic_object_id`. "
        "This alias is kept temporarily for backward compatibility."
    ),
    tags=["Measurements"],
    deprecated=True,
)
async def read_measurements_v1(
    page: int = Query(1, ge=1, description="Номер страницы (начиная с 1)"),
    page_size: int = Query(10, ge=1, le=1000, description="Количество элементов на странице (от 1 до 1000)"),
    energetic_object_id: str = Query(..., description="Фильтр по ID объекта"),
    start_date: Optional[datetime] = Query(None, description="Начальная дата измерения (ISO 8601, например '2023-01-01T00:00:00')"),
    end_date: Optional[datetime] = Query(None, description="Конечная дата измерения (ISO 8601, например '2023-12-31T23:59:59')"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth_service.get_current_user),
    _: User = Depends(user_access),
):
    energetic_object = await ensure_object_permission(
        db,
        energetic_object_id=energetic_object_id,
        user=current_user,
        required_access=AccessLevel.READ,
    )
    return await _get_object_measurements(
        db=db,
        energetic_object=energetic_object,
        page=page,
        page_size=page_size,
        start_date=start_date,
        end_date=end_date,
    )


# Добавить energetic object id ?
  
@router.get(
    "/measurements/averaged/",
    response_model=List[CerboMeasurementResponse],
    summary="Усреднённые измерения по интервалам",
    description="Возвращает усреднённые измерения, сгруппированные по интервалам времени (например, 60 точек за час)",
    tags=["Measurements"]
)
async def get_averaged_measurements(
    energetic_object_id: Optional[str] = Query(None, description="Фильтр по ID объекта"),
    object_name: Optional[str] = Query(None, description="Фильтр по имени объекта"),
    start_date: datetime = Query(..., description="Начальная дата периода (ISO 8601)"),
    end_date: datetime = Query(..., description="Конечная дата периода (ISO 8601)"),
    intervals: int = Query(60, gt=0, description="Количество интервалов для усреднения"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth_service.get_current_user),
    _: User = Depends(user_access),
):
    """
    Получает усреднённые измерения за указанный период.
    Каждая точка данных представляет собой среднее значение за интервал времени.
    Например, при intervals=60 за час будет возвращено 60 точек (по одной на минуту),
    где каждая точка - среднее из ~30 измерений (с интервалом 2 секунды).
    """
    try:
        resolved_object_id = await _resolve_history_object_id(
            db,
            energetic_object_id=energetic_object_id,
            object_name=object_name,
        )
        await ensure_object_permission(
            db,
            energetic_object_id=resolved_object_id,
            user=current_user,
            required_access=AccessLevel.READ,
        )
        return await get_averaged_power_history(
            db=db,
            energetic_object_id=resolved_object_id,
            start_date=start_date,
            end_date=end_date,
            intervals=intervals,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Ошибка при получении усреднённых измерений: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get(
    "/measurements/energy/",
    summary="Энергетический баланс по интервалам",
    description="Считает энергию (кВт·ч) по каждому интервалу времени: солнце, нагрузка, сеть, батарея. "
                "Возвращает интервалы и итоговые значения за период.",
    tags=["Measurements"]
)
async def get_energy_measurements(
    energetic_object_id: Optional[str] = Query(None, description="Фильтр по ID объекта"),
    object_name: Optional[str] = Query(None, description="Фильтр по имени объекта"),
    start_date: datetime = Query(..., description="Начальная дата периода (ISO 8601)"),
    end_date: datetime = Query(..., description="Конечная дата периода (ISO 8601)"),
    interval_minutes: int = Query(30, gt=0, le=60*24*30, description="Длина интервала в минутах (например 30 → 30-минутные интервалы)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth_service.get_current_user),
    _: User = Depends(user_access),
):
    try:
        resolved_object_id = await _resolve_history_object_id(
            db,
            energetic_object_id=energetic_object_id,
            object_name=object_name,
        )
        await ensure_object_permission(
            db,
            energetic_object_id=resolved_object_id,
            user=current_user,
            required_access=AccessLevel.READ,
        )
        return await get_energy_power_history(
            db=db,
            energetic_object_id=resolved_object_id,
            start_date=start_date,
            end_date=end_date,
            interval_minutes=interval_minutes,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Ошибка при расчёте энергии: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")
