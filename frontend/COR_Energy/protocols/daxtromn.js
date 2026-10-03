
// ============================================================
// Daxtromn COR-Bridge monitoring
// ============================================================

let daxtromnWS = null;

let daxtromnRs485FailCount = 0;
let daxtromnNakFailCount = 0;
let daxtromnCorFailCount = 0;
let daxtromnIsFirstDataReceived = false;

const DAXTROMN_FAIL_COR_BRIDGE = 5;
const DAXTROMN_FAIL_RS_BUS = 10;
const DAXTROMN_FAIL_NAK = 10;

let daxtromnOfflineTimer = null;
const DAXTROMN_OFFLINE_DELAY = 10000;


// ============================================================
// START MONITORING
// ============================================================

async function startMonitoringDaxtromnCorBridge(objectData) {

    showLoading();

    setDeviceVisibility("Generator", "hidden");

    switch (objectData.protocol) {

        case "cor_bridge": {

            console.log(
                "🚀 Запуск Daxtromn COR-Bridge WS мониторинга"
            );

            const corBridgeId =
                objectData.cor_bridges?.[0];

            if (!corBridgeId) {

                console.error(
                    "❌ У объекта нет cor_bridges"
                );

                return;
            }

            const deviceId =
                await resolveCORBridgeDeviceId(
                    corBridgeId
                );

            console.log(
                "🔍 Полученный device_id:",
                deviceId
            );

            if (!deviceId) {

                console.error(
                    "❌ Не удалось получить device_id"
                );

                return;
            }

            startDaxtromnCORBridgeWS(deviceId);

            break;
        }

        default:

            console.warn(
                "❌ Неизвестный протокол Daxtromn:",
                objectData.protocol
            );
    }
}


window.startMonitoringDaxtromnCorBridge =
    startMonitoringDaxtromnCorBridge;


// ============================================================
// HEX -> ASCII
// ============================================================

function daxtromnHexToAscii(hex) {

    if (!hex || typeof hex !== "string") {
        return "";
    }

    let result = "";

    for (
        let i = 0;
        i + 1 < hex.length;
        i += 2
    ) {

        const byte =
            parseInt(
                hex.substr(i, 2),
                16
            );

        if (!Number.isNaN(byte)) {

            result +=
                String.fromCharCode(byte);
        }
    }

    return result;
}


// ============================================================
// CRC16
//
// CRC-16/CCITT
//
// polynomial = 0x1021
// initial    = 0x0000
// ============================================================

function crc16Daxtromn(hexStr) {

    if (!hexStr || typeof hexStr !== "string") {
        return 0;
    }

    const buf = [];

    for (
        let i = 0;
        i + 1 < hexStr.length;
        i += 2
    ) {

        const byte =
            parseInt(
                hexStr.substr(i, 2),
                16
            );

        if (!Number.isNaN(byte)) {
            buf.push(byte);
        }
    }

    let crc = 0x0000;

    for (
        let pos = 0;
        pos < buf.length;
        pos++
    ) {

        crc ^= buf[pos] << 8;

        for (
            let i = 0;
            i < 8;
            i++
        ) {

            if (crc & 0x8000) {

                crc =
                    (crc << 1) ^ 0x1021;

            } else {

                crc <<= 1;
            }

            crc &= 0xFFFF;
        }
    }

    return crc;
}


// ============================================================
// Удаление CRC + CR
//
// DATA + CRC(2 bytes) + CR
// ============================================================

function removeDaxtromnFrame(hex) {

    if (!hex || typeof hex !== "string") {
        return null;
    }

    let frame =
        hex
            .replace(/\s+/g, "")
            .toUpperCase();

    if (frame.length < 6) {
        return null;
    }

    // --------------------------------------------------------
    // Удаляем CR
    // --------------------------------------------------------

    if (frame.endsWith("0D")) {

        frame =
            frame.slice(
                0,
                -2
            );
    }

    if (frame.length < 4) {
        return null;
    }

    // --------------------------------------------------------
    // Последние 2 байта = CRC
    // --------------------------------------------------------

    const receivedCrcHex =
        frame.slice(-4);

    const dataHex =
        frame.slice(0, -4);

    return {
        dataHex,
        receivedCrcHex
    };
}


// ============================================================
// SPECIAL RESPONSE DETECTION
// ============================================================

// ------------------------------------------------------------
// Нет ответа от инвертора
//
// COR-Bridge возвращает обычную строку:
//
// "No response from RS485"
//
// Это НЕ HEX и НЕ нужно передавать в CRC/parser.
// ------------------------------------------------------------

function isDaxtromnNoResponse(hex) {

    if (typeof hex !== "string") {
        return false;
    }

    return (
        hex
            .trim()
            .toLowerCase() ===
        "no response from rs485"
    );
}


// ------------------------------------------------------------
// NAK
//
// Реальный кадр:
//
// 28 4E 41 4B 73 73 0D
//
// ASCII:
//
// ( N A K s s \r
//
// где:
//
// 28       = '('
// 4E414B   = "NAK"
// 7373     = CRC
// 0D       = CR
//
// Поэтому проверяем только:
//
// 284E414B
//
// а не строку "NAKss" целиком.
// ------------------------------------------------------------

