


/* ============================================================
 * BlueSun COR-Bridge / Modbus RTU monitoring
 * Protocol 4 (BSM-11000BLV-48, single-phase 11 kW)
 *
 * Backend опрашивает инвертор по блокам (FC03),
 * Browser слушает /dev-modbus/responses и рисует UI.
 *
 * ВСЕ локальные идентификаторы с префиксом bluesun*
 * ============================================================ */

/* ---------------- GLOBAL STATE ---------------- */
let bluesunWS = null;
let bluesunMonitorRunning = false;
let bluesunWSReconnectTimer = null;
let bluesunOfflineTimer = null;

let bluesunFails = 0;
let bluesunFirstDataReceived = false;

const BLUESUN_FAIL_LIMIT     = 3;
const BLUESUN_RECONNECT_MS   = 3000;
const BLUESUN_OFFLINE_DELAY  = 5000;

const BLUESUN_VERBOSE        = true;
const BLUESUN_MISSING_PERIOD = 30000;

/* ============================================================
 * SUFFIX → BLOCK MAP
 * Синхронизировано с bluesun_polling.json (BSM-11000BLV-48).
 * ============================================================ */
const BLUESUN_SUFFIX_MAP = {
    identity:  { start: 184, count: 1  },
    serial:    { start: 186, count: 12 },
    powerflow: { start: 198, count: 1  },
    workmode:  { start: 201, count: 1  },
    grid:      { start: 202, count: 6  },
    inverter:  { start: 226, count: 6  },
    output:    { start: 252, count: 5  },
    battery:   { start: 277, count: 5  },
    pv:        { start: 302, count: 4  },
    ct:        { start: 326, count: 2  },
    phaseL1:   { start: 338, count: 18 },
    gen_daily: { start: 702, count: 1  },
    gen_total: { start: 703, count: 2  },
    rating:    { start: 762, count: 1  },

    /* необязательные — включаются в polling config по желанию */
    fault:     { start: 100, count: 2  },   // ULong, 32-bit
    warning:   { start: 108, count: 2  },   // ULong, 32-bit
    devtype:   { start: 171, count: 1  }    // UInt
};

const BLUESUN_EXPECTED = Object.keys(BLUESUN_SUFFIX_MAP);
const bluesunSeenCounts = {};
let bluesunLastMissingCheck = 0;

/* ============================================================
 * LOG
 * ============================================================ */
function bluesunLog(msg, data) {
    if (data !== undefined) console.log(`[BlueSun] ${msg}`, data);
    else                    console.log(`[BlueSun] ${msg}`);
}
function bluesunWarn(msg, data) {
    if (data !== undefined) console.warn(`[BlueSun] ${msg}`, data);
    else                    console.warn(`[BlueSun] ${msg}`);
}
function bluesunErr(msg, data) {
    if (data !== undefined) console.error(`[BlueSun] ${msg}`, data);
    else                    console.error(`[BlueSun] ${msg}`);
}
function bluesunFmt(n, d = 1) {
    if (n == null || !isFinite(n)) return "—";
    return Number(n).toFixed(d);
}

/* ============================================================
 * CRC16 Modbus + бинарные хелперы
 * ============================================================ */
function bluesunCrc16(buf) {
    let crc = 0xFFFF;
    for (let i = 0; i < buf.length; i++) {
        crc ^= buf[i];
        for (let b = 0; b < 8; b++)
            crc = (crc & 1) ? ((crc >> 1) ^ 0xA001) : (crc >> 1);
    }
    return crc & 0xFFFF;
}
function bluesunCheckCrc(hex) {
    if (typeof hex !== "string") return false;
    const clean = hex.replace(/[\s:,-]/g, "").toUpperCase();
    if (clean.length < 8 || clean.length % 2 !== 0) return false;
    if (!/^[0-9A-F]+$/.test(clean)) return false;
    const bytes = [];
    for (let i = 0; i < clean.length; i += 2) bytes.push(parseInt(clean.substr(i, 2), 16));
    const recv = bytes[bytes.length - 2] | (bytes[bytes.length - 1] << 8);
    const calc = bluesunCrc16(bytes.slice(0, -2));
    if (calc !== recv) {
        bluesunWarn(`CRC mismatch calc=${calc.toString(16).padStart(4,"0")} recv=${recv.toString(16).padStart(4,"0")}`);
        return false;
    }
    return true;
}
function bluesunToSigned16(v) { v &= 0xFFFF; return v >= 0x8000 ? v - 0x10000 : v; }
function bluesunToSigned32(hi, lo) { return ((hi << 16) | (lo & 0xFFFF)) | 0; }
function bluesunToUnsigned32(hi, lo) { return (((hi >>> 0) * 0x10000) + (lo & 0xFFFF)) >>> 0; }
function bluesunRegsToAscii(regs) {
    let s = "";
    for (const r of regs) {
        s += String.fromCharCode((r >> 8) & 0xFF);
        s += String.fromCharCode(r & 0xFF);
    }
    return s.replace(/\0/g, "").trim();
}
function bluesunModbusHexToRegisters(hex) {
    if (typeof hex !== "string") return null;
    const clean = hex.replace(/[\s:,-]/g, "").toUpperCase();
    if (!/^[0-9A-F]+$/.test(clean) || clean.length < 10 || clean.length % 2 !== 0) {
        bluesunErr("Modbus: некорректный HEX", hex);
        return null;
    }
    const bytes = new Uint8Array(clean.length / 2);
    for (let i = 0; i < bytes.length; i++) bytes[i] = parseInt(clean.substr(i * 2, 2), 16);
    const slaveId = bytes[0], fc = bytes[1], byteCount = bytes[2];
    if (fc & 0x80) { bluesunErr(`Modbus exception slave=${slaveId} fc=0x${fc.toString(16)} exc=0x${bytes[2].toString(16)}`); return null; }
    if (fc !== 0x03) { bluesunErr(`Modbus: не FC03 (${fc})`); return null; }
    if (bytes.length !== 3 + byteCount + 2 || byteCount % 2 !== 0) {
        bluesunErr(`Modbus: length mismatch byteCount=${byteCount} len=${bytes.length}`);
        return null;
    }
    const regs = [];
    for (let i = 0; i < byteCount; i += 2) regs.push((bytes[3 + i] << 8) | bytes[3 + i + 1]);
    return { slaveId, fc, byteCount, regs };
}
function bluesunCheckBlockLength(suffix, r) {
    const expected = BLUESUN_SUFFIX_MAP[suffix]?.count;
    if (expected == null) return true;
    if (!r || r.regs.length !== expected) {
        bluesunWarn(`блок ${suffix}: пришло ${r?.regs?.length ?? 0} рег., ожидалось ${expected}`);
        return false;
    }
    return true;
}
function bluesunTrackSeen(suffix) {
    bluesunSeenCounts[suffix] = (bluesunSeenCounts[suffix] || 0) + 1;
    const now = Date.now();
    if (now - bluesunLastMissingCheck < BLUESUN_MISSING_PERIOD) return;
    bluesunLastMissingCheck = now;
    const missing = BLUESUN_EXPECTED.filter(s => !bluesunSeenCounts[s] && BLUESUN_SUFFIX_MAP[s].optional !== true);
    if (missing.length) bluesunWarn(`не пришли блоки: [${missing.join(", ")}]`);
}

