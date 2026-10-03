



let rs485FailCount = 0;
let corFailCount = 0;
let isFirstDataReceived = false;

const FAIL_COR_BRIDGE = 3; 
const FAIL_RS_BUS = 4;
const FAIL_NAKK=9;
let offlineTimer = null;
const OFFLINE_DELAY = 5000; // 5 секунд
let axiomaWS = null;
 
async function startMonitoringAxiomaCorBridge(objectData) {
    showLoading();
    const INTERVAL = 1000;

    setDeviceVisibility("Generator", "hidden");

    switch (objectData.protocol) {

        case "modbus_over_tcp":
            while (true) {
                await new Promise(r => setTimeout(r, INTERVAL));
            }
            break;

        case "cor_bridge":
            console.log("🚀 Запуск COR-Bridge WS мониторинга");

            const corBridgeId = objectData.cor_bridges?.[0];

            if (!corBridgeId) {
                console.error("❌ У объекта нет cor_bridges");
                return;
            }

            const deviceId = await resolveCORBridgeDeviceId(corBridgeId);
            console.log("🔍 Полученный device_id:", deviceId);
            if (!deviceId) {
                console.error("❌ Не удалось получить device_id");
                return;
            }

            startAxiomaCORBridgeWS(deviceId);
        break;

        default:
            console.warn("Неизвестный протокол Axioma:", objectData.protocol);
    }
}


async function startMonitoringAxiomaModbusOverTcp(objectData) {
    // Implementation for Deye Modbus over TCP monitoring

    return;
}


function hexToAscii(hex) {
    if (!hex || typeof hex !== "string") return "";

    let result = "";
    for (let i = 0; i < hex.length; i += 2) {
        const byte = parseInt(hex.substr(i, 2), 16);
        if (!isNaN(byte)) {
            result += String.fromCharCode(byte);
        }
    }
    return result;
}

function crc16Axioma(hexStr) {
    const buf = [];
    for (let i = 0; i < hexStr.length; i += 2) {
        buf.push(parseInt(hexStr.substr(i, 2), 16));
    }

    let crc = 0x0000;
    for (let pos = 0; pos < buf.length; pos++) {
        crc ^= buf[pos] << 8;
        for (let i = 0; i < 8; i++) {
            if (crc & 0x8000) {
                crc = (crc << 1) ^ 0x1021;
            } else {
                crc <<= 1;
            }
            crc &= 0xFFFF;
        }
    }
    return crc;
}








function extractAxiomaWsPayload(raw) {
    const nested = raw?.data && typeof raw.data === "object" ? raw.data : raw;
    const cmd = nested?.cmd ?? raw?.cmd ?? nested?.command_name ?? raw?.command_name ?? null;
    const hex = nested?.hex_response ?? raw?.hex_response ?? nested?.hex_data ?? raw?.hex_data ?? null;
    return { cmd, hex };
}