function isDaxtromnNAK(hex) {

    if (typeof hex !== "string") {
        return false;
    }

    const normalized =
        hex
            .replace(/\s+/g, "")
            .toUpperCase();

    return normalized.startsWith(
        "284E414B"
    );
}


// ============================================================
// CRC CHECK
// ============================================================

function checkDaxtromnCRC(hex) {

    // --------------------------------------------------------
    // Специальные ответы сюда вообще не должны попадать.
    // Но дополнительная защита не помешает.
    // --------------------------------------------------------

    if (
        !hex ||
        isDaxtromnNoResponse(hex) ||
        isDaxtromnNAK(hex)
    ) {
        return false;
    }

    const frame =
        removeDaxtromnFrame(hex);

    if (!frame) {

        console.warn(
            "⚠️ Daxtromn: некорректный frame",
            hex
        );

        return false;
    }

    const calculatedCrc =
        crc16Daxtromn(
            frame.dataHex
        );

    const receivedCrc =
        parseInt(
            frame.receivedCrcHex,
            16
        );

    const passed =
        calculatedCrc === receivedCrc;

    console.log(
        passed
            ? "✅ Daxtromn CRC OK"
            : "❌ Daxtromn CRC ERROR",
        {
            hex,

            calculatedCrc:
                calculatedCrc
                    .toString(16)
                    .padStart(4, "0"),

            receivedCrc:
                receivedCrc
                    .toString(16)
                    .padStart(4, "0"),

            receivedCrcHex:
                frame.receivedCrcHex
        }
    );

    return passed;
}


// ============================================================
// Получение ASCII DATA
// без CRC и без CR
// ============================================================

function daxtromnGetAsciiData(hex) {

    const frame =
        removeDaxtromnFrame(hex);

    if (!frame) {
        return "";
    }

    return daxtromnHexToAscii(
        frame.dataHex
    );
}


// ============================================================
// WS PAYLOAD
// ============================================================

function extractDaxtromnWsPayload(raw) {

    const nested =
        raw?.data &&
        typeof raw.data === "object"
            ? raw.data
            : raw;

    const cmd =
        nested?.cmd ??
        raw?.cmd ??
        nested?.command_name ??
        raw?.command_name ??
        null;

    const hex =
        nested?.hex_response ??
        raw?.hex_response ??
        nested?.hex_data ??
        raw?.hex_data ??
        null;

    return {
        cmd,
        hex
    };
}


// ============================================================
// Daxtromn COR-Bridge WS
// ============================================================