/* ============================================================
 * WORK MODES
 * ============================================================ */
const BLUESUN_WORK_MODE_MAP = {
    0: "power_on", 1: "standby", 2: "grid",
    3: "off_grid", 4: "bypass",  5: "charging", 6: "fault"
};
const BLUESUN_WORK_MODE_RU = {
    0: "Включён", 1: "Ожидание", 2: "От сети",
    3: "Автономный", 4: "Байпас", 5: "Заряд", 6: "Авария"
};


/* ============================================================
 * ИНДЕКС ПОЛЕЙ — используется для сводной таблицы
 * ============================================================ */
const BLUESUN_FIELD_INDEX = [
    /* ---- ID ---- */
    { key: "protocolNumber",       group: "ID",        label: "Протокол",              unit: "" },
    { key: "serialNumber",         group: "ID",        label: "Серийный номер",        unit: "" },
    { key: "deviceType",           group: "ID",        label: "Тип устройства",        unit: "" },
    { key: "ratedPower",           group: "ID",        label: "Номинал",               unit: "W" },

    /* ---- STATE ---- */
    { key: "workingModeRu",        group: "State",     label: "Режим",                 unit: "" },
    { key: "faultCode",            group: "State",     label: "Fault code",            unit: "hex", hex: true },
    { key: "warningCode",          group: "State",     label: "Warning code",          unit: "hex", hex: true },

    /* ---- GRID (202..207) ---- */
    { key: "gridCurrent",          group: "Grid",      label: "I сети",                unit: "A",  reg: 202, scale: 0.1 },
    { key: "gridFrequency",        group: "Grid",      label: "F сети",                unit: "Hz", reg: 203, scale: 0.01 },
    { key: "gridPower",            group: "Grid",      label: "P сети",                unit: "W",  reg: 204, signed: true, meaning: "grid" },
    { key: "gridApparentPower",    group: "Grid",      label: "S сети",                unit: "VA", reg: 205 },
    { key: "gridChargingPower",    group: "Grid",      label: "P заряда из сети",      unit: "W",  reg: 206 },
    { key: "gridVoltageL1",        group: "Grid",      label: "U L1",                  unit: "V",  reg: 338, scale: 0.1 },
    { key: "gridCurrentL1",        group: "Grid",      label: "I L1",                  unit: "A",  reg: 339, scale: 0.1 },

    /* ---- INVERTER (226..231) ---- */
    { key: "inverterCurrent",      group: "Inverter",  label: "I AC",                  unit: "A",  reg: 226, scale: 0.1 },
    { key: "inverterFrequency",    group: "Inverter",  label: "F AC",                  unit: "Hz", reg: 227, scale: 0.01 },
    { key: "inverterPower",        group: "Inverter",  label: "P AC",                  unit: "W",  reg: 228 },
    { key: "inverterApparentPower",group: "Inverter",  label: "S AC",                  unit: "VA", reg: 229 },
    { key: "inverterChargingCurrent",group:"Inverter", label: "I заряда",              unit: "A",  reg: 230, scale: 0.1 },
    { key: "inverterTemperature",  group: "Inverter",  label: "T инвертора",           unit: "°C", reg: 231 },
    { key: "inverterVoltageL1",    group: "Inverter",  label: "U AC L1",               unit: "V",  reg: 342, scale: 0.1 },

    /* ---- OUTPUT / LOAD (252..256) ---- */
    { key: "outputCurrent",        group: "Load",      label: "I нагрузки",            unit: "A",  reg: 252, scale: 0.1 },
    { key: "outputFrequency",      group: "Load",      label: "F нагрузки",            unit: "Hz", reg: 253, scale: 0.01 },
    { key: "outputPower",          group: "Load",      label: "P нагрузки",            unit: "W",  reg: 254 },
    { key: "outputApparentPower",  group: "Load",      label: "S нагрузки",            unit: "VA", reg: 255 },
    { key: "loadPercent",          group: "Load",      label: "Загрузка",              unit: "%",  reg: 256 },
    { key: "outputVoltageL1",      group: "Load",      label: "U нагрузки L1",         unit: "V",  reg: 346, scale: 0.1 },

    /* ---- BATTERY (277..281) ---- */
    { key: "batteryVoltage",       group: "Battery",   label: "U батареи",             unit: "V",  reg: 277, scale: 0.1 },
    { key: "batteryCurrent",       group: "Battery",   label: "I батареи",             unit: "A",  reg: 278, scale: 0.1, signed: true, meaning: "batteryUI" },
    { key: "batteryPower",         group: "Battery",   label: "P батареи",             unit: "W",  reg: 279, signed: true, meaning: "batteryUI" },
    { key: "batterySOC",           group: "Battery",   label: "SOC",                   unit: "%",  reg: 280 },
    { key: "dcdcTemperature",      group: "Battery",   label: "T DCDC",                unit: "°C", reg: 281 },

    /* ---- PV (302..305, 351..353) ---- */
    { key: "pvPower",              group: "PV",        label: "P PV",                  unit: "W",  reg: 302 },
    { key: "pvChargingPower",      group: "PV",        label: "P заряда PV",           unit: "W",  reg: 303 },
    { key: "pvChargingCurrent",    group: "PV",        label: "I заряда PV",           unit: "A",  reg: 304, scale: 0.1 },
    { key: "pvTemperature",        group: "PV",        label: "T PV",                  unit: "°C", reg: 305 },
    { key: "pv1Voltage",           group: "PV",        label: "U PV1",                 unit: "V",  reg: 351, scale: 0.1 },
    { key: "pv1Current",           group: "PV",        label: "I PV1",                 unit: "A",  reg: 352, scale: 0.1 },
    { key: "pv1Power",             group: "PV",        label: "P PV1",                 unit: "W",  reg: 353 },

    /* ---- ENERGY (702..704) ---- */
    { key: "generationDaily",      group: "Energy",    label: "Выработка день",        unit: "kWh", reg: 702, scale: 0.01 },
    { key: "generationTotal",      group: "Energy",    label: "Выработка всего",       unit: "kWh", reg: 703, scale: 0.01 },

    /* ---- CT (326..327, 354..355) ---- */
    { key: "ctPower",              group: "CT",        label: "CT L (глоб.)",          unit: "W",  reg: 326, signed: true, meaning: "ct" },
    { key: "ctPowerL1",            group: "CT",        label: "CT L1",                 unit: "W",  reg: 354, signed: true, meaning: "ct" },

    /* ---- НОРМАЛИЗОВАННЫЕ (то, что уходит в UI) ---- */
    { key: "inputPower",           group: "→ UI",      label: "P сети",                unit: "W",  signed: true, meaning: "grid" },
    { key: "inputVoltage",         group: "→ UI",      label: "U сети",                unit: "V" },
    { key: "inputCurrent",         group: "→ UI",      label: "I сети",                unit: "A" },
    { key: "inputFrequency",       group: "→ UI",      label: "F сети",                unit: "Hz" },
    { key: "outputActivePower",    group: "→ UI",      label: "P нагрузки",            unit: "W" },
    { key: "outputVoltage",        group: "→ UI",      label: "U нагрузки",            unit: "V" },
    { key: "outputCurrent",        group: "→ UI",      label: "I нагрузки",            unit: "A" },
    { key: "solarPower",           group: "→ UI",      label: "P PV",                  unit: "W" },
    { key: "pvVoltage",            group: "→ UI",      label: "U PV",                  unit: "V" },
    { key: "pvChargeCurrent",      group: "→ UI",      label: "I PV",                  unit: "A" },
    { key: "batteryTotalPower",    group: "→ UI",      label: "P батареи",             unit: "W",  signed: true, meaning: "batteryUI" },
    { key: "batterySOC",           group: "→ UI",      label: "SOC",                   unit: "%" },
    { key: "inverterTemp",         group: "→ UI",      label: "T инвертора",           unit: "°C" }
];