function startAxiomaCORBridgeWS(deviceId) {
     console.log("🚀 Инициализация Axioma COR-Bridge WS", { deviceId });
           

    if (!deviceId) {
        console.error("❌ device_id не задан");
        return;
    }

    const wsUrl = buildAuthenticatedWebSocketUrl(
        `wss://dev.monitoring.cor-int.com/dev-modbus/responses?device_id=${encodeURIComponent(deviceId)}`
    );
    console.log("🌐 WS URL:", maskWebSocketUrlForLog(wsUrl));

    if (axiomaWS && axiomaWS.readyState === WebSocket.OPEN) {
        console.warn("⚠️ WS уже запущен");
        return;
    }

    axiomaWS = new WebSocket(wsUrl);

    axiomaWS.onopen = () =>{
        console.log("✅ Axioma COR-Bridge WS подключён");
        resetOfflineTimer();};
    axiomaWS.onmessage = (event) => {
        resetOfflineTimer();

        try {
            const raw = JSON.parse(event.data);

            if (raw?.type === "connection_established" || raw?.type === "subscription_changed" || raw?.type === "ping") {
                return;
            }

            if (raw?.type === "cor_agent_snapshot" && Array.isArray(raw.events)) {
                let snapshotUpdated = false;

                for (const snapshotEvent of raw.events) {
                    const { cmd: snapshotCmd, hex: snapshotHex } = extractAxiomaWsPayload(snapshotEvent);

                    if (!snapshotCmd || !snapshotHex || snapshotHex === "No response from RS485") {
                        continue;
                    }

                    const parsedSnapshot = parseAxiomaByCmd(snapshotCmd, snapshotHex);
                    if (!parsedSnapshot) {
                        continue;
                    }

                    window.lastData = { ...window.lastData, ...parsedSnapshot };
                    snapshotUpdated = true;
                }

                if (snapshotUpdated) {
                    console.log("📦 Применён кешированный snapshot Axioma:", window.lastData);
                    updateUIByData(window.lastData);
                    resetFails();
                }
                return;
            }

            const { cmd, hex } = extractAxiomaWsPayload(raw);

          // ==========================
        // ✅ Проверка CRC16 для протокола Axioma/PIP
        // ==========================
        if (hex && hex.length >= 6) { // минимум: данные + 2 байта CRC + CR
            // Убираем завершающий 0x0D (CR) если есть
            let hexWithoutCR = hex;
            if (hex.slice(-2).toUpperCase() === '0D') {
                hexWithoutCR = hex.slice(0, -2);
            }
            
            // Данные без CRC (последние 4 символа = 2 байта CRC)
            const dataWithoutCRC = hexWithoutCR.slice(0, -4);
            const receivedCrcHex = hexWithoutCR.slice(-4).toLowerCase();
            
            // Рассчитываем CRC (пробуем оба алгоритма, какой сработает)
            const calculatedCrc1 = crc16Axioma(dataWithoutCRC);
            
            // Преобразуем полученный CRC из hex в число
            const receivedCrc = parseInt(receivedCrcHex, 16);
            
            // Проверяем оба варианта
            const crcPassed = (calculatedCrc1 === receivedCrc);
            
            if (!crcPassed) {
                console.warn("❌ CRC не прошла проверку", {
                    cmd,
                    hex,
                    calculatedCrc1: calculatedCrc1.toString(16),
                    receivedCrc: receivedCrc.toString(16),
                    receivedCrcHex
                });
              
                // Можно продолжить обработку, но данные могут быть неверны
            } else {
             

                 console.log("✅ CRC проверка пройдена", { cmd, hex,  calculatedCrc1: calculatedCrc1.toString(16), receivedCrc: receivedCrc.toString(16), receivedCrcHex });
            }
        } else if (hex) {
            console.warn("⚠️ HEX слишком короткий — пропускаем проверку CRC", { hex });
        }

            if (!cmd || !hex) {
                if (raw?.data?.command_type === "device_online" || raw?.command_type === "device_online") {
                    return;
                }
                console.warn("⚠️ Пропущено WS-сообщение без PI30 payload", raw);
                return;
            }

            const asciiTest = hexToAscii(hex).trim();

            if (asciiTest.includes("NAK")) {
                console.warn("⚠️ Инвертор вернул NAK (команда отклонена):", asciiTest);
                registerFail("nakk", "Инвертор отклонил команду (NAK)");
                return; 
            }

              // ✅ Случай: RS485 не отвечает
            if (hex === "No response from RS485") {
                 console.warn("⚠️ Инвертор не отвечает:", asciiTest);
                registerFail("rs485", "Нет связи с инвертором (RS bus)");
                return;
            }

            const parsed = parseAxiomaByCmd(cmd, hex);

          

            if (!parsed) {
                console.warn("⚠️ Данные не распознаны");
                return;
            }

            window.lastData = { ...window.lastData, ...parsed };
            console.log("📊 lastData обновлён:", window.lastData);

            updateUIByData(window.lastData);
            resetFails();
        } catch (e) {
            console.error("❌ Ошибка обработки WS:", e, event.data);
        }
    };


    axiomaWS.onerror = (err) => console.error("❌ Axioma WS ошибка:", err);

    axiomaWS.onclose = (e) => {
        console.warn("🔌 Axioma WS закрыт", {
            code: e.code,
            reason: e.reason,
            wasClean: e.wasClean
        });

        axiomaWS = null;
        console.log("⏳ Переподключение через 3 секунды...");
        setTimeout(() => startAxiomaCORBridgeWS(deviceId), 3000);
    };
}




function stopAxiomaWS() {
    if (axiomaWS) {
        console.log("🛑 Остановка Axioma WS");
        axiomaWS.close();
        axiomaWS = null;
    }
}