function startDaxtromnCORBridgeWS(deviceId) {

    console.log(
        "🚀 Инициализация Daxtromn COR-Bridge WS",
        { deviceId }
    );

    if (!deviceId) {

        console.error(
            "❌ Daxtromn: device_id не задан"
        );

        return;
    }


    const wsUrl =
        buildAuthenticatedWebSocketUrl(
            `wss://dev.monitoring.cor-int.com/dev-modbus/responses?device_id=${encodeURIComponent(deviceId)}`
        );


    console.log(
        "🌐 Daxtromn WS URL:",
        maskWebSocketUrlForLog(wsUrl)
    );


    if (
        daxtromnWS &&
        daxtromnWS.readyState ===
        WebSocket.OPEN
    ) {

        console.warn(
            "⚠️ Daxtromn WS уже запущен"
        );

        return;
    }


    daxtromnWS =
        new WebSocket(wsUrl);


    // ========================================================
    // OPEN
    // ========================================================

    daxtromnWS.onopen = () => {

        console.log( "✅ Daxtromn COR-Bridge WS подключён");
        resetDaxtromnOfflineTimer();
    };


    // ========================================================
    // MESSAGE
    // ========================================================

    daxtromnWS.onmessage = (event) => {

        try {

            const raw =
                JSON.parse(event.data);


            // ------------------------------------------------
            // Служебные сообщения COR-Bridge
            // ------------------------------------------------

            if (
                raw?.type ===
                    "connection_established" ||

                raw?.type ===
                    "subscription_changed" ||

                raw?.type ===
                    "ping"
            ) {

                return;
            }


            // =================================================
            // SNAPSHOT
            // =================================================

            if (
                raw?.type ===
                    "cor_agent_snapshot" &&

                Array.isArray(raw.events)
            ) {

                let snapshotUpdated = false;


                for (
                    const snapshotEvent
                    of raw.events
                ) {

                    const {
                        cmd,
                        hex
                    } =
                        extractDaxtromnWsPayload(
                            snapshotEvent
                        );


                    // ------------------------------------------------
                    // NO RESPONSE
                    // ------------------------------------------------

                    if (
                        isDaxtromnNoResponse(hex)
                    ) {

                        console.warn(
                            "⚠️ Daxtromn snapshot: нет ответа по RS485"
                        );

                        continue;
                    }


                    // ------------------------------------------------
                    // NAK
                    // ------------------------------------------------

                    if (
                        isDaxtromnNAK(hex)
                    ) {

                        console.warn(
                            "⚠️ Daxtromn snapshot: NAK",
                            {
                                cmd,
                                hex
                            }
                        );

                        continue;
                    }


                    // ------------------------------------------------
                    // Нет payload
                    // ------------------------------------------------

                    if (!cmd || !hex) {
                        continue;
                    }


                    // ------------------------------------------------
                    // CRC
                    // ------------------------------------------------

                    if (
                        !checkDaxtromnCRC(hex)
                    ) {

                        continue;
                    }


                    // ------------------------------------------------
                    // PARSER
                    // ------------------------------------------------

                    const parsed =
                        parseDaxtromnByCmd(
                            cmd,
                            hex
                        );


                    if (!parsed) {
                        continue;
                    }


                    window.lastData = {
                        ...window.lastData,
                        ...parsed
                    };


                    snapshotUpdated = true;
                }


                if (snapshotUpdated) {

                    console.log(
                        "📦 Применён кешированный snapshot Daxtromn:",
                        window.lastData
                    );


                    updateUIByData(
                        window.lastData
                    );


                    resetDaxtromnFails();

                    resetDaxtromnOfflineTimer();
                }


                return;
            }


            // =================================================
            // ОБЫЧНЫЙ PAYLOAD
            // =================================================

            const {
                cmd,
                hex
            } =
                extractDaxtromnWsPayload(raw);


            // =================================================
            // 1. NO RESPONSE
            //
            // ОБЯЗАТЕЛЬНО ДО RAW DEBUG
            // =================================================

            if (
                isDaxtromnNoResponse(hex)
            ) {

                console.warn(
                    "⚠️ Daxtromn: инвертор не отвечает по RS485"
                );


                registerDaxtromnFail(
                    "rs485",
                    "Нет связи с инвертором (RS bus)"
                );


                return;
            }


            // =================================================
            // 2. NAK
            //
            // ОБЯЗАТЕЛЬНО ДО RAW DEBUG
            // И ДО CRC
            // =================================================

            if (
                isDaxtromnNAK(hex)
            ) {

                console.warn(
                    "⚠️ Daxtromn: инвертор вернул NAK",
                    {
                        cmd,
                        hex
                    }
                );


                registerDaxtromnFail(
                    "nak",
                    "Инвертор отклонил команду (NAK)"
                );


                return;
            }


            // =================================================
            // 3. НЕТ PAYLOAD
            // =================================================

            if (!cmd || !hex) {

                if (
                    raw?.data?.command_type ===
                        "device_online" ||

                    raw?.command_type ===
                        "device_online"
                ) {

                    return;
                }


                console.warn(
                    "⚠️ Daxtromn: WS сообщение без payload",
                    raw
                );

                return;
            }


            // =================================================
            // 4. Обычный ответ
            //
            // Только здесь разрешаем подробный RAW DEBUG
            // =================================================

            debugDaxtromnRawFrame(
                cmd,
                hex
            );


            // =================================================
            // 5. CRC
            // =================================================

            if (
                !checkDaxtromnCRC(hex)
            ) {

                console.warn(
                    "❌ Daxtromn: CRC ошибка",
                    {
                        cmd,
                        hex
                    }
                );

                return;
            }


            // =================================================
            // 6. ASCII
            // =================================================

            const ascii =
                daxtromnGetAsciiData(hex)
                    .replace(
                        /[\r\n\x03\x19]/g,
                        ""
                    )
                    .trim();


            // =================================================
            // 7. PARSER
            // =================================================

            const parsed =
                parseDaxtromnByCmd(
                    cmd,
                    hex
                );


            if (!parsed) {

                console.warn(
                    "⚠️ Daxtromn: данные не распознаны",
                    {
                        cmd,
                        ascii
                    }
                );

                return;
            }


            // =================================================
            // 8. DATA OK
            // =================================================

            window.lastData = {
                ...window.lastData,
                ...parsed
            };


            console.log(
                "📊 Daxtromn lastData:",
                window.lastData
            );


            updateUIByData(
                window.lastData
            );


            resetDaxtromnFails();

            resetDaxtromnOfflineTimer();


        } catch (e) {

            console.error(
                "❌ Ошибка обработки Daxtromn WS:",
                e,
                event.data
            );
        }
    };


    // ========================================================
    // ERROR
    // ========================================================

    daxtromnWS.onerror = (err) => {

        console.error(
            "❌ Daxtromn WS ошибка:",
            err
        );
    };


    // ========================================================
    // CLOSE
    // ========================================================

    daxtromnWS.onclose = (e) => {

        console.warn(
            "🔌 Daxtromn WS закрыт",
            {
                code: e.code,
                reason: e.reason,
                wasClean: e.wasClean
            }
        );


        daxtromnWS = null;


        console.log(
            "⏳ Daxtromn: переподключение через 3 секунды..."
        );


        setTimeout(
            () =>
                startDaxtromnCORBridgeWS(
                    deviceId
                ),
            3000
        );
    };
}


