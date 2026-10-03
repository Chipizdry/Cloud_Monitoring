"""
PI30 response parser used by backend websocket ingestion.

Mirrors the frontend parsing rules for Axioma PI30 messages:
- payload extraction from nested websocket message shapes
- CRC16/XMODEM validation
- command-specific parsing for QPIGS, QPIWS, QFLAG and QPGS[n]
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def is_pi30_command(cmd: str | None) -> bool:
    if not cmd:
        return False

    normalized = str(cmd).strip().upper()
    if not normalized:
        return False

    # PI30 telemetry commands currently supported by backend parser.
    return normalized in {"QPIGS", "QPIWS", "QFLAG"} or normalized.startswith("QPGS")


def _to_int(value: str) -> int:
    text = str(value).strip()
    try:
        return int(text)
    except ValueError:
        # Some devices send integer fields as float-like strings, e.g. "00.0".
        return int(float(text))


def _to_float(value: str) -> float:
    return float(value)


def hex_to_ascii(hex_payload: str) -> str:
    if not hex_payload or not isinstance(hex_payload, str):
        return ""

    clean = "".join(hex_payload.split())
    if len(clean) % 2 != 0:
        clean = clean[:-1]

    chars: list[str] = []
    for i in range(0, len(clean), 2):
        byte_hex = clean[i : i + 2]
        try:
            chars.append(chr(int(byte_hex, 16)))
        except ValueError:
            continue
    return "".join(chars)


def crc16_axioma(hex_payload: str) -> int:
    """CRC-16/XMODEM (poly 0x1021, init 0x0000)."""
    clean = "".join(hex_payload.split())
    if len(clean) % 2 != 0:
        raise ValueError("hex payload length must be even")

    data = bytes.fromhex(clean)
    crc = 0x0000
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def verify_pi30_crc(hex_response: str) -> Dict[str, Any]:
    if not hex_response:
        return {"checked": False, "valid": None}

    clean = "".join(str(hex_response).split()).upper()
    if len(clean) < 6:
        return {"checked": False, "valid": None}

    no_cr = clean[:-2] if clean.endswith("0D") else clean
    if len(no_cr) < 4:
        return {"checked": False, "valid": None}

    data_without_crc = no_cr[:-4]
    received_crc_hex = no_cr[-4:]

    try:
        calculated_crc = crc16_axioma(data_without_crc)
        received_crc = int(received_crc_hex, 16)
    except ValueError:
        return {"checked": False, "valid": None}

    valid = calculated_crc == received_crc
    return {
        "checked": True,
        "valid": valid,
        "calculated_crc_hex": f"{calculated_crc:04x}",
        "received_crc_hex": f"{received_crc:04x}",
        "data_without_crc": data_without_crc,
    }


def extract_pi30_payload(raw: Dict[str, Any]) -> Dict[str, Optional[str]]:
    nested = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    cmd_candidates = [
        nested.get("cmd"),
        raw.get("cmd"),
        nested.get("pi30_command_name"),
        raw.get("pi30_command_name"),
        nested.get("command_name"),
        raw.get("command_name"),
        nested.get("pi30_command"),
        raw.get("pi30_command"),
        nested.get("command_type"),
        raw.get("command_type"),
    ]
    cmd = next(
        (
            str(candidate).strip().upper()
            for candidate in cmd_candidates
            if candidate and is_pi30_command(str(candidate).strip().upper())
        ),
        next((str(candidate) for candidate in cmd_candidates if candidate), None),
    )
    hex_response = (
        nested.get("hex_response")
        or raw.get("hex_response")
        or nested.get("hex_data")
        or raw.get("hex_data")
        or nested.get("pi30")
        or raw.get("pi30")
    )
    return {
        "cmd": str(cmd) if cmd else None,
        "hex_response": str(hex_response) if hex_response else None,
    }


def _clean_ascii(ascii_response: str) -> str:
    return ascii_response.replace("\x03", "").replace("\x19", "").replace("\r", "").replace("\n", "").replace("(", "").replace(")", "").strip()


def parse_qflag(ascii_or_hex: str) -> Optional[Dict[str, Any]]:
    if not ascii_or_hex:
        return None

    compact = "".join(ascii_or_hex.split())
    if compact and all(ch in "0123456789abcdefABCDEF" for ch in compact):
        ascii_payload = hex_to_ascii(compact)
    else:
        ascii_payload = ascii_or_hex

    flags_map = {
        "A": "silenceBuzzer",
        "B": "overloadBypass",
        "J": "powerSaving",
        "K": "lcdEscape",
        "U": "overloadRestart",
        "V": "overTempRestart",
        "X": "backlight",
        "Y": "alarmOnPrimaryInterrupt",
        "Z": "faultCodeRecord",
    }

    clean = _clean_ascii(ascii_payload).replace(" ", "")
    result: Dict[str, bool] = {}
    current_state: Optional[bool] = None

    for ch in clean:
        upper = ch.upper()
        if upper == "E":
            current_state = True
            continue
        if upper == "D":
            current_state = False
            continue
        if upper in flags_map and current_state is not None:
            result[flags_map[upper]] = current_state

    return result


def parse_qpgsn(cmd: str, ascii_response: str) -> Optional[Dict[str, Any]]:
    unit_index: Optional[int] = None
    suffix = cmd.replace("QPGS", "", 1)
    if suffix:
        try:
            unit_index = int(suffix)
        except ValueError:
            unit_index = None

    clean = _clean_ascii(ascii_response)
    parts = clean.split()
    if len(parts) < 18:
        return None

    result: Dict[str, Any] = {
        "unit": unit_index,
        "parallelExist": parts[0] == "1",
        "serialNumber": parts[1],
        "workMode": parts[2],
        "faultCode": _to_int(parts[3]),
        "gridVoltage": _to_float(parts[4]),
        "gridFrequency": _to_float(parts[5]),
        "outputVoltage": _to_float(parts[6]),
        "outputFrequency": _to_float(parts[7]),
        "outputApparentPower": _to_int(parts[8]),
        "outputActivePower": _to_int(parts[9]),
        "loadPercent": _to_int(parts[10]),
        "batteryVoltage": _to_float(parts[11]),
        "batteryChargeCurrent": _to_int(parts[12]),
        "batterySOC": _to_int(parts[13]),
        "pvVoltage": _to_float(parts[14]),
        "totalChargeCurrent": _to_int(parts[15]),
        "totalOutputApparentPower": _to_int(parts[16]),
        "totalOutputActivePower": _to_int(parts[17]),
    }

    if len(parts) >= 27:
        result.update(
            {
                "totalLoadPercent": _to_int(parts[18]),
                "inverterStatusBits": parts[19],
                "outputMode": _to_int(parts[20]),
                "chargerPriority": _to_int(parts[21]),
                "maxChargerCurrent": _to_int(parts[22]),
                "maxChargerRange": _to_int(parts[23]),
                "maxACChargerCurrent": _to_int(parts[24]),
                "pvChargeCurrent": _to_int(parts[25]),
                "batteryDischargeCurrent": _to_int(parts[26]),
            }
        )

    return result


def parse_qpiws(ascii_response: str) -> Optional[Dict[str, Any]]:
    clean = _clean_ascii(ascii_response)
    if len(clean) < 32:
        return None

    bits = clean[:32]
    warnings_map = [
        (0, "reserved0", "Reserved"),
        (1, "inverterFault", "Inverter fault"),
        (2, "busOverFault", "Bus Over Fault"),
        (3, "busUnderFault", "Bus Under Fault"),
        (4, "busSoftFail", "Bus Soft Fail Fault"),
        (5, "lineFail", "LINE_FAIL Warning"),
        (6, "opvShort", "OPVShort Warning"),
        (7, "inverterVoltageLow", "Inverter voltage too low"),
        (8, "inverterVoltageHigh", "Inverter voltage too high"),
        (9, "overTemperature", "Over temperature"),
        (10, "fanLocked", "Fan locked"),
        (11, "batteryVoltageHigh", "Battery voltage high"),
        (12, "batteryLowAlarm", "Battery low alarm"),
        (13, "reserved13", "Reserved"),
        (14, "batteryUnderShutdown", "Battery under shutdown"),
        (15, "reserved15", "Reserved"),
        (16, "overload", "Over load"),
        (17, "eepromFault", "Eeprom fault"),
        (18, "inverterOverCurrent", "Inverter Over Current Fault"),
        (19, "inverterSoftFail", "Inverter Soft Fail Fault"),
        (20, "selfTestFail", "Self Test Fail Fault"),
        (21, "opDcVoltageOver", "OP DC Voltage Over Fault"),
        (22, "batteryOpen", "Bat Open Fault"),
        (23, "currentSensorFail", "Current Sensor Fail Fault"),
        (24, "batteryShort", "Battery Short Fault"),
        (25, "powerLimit", "Power limit Warning"),
        (26, "pvVoltageHigh", "PV voltage high Warning"),
        (27, "mpptOverloadFault", "MPPT overload fault"),
        (28, "mpptOverloadWarning", "MPPT overload warning"),
        (29, "batteryTooLowToCharge", "Battery too low to charge"),
        (30, "reserved30", "Reserved"),
        (31, "reserved31", "Reserved"),
    ]

    result: Dict[str, Any] = {
        "rawBits": bits,
        "activeWarnings": [],
        "activeFaults": [],
    }

    inverter_fault_main = bits[1] == "1"
    always_fault_bits = {1, 2, 3, 4, 7, 8, 18, 19, 20, 21, 22, 23, 24, 27}
    depends_on_main_fault_bits = {9, 10, 11, 16}

    for bit, name, description in warnings_map:
        if name.startswith("reserved"):
            continue
        if bits[bit] != "1":
            continue

        issue_type = "warning"
        if bit in depends_on_main_fault_bits:
            issue_type = "fault" if inverter_fault_main else "warning"
        if bit in always_fault_bits:
            issue_type = "fault"

        entry = {
            "bit": bit,
            "name": name,
            "description": description,
            "type": issue_type,
        }
        if issue_type == "fault":
            result["activeFaults"].append(entry)
        else:
            result["activeWarnings"].append(entry)

    return result


def parse_qpigs(hex_response: str) -> Optional[Dict[str, Any]]:
    ascii_response = hex_to_ascii(hex_response).strip()
    if not ascii_response.startswith("("):
        return None

    clean = ascii_response.replace("(", "").replace(")", "")
    parts = clean.split()
    if len(parts) < 17:
        return None

    apparent_power = _to_float(parts[4])
    output_voltage = _to_float(parts[2]) if _to_float(parts[2]) != 0 else 1.0
    output_current = apparent_power / output_voltage

    result: Dict[str, Any] = {
        "inputVoltage": _to_float(parts[0]),
        "inputFrequency": _to_float(parts[1]),
        "outputVoltage": _to_float(parts[2]),
        "outputFrequency": _to_float(parts[3]),
        "outputApparentPower": apparent_power,
        "outputActivePower": _to_int(parts[5]),
        "loadPercent": _to_int(parts[6]),
        "busVoltage": _to_int(parts[7]),
        "batteryVoltage": _to_float(parts[8]),
        "batteryChargeCurrent": _to_int(parts[9]),
        "batterySOC": _to_int(parts[10]),
        "inverterTemp": _to_int(parts[11]),
        "pvChargeCurrent": _to_int(parts[12]),
        "pvVoltage": _to_float(parts[13]),
        "batteryVoltageSCC": _to_float(parts[14]),
        "batteryDischargeCurrent": _to_int(parts[15]),
        "statusBits": parts[16],
        "outputCurrent": output_current,
    }

    pv_voltage = float(result["pvVoltage"] or 0)
    pv_current = float(result["pvChargeCurrent"] or 0)
    result["solarPower"] = pv_voltage * pv_current

    charge_current = float(result["batteryChargeCurrent"] or 0)
    discharge_current = float(result["batteryDischargeCurrent"] or 0)
    battery_voltage = float(result["batteryVoltage"] or 0)
    battery_current = -charge_current if charge_current > 0 else discharge_current
    battery_total_power = 0.0
    input_power = apparent_power

    if charge_current > 0:
        battery_total_power = -battery_voltage * charge_current
        if result["inputVoltage"] != 0:
            input_power = apparent_power + abs(battery_total_power)
    elif discharge_current > 0:
        battery_total_power = battery_voltage * discharge_current
        if result["inputVoltage"] != 0:
            input_power = apparent_power - abs(battery_total_power)

    if result["inputVoltage"] == 0:
        input_power = 0.0

    input_current = input_power / result["inputVoltage"] if result["inputVoltage"] > 0 else 0.0

    result["batteryCurrent"] = battery_current
    result["batteryTotalPower"] = battery_total_power
    result["inputPower"] = input_power
    result["inputCurrent"] = input_current

    return result


def parse_pi30_by_cmd(cmd: str, hex_response: str) -> Optional[Dict[str, Any]]:
    ascii_response = _clean_ascii(hex_to_ascii(hex_response))
    if cmd.startswith("QPGS"):
        return parse_qpgsn(cmd, ascii_response)
    if cmd == "QPIGS":
        return parse_qpigs(hex_response)
    if cmd == "QFLAG":
        return parse_qflag(ascii_response)
    if cmd == "QPIWS":
        return parse_qpiws(ascii_response)
    return None


def parse_pi30_event(raw_message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    payload = extract_pi30_payload(raw_message)
    cmd_raw = payload.get("cmd")
    cmd = str(cmd_raw).strip().upper() if cmd_raw else None
    hex_response = payload.get("hex_response")
    if not cmd or not hex_response:
        return None

    if not is_pi30_command(cmd):
        return None

    if hex_response == "No response from RS485":
        return {
            "cmd": cmd,
            "hex_response": hex_response,
            "ascii_response": "",
            "status": "no_response",
            "crc": {"checked": False, "valid": None},
            "parsed": None,
        }

    ascii_response = _clean_ascii(hex_to_ascii(hex_response))
    if "NAK" in ascii_response:
        return {
            "cmd": cmd,
            "hex_response": hex_response,
            "ascii_response": ascii_response,
            "status": "nak",
            "crc": verify_pi30_crc(hex_response),
            "parsed": None,
        }

    parsed = parse_pi30_by_cmd(cmd, hex_response)
    status = "ok" if parsed is not None else "unsupported_or_invalid"
    return {
        "cmd": cmd,
        "hex_response": hex_response,
        "ascii_response": ascii_response,
        "status": status,
        "crc": verify_pi30_crc(hex_response),
        "parsed": parsed,
    }