function parseAxiomaByCmd(cmd, hexResponse) {

    const ascii = hexToAscii(hexResponse)
        .replace(/[()\r\n\x03\x19]/g, "")
        .trim();

    // ✅ Все команды QPGS, QPGS0, QPGS1... идут в один парсер
    if (cmd.startsWith("QPGS")) {
        return parseQPGSn(cmd, ascii);
    }

    switch (cmd) {
        case "QMOD":
            return parseQMOD(ascii);

        case "QPIGS":
            return parseQPIGS(hexResponse);

        case "QFLAG":
            return parseQFLAG(ascii);

        case "QPIWS":
            return parseQPIWS(ascii);    

        default:
            console.warn("❌ Неизвестная команда:", cmd);
            return null;
    }
}
/**
 * Парсер QFLAG
 */
function parseQFLAG(hexOrAscii) {
    if (!hexOrAscii) return null;

    // Если пришла hex-строка — конвертируем в ASCII
    let ascii;
    if (/^[0-9A-Fa-f]+$/.test(hexOrAscii.replace(/\s/g, ''))) {
        ascii = '';
        for (let i = 0; i < hexOrAscii.length; i += 2) {
            const hexByte = hexOrAscii.substr(i, 2);
            if (hexByte.trim() === '') continue;
            ascii += String.fromCharCode(parseInt(hexByte, 16));
        }
    } else {
        ascii = hexOrAscii;
    }

    // Расшифровка всех флагов QFLAG
    const flagsMap = {
        A: "silenceBuzzer",
        B: "overloadBypass",
        J: "powerSaving",
        K: "lcdEscape",
        U: "overloadRestart",
        V: "overTempRestart",
        X: "backlight",
        Y: "alarmOnPrimaryInterrupt",
        Z: "faultCodeRecord"
    };

    const result = {};
    let currentState = null;

    // Убираем скобки, пробелы и символ конца строки
    ascii = ascii.replace(/[()\s\r\n]/g, "").trim();

    for (let i = 0; i < ascii.length; i++) {
        const ch = ascii[i].toUpperCase();

        if (ch === "E") {
            currentState = true;  // все последующие буквы включены
        } 
        else if (ch === "D") {
            currentState = false; // все последующие буквы выключены
        }
        else if (flagsMap[ch] && currentState !== null) {
            result[flagsMap[ch]] = currentState;
        }
    }

    console.log("✅ QFLAG parsed:", result);
    return result;
}

function parseQPGSn(cmd, asciiResponse) {

    // cmd может быть "QPGS" или "QPGS0"
    let unitIndex = null;

    if (cmd.length === 5) {
        unitIndex = parseInt(cmd.replace("QPGS", ""), 10);
    }

    const clean = asciiResponse
        .replace(/[()\r\n]/g, "")
        .trim();

    const parts = clean.split(/\s+/);

    if (parts.length < 18) {
        console.warn("❌ Недостаточно полей QPGS:", parts.length, parts);
        return null;
    }

    const result = {
        unit: unitIndex, // null если просто QPGS

        parallelExist: parts[0] === "1",
        serialNumber: parts[1],
        workMode: parts[2],
        faultCode: parseInt(parts[3]),

        gridVoltage: parseFloat(parts[4]),
        gridFrequency: parseFloat(parts[5]),

        outputVoltage: parseFloat(parts[6]),
        outputFrequency: parseFloat(parts[7]),

        outputApparentPower: parseInt(parts[8]),
        outputActivePower: parseInt(parts[9]),
        loadPercent: parseInt(parts[10]),

        batteryVoltage: parseFloat(parts[11]),
        batteryChargeCurrent: parseInt(parts[12]),
        batterySOC: parseInt(parts[13]),

        pvVoltage: parseFloat(parts[14]),
        totalChargeCurrent: parseInt(parts[15]),

        totalOutputApparentPower: parseInt(parts[16]),
        totalOutputActivePower: parseInt(parts[17])
    };

    // Полный пакет (27 полей)
    if (parts.length >= 27) {

        result.totalLoadPercent = parseInt(parts[18]);
        result.inverterStatusBits = parts[19];

        result.outputMode = parseInt(parts[20]);
        result.chargerPriority = parseInt(parts[21]);

        result.maxChargerCurrent = parseInt(parts[22]);
        result.maxChargerRange = parseInt(parts[23]);
        result.maxACChargerCurrent = parseInt(parts[24]);

        result.pvChargeCurrent = parseInt(parts[25]);
        result.batteryDischargeCurrent = parseInt(parts[26]);
    }

    console.log(`✅ ${cmd} parsed (${parts.length} fields):`, result);

    return result;
}