// ============================================================
// STOP WS
// ============================================================

function stopDaxtromnWS() {

    if (daxtromnWS) {

        console.log(
            "🛑 Остановка Daxtromn WS"
        );

        daxtromnWS.close();

        daxtromnWS = null;
    }
}


// ============================================================
// COMMAND DISPATCHER
// ============================================================

function parseDaxtromnByCmd(
    cmd,
    hexResponse
) {

    if (!cmd || !hexResponse) {
        return null;
    }


    const ascii =
        daxtromnGetAsciiData(
            hexResponse
        )
        .replace(
            /[\r\n\x03\x19]/g,
            ""
        )
        .trim();


    console.log(
        "🔎 Daxtromn command:",
        cmd,
        "ASCII:",
        ascii
    );


    switch (cmd) {


        case "QMOD":
            return parseDaxtromnQMOD(ascii);

        case "QPIGS":
            return parseDaxtromnQPIGS(ascii);

        case "QFLAG":
            return parseDaxtromnQFLAG(ascii);

        case "QPIWS":
            return parseDaxtromnQPIWS(ascii);

        case "QPIRI":  
            return parseDaxtromnQPIRI(ascii);    

        default:

            if (
                cmd.startsWith("QPGS")
            ) {

                return parseDaxtromnQPGSn(
                    cmd,
                    ascii
                );
            }


            console.warn(
                "❌ Daxtromn: неизвестная команда:",
                cmd
            );

            return null;
    }
}


// ============================================================
// QPIGS
// ============================================================

// ============================================================
// Daxtromn QPIGS
//
// Формат AGH-10.2KW-PLUS:
//
// (234.8 49.9 234.8 49.9 0234 0170 002 453 52.80
//  000 100 0035 00.0 000.0 00.00 00000 00010101
//  00 00 00000 110
//
// Поля:
//  0  AC input voltage
//  1  AC input frequency
//  2  AC output voltage
//  3  AC output frequency
//  4  AC output apparent power
//  5  AC output active power
//  6  Load percent
//  7  DC bus voltage
//  8  Battery voltage
//  9  Battery charging current
// 10  Battery SOC
// 11  Inverter temperature
// 12  PV charging current
// 13  PV voltage
// 14  Battery voltage from SCC
// 15  Battery discharge current
// 16  Status bits
// 17  additional status
// 18  additional status
// 19  additional value
// 20  final status
// ============================================================