/* Человеческое объяснение знака */
function bluesunInterpretSign(key, v, field) {
    if (v == null || typeof v !== "number") return "";
    if (v === 0) return "нет потока";

    const meaning = field.meaning;
    if (meaning === "grid") {
        return v > 0 ? "потребление из сети" : "отдача в сеть";
    }
    if (meaning === "batteryUI") {
        // После инверсии: +разряд / −заряд
        return v > 0 ? "разряд АКБ" : "заряд АКБ";
    }
    if (meaning === "ct") {
        return v > 0 ? "выработка в сеть" : "инжекция из сети";
    }
    if (field.signed) {
        return v > 0 ? "положительное" : "отрицательное";
    }
    return "";
}

/* ============================================================
 * СВОДНАЯ ТАБЛИЦА — печатается по требованию или раз в цикл
 * ============================================================ */
function bluesunDumpSummary(reason = "manual") {
    const data = window.lastData || {};
    const rows = [];
    let received = 0, missing = 0, zero = 0;

    for (const f of BLUESUN_FIELD_INDEX) {
        const v = data[f.key];
        const has = v !== undefined && v !== null;

        let valueStr;
        if (!has)                 { valueStr = "—";   missing++;  }
        else if (f.hex && typeof v === "number") valueStr = "0x" + v.toString(16).padStart(8, "0");
        else if (typeof v === "number") valueStr = bluesunFmt(v, 2);
        else                      valueStr = String(v);

        if (has && v === 0) zero++;
        if (has) received++;

        rows.push({
            "#":     f.reg != null ? f.reg : "",
            group:   f.group,
            field:   f.key,
            label:   f.label,
            value:   valueStr,
            unit:    f.unit || "",
            scale:   f.scale != null ? f.scale : "",
            state:   !has ? "НЕТ" : (v === 0 ? "нуль" : "✓"),
            meaning: has ? bluesunInterpretSign(f.key, v, f) : ""
        });
    }

    console.group(`[BlueSun] СВОДКА (${reason})  получено=${received}  нулей=${zero}  пропущено=${missing}  всего=${rows.length}`);
    console.table(rows);
    console.groupEnd();
}

window.bluesunDump = () => bluesunDumpSummary("manual");



const BLUESUN_CYCLE_REQUIRED = [
    "grid", "inverter", "output", "battery", "pv",
    "workmode", "powerflow", "identity", "serial",
    "ct", "phaseL1", "gen_daily", "gen_total", "rating"
];
const bluesunCycleSeen = new Set();