function parseQMOD(asciiResponse) {

    if (!asciiResponse) {
        return null;
    }

    const clean = asciiResponse
        .replace(/[()\r\n\s]/g, "")
        .trim();

    if (!clean) {
        console.warn("❌ QMOD: пустой ответ");
        return null;
    }

    const modeCode = clean.charAt(0).toUpperCase();
 const modeMap = {                 //Режимы     
        P: "Включён",             // Power On
        S: "Ожидание",            // Standby
        L: "От сети",             // Line
        B: "От аккумулятора",     // Battery
        F: "Авария",               // Fault
        H: "Энергосбережение",    // Hibernate
        D: "Выключен"              // Shutdown
    };


    const inverterMode = modeMap[modeCode] || modeCode;

    const result = {
        inverterMode: inverterMode,
        inverterModeCode: modeCode
    };

    console.log("✅ QMOD parsed:", result);

    return result;
}


function parseQPIWS(asciiResponse) {

    if (!asciiResponse) return null;

    // ==========================
    // 1) Очистка ответа
    // ==========================
    const clean = asciiResponse
        .replace(/[()\r\n]/g, "")
        .trim();

    if (clean.length < 32) {
        console.warn("❌ QPIWS: недостаточно бит:", clean.length, clean);
        return null;
    }

    const bits = clean.substring(0, 32).split("");

    // ==========================
    // 2) Таблица битов
    // ==========================
    const warningsMap = [
        { bit: 0,  name: "reserved0", description: "Reserved" },

        { bit: 1,  name: "inverterFault", description: "Inverter fault" },
        { bit: 2,  name: "busOverFault", description: "Bus Over Fault" },
        { bit: 3,  name: "busUnderFault", description: "Bus Under Fault" },
        { bit: 4,  name: "busSoftFail", description: "Bus Soft Fail Fault" },

        { bit: 5,  name: "lineFail", description: "LINE_FAIL Warning" },
        { bit: 6,  name: "opvShort", description: "OPVShort Warning" },

        { bit: 7,  name: "inverterVoltageLow", description: "Inverter voltage too low" },
        { bit: 8,  name: "inverterVoltageHigh", description: "Inverter voltage too high" },

        { bit: 9,  name: "overTemperature", description: "Over temperature" },
        { bit: 10, name: "fanLocked", description: "Fan locked" },
        { bit: 11, name: "batteryVoltageHigh", description: "Battery voltage high" },
        { bit: 12, name: "batteryLowAlarm", description: "Battery low alarm" },
        { bit: 13, name: "reserved13", description: "Reserved" },
        { bit: 14, name: "batteryUnderShutdown", description: "Battery under shutdown" },
        { bit: 15, name: "reserved15", description: "Reserved" },

        { bit: 16, name: "overload", description: "Over load" },

        { bit: 17, name: "eepromFault", description: "Eeprom fault" },

        { bit: 18, name: "inverterOverCurrent", description: "Inverter Over Current Fault" },
        { bit: 19, name: "inverterSoftFail", description: "Inverter Soft Fail Fault" },
        { bit: 20, name: "selfTestFail", description: "Self Test Fail Fault" },

        { bit: 21, name: "opDcVoltageOver", description: "OP DC Voltage Over Fault" },
        { bit: 22, name: "batteryOpen", description: "Bat Open Fault" },
        { bit: 23, name: "currentSensorFail", description: "Current Sensor Fail Fault" },
        { bit: 24, name: "batteryShort", description: "Battery Short Fault" },

        { bit: 25, name: "powerLimit", description: "Power limit Warning" },
        { bit: 26, name: "pvVoltageHigh", description: "PV voltage high Warning" },

        { bit: 27, name: "mpptOverloadFault", description: "MPPT overload fault" },
        { bit: 28, name: "mpptOverloadWarning", description: "MPPT overload warning" },

        { bit: 29, name: "batteryTooLowToCharge", description: "Battery too low to charge" },

        { bit: 30, name: "reserved30", description: "Reserved" },
        { bit: 31, name: "reserved31", description: "Reserved" }
    ];

    // ==========================
    // 3) Результат
    // ==========================
    const result = {
        rawBits: clean,
        activeWarnings: [],
        activeFaults: []
    };

    const inverterFaultMain = bits[1] === "1";

    // ==========================
    // 4) Разбор битов
    // ==========================
    warningsMap.forEach(item => {

        // ❌ Reserved не показываем
        if (item.name.startsWith("reserved")) return;

        if (bits[item.bit] !== "1") return;

        let type = "warning";

        // Bits зависят от a1
        if ([9,10,11,16].includes(item.bit)) {
            type = inverterFaultMain ? "fault" : "warning";
        }

        // Всегда Fault
        if ([1,2,3,4,7,8,18,19,20,21,22,23,24,27].includes(item.bit)) {
            type = "fault";
        }

        const entry = {
            bit: item.bit,
            description: item.description,
            type
        };

        if (type === "fault") {
            result.activeFaults.push(entry);
        } else {
            result.activeWarnings.push(entry);
        }
    });

    console.log("✅ QPIWS parsed:", result);

    // ==========================
    // 5) UI отображение
    // ==========================

    // ❗ Fault важнее Warning
    if (result.activeFaults.length > 0) {

        setDeviceVisibility("FaultIcon", "visible");
        setErrorText("Ошибка: " + result.activeFaults[0].description);

        setDeviceVisibility("WarningIcon", "hidden");
    }

    else if (result.activeWarnings.length > 0) {

        setDeviceVisibility("WarningIcon", "visible");
        setWarningText("Предупреждение: " + result.activeWarnings[0].description);

        setDeviceVisibility("FaultIcon", "hidden");
    }

    else {
        // Всё чисто
        setDeviceVisibility("WarningIcon", "hidden");
        setDeviceVisibility("FaultIcon", "hidden");
    }

    return result;
}