function parseDaxtromnQMOD(asciiResponse) {

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

// ============================================================
// Daxtromn QPIRI — Device Rating Information
//
// Формат AGH-10.2KW-PLUS:
// ( BBB.B CC.C DDD.D EE.E FF.F HHHH IIII JJ.J KK.K JJ.J KK.K LL.L
//   O PP Q0 O P Q R SS T U VV.V W X <CRC><cr>
//
// Возвращает те же ключи, что и Axioma-версия,
// чтобы настройки (data-source) заполнялись единообразно.
// ============================================================
function parseDaxtromnQPIRI(asciiResponse) {

    if (!asciiResponse) return null;

    const clean = asciiResponse
        .replace(/[()\r\n]/g, "")
        .trim();

    const parts = clean.split(/\s+/);

    if (parts.length < 25) {
        console.warn("❌ Daxtromn QPIRI: недостаточно полей:", parts.length, parts);
        return null;
    }

    const num = (v, fb = 0) => {
        const n = parseFloat(v);
        return Number.isFinite(n) ? n : fb;
    };
    const int = (v, fb = 0) => {
        const n = parseInt(v, 10);
        return Number.isFinite(n) ? n : fb;
    };

    const batteryType       = int(parts[12]);
    const inputVoltageRange = int(parts[15]);
    const outputPriority    = int(parts[16]);
    const chargerPriority   = int(parts[17]);
    const topology          = int(parts[20]);
    const machineTypeRaw    = String(parts[19] ?? "").padStart(2, "0");  // '01'  → '01'
    const outputModeRaw     = String(parts[21] ?? "").padStart(2, "0");  // '0'   → '00'
    const pvOkCond          = int(parts[23]);
    const pvPowerBalance    = int(parts[24]);

    const result = {

        // Сеть
        gridRatingVoltage:        num(parts[0]),
        gridRatingCurrent:        num(parts[1]),

        // AC output rating
        acOutputRatingVoltage:    num(parts[2]),
        acOutputRatingFrequency:  num(parts[3]),
        acOutputRatingCurrent:    num(parts[4]),
        acOutputRatingApparentPower: int(parts[5]),
        acOutputRatingActivePower:   int(parts[6]),

        // Батарея
        batteryRatingVoltage:     num(parts[7]),
        batteryRechargeVoltage:   num(parts[8]),
        batteryUnderVoltage:      num(parts[9]),
        batteryBulkVoltage:       num(parts[10]),
        batteryFloatVoltage:      num(parts[11]),
        batteryType:              batteryType,
        batteryTypeLabel:         ["AGM", "Flooded", "User"][batteryType] || "Unknown",

        // Токи зарядки
        maxAcChargingCurrent:     int(parts[13]),
        maxChargingCurrent:       int(parts[14]),

        // Конфигурация
        inputVoltageRange:        inputVoltageRange,
        inputVoltageRangeLabel:   inputVoltageRange === 0 ? "Appliance" : "UPS",

        outputSourcePriority:     outputPriority,
        outputSourcePriorityLabel:
            ["Utility first", "Solar first", "SBU first"][outputPriority] || "Unknown",

        chargerSourcePriority:    chargerPriority,
        chargerSourcePriorityLabel:
            ["Utility first", "Solar first", "Solar + Utility", "Only solar"][chargerPriority]
            || "Unknown",

        parallelMaxNum:           int(parts[18]),

        // Тип машины / топология / режим
        machineType:              machineTypeRaw,
        machineTypeLabel:
            ({ "00": "Grid tie", "01": "Off Grid", "10": "Hybrid" })[machineTypeRaw]
            || "Unknown",

        topology:                 topology,
        topologyLabel:            topology === 0 ? "Transformerless" : "Transformer",

        outputMode:               outputModeRaw,
        outputModeLabel:
            ({
                "00": "Single machine",
                "01": "Parallel",
                "02": "Phase 1 of 3",
                "03": "Phase 2 of 3",
                "04": "Phase 3 of 3"
            })[outputModeRaw] || "Unknown",

        // Разряд / PV
        batteryReDischargeVoltage: num(parts[22]),

        pvOkCondition:            pvOkCond,
        pvOkConditionLabel:       pvOkCond === 0
            ? "Достаточно одной единицы с PV"
            : "Только все единицы с PV",

        pvPowerBalance:           pvPowerBalance,
        pvPowerBalanceLabel:      pvPowerBalance === 0
            ? "Макс. ток зарядки"
            : "Макс. мощность = зарядка + нагрузка",

        // RAW
        qpiriRaw:    clean,
        qpiriFields: parts
    };

    console.log("✅ QPIRI (Daxtromn) parsed:", result);
    return result;
}

function parseDaxtromnQPIGS(asciiResponse) {

    if (!asciiResponse) {
        return null;
    }

    const max = window.deviceMaxPower || {};

    // --------------------------------------------------------
    // Очистка
    // --------------------------------------------------------

    let clean = asciiResponse
        .replace(/[()\r\n]/g, "")
        .trim();

    const parts = clean.split(/\s+/);

    console.log("🧩 Daxtromn QPIGS fields:",parts);

    if (parts.length < 17) {
        console.warn( "❌ Daxtromn QPIGS: недостаточно полей",parts.length, parts);
        return null;
    }

    // --------------------------------------------------------
    // Безопасное преобразование
    // --------------------------------------------------------

    const num = (value, fallback = 0) => {

        const n = parseFloat(value);

        return Number.isFinite(n)
            ? n
            : fallback;
    };

    // --------------------------------------------------------
    // Основные QPIGS значения
    // --------------------------------------------------------

    const inputVoltage =num(parts[0]);
    const inputFrequency = num(parts[1]);
    const outputVoltage = num(parts[2]);
    const outputFrequency =num(parts[3]);
    const apparentPower =num(parts[4]);
    const activePower = num(parts[5]);
    const loadPercent = num(parts[6]);
    const busVoltage = num(parts[7]);
    const batteryVoltage = num(parts[8]);
    const batteryChargeCurrent = num(parts[9]);
    const batterySOC = num(parts[10]);
    const inverterTemp = num(parts[11]);
    const pvChargeCurrent = num(parts[12]);
    const pvVoltage = num(parts[13]);
    const batteryVoltageSCC = num(parts[14]);
    const batteryDischargeCurrent = num(parts[15]);
    const statusBits = parts[16] || "";

    // --------------------------------------------------------
    // Output current
    //
    // Как и в Axioma:
    //
    // I = S / U
    //
    // Но защищаемся от U = 0.
    // --------------------------------------------------------

    let outputCurrent = 0;

    if (outputVoltage > 0) {
        outputCurrent = apparentPower / outputVoltage;
    }

    // --------------------------------------------------------
    // Создаём нормализованный результат
    // --------------------------------------------------------

    const result = {

        // RAW
        daxtromnRaw: clean,
        daxtromnFields: parts,

        // GRID
        inputVoltage,
        inputFrequency,

        // OUTPUT / LOAD
        outputVoltage,
        outputFrequency,
        outputApparentPower: apparentPower,
        outputActivePower: activePower,
        outputCurrent,
        loadPercent,

        // DC BUS
        busVoltage,

        // BATTERY
        batteryVoltage,
        batteryChargeCurrent,
        batteryDischargeCurrent,
        batterySOC,
        batteryVoltageSCC,

        // INVERTER
        inverterTemp,

        // SOLAR
        pvChargeCurrent,
        pvVoltage,

        // STATUS
        statusBits,

        // Дополнительные поля AGH
        status17: parts[17] || null,
        status18: parts[18] || null,
        status19: parts[19] || null,
        status20: parts[20] || null
    };

    // ========================================================
    // SOLAR POWER
    // ========================================================

    let solarPower = 0;

    if (
        pvVoltage > 0 &&
        pvChargeCurrent > 0
    ) {

        solarPower =
            pvVoltage *
            pvChargeCurrent;
    }

    if (!Number.isFinite(solarPower)) {
        solarPower = 0;
    }

    result.solarPower = solarPower;

    // ========================================================
    // BATTERY POWER
    //
    // Заряд:
    //   отрицательная мощность
    //
    // Разряд:
    //   положительная мощность
    //
    // Это соответствует логике Axioma.
    // ========================================================

    let batteryCurrent = 0;
    let batteryTotalPower = 0;

    if (batteryChargeCurrent > 0) {

        batteryCurrent =
            -batteryChargeCurrent;

        batteryTotalPower =
            -batteryVoltage *
            batteryChargeCurrent;

    }
    else if (batteryDischargeCurrent > 0) {

        batteryCurrent =
            batteryDischargeCurrent;

        batteryTotalPower =
            batteryVoltage *
            batteryDischargeCurrent;
    }

    result.batteryCurrent =
        batteryCurrent;

    if (!Number.isFinite(batteryTotalPower)) {
        batteryTotalPower = 0;
    }

    result.batteryTotalPower =
        batteryTotalPower;

    // ========================================================
    // GRID POWER
    //
    // Здесь сохраняем ту же логику, что и в Axioma.
    //
    // При наличии батарейного потока:
    //
    // заряд:
    //   Grid ≈ Load + Battery charge
    //
    // разряд:
    //   Grid ≈ Load - Battery discharge
    //
    // Но только когда сеть действительно присутствует.
    // ========================================================

    let inputPower = apparentPower;

    if (batteryChargeCurrent > 0) {

        inputPower =
            apparentPower +
            Math.abs(batteryTotalPower);

    }
    else if (batteryDischargeCurrent > 0) {

        inputPower =
            apparentPower -
            Math.abs(batteryTotalPower);
    }

    // Если сети нет — Grid должен быть 0.
    if (inputVoltage <= 0) {

        inputPower = 0;
    }

    if (!Number.isFinite(inputPower)) {

        inputPower = 0;
    }

    result.inputPower =
        inputPower;

    // ========================================================
    // GRID CURRENT
    // ========================================================

    let inputCurrent = 0;

    if (
        inputVoltage > 0 &&
        Number.isFinite(inputPower)
    ) {

        inputCurrent =
            inputPower /
            inputVoltage;
    }

    result.inputCurrent =
        inputCurrent;

    // ========================================================
    // DEBUG
    // ========================================================

    console.log( "✅ Daxtromn QPIGS interpreted:",result);

    // ========================================================
    // ICON STATUS
    // ========================================================

    setIconStatus(
        "Grid",
        "normal"
    );

    setIconStatus(
        "Battery",
        "normal"
    );

    setIconStatus(
        "Inverter",
        "normal"
    );

    setIconStatus(
        "Load",
        "normal"
    );

    setIconStatus(
        "Solar",
        "normal"
    );

    // ========================================================
    // FIRST DATA
    // ========================================================

    if (!daxtromnIsFirstDataReceived) {

        daxtromnIsFirstDataReceived = true;

        console.log(
            "✅ Daxtromn: первые данные получены → убираем loading"
        );

        hideLoading();
    }

    // ========================================================
    // UI — BATTERY
    // ========================================================

    if (
        result.batterySOC != null
    ) {

        updateBatteryFill( result.batterySOC);
    }

    if (
        result.batteryTotalPower != null
    ) {

        updatePowerByName(
            "Battery",
            PowerToIndicator(
                result.batteryTotalPower,
                max.Battery
            )
        );

        if (
            typeof batteryFlowLabel !==
            "undefined"
        ) {

            batteryFlowLabel.textContent =
                formatPowerLabel(
                    result.batteryTotalPower,
                    "battery"
                );
        }
    }

    // ========================================================
    // UI — LOAD
    // ========================================================

    if (
        result.outputActivePower != null
    ) {

        updatePowerByName( "Load", PowerToIndicator(result.outputActivePower, max.Load));

        if (
            typeof loadIndicatorLabel !== "undefined"
        ) {

            loadIndicatorLabel.textContent = formatPowerLabel( result.outputActivePower, "load");
        }
    }

    // ========================================================
    // UI — GRID
    // ========================================================

    if (
        result.inputPower != null
    ) {

        updatePowerByName(
            "Grid",
            PowerToIndicator(
                result.inputPower,
                max.Grid
            )
        );

        if (
            typeof networkFlowLabel !==
            "undefined"
        ) {

            networkFlowLabel.textContent =
                formatPowerLabel(
                    result.inputPower,
                    "grid"
                );
        }
    }

    // ========================================================
    // UI — SOLAR
    // ========================================================

    if (
        result.solarPower != null
    ) {

        updatePowerByName("Solar", PowerToIndicator( result.solarPower, max.Solar ));

        if (
            typeof solarPowerLabel !==
            "undefined"
        ) {
            solarPowerLabel.textContent = formatPowerLabel(result.solarPower,"solar");
        }
    }

    return result;
}


// ============================================================
// QFLAG
// ============================================================

function parseDaxtromnQFLAG(
    asciiResponse
) {

    if (!asciiResponse) {
        return null;
    }


    const clean =
        asciiResponse
            .replace(
                /[()\s\r\n]/g,
                ""
            )
            .trim();


    console.log(
        "🔎 Daxtromn QFLAG:",
        clean
    );


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


    for (
        let i = 0;
        i < clean.length;
        i++
    ) {

        const ch =
            clean[i].toUpperCase();


        if (ch === "E") {

            currentState = true;

        } else if (ch === "D") {

            currentState = false;

        } else if (
            flagsMap[ch] &&
            currentState !== null
        ) {

            result[
                flagsMap[ch]
            ] = currentState;
        }
    }


    console.log( "✅ Daxtromn QFLAG:", result);
    return result;
}


// ============================================================
// QPIWS
// ============================================================

function parseDaxtromnQPIWS(
    asciiResponse
) {

    if (!asciiResponse) {
        return null;
    }


    const clean =
        asciiResponse
            .replace(
                /[()\s\r\n]/g,
                ""
            )
            .trim();


    if (clean.length < 32) {
        console.warn( "❌ Daxtromn QPIWS: недостаточно бит", clean);
        return null;
    }


    const bits =
        clean
            .substring(0, 32)
            .split("");


    const result = {

        rawBits:
            clean,

        activeWarnings:
            [],

        activeFaults:
            []
    };


    const warningsMap = [

        {
            bit: 1,
            name: "inverterFault",
            description: "Inverter fault"
        },

        {
            bit: 2,
            name: "busOverFault",
            description: "Bus Over Fault"
        },

        {
            bit: 3,
            name: "busUnderFault",
            description: "Bus Under Fault"
        },

        {
            bit: 4,
            name: "busSoftFail",
            description: "Bus Soft Fail Fault"
        },

        {
            bit: 5,
            name: "lineFail",
            description: "LINE_FAIL Warning"
        },

        {
            bit: 6,
            name: "opvShort",
            description: "OPVShort Warning"
        },

        {
            bit: 7,
            name: "inverterVoltageLow",
            description: "Inverter voltage too low"
        },

        {
            bit: 8,
            name: "inverterVoltageHigh",
            description: "Inverter voltage too high"
        },

        {
            bit: 9,
            name: "overTemperature",
            description: "Over temperature"
        },

        {
            bit: 10,
            name: "fanLocked",
            description: "Fan locked"
        },

        {
            bit: 11,
            name: "batteryVoltageHigh",
            description: "Battery voltage high"
        },

        {
            bit: 12,
            name: "batteryLowAlarm",
            description: "Battery low alarm"
        },

        {
            bit: 14,
            name: "batteryUnderShutdown",
            description: "Battery under shutdown"
        },

        {
            bit: 16,
            name: "overload",
            description: "Over load"
        },

        {
            bit: 17,
            name: "eepromFault",
            description: "Eeprom fault"
        },

        {
            bit: 18,
            name: "inverterOverCurrent",
            description: "Inverter Over Current Fault"
        },

        {
            bit: 19,
            name: "inverterSoftFail",
            description: "Inverter Soft Fail Fault"
        },

        {
            bit: 20,
            name: "selfTestFail",
            description: "Self Test Fail Fault"
        },

        {
            bit: 21,
            name: "opDcVoltageOver",
            description: "OP DC Voltage Over Fault"
        },

        {
            bit: 22,
            name: "batteryOpen",
            description: "Bat Open Fault"
        },

        {
            bit: 23,
            name: "currentSensorFail",
            description: "Current Sensor Fail Fault"
        },

        {
            bit: 24,
            name: "batteryShort",
            description: "Battery Short Fault"
        },

        {
            bit: 25,
            name: "powerLimit",
            description: "Power limit Warning"
        },

        {
            bit: 26,
            name: "pvVoltageHigh",
            description: "PV voltage high Warning"
        },

        {
            bit: 27,
            name: "mpptOverloadFault",
            description: "MPPT overload fault"
        },

        {
            bit: 28,
            name: "mpptOverloadWarning",
            description: "MPPT overload warning"
        },

        {
            bit: 29,
            name: "batteryTooLowToCharge",
            description: "Battery too low to charge"
        }
    ];


    const inverterFaultMain =
        bits[1] === "1";


    warningsMap.forEach(item => {

        if (
            bits[item.bit] !== "1"
        ) {
            return;
        }


        let type =
            "warning";


        if (
            [9, 10, 11, 16]
                .includes(item.bit)
        ) {

            type =
                inverterFaultMain
                    ? "fault"
                    : "warning";
        }


        if (
            [
                1, 2, 3, 4,
                7, 8,
                18, 19, 20,
                21, 22, 23, 24,
                27
            ].includes(item.bit)
        ) {

            type =
                "fault";
        }


        const entry = {

            bit:
                item.bit,

            description:
                item.description,

            type
        };


        if (
            type === "fault"
        ) {

            result.activeFaults.push(entry);

        } else {
            result.activeWarnings.push(entry);
        }
    });
    console.log("✅ Daxtromn QPIWS:", result);
    return result;
}


// ============================================================
// QPGS
// ============================================================

function parseDaxtromnQPGSn(
    cmd,
    asciiResponse
) {

    if (!asciiResponse) {
        return null;
    }


    const clean =
        asciiResponse
            .replace(
                /[()\r\n]/g,
                ""
            )
            .trim();


    const parts =
        clean.split(/\s+/);


    if (parts.length < 18) {

        console.warn(
            "❌ Daxtromn QPGS: недостаточно полей",
            parts.length,
            parts
        );

        return null;
    }


    const unitIndex =
        cmd.length === 5
            ? parseInt(
                cmd.replace(
                    "QPGS",
                    ""
                ),
                10
            )
            : null;


    const result = {

        unit:
            unitIndex,

        daxtromnRaw:
            clean,

        daxtromnFields:
            parts
    };


    console.log(
        `✅ Daxtromn ${cmd} parsed:`,
        result
    );


    return result;
}


// ============================================================
// FAIL HANDLING
// ============================================================

function registerDaxtromnFail(
    type,
    message
) {

    console.warn(
        `⚠️ Daxtromn FAIL [${type}]:`,
        message
    );


    // ========================================================
    // Физическая ошибка RS485 / timeout
    // ========================================================

    if (
        type === "rs485"
    ) {

        daxtromnRs485FailCount++;


        if (
            daxtromnRs485FailCount >=
            DAXTROMN_FAIL_RS_BUS
        ) {

            setDeviceVisibility(
                "ErrorIcon",
                "visible"
            );
        }
    }


    // ========================================================
    // NAK
    //
    // NAK НЕ означает потерю RS485.
    //
    // Инвертор получил команду и ответил.
    // Просто команда была отклонена.
    // ========================================================

    if (
        type === "nak"
    ) {

        daxtromnNakFailCount++;


        if (
            daxtromnNakFailCount >=
            DAXTROMN_FAIL_NAK
        ) {

            setDeviceVisibility(
                "ErrorIcon",
                "visible"
            );
        }
    }


    // ========================================================
    // COR-Bridge
    // ========================================================

    if (
        type === "cor"
    ) {

        daxtromnCorFailCount++;


        if (
            daxtromnCorFailCount >=
            DAXTROMN_FAIL_COR_BRIDGE
        ) {

            setDeviceVisibility(
                "ErrorIcon",
                "visible"
            );
        }
    }
}


// ============================================================
// RESET FAILS
// ============================================================

function resetDaxtromnFails() {

    daxtromnRs485FailCount = 0;

    daxtromnNakFailCount = 0;

    daxtromnCorFailCount = 0;


    setDeviceVisibility(
        "ErrorIcon",
        "hidden"
    );
}


// ============================================================
// OFFLINE TIMER
// ============================================================

function resetDaxtromnOfflineTimer() {

    if ( daxtromnOfflineTimer) { clearTimeout( daxtromnOfflineTimer);}
    daxtromnOfflineTimer =
        setTimeout(() => {

            console.warn( "⚠️ Daxtromn: нет валидных данных > 5 сек → OFFLINE");
            setDeviceVisibility("ErrorIcon","visible");

        }, DAXTROMN_OFFLINE_DELAY);
}


// ============================================================
// RAW FRAME DEBUG
//
// Вызывается ТОЛЬКО для обычного HEX-ответа.
//
// НЕ вызывается для:
//
// - No response from RS485
// - NAK
// ============================================================

function debugDaxtromnRawFrame(
    cmd,
    hex
) {

    if (
        !hex ||
        typeof hex !== "string"
    ) {

        console.log( "📡 DAXTROMN RAW:", { cmd, hex,type: typeof hex });
        return;
    }


    const normalized = hex.replace( /\s+/g, "") .toUpperCase();
    console.log("============================================================");
    console.log("📡 DAXTROMN RAW FRAME");
    console.log("CMD:", cmd);
    console.log("RAW hex_response:",hex);
    console.log("RAW HEX normalized:",normalized);
    console.log("HEX length:", normalized.length, "chars");
    console.log( "BYTE length:",normalized.length / 2); 
    const bytes = [];


    for (
        let i = 0;
        i + 1 < normalized.length;
        i += 2
    ) {

        bytes.push(normalized.slice( i, i + 2));
    }


    console.log("RAW bytes:", bytes);
    console.log("RAW ASCII:",daxtromnHexToAscii(normalized));
    console.log("LAST 10 bytes:", bytes.slice(-10));
    console.log( "TAIL HEX:",normalized.slice(-10));
    console.log("TAIL ASCII:",daxtromnHexToAscii( normalized.slice(-10)));
    console.log("============================================================");
}