function bluesunTrackCycle(suffix) {
    bluesunCycleSeen.add(suffix);

    const missing = BLUESUN_CYCLE_REQUIRED.filter(s => !bluesunCycleSeen.has(s));
    if (missing.length > 0) return;

    // Полный цикл собран — печатаем сводку и сбрасываем трекер
    bluesunDumpSummary("полный цикл");
    bluesunCycleSeen.clear();
}






/* ============================================================
 * PARSERS
 * ============================================================ */
function bluesunParseBySuffix(suffix, hex) {
    if (!BLUESUN_SUFFIX_MAP[suffix]) { bluesunWarn(`неизвестный suffix: ${suffix}`); return null; }
    switch (suffix) {
        case "identity":  return bluesunParseIdentity(hex);
        case "serial":    return bluesunParseSerial(hex);
        case "powerflow": return bluesunParsePowerFlow(hex);
        case "workmode":  return bluesunParseWorkMode(hex);
        case "grid":      return bluesunParseGrid(hex);
        case "inverter":  return bluesunParseInverter(hex);
        case "output":    return bluesunParseOutput(hex);
        case "battery":   return bluesunParseBattery(hex);
        case "pv":        return bluesunParsePV(hex);
        case "ct":        return bluesunParseCT(hex);
        case "phaseL1":   return bluesunParsePhaseL1(hex);
        case "gen_daily": return bluesunParseGenDaily(hex);
        case "gen_total": return bluesunParseGenTotal(hex);
        case "rating":    return bluesunParseRating(hex);
        case "fault":     return bluesunParseFault(hex);
        case "warning":   return bluesunParseWarning(hex);
        case "devtype":   return bluesunParseDeviceType(hex);
    }
    return null;
}

function bluesunParseIdentity(hex) {
    const r = bluesunModbusHexToRegisters(hex);
    if (!r) return null;
    bluesunCheckBlockLength("identity", r);
    const protocol = r.regs[0];
    // bluesunLog(`protocol=${protocol}${protocol === 4 ? " ✔" : " ⚠ ожидали 4"}`);
    return { protocolNumber: protocol };
}
function bluesunParseSerial(hex) {
    const r = bluesunModbusHexToRegisters(hex);
    if (!r) return null;
    bluesunCheckBlockLength("serial", r);
    const serial = bluesunRegsToAscii(r.regs);
   // bluesunLog(`serial="${serial}"`);
    return { serialNumber: serial };
}
function bluesunParsePowerFlow(hex) {
    const r = bluesunModbusHexToRegisters(hex);
    if (!r) return null;
    bluesunCheckBlockLength("powerflow", r);
    const f = r.regs[0];
    const parsed = {
        powerFlowRaw:  `0x${f.toString(16).padStart(4,"0")}`,
        pv1Connected:  !!(f & 0x01),
        pv2Connected:  !!(f & 0x02),
        acInput1:      (f >> 2) & 0x03,
        acInput2:      (f >> 4) & 0x03,
        acInput3:      (f >> 6) & 0x03,
        mainOutputOn:  !(f & 0x0100),
        secOutputOn:   !(f & 0x0200)
    };
  //  bluesunLog(`powerflow raw=0x${f.toString(16).padStart(4,"0")} PV1=${parsed.pv1Connected} AC1=${parsed.acInput1} mainOut=${parsed.mainOutputOn}`);
    return parsed;
}
function bluesunParseWorkMode(hex) {
    const r = bluesunModbusHexToRegisters(hex);
    if (!r) return null;
    bluesunCheckBlockLength("workmode", r);
    const code = r.regs[0];
    const parsed = {
        workingModeRaw: code,
        workingMode:    BLUESUN_WORK_MODE_MAP[code] ?? `unknown_${code}`,
        workingModeRu:  BLUESUN_WORK_MODE_RU[code]  ?? `Неизвестно (${code})`
    };
    // bluesunLog(`workmode=${code} (${parsed.workingModeRu})`);
    return parsed;
}

/* --- 202..207: сеть (202 ток, 203 частота, 204 P, 205 S, 206 Pcharge, 207 flags) --- */
function bluesunParseGrid(hex) {
    const r = bluesunModbusHexToRegisters(hex);
    if (!r) return null;
    bluesunCheckBlockLength("grid", r);
    const parsed = {
        gridCurrent:       bluesunToSigned16(r.regs[0]) * 0.1,
        gridFrequency:     bluesunToSigned16(r.regs[1]) * 0.01,
        gridPower:         bluesunToSigned16(r.regs[2]),
        gridApparentPower: bluesunToSigned16(r.regs[3]),
        gridChargingPower: bluesunToSigned16(r.regs[4]),
        gridFlowFlags:     r.regs[5]
    };
    // bluesunLog(`✔ grid  I=${bluesunFmt(parsed.gridCurrent)}A  F=${bluesunFmt(parsed.gridFrequency,2)}Hz  P=${parsed.gridPower}W  Pchg=${parsed.gridChargingPower}W  S=${parsed.gridApparentPower}VA`);
    return parsed;
}

/* --- 226..231: инвертор --- */
function bluesunParseInverter(hex) {
    const r = bluesunModbusHexToRegisters(hex);
    if (!r) return null;
    bluesunCheckBlockLength("inverter", r);
    const parsed = {
        inverterCurrent:         bluesunToSigned16(r.regs[0]) * 0.1,
        inverterFrequency:       bluesunToSigned16(r.regs[1]) * 0.01,
        inverterPower:           bluesunToSigned16(r.regs[2]),
        inverterApparentPower:   bluesunToSigned16(r.regs[3]),
        inverterChargingCurrent: bluesunToSigned16(r.regs[4]) * 0.1,
        inverterTemperature:     bluesunToSigned16(r.regs[5])
    };
    // bluesunLog(`✔ inverter  I=${bluesunFmt(parsed.inverterCurrent)}A  F=${bluesunFmt(parsed.inverterFrequency,2)}Hz  P=${parsed.inverterPower}W  T=${parsed.inverterTemperature}°C`);
    return parsed;
}