/**
 * Парсер QPIGS
 */
function parseQPIGS(hexResponse) {
 
    const ascii = hexToAscii(hexResponse).trim();
    const max = window.deviceMaxPower || {};
    if (!ascii.startsWith("(")) {
        console.warn("❌ Не QPIGS:", ascii);
        return null;
    }

    const clean = ascii.replace(/[()]/g, "");
    const parts = clean.split(/\s+/);
   // console.log("🧩 QPIGS parts:", parts);

    if (parts.length < 17) {
        console.warn("❌ Недостаточно полей QPIGS:", parts.length, parts);
        return null;
    }

    const apparentPower = parseFloat(parts[4]); // VA
    const outputVoltage = parseFloat(parts[2]) || 1; // V, защита от 0
    const outputCurrent = apparentPower / outputVoltage; // A

    const result = {
        inputVoltage: parseFloat(parts[0]),
        inputFrequency: parseFloat(parts[1]),
        outputVoltage: outputVoltage,
        outputFrequency: parseFloat(parts[3]),
        outputApparentPower: apparentPower,
        outputActivePower: parseInt(parts[5]),
        loadPercent: parseInt(parts[6]),
        busVoltage: parseInt(parts[7]),
        batteryVoltage: parseFloat(parts[8]),
        batteryChargeCurrent: parseInt(parts[9]),
        batterySOC: parseInt(parts[10]),
        inverterTemp: parseInt(parts[11]),
        pvChargeCurrent: parseInt(parts[12]),
        pvVoltage: parseFloat(parts[13]),
        batteryVoltageSCC: parseFloat(parts[14]),
        batteryDischargeCurrent: parseInt(parts[15]),
        statusBits: parts[16],
        outputCurrent: outputCurrent
    };

    console.log("✅ QPIGS результат:", result);

        setIconStatus("Grid", "normal");
        setIconStatus("Battery", "normal");
        setIconStatus("Inverter", "normal");
        setIconStatus("Load", "normal");
        setIconStatus("Solar", "normal");
           if (!isFirstDataReceived) {
                console.log("✅ Первые данные получены → убираем загрузку");
                hideLoading();
            }

  // --- ☀️ Solar (PV панели) ---
    const pvVoltage = Number(result.pvVoltage) || 0;
    const pvCurrent = Number(result.pvChargeCurrent) || 0;

    // Мощность солнечного входа
    let solarPower = pvVoltage * pvCurrent;
    if (!isFinite(solarPower)) solarPower = 0;
    result.solarPower = solarPower;

 // --- АКБ ---
const chargeCurrent = Number(result.batteryChargeCurrent) || 0;
const dischargeCurrent = Number(result.batteryDischargeCurrent) || 0;

const batteryVoltage = Number(result.batteryVoltage) || 0;
const batteryCurrent = chargeCurrent > 0 ? -chargeCurrent : dischargeCurrent;
let inputPower = result.apparentPower || 0;

result.batteryCurrent = batteryCurrent;
let batteryTotalPower = 0;

if (chargeCurrent >0) {
    batteryTotalPower = -batteryVoltage * chargeCurrent;

    if(result.inputVoltage !== 0) {
        inputPower = apparentPower + Math.abs(batteryTotalPower);
    }


} else if (dischargeCurrent > 0) {
    batteryTotalPower = batteryVoltage * dischargeCurrent;

    if(result.inputVoltage !== 0) {
        inputPower = apparentPower - Math.abs(batteryTotalPower);
    }


}

if(chargeCurrent === 0 && dischargeCurrent ===0) {
    batteryTotalPower = 0;
    inputPower = apparentPower;
}

 if(result.inputVoltage == 0) {inputPower = 0;}
result.inputPower = inputPower;


// --- Input current ---
let inputCurrent = 0;
if (result.inputVoltage > 0 && isFinite(result.inputPower)) {
    inputCurrent = result.inputPower / result.inputVoltage;
}
result.inputCurrent = inputCurrent;


if (!isFinite(batteryTotalPower)) {
    batteryTotalPower = 0;
}


result.batteryTotalPower = batteryTotalPower;

////Передача в UI

updateBatteryFill(result.batterySOC);
if (result.batteryTotalPower != null) {
    updatePowerByName( "Battery", PowerToIndicator(result.batteryTotalPower,  max.Battery ) );
    batteryFlowLabel.textContent = formatPowerLabel(result.batteryTotalPower, "battery");
}


if ( result.outputActivePower != null) {
    updatePowerByName( "Load", PowerToIndicator(result.outputActivePower, max.Load));
    loadIndicatorLabel.textContent = formatPowerLabel(result.outputActivePower, "load");
}

if (result.inputPower != null) {
    updatePowerByName("Grid", PowerToIndicator(result.inputPower, max.Grid));
    networkFlowLabel.textContent = formatPowerLabel((result.inputPower), "grid");
}


if (result.solarPower != null) {
    updatePowerByName("Solar",PowerToIndicator(result.solarPower, max.Solar));
    solarPowerLabel.textContent = formatPowerLabel(result.solarPower, "solar");
}
    return result;
}