/* --- 252..256: выход/нагрузка --- */
function bluesunParseOutput(hex) {
    const r = bluesunModbusHexToRegisters(hex);
    if (!r) return null;
    bluesunCheckBlockLength("output", r);
    const parsed = {
        outputCurrent:       bluesunToSigned16(r.regs[0]) * 0.1,
        outputFrequency:     bluesunToSigned16(r.regs[1]) * 0.01,
        outputPower:         bluesunToSigned16(r.regs[2]),
        outputApparentPower: bluesunToSigned16(r.regs[3]),
        loadPercent:         bluesunToSigned16(r.regs[4])
    };
    // bluesunLog(`✔ output  I=${bluesunFmt(parsed.outputCurrent)}A  F=${bluesunFmt(parsed.outputFrequency,2)}Hz  P=${parsed.outputPower}W  S=${parsed.outputApparentPower}VA  Load=${parsed.loadPercent}%`);
    return parsed;
}

/* --- 277..281: батарея --- */
function bluesunParseBattery(hex) {
    const r = bluesunModbusHexToRegisters(hex);
    if (!r) return null;
    bluesunCheckBlockLength("battery", r);

    const parsed = {
        batteryVoltage:  bluesunToSigned16(r.regs[0]) * 0.1,
        /* ⚠️ ИНВЕРСИЯ ЗНАКА:
         * PDF: positive = charging, negative = discharging
         * UI:  positive = discharging, negative = charging
         */
        batteryCurrent:  -bluesunToSigned16(r.regs[1]) * 0.1,
        batteryPower:    -bluesunToSigned16(r.regs[2]),
        batterySOC:      r.regs[3] & 0xFFFF,
        dcdcTemperature: bluesunToSigned16(r.regs[4])
    };
    /*bluesunLog(
        `✔ battery  V=${bluesunFmt(parsed.batteryVoltage)}V  ` +
        `I=${bluesunFmt(parsed.batteryCurrent)}A  ` +
        `P=${parsed.batteryPower}W  ` +
        `SOC=${parsed.batterySOC}%  DCDC=${parsed.dcdcTemperature}°C`
    );  */
    return parsed;
}

/* --- 302..305: PV --- */
function bluesunParsePV(hex) {
    const r = bluesunModbusHexToRegisters(hex);
    if (!r) return null;
    bluesunCheckBlockLength("pv", r);
    const parsed = {
        pvPower:           bluesunToSigned16(r.regs[0]),
        pvChargingPower:   bluesunToSigned16(r.regs[1]),
        pvChargingCurrent: bluesunToSigned16(r.regs[2]) * 0.1,
        pvTemperature:     bluesunToSigned16(r.regs[3])
    };
    // bluesunLog(`✔ pv  P=${parsed.pvPower}W  Pchg=${parsed.pvChargingPower}W  Ichg=${bluesunFmt(parsed.pvChargingCurrent)}A  T=${parsed.pvTemperature}°C`);
    return parsed;
}

/* --- 326..327: CT (Long) --- */
function bluesunParseCT(hex) {
    const r = bluesunModbusHexToRegisters(hex);
    if (!r) return null;
    bluesunCheckBlockLength("ct", r);
    const parsed = { ctPower: bluesunToSigned32(r.regs[0], r.regs[1]) };
  //  bluesunLog(`✔ ct  P=${parsed.ctPower}W`);
    return parsed;
}

/* --- 338..355: фаза L1 (18 рег.) --- */
function bluesunParsePhaseL1(hex) {
    const r = bluesunModbusHexToRegisters(hex);
    if (!r) return null;
    bluesunCheckBlockLength("phaseL1", r);
    const parsed = {
        gridVoltageL1:      bluesunToSigned16(r.regs[0])  * 0.1,   // 338
        gridCurrentL1:      bluesunToSigned16(r.regs[1])  * 0.1,   // 339
        gridPowerL1:        bluesunToSigned16(r.regs[2]),          // 340
        gridApparentL1:     bluesunToSigned16(r.regs[3]),          // 341
        inverterVoltageL1:  bluesunToSigned16(r.regs[4])  * 0.1,   // 342
        inverterCurrentL1:  bluesunToSigned16(r.regs[5])  * 0.1,   // 343
        inverterPowerL1:    bluesunToSigned16(r.regs[6]),          // 344
        inverterApparentL1: bluesunToSigned16(r.regs[7]),          // 345
        outputVoltageL1:    bluesunToSigned16(r.regs[8])  * 0.1,   // 346
        outputCurrentL1:    bluesunToSigned16(r.regs[9])  * 0.1,   // 347
        outputPowerL1:      bluesunToSigned16(r.regs[10]),         // 348
        outputApparentL1:   bluesunToSigned16(r.regs[11]),         // 349
        outputLoadPctL1:    bluesunToSigned16(r.regs[12]),         // 350
        pv1Voltage:         bluesunToSigned16(r.regs[13]) * 0.1,   // 351
        pv1Current:         bluesunToSigned16(r.regs[14]) * 0.1,   // 352
        pv1Power:           bluesunToSigned16(r.regs[15]),         // 353
        ctPowerL1:          bluesunToSigned32(r.regs[16], r.regs[17]) // 354..355
    };
 //   bluesunLog(`✔ phaseL1  Ug=${bluesunFmt(parsed.gridVoltageL1)}V  Ig=${bluesunFmt(parsed.gridCurrentL1)}A  Pg=${parsed.gridPowerL1}W  Uout=${bluesunFmt(parsed.outputVoltageL1)}V  PV1=${bluesunFmt(parsed.pv1Voltage)}V/${bluesunFmt(parsed.pv1Current)}A`);
    return parsed;
}