function updateAxiomaUI(data) {
    if (!data) return;

    const max = window.deviceMaxPower || {};

    // --- GRID ---
    if (data.inputPower != null) {
        updatePowerByName("Grid", PowerToIndicator(data.inputPower, max.Grid));
        if (typeof networkFlowLabel !== 'undefined') {
            networkFlowLabel.textContent = formatPowerLabel(data.inputPower, "grid");
        }
        setIconStatus("Grid", "normal");
    }

    // --- LOAD ---
    if (data.outputActivePower != null) {
        updatePowerByName("Load", PowerToIndicator(data.outputActivePower, max.Load));
        if (typeof loadIndicatorLabel !== 'undefined') {
            loadIndicatorLabel.textContent = formatPowerLabel(data.outputActivePower, "load");
        }
        setIconStatus("Load", "normal");
    }

    // --- SOLAR ---
    if (data.solarPower != null) {
        updatePowerByName("Solar", PowerToIndicator(data.solarPower, max.Solar));
        if (typeof solarPowerLabel !== 'undefined') {
            solarPowerLabel.textContent = formatPowerLabel(data.solarPower, "solar");
        }
        setIconStatus("Solar", "normal");
    }

    // --- BATTERY ---
    if (data.batteryTotalPower != null) {
        updatePowerByName("Battery", PowerToIndicator(data.batteryTotalPower, max.Battery));
        if (typeof batteryFlowLabel !== 'undefined') {
            batteryFlowLabel.textContent = formatPowerLabel(data.batteryTotalPower, "battery");
        }
        setIconStatus("Battery", "normal");
    }

    if (data.batterySOC != null) {
        updateBatteryFill(data.batterySOC);
    }

    // --- GLOBAL ---
    window.lastData = {
        ...window.lastData,
        ...data
    };

    updateUIByData(window.lastData);
    hideLoading();
    setDeviceVisibility("ErrorIcon", "hidden");
}