function bluesunParseGenDaily(hex) {
    const r = bluesunModbusHexToRegisters(hex);
    if (!r) return null;
    bluesunCheckBlockLength("gen_daily", r);
    const parsed = { generationDaily: r.regs[0] * 0.01 };
  //  bluesunLog(`✔ gen_daily=${parsed.generationDaily.toFixed(2)} kWh`);
    return parsed;
}
function bluesunParseGenTotal(hex) {
    const r = bluesunModbusHexToRegisters(hex);
    if (!r) return null;
    bluesunCheckBlockLength("gen_total", r);
    const parsed = { generationTotal: bluesunToUnsigned32(r.regs[0], r.regs[1]) * 0.01 };
    // bluesunLog(`✔ gen_total=${parsed.generationTotal.toFixed(2)} kWh`);
    return parsed;
}
function bluesunParseRating(hex) {
    const r = bluesunModbusHexToRegisters(hex);
    if (!r) return null;
    bluesunCheckBlockLength("rating", r);
    const rated = r.regs[0];
   // bluesunLog(`✔ rating=${rated} W`);
    return { ratedPower: rated };
}

/* --- 100..101: Fault code (ULong) --- */
function bluesunParseFault(hex) {
    const r = bluesunModbusHexToRegisters(hex);
    if (!r) return null;
    bluesunCheckBlockLength("fault", r);
    const code = bluesunToUnsigned32(r.regs[0], r.regs[1]);
    bluesunLog(`faultCode=0x${code.toString(16).padStart(8,"0")}`);
    return { faultCode: code };
}

/* --- 108..109: Warning code (ULong) --- */
function bluesunParseWarning(hex) {
    const r = bluesunModbusHexToRegisters(hex);
    if (!r) return null;
    bluesunCheckBlockLength("warning", r);
    const code = bluesunToUnsigned32(r.regs[0], r.regs[1]);
    bluesunLog(`warningCode=0x${code.toString(16).padStart(8,"0")}`);
    return { warningCode: code };
}

/* --- 171: Device type --- */
function bluesunParseDeviceType(hex) {
    const r = bluesunModbusHexToRegisters(hex);
    if (!r) return null;
    bluesunCheckBlockLength("devtype", r);
    const t = r.regs[0];
    bluesunLog(`deviceType=${t}`);
    return { deviceType: t };
}

/* ============================================================
 * NORMALIZE — универсальные ключи для UI
 *
 * ⚠️ ВАЖНО:
 *   inputPower = gridPower(204) + gridChargingPower(206)
 *   Напряжения (V) приходят ТОЛЬКО из phaseL1 (338/342/346/351).
 * ============================================================ */
function bluesunNormalize(raw, source = "BlueSun") {
    if (!raw) return null;
    const d = { ...raw, source };

    /* ---------- GRID ---------- */
    const gP   = Number(raw.gridPower)         || 0;
    const gChg = Number(raw.gridChargingPower) || 0;
    if (raw.gridPower != null || raw.gridChargingPower != null) {
        d.inputPower = gP + gChg;
    }
    if (raw.gridVoltageL1 != null) d.inputVoltage   = Number(raw.gridVoltageL1);
    if (raw.gridFrequency != null) d.inputFrequency = Number(raw.gridFrequency);
    if (raw.gridCurrent   != null) d.inputCurrent   = Number(raw.gridCurrent);

    /* ---------- LOAD / OUTPUT ---------- */
    if (raw.outputPower     != null) d.outputActivePower = Number(raw.outputPower) || 0;
    if (raw.outputVoltageL1 != null) d.outputVoltage     = Number(raw.outputVoltageL1);
    if (raw.outputCurrent   != null) d.outputCurrent     = Number(raw.outputCurrent);
    if (raw.outputFrequency != null) d.outputFrequency   = Number(raw.outputFrequency);
    if (raw.loadPercent     != null) d.loadPercent       = Number(raw.loadPercent);

    /* ---------- INVERTER (AC side) ---------- */
    if (raw.inverterVoltageL1 != null) d.inverterVoltage = Number(raw.inverterVoltageL1);
    if (raw.inverterCurrent   != null) d.inverterCurrent = Number(raw.inverterCurrent);
    if (raw.inverterPower     != null) d.inverterPowerAC = Number(raw.inverterPower);
    if (raw.inverterTemperature != null) d.inverterTemp  = Number(raw.inverterTemperature);

    /* ---------- SOLAR / PV ---------- */
    if (raw.pvPower != null) d.solarPower = Number(raw.pvPower) || 0;
    if (raw.pv1Voltage != null) d.pvVoltage = Number(raw.pv1Voltage);
    else if (raw.pvPower != null && raw.pvChargingCurrent > 0)
        d.pvVoltage = Number(raw.pvPower) / Number(raw.pvChargingCurrent);
    if (raw.pvChargingCurrent != null) d.pvChargeCurrent = Number(raw.pvChargingCurrent);
    if (raw.pv1Current        != null) d.pv1Current      = Number(raw.pv1Current);
    if (raw.pv1Power          != null) d.pv1Power        = Number(raw.pv1Power);
    if (raw.pvTemperature     != null) d.pvTemperature   = Number(raw.pvTemperature);
    if (raw.pvChargingPower   != null) d.pvChargingPower = Number(raw.pvChargingPower);

    /* ---------- BATTERY ---------- */
    if (raw.batteryPower   != null) d.batteryTotalPower = Number(raw.batteryPower) || 0;
    if (raw.batterySOC     != null) d.batterySOC        = Number(raw.batterySOC);
    if (raw.batteryVoltage != null) d.batteryVoltage    = Number(raw.batteryVoltage);
    if (raw.batteryCurrent != null) d.batteryCurrent    = Number(raw.batteryCurrent);
    if (raw.dcdcTemperature!= null) d.dcdcTemp          = Number(raw.dcdcTemperature);

    /* ---------- MODE / IDENTITY ---------- */
    if (raw.workingMode != null) d.mode = raw.workingMode;

    /* ---------- Aliases для updateUIByData / refreshPowerIndicators ---------- */
    if (d.solarPower        != null) d.solar       = d.solarPower;
    if (d.batteryTotalPower != null) d.battery     = d.batteryTotalPower;
    if (d.outputActivePower != null) d.load        = d.outputActivePower;
    if (d.inputPower        != null) d.grid        = d.inputPower;
    if (d.batterySOC        != null) d.battery_soc = d.batterySOC;

    /* ---------- Проверка баланса (только для логов) ---------- */
    const battP  = d.batteryTotalPower || 0;   // +разряд / −заряд
    const solarP = d.solarPower        || 0;   // +
    const gridP  = d.inputPower        || 0;   // +потребление / −отдача
    const loadP  = d.outputActivePower || 0;   // +

    const balance = solarP + gridP + battP - loadP;
    if (Math.abs(balance) > 200) {
        bluesunWarn(`баланс: PV(${solarP}) + Grid(${gridP}) + Batt(${battP}) - Load(${loadP}) = ${balance.toFixed(0)} W`);
    }

    return d;
}

/* ============================================================
 * UI
 * ============================================================ */
function bluesunUpdateUI(data) {
    if (!data) return;
    const max = window.deviceMaxPower || {};

    if (data.inputPower != null) {
        const p = Number(data.inputPower) || 0;
        window.updatePowerByName("Grid", window.PowerToIndicator(p, max.Grid));
        if (typeof networkFlowLabel !== "undefined" && networkFlowLabel)
            networkFlowLabel.textContent = window.formatPowerLabel(p, "grid");
        window.setIconStatus("Grid", "normal");
    }
    if (data.outputActivePower != null) {
        const p = Number(data.outputActivePower) || 0;
        window.updatePowerByName("Load", window.PowerToIndicator(p, max.Load));
        if (typeof loadIndicatorLabel !== "undefined" && loadIndicatorLabel)
            loadIndicatorLabel.textContent = window.formatPowerLabel(p, "load");
        window.setIconStatus("Load", "normal");
    }
    if (data.solarPower != null) {
        const p = Number(data.solarPower) || 0;
        window.updatePowerByName("Solar", window.PowerToIndicator(p, max.Solar));
        if (typeof solarPowerLabel !== "undefined" && solarPowerLabel)
            solarPowerLabel.textContent = window.formatPowerLabel(p, "solar");
        window.setIconStatus("Solar", "normal");
    }
    if (data.batteryTotalPower != null) {
        const p = Number(data.batteryTotalPower) || 0;
        window.updatePowerByName("Battery", window.PowerToIndicator(p, max.Battery));
        if (typeof batteryFlowLabel !== "undefined" && batteryFlowLabel)
            batteryFlowLabel.textContent = window.formatPowerLabel(p, "battery");
        window.setIconStatus("Battery", "normal");
    }
    if (data.batterySOC != null) window.updateBatteryFill(Number(data.batterySOC));

    window.setIconStatus("Inverter", "normal");
    window.hideLoading();
    window.setDeviceVisibility("ErrorIcon", "hidden");
    window.setDeviceVisibility("Generator",  "hidden");

}

/* ============================================================
 * ПРИМЕНЕНИЕ ОДНОГО РАСПАРСЕННОГО БЛОКА
 * ============================================================ */
function bluesunApplyParsed(parsed, suffix) {
    if (!parsed || typeof parsed !== "object") return false;

    bluesunTrackSeen(suffix);
    bluesunTrackCycle(suffix);
    const normalized = bluesunNormalize(parsed, `WS:${suffix}`);
    window.lastData = { ...window.lastData, ...normalized };

    try { window.updateUIByData(window.lastData); } catch (e) { /* noop */ }
    bluesunUpdateUI(window.lastData);

    if (parsed.ratedPower && !window.deviceMaxPower?.RatedSet) {
        window.deviceMaxPower = window.deviceMaxPower || {};
        window.deviceMaxPower.RatedSet = parsed.ratedPower;
    }

    bluesunResetFails();

    if (!bluesunFirstDataReceived) {
        bluesunFirstDataReceived = true;
        window.hideLoading();
        bluesunLog("первые данные получены ✔");
    }
    return true;
}

/* ============================================================
 * ОБРАБОТКА PAYLOAD
 * ============================================================ */
function bluesunProcessPayload(payload) {
    if (!payload) return false;

    const nested = (payload.data && typeof payload.data === "object")
        ? payload.data : payload;

    const suffix = nested.command_name ?? payload.command_name
                 ?? nested.cmd          ?? payload.cmd
                 ?? null;
    const hex = nested.hex_response ?? payload.hex_response
              ?? nested.hex_data     ?? payload.hex_data
              ?? null;

    if (!suffix || !hex) { bluesunWarn("нет command_name/hex:", payload); return false; }
    if (!BLUESUN_SUFFIX_MAP[suffix]) { bluesunWarn(`пропускаем неизвестный suffix="${suffix}"`); return false; }

    if (typeof hex === "string" && hex.trim().toLowerCase() === "no response from rs485") {
        bluesunWarn(`нет ответа RS485 на "${suffix}"`);
        bluesunRegisterFail(`Нет связи с BlueSun (RS485) — ${suffix}`);
        return false;
    }

    /*
    if (BLUESUN_VERBOSE) {
        const short = typeof hex === "string" ? hex.slice(0, 40) : hex;
        console.log(`📥 [BlueSun] ← suffix="${suffix}" hex=${short}${typeof hex === "string" && hex.length > 40 ? "…" : ""}`);
    }  */

    if (!bluesunCheckCrc(hex)) { bluesunWarn(`CRC error в "${suffix}"`); return false; }

    const parsed = bluesunParseBySuffix(suffix, hex);
    if (!parsed) { bluesunWarn(`не распознан ответ для "${suffix}"`); return false; }
    return bluesunApplyParsed(parsed, suffix);
}

/* ============================================================
 * SNAPSHOTS
 * ============================================================ */
function bluesunHandlePollingSnapshot(data) {
    if (!data || typeof data !== "object") return;
    let applied = 0;
    for (const [suffix, value] of Object.entries(data)) {
        if (typeof value === "string" && /^[0-9A-Fa-f]{10,}$/.test(value.replace(/\s/g, ""))) {
            if (bluesunProcessPayload({ command_name: suffix, hex_response: value })) applied++;
        }
    }
    if (applied) { bluesunLog(`polling_snapshot применён (${applied} блоков)`); bluesunResetOfflineTimer(); }
    else         { bluesunWarn("polling_snapshot: ничего не применено"); }
}
function bluesunHandleAgentSnapshot(events) {
    if (!Array.isArray(events)) return;
    let applied = 0;
    for (const ev of events) if (bluesunProcessPayload(ev)) applied++;
    if (applied) { bluesunLog(`cor_agent_snapshot применён (${applied}/${events.length})`); bluesunResetOfflineTimer(); }
}

/* ============================================================
 * WEBSOCKET
 * ============================================================ */
function bluesunStartWS(deviceId) {
    if (!deviceId) { bluesunErr("deviceId не задан"); return; }
    if (bluesunWS && (bluesunWS.readyState === WebSocket.OPEN || bluesunWS.readyState === WebSocket.CONNECTING)) {
        bluesunWarn("WS уже запущен"); return;
    }
    const wsUrl = window.buildAuthenticatedWebSocketUrl(
        `wss://dev.monitoring.cor-int.com/dev-modbus/responses?device_id=${encodeURIComponent(deviceId)}`
    );
    bluesunLog(`WS: ${window.maskWebSocketUrlForLog(wsUrl)}`);
    bluesunWS = new WebSocket(wsUrl);

    bluesunWS.onopen = () => { bluesunLog("WS подключён ✔"); bluesunResetOfflineTimer(); };

    bluesunWS.onmessage = (event) => {
        bluesunResetOfflineTimer();
        try {
            const raw = JSON.parse(event.data);
            if (raw?.type === "connection_established" ||
                raw?.type === "subscription_changed"  ||
                raw?.type === "ping") return;
            if (raw?.type === "polling_snapshot" && raw?.data) { bluesunHandlePollingSnapshot(raw.data); return; }
            if (raw?.type === "cor_agent_snapshot" && Array.isArray(raw.events)) { bluesunHandleAgentSnapshot(raw.events); return; }
            bluesunProcessPayload(raw);
        } catch (err) { bluesunErr("WS parse:", err, event.data); }
    };
    bluesunWS.onerror = (err) => bluesunErr("WS ошибка:", err);
    bluesunWS.onclose = (e) => {
        bluesunWarn(`WS закрыт code=${e.code} reason=${e.reason}`);
        bluesunWS = null;
        if (!bluesunMonitorRunning || bluesunWSReconnectTimer) return;
        bluesunWSReconnectTimer = setTimeout(() => {
            bluesunWSReconnectTimer = null;
            if (bluesunMonitorRunning) bluesunStartWS(deviceId);
        }, BLUESUN_RECONNECT_MS);
    };
}

/* ============================================================
 * FAIL / OFFLINE
 * ============================================================ */
function bluesunResetOfflineTimer() {
    if (bluesunOfflineTimer) clearTimeout(bluesunOfflineTimer);
    bluesunOfflineTimer = setTimeout(() => {
        bluesunWarn(`нет данных > ${BLUESUN_OFFLINE_DELAY/1000} сек`);
        if (bluesunFirstDataReceived) window.setOfflineState();
    }, BLUESUN_OFFLINE_DELAY);
}
function bluesunRegisterFail(message) {
    bluesunFails++;
    if (bluesunFails >= BLUESUN_FAIL_LIMIT) {
        window.setDeviceVisibility("ErrorIcon", "visible");
        window.setErrorText(message);
        window.setOfflineState();
    }
}
function bluesunResetFails() {
    bluesunFails = 0;
    window.setDeviceVisibility("ErrorIcon", "hidden");
}

/* ============================================================
 * ENTRY / EXIT
 * ============================================================ */
async function startMonitoringBlueSunCorBridge(objectData) {
    window.showLoading();
    bluesunLog("🚀 startMonitoringBlueSunCorBridge");
    bluesunMonitorRunning = true;
    bluesunFirstDataReceived = false;
    bluesunFails = 0;
    for (const k of BLUESUN_EXPECTED) delete bluesunSeenCounts[k];
    bluesunLastMissingCheck = 0;

    if (bluesunWS) { try { bluesunWS.close(); } catch (e) {} bluesunWS = null; }

    const corBridgeId = objectData?.cor_bridges?.[0];
    if (!corBridgeId) { bluesunErr("нет cor_bridges"); return; }

    let deviceId = null;
    try { deviceId = await window.resolveCORBridgeDeviceId(corBridgeId); }
    catch (e) { bluesunErr("resolveCORBridgeDeviceId:", e); return; }
    if (!deviceId) { bluesunErr("не удалось получить device_id"); return; }

    bluesunLog(`device_id=${deviceId}`);
    bluesunStartWS(deviceId);
}
function stopMonitoringBlueSunCorBridge() {
    bluesunLog("🛑 stopMonitoringBlueSunCorBridge");
    bluesunMonitorRunning = false;
    if (bluesunWSReconnectTimer) { clearTimeout(bluesunWSReconnectTimer); bluesunWSReconnectTimer = null; }
    if (bluesunOfflineTimer)     { clearTimeout(bluesunOfflineTimer);     bluesunOfflineTimer = null; }
    if (bluesunWS) { try { bluesunWS.close(); } catch (e) {} bluesunWS = null; }
}

window.startMonitoringBlueSunCorBridge = startMonitoringBlueSunCorBridge;
window.stopMonitoringBlueSunCorBridge  = stopMonitoringBlueSunCorBridge;


