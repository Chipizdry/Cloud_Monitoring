
// ============================
// Цикл мониторинга Deye
// ============================

let cachedEnergySettings = null;
let TotalPVPower = 0;
let deyeWS = null;
let lastBattData = null;
// Объект с полной информацией о предупреждениях (аналогично DEYE_FAULT_INFO)
const DEYE_WARNING_INFO = {
    1:  { name:"W01", description:"Зарезервировано, возможна внутренняя диагностика системы", solution:"Проверьте вентиляторы, их питание и корректность подключения." },
    2:  { name:"W02", description:"Ошибка входного вентилятора охлаждения", solution:"Проверьте работу вентилятора, питание и разъёмы подключения." },
    3:  { name:"W03", description:"Неверная последовательность фаз сети", solution:"Проверьте порядок фаз, тип сети и правильность подключения." },
    4:  { name:"W04", description:"Нет связи со счётчиком энергии", solution:"Проверьте кабель связи, питание счётчика и настройки порта." },
    5:  { name:"W05", description:"Неверное направление трансформатора тока CT", solution:"Проверьте направление стрелки CT и место установки датчика." },
    6:  { name:"W06", description:"Трансформатор тока CT не подключен", solution:"Проверьте проводку CT и надёжность подключения к инвертору." },
    7:  { name:"W07", description:"Ошибка вентилятора охлаждения канал 1", solution:"Проверьте подключение и исправность вентилятора первого канала." },
    8:  { name:"W08", description:"Ошибка вентилятора охлаждения канал 2", solution:"Проверьте подключение и исправность вентилятора второго канала." },
    9:  { name:"W09", description:"Проблема охлаждения или перегрузка AC линии", solution:"Проверьте напряжение сети и достаточность сечения кабеля." },
    10: { name:"W10", description:"Активирован режим ограничения напряжения VW", solution:"Проверьте настройки режима и корректность работы системы." },
    11: { name:"W11", description:"Обнаружена утечка или проблема DC шины", solution:"Проверьте PV, АКБ, кабели, контакты и выполните полный перезапуск." },
    31: { name:"W31", description:"Нестабильная связь с батареей или BMS", solution:"Проверьте кабель BMS, соединения и корректность обмена данными." },
    32: { name:"W32", description:"Ошибка связи между параллельными инверторами", solution:"Проверьте кабель связи, DIP-переключатели и адреса устройств." }
};


 const DEYE_FAULT_INFO = {
  1:  { name:"F01", description:"Перепутана полярность входа солнечных панелей PV", solution:"Проверьте полярность подключения PV и исправьте соединение." },
  2:  { name:"F02", description:"Низкое сопротивление изоляции PV относительно земли", solution:"Проверьте заземление и изоляцию кабелей солнечных панелей." },
  3:  { name:"F03", description:"Обнаружена утечка тока на землю PV", solution:"Проверьте заземление модулей и наличие токов утечки." },
  4:  { name:"F04", description:"Ошибка заземления солнечных панелей PV", solution:"Проверьте наличие и корректность подключения заземления." },
  5:  { name:"F05", description:"Ошибка чтения внутренней памяти устройства", solution:"Перезапустите инвертор несколько раз или выполните сброс." },
  6:  { name:"F06", description:"Ошибка записи во внутреннюю память устройства", solution:"Перезапустите инвертор или выполните сброс настроек." },
  7:  { name:"F07", description:"DC/DC не запускается, недостаточное напряжение шины", solution:"Проверьте напряжение PV или АКБ, затем перезапустите." },
  8:  { name:"F08", description:"Второй DC/DC не запускается из-за низкого напряжения", solution:"Проверьте питание PV или батареи, выполните перезапуск." },
  9:  { name:"F09", description:"Ошибка силового модуля IGBT инвертора", solution:"Перезапустите инвертор, при повторе требуется сервис." },
  10: { name:"F10", description:"Ошибка вспомогательной платы питания инвертора", solution:"Проверьте питание и включение инвертора, затем перезапустите." },
  11: { name:"F11", description:"Неисправность главного контактора AC линии", solution:"Перезапустите инвертор, при повторе требуется диагностика." },
  12: { name:"F12", description:"Неисправность вспомогательного контактора AC линии", solution:"Перезапустите инвертор и проверьте цепи управления." },
  13: { name:"F13", description:"Изменение режима работы или параметров системы", solution:"Обычно исчезает само, при необходимости выполните перезапуск." },
  14: { name:"F14", description:"Перегрузка или превышение тока по DC стороне", solution:"Перезапустите инвертор и проверьте нагрузки и подключения." },
  15: { name:"F15", description:"Превышение тока на AC выходе инвертора", solution:"Проверьте мощность нагрузки и соответствие допустимым значениям." },
  16: { name:"F16", description:"Обнаружена утечка тока на землю", solution:"Проверьте заземление и кабели PV, затем перезапустите." },
  17: { name:"F17", description:"Перегрузка или нестабильность тока солнечных панелей", solution:"Проверьте подключение PV и стабильность генерации." },
  18: { name:"F18", description:"Перегрузка по току на AC стороне", solution:"Проверьте нагрузку и уменьшите потребляемую мощность." },
  19: { name:"F19", description:"Внутренняя ошибка системы инвертора", solution:"Перезапустите устройство или выполните сброс настроек." },
  20: { name:"F20", description:"Перегрузка DC при запуске или работе системы", solution:"Проверьте PV, АКБ и уменьшите подключённую нагрузку." },
  21: { name:"F21", description:"Превышение тока на высоковольтной DC шине", solution:"Проверьте токи PV и батареи, затем перезапустите." },
  22: { name:"F22", description:"Инвертор остановлен внешней удалённой командой", solution:"Проверьте систему управления и разрешите запуск инвертора." },
  23: { name:"F23", description:"Утечка тока на землю в системе PV", solution:"Проверьте кабели PV и корректность заземления системы." },
  24: { name:"F24", description:"Слишком низкое сопротивление изоляции PV", solution:"Проверьте соединения панелей и заземление инвертора." },
  25: { name:"F25", description:"Ошибка обратной связи по DC шине", solution:"Перезапустите инвертор, при повторе требуется сервис." },
  26: { name:"F26", description:"Дисбаланс фаз или неравномерная нагрузка системы", solution:"Проверьте распределение нагрузки и устраните перекос фаз." },
  29: { name:"F29", description:"Ошибка связи между параллельными инверторами", solution:"Проверьте кабели связи и настройки адресов устройств." },
  33: { name:"F33", description:"Превышение допустимого тока сети", solution:"Проверьте ток нагрузки и параметры сети." },
  34: { name:"F34", description:"Перегрузка по мощности на выходе инвертора", solution:"Уменьшите нагрузку до допустимого уровня работы." },
  35: { name:"F35", description:"Проблема сети, напряжение или частота вне нормы", solution:"Проверьте параметры сети и корректность подключения." },
  45: { name:"F45", description:"Перенапряжение сети выше допустимого диапазона", solution:"Проверьте напряжение и надёжность подключения кабелей." },
  46: { name:"F46", description:"Пониженное напряжение сети ниже допустимого", solution:"Проверьте параметры сети и состояние линии питания." },
  47: { name:"F47", description:"Частота сети выше допустимого диапазона", solution:"Проверьте частоту сети и настройки инвертора." },
  48: { name:"F48", description:"Частота сети ниже допустимого диапазона", solution:"Проверьте параметры сети и корректность работы генерации." },
  51: { name:"F51", description:"Температура батареи превышает допустимый уровень", solution:"Проверьте данные BMS и условия охлаждения батареи." },
  52: { name:"F52", description:"Слишком высокое напряжение DC шины инвертора", solution:"Проверьте напряжение батареи и вход PV." },
  53: { name:"F53", description:"Слишком низкое напряжение DC шины инвертора", solution:"Зарядите батарею от сети или солнечных панелей." },
  58: { name:"F58", description:"Потеря связи между инвертором и BMS батареи", solution:"Проверьте кабель связи и настройки BMS системы." },
  60: { name:"F60", description:"Ошибка работы генератора или его параметров", solution:"Проверьте напряжение, частоту генератора и перезапустите." },
  64: { name:"F64", description:"Перегрев радиатора или системы охлаждения инвертора", solution:"Проверьте температуру, вентиляцию и работу системы охлаждения." }
};

/*  ======================================================*/


// Функция для нормализации данных из разных источников
function normalizeDeyeData(rawData, source = "unknown") {
    const normalized = { ...rawData };
    
    // Вычисляем TotalPVPower если есть данные с панелей
    if (rawData.PVTotalPower_low !== undefined || rawData.PVTotalPower_high !== undefined) {
        normalized.TotalPVPower = (rawData.PVTotalPower_low || 0) + (rawData.PVTotalPower_high || 0);
    }
    
    // Вычисляем batteryTotalPower если есть данные батарей
    if (rawData.battery1Power !== undefined || rawData.battery2Power !== undefined) {
        normalized.batteryTotalPower = (rawData.battery1Power || 0) + (rawData.battery2Power || 0);
    }
    
    return normalized;
}


// Основная функция обновления UI на основе данных
function updateDeyeUI(data) {
    if (!data) return;
    const max = window.deviceMaxPower || {};
    // Обновляем солнечные панели
    if (data.TotalPVPower !== undefined) {
        updatePowerByName("Solar", PowerToIndicator(data.TotalPVPower, max.Solar));
        if (typeof solarPowerLabel !== 'undefined') {
            solarPowerLabel.textContent = formatPowerLabel(data.TotalPVPower, "solar");
        }
        setIconStatus("Solar", "normal");
    }
    
    // Обновляем генератор
   
    if (data.GenTotalPower !== undefined) {
        updatePowerByName("Generator", PowerToIndicator(data.GenTotalPower, max.Generator));
        if (typeof generatorFlowLabel !== 'undefined') {
            generatorFlowLabel.textContent = formatPowerLabel(data.GenTotalPower, "generator");
        }
        setIconStatus("Generator", "normal");
    }

    // Обновляем генератор - ВИДИМОСТЬ (из команды "service")
    // Этот блок должен выполняться всегда, если есть данные о реле
    if (data.genRelay !== undefined) {
        setDeviceVisibility("Generator", data.genRelay ? "visible" : "hidden");
    }
    
    // Обновляем нагрузку
    if (data.LoadTotalPower !== undefined) {
        updatePowerByName("Load", PowerToIndicator(data.LoadTotalPower, max.Load));
        if (typeof loadIndicatorLabel !== 'undefined') {
            loadIndicatorLabel.textContent = formatPowerLabel(data.LoadTotalPower, "load");
        }
        setIconStatus("Load", "normal");
    }
    
    // Обновляем батарею
    if (data.batteryTotalPower !== undefined && data.battery1SOC !== undefined) {
        updatePowerByName("Battery", PowerToIndicator(data.batteryTotalPower, max.Battery));
        updateBatteryFill(data.battery1SOC);
        if (typeof batteryFlowLabel !== 'undefined') {
            batteryFlowLabel.textContent = formatPowerLabel(data.batteryTotalPower, "battery");
        }
        setIconStatus("Battery", "normal");
    }
    
    // Обновляем сеть
    if (data.inputPowerTotal !== undefined) {
        updatePowerByName("Grid", PowerToIndicator(data.inputPowerTotal, max.Grid));
        if (typeof networkFlowLabel !== 'undefined') {
            networkFlowLabel.textContent = formatPowerLabel(data.inputPowerTotal, "grid");
        }
        setIconStatus("Grid", "normal");
    }
    
    // Обновляем lastData и вызываем универсальный UI
    window.lastData = {
        ...window.lastData,
        ...data
    };
    
    window.updateUIByData(window.lastData);
    setDeviceVisibility("ErrorIcon", "hidden");
    hideLoading();
}

function handleWSData(parsedData, cmd) {
    // Кешируем настройки
    if (cmd === "settings") {
        cachedEnergySettings = { ...parsedData };
        console.log("💾 Cached energy settings:", cachedEnergySettings);

        // Пересчёт SOC для ранее сохранённых данных батареи
        if (lastBattData && cachedEnergySettings.batteryWorkMode === "voltage") {
            const calculatedSOC = calculateBatterySOCVoltage(lastBattData, cachedEnergySettings);
            if (calculatedSOC !== null) {
                lastBattData.calculatedSOC = calculatedSOC;
                lastBattData.battery1SOC = calculatedSOC;
                lastBattData.battery2SOC = calculatedSOC;
                const normalized = normalizeDeyeData(lastBattData, "WS:recalc");
                updateDeyeUI(normalized);
                console.log(`🔋 Recalculated SOC from settings update: ${calculatedSOC}%`);
            }
        }
    }

    // Обработка батареи
    if (cmd === "batt") {
        lastBattData = { ...parsedData };
        // Обновляем UI только если есть настройки
        if (cachedEnergySettings && cachedEnergySettings.batteryWorkMode === "voltage") {
            const calculatedSOC = calculateBatterySOCVoltage(parsedData, cachedEnergySettings);
            if (calculatedSOC !== null) {
                parsedData.calculatedSOC = calculatedSOC;
                parsedData.battery1SOC = calculatedSOC;
                parsedData.battery2SOC = calculatedSOC;
                console.log(`🔋 Calculated SOC from voltage: ${calculatedSOC}%`);
            }
            const normalized = normalizeDeyeData(parsedData, `WS:${cmd}`);
            updateDeyeUI(normalized);
        } else {
            console.log("⏳ Battery data received, waiting for settings...");
            // Не обновляем UI, только сохраняем данные
        }
        resetFails();
        console.log(`📡 [WS:${cmd}] processed (batt data saved):`, parsedData);
        return; // Чтобы не вызывать updateDeyeUI повторно
    }

    // Для всех остальных команд обновляем UI сразу
    const normalized = normalizeDeyeData(parsedData, `WS:${cmd}`);
    updateDeyeUI(normalized);
    resetFails();
    console.log(`📡 [WS:${cmd}] processed:`, normalized);
}


// Функция для обработки полного цикла Modbus данных
async function processModbusData(host, port, slave, object_id, protocol) {
    try {
        // Загружаем все данные параллельно для производительности
        const [
            gridData,
            solarData_low,
            solarData_high,
            genData,
            battData,
            loadData,
            InvGridOut,
            gridDataPower,
            serviceData,
            energyServiceData,
            deyeFaults
        ] = await Promise.all([
            readOutGridRegisters(host, port, slave, object_id, protocol),
            readSunPanelRegisters_low(host, port, slave, object_id, protocol),
            readSunPanelRegisters_high(host, port, slave, object_id, protocol),
            readGeneratorRegisters(host, port, slave, object_id, protocol),
            readBatteryRegisters(host, port, slave, object_id, protocol),
            readLoadRegisters(host, port, slave, object_id, protocol),
            readInverterGridRegisters(host, port, slave, object_id, protocol),
            readPower32_V104(host, port, slave, object_id, protocol),
            readServiceRegisters(host, port, slave, object_id, protocol),
            readEnergyServiceRegisters(host, port, slave, object_id, protocol),
            readDeyeFaults(host, port, slave, object_id, protocol)
        ]);
        
        // Чтение реле (не блокирующее)
        const [coils, inputs] = await Promise.all([
            readRelayCoils(host, port, 10, object_id, 0, 8),
            readRelayDiscreteInputs(host, port, 10, object_id, 0, 8)
        ]);
        
        console.log("Реле:", coils?.coils);
        console.log("Входы:", inputs?.inputs);
        
        // Синхронизация UI реле
        await syncRelayUIFromDevice(host, port, 10, object_id);
        
        // Расчет SOC если нужно
        if (battData && energyServiceData) {
            const calculatedSOC = calculateBatterySOCVoltage(battData, energyServiceData);
            if (calculatedSOC !== null) {
                battData.calculatedSOC = calculatedSOC;
                battData.battery1SOC = calculatedSOC;
                battData.battery2SOC = calculatedSOC;
            }
        }
        
        // Объединяем все данные
        const mergedData = {
            ...gridData,
            ...solarData_low,
            ...solarData_high,
            ...genData,
            ...battData,
            ...loadData,
            ...InvGridOut,
            ...gridDataPower,
            ...serviceData,
            ...energyServiceData,
            deyeFaults
        };
        
        // Нормализуем и обновляем UI
        const normalized = normalizeDeyeData(mergedData, "modbus_tcp");
        updateDeyeUI(normalized);
        
        return mergedData;
        
    } catch (err) {
        console.error("Ошибка обработки Modbus данных:", err);
        throw err;
    }
}


// Обновленный цикл мониторинга Modbus TCP
async function startMonitoringDeyeModbusOverTcp(objectData) {
    if (deyeMonitorRunning) {
        console.warn("Deye monitoring already running");
        return;
    }
    
    deyeMonitorRunning = true;
    const INTERVAL = 1000;
    const { ip_address: host, port, slave_ids, id: object_id, protocol } = objectData;
    const slave = slave_ids?.[0] ?? 1;
    
    initRelayControls(host, port, 10, object_id);
    
    while (deyeMonitorRunning) {
        try {
            if (protocol === "modbus_over_tcp") {
                await processModbusData(host, port, slave, object_id, protocol);
            } else {
                console.warn("Unsupported Deye protocol:", protocol);
                break;
            }
        } catch (err) {
            console.error("Ошибка мониторинга Deye:", err);
        }
        
        await new Promise(r => setTimeout(r, INTERVAL));
    }
    
    deyeMonitorRunning = false;
}



// Обновленный WebSocket обработчик
function startDeyeCORBridgeWS(deviceId) {
    console.log("🚀 Инициализация Deye COR-Bridge WS", { deviceId });
    
    if (!deviceId) {
        console.error("❌ device_id не задан");
        return;
    }
    
    const wsUrl = buildAuthenticatedWebSocketUrl(
        `wss://dev.monitoring.cor-int.com/dev-modbus/responses?device_id=${encodeURIComponent(deviceId)}`
    );
    console.log("🌐 WS URL:", maskWebSocketUrlForLog(wsUrl));
    
    if (deyeWS && deyeWS.readyState === WebSocket.OPEN) {
        console.warn("⚠️ WS уже запущен");
        return;
    }
    
    deyeWS = new WebSocket(wsUrl);
    
    deyeWS.onopen = () => {
        console.log("✅ Deye WS подключён");
        resetOfflineTimer();
    };
    
    deyeWS.onmessage = (event) => {
        resetOfflineTimer();
        try {
            const raw = JSON.parse(event.data);
            console.log("📩 RAW WS:", raw);

            if (raw?.type === "connection_established" || raw?.type === "subscription_changed" || raw?.type === "ping") {
                return;
            }

            if (raw?.type === "polling_snapshot" && raw?.data) {
                const normalized = normalizeDeyeData(raw.data, "WS:snapshot");
                updateDeyeUI(normalized);
                resetFails();
                return;
            }

            if (raw?.type === "cor_agent_snapshot" && Array.isArray(raw.events)) {
                for (const snapshotEvent of raw.events) {
                    const snapCmd = snapshotEvent?.command_name ?? snapshotEvent?.data?.command_name;
                    const snapHex = snapshotEvent?.hex_response ?? snapshotEvent?.data?.hex_response ?? snapshotEvent?.hex_data;

                    if (!snapCmd || !snapHex || snapHex === "No response from RS485") {
                        continue;
                    }
                    if (!checkCRC16Modbus(snapHex)) {
                        continue;
                    }

                    const parsedSnapshot = parseDeyeByCmdWS(snapCmd, snapHex);
                    if (parsedSnapshot) {
                        handleWSData(parsedSnapshot, snapCmd);
                    }
                }
                return;
            }
            
            const cmd = raw?.command_name ?? raw?.data?.command_name;
            const hex = raw?.hex_response ?? raw?.data?.hex_response ?? raw?.hex_data;
            
            if (!cmd || !hex) {
                console.warn("⚠️ Нет cmd или hex_response", raw);
                return;
            }
            
            if (hex === "No response from RS485") {
                registerFail("rs485", "Нет связи с инвертором");
                return;
            }
            
            if (!checkCRC16Modbus(hex)) {
                console.warn("❌ CRC Modbus не прошла", { cmd, hex });
                return;
            }
            
            const parsed = parseDeyeByCmdWS(cmd, hex);
            
            if (!parsed) {
                console.warn("⚠️ Deye данные не распознаны:", cmd);
                return;
            }
            
            // Используем единый обработчик
            handleWSData(parsed, cmd);
            
        } catch (e) {
            console.error("❌ Ошибка обработки Deye WS:", e, event.data);
        }
    };
    
    deyeWS.onerror = (err) => console.error("❌ Deye WS ошибка:", err);
    
    deyeWS.onclose = (e) => {
        console.warn("🔌 Deye WS закрыт", e);
        deyeWS = null;
        setTimeout(() => startDeyeCORBridgeWS(deviceId), 1000);
    };
}


// Функция pushDataToUI (для обратной совместимости)
function pushDataToUI(partialData, source = "unknown") {
    if (!partialData) return;  
    const normalized = normalizeDeyeData(partialData, source);
    updateDeyeUI(normalized);
    console.log(`📡 [${source}] merged data:`, partialData);
}



async function startMonitoringDeyeCorBridge(objectData ) {
    showLoading();

    console.log("🚀 Запуск Deye COR-Bridge мониторинга");
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
    startDeyeCORBridgeWS(deviceId);
}


function checkCRC16Modbus(hex) {
    if (!hex || hex.length < 8) return false;
    const bytes = hex.match(/.{1,2}/g).map(b => parseInt(b, 16));
    const data = bytes.slice(0, -2);
    const crcReceived = (bytes[bytes.length - 1] << 8) | bytes[bytes.length - 2];
    let crc = 0xFFFF;

    for (let b of data) {
        crc ^= b;
        for (let i = 0; i < 8; i++) {
            crc = (crc & 1) ? (crc >> 1) ^ 0xA001 : (crc >> 1);
        }
    }

    return crc === crcReceived;
}


function parseDeyeByCmdWS(cmd, hex) {
    if (!hex || hex.length < 10) return null;
    const bytes = hex.match(/.{1,2}/g).map(b => parseInt(b, 16));

    // ❗ Проверка минимальной длины
    if (bytes.length < 5) return null;
    const byteCount = bytes[2];

    // ✅ ВЫРЕЗАЕМ ТОЛЬКО DATA
    const dataBytes = bytes.slice(3, 3 + byteCount);
    const results = {};

    switch (cmd) {
        case "batt": {
            const regs = [
                { name: "battery1Temperature", scale: 0.1, signed: false },
                { name: "battery1Voltage", scale: 0.1, signed: false },
                { name: "battery1SOC", scale: 1, signed: false },
                { name: "battery2SOC", scale: 1, signed: false },
                { name: "battery1Power", scale: 10, signed: true },
                { name: "battery1Current", scale: 0.01, signed: true },
                { name: "batteryCorrectedAh", scale: 1, signed: false },
                { name: "battery2Voltage", scale: 0.1, signed: false },
                { name: "battery2Current", scale: 0.01, signed: true },
                { name: "battery2Power", scale: 10, signed: true },
                { name: "battery2Temperature", scale: 0.1, signed: false },
            ];

            regs.forEach((reg, idx) => {
                const hi = dataBytes[idx * 2];
                const lo = dataBytes[idx * 2 + 1];
                if (hi === undefined || lo === undefined) return;
                let val = (hi << 8) | lo;
                if (reg.signed && val > 0x7FFF) {
                    val -= 0x10000;
                }
                results[reg.name] = val * reg.scale;
            });

            results.batteryTotalPower = (results.battery1Power || 0) + (results.battery2Power || 0);

            break;
        }

        case "PV1-4": {
            const regs = [
                { name: "PV1Power", scale: 10 },
                { name: "PV2Power", scale: 10 },
                { name: "PV3Power", scale: 10 },
                { name: "PV4Power", scale: 10 },
                { name: "PV1Voltage", scale: 0.1 },
                { name: "PV1Current", scale: 0.1 },
                { name: "PV2Voltage", scale: 0.1 },
                { name: "PV2Current", scale: 0.1 },
                { name: "PV3Voltage", scale: 0.1 },
                { name: "PV3Current", scale: 0.1 },
                { name: "PV4Voltage", scale: 0.1 },
                { name: "PV4Current", scale: 0.1 },
            ];

            regs.forEach((reg, idx) => {
                const hi = dataBytes[idx * 2];
                const lo = dataBytes[idx * 2 + 1];
                if (hi === undefined || lo === undefined) return;
                let val = (hi << 8) | lo;
                results[reg.name] = val * reg.scale;
            });

            results.PVTotalPower_low =(results.PV1Power || 0) + (results.PV2Power || 0) + (results.PV3Power || 0) + (results.PV4Power || 0);

            break;
        }

        case "grid": {
            const toSigned16 = (val) => (val > 0x7FFF ? val - 0x10000 : val);
            const getReg = (idx) => {
                const hi = dataBytes[idx * 2];
                const lo = dataBytes[idx * 2 + 1];
                if (hi === undefined || lo === undefined) return null;
                return (hi << 8) | lo;
            };

            // === Первый блок (регистры 598-609) ===
            // 598-600: Фазные напряжения
            results.inputVoltageL1 = (getReg(0) ?? 0) * 0.1;
            results.inputVoltageL2 = (getReg(1) ?? 0) * 0.1;
            results.inputVoltageL3 = (getReg(2) ?? 0) * 0.1;
            
            // 601-603: Линейные напряжения
            results.lineVoltageAB = (getReg(3) ?? 0) * 0.1;
            results.lineVoltageBC = (getReg(4) ?? 0) * 0.1;
            results.lineVoltageCA = (getReg(5) ?? 0) * 0.1;
            
            // 604-608: Мощности (S16)
            results.inputPowerL1 = toSigned16(getReg(6) ?? 0);
            results.inputPowerL2 = toSigned16(getReg(7) ?? 0);
            results.inputPowerL3 = toSigned16(getReg(8) ?? 0);
            results.inputPowerTotal = toSigned16(getReg(9) ?? 0);      // 609
            results.totalApparentPower = toSigned16(getReg(10) ?? 0);  // 610
            
            // 611: Частота
            results.inputFrequency = (getReg(11) ?? 0) * 0.01;
            
            // === Второй блок (регистры 612-620) ===
            // 612-614: Токи входа
            results.inputCurrentL1 = toSigned16(getReg(12) ?? 0) * 0.01;
            results.inputCurrentL2 = toSigned16(getReg(13) ?? 0) * 0.01;
            results.inputCurrentL3 = toSigned16(getReg(14) ?? 0) * 0.01;
            
            // 615-617: Токи выхода
            results.outCurrentA = toSigned16(getReg(15) ?? 0) * 0.01;
            results.outCurrentB = toSigned16(getReg(16) ?? 0) * 0.01;
            results.outCurrentC = toSigned16(getReg(17) ?? 0) * 0.01;
            
            // 618-620: Мощности выхода
            results.outPowerA = toSigned16(getReg(18) ?? 0);
            results.outPowerB = toSigned16(getReg(19) ?? 0);
            results.outPowerC = toSigned16(getReg(20) ?? 0);
            results.outTotalPower = toSigned16(getReg(21) ?? 0);
            results.outTotalApparentPower = toSigned16(getReg(22) ?? 0);
            
            break;
        }


        case "load": {
            const getReg = (idx) => {
                const hi = dataBytes[idx * 2];
                const lo = dataBytes[idx * 2 + 1];
                if (hi === undefined || lo === undefined) return null;
                return (hi << 8) | lo;
            };

            const toSigned32 = (val) => {
                return val > 0x7FFFFFFF ? val - 0x100000000 : val;
            };

            const getS32 = (lowIdx, highIdx) => {
                const low = getReg(lowIdx) ?? 0;
                const high = getReg(highIdx) ?? 0;

                const combined = (high << 16) | low;
                return toSigned32(combined);
            };

            /* ---------- Voltages ---------- */
            results.LoadPhaseVoltageA = (getReg(0) ?? 0) * 0.1;
            results.LoadPhaseVoltageB = (getReg(1) ?? 0) * 0.1;
            results.LoadPhaseVoltageC = (getReg(2) ?? 0) * 0.1;

            /* ---------- Frequency ---------- */
            results.LoadFrequency = (getReg(11) ?? 0) * 0.01;

            /* ---------- Power (S32) ---------- */
            results.LoadPhasePowerA = getS32(6, 12);
            results.LoadPhasePowerB = getS32(7, 13);
            results.LoadPhasePowerC = getS32(8, 14);
            results.LoadTotalPower  = getS32(9, 15);

            /* ---------- Calculated current ---------- */
            results.LoadPhaseCurrentA = results.LoadPhasePowerA / (results.LoadPhaseVoltageA || 1);
            results.LoadPhaseCurrentB = results.LoadPhasePowerB / (results.LoadPhaseVoltageB || 1);
            results.LoadPhaseCurrentC = results.LoadPhasePowerC / (results.LoadPhaseVoltageC || 1);

            break;
        }

        case "gen": {
            const getReg = (idx) => {
                const hi = dataBytes[idx * 2];
                const lo = dataBytes[idx * 2 + 1];
                if (hi === undefined || lo === undefined) return null;
                return (hi << 8) | lo;
            };

            const toSigned32 = (val) => { return val > 0x7FFFFFFF ? val - 0x100000000 : val;};

            const getS32 = (lowIdx, highIdx) => {
                const low = getReg(lowIdx) ?? 0;
                const high = getReg(highIdx) ?? 0;
                const combined = (high << 16) | low;
                return toSigned32(combined);
            };

            /* ---------- Voltages ---------- */
            results.GenPhaseVoltageA = (getReg(0) ?? 0) * 0.1;
            results.GenPhaseVoltageB = (getReg(1) ?? 0) * 0.1;
            results.GenPhaseVoltageC = (getReg(2) ?? 0) * 0.1;
            /* ---------- Power (S32, подряд) ---------- */
            results.GenPhasePowerA = getS32(3, 7);
            results.GenPhasePowerB = getS32(4, 8);
            results.GenPhasePowerC = getS32(5, 9);
            results.GenTotalPower  = getS32(6, 10);
            /* ---------- Calculated current ---------- */
            results.GenPhaseCurrentA = results.GenPhasePowerA / (results.GenPhaseVoltageA || 1);
            results.GenPhaseCurrentB = results.GenPhasePowerB / (results.GenPhaseVoltageB || 1);
            results.GenPhaseCurrentC = results.GenPhasePowerC / (results.GenPhaseVoltageC || 1);

            break;
        }

        case "PV5-8": {
            const regs = [
                { name: "PV5Power", scale: 10 },
                { name: "PV6Power", scale: 10 },
                { name: "PV7Power", scale: 10 },
                { name: "PV8Power", scale: 10 },

                { name: "PV5Voltage", scale: 0.1 },
                { name: "PV5Current", scale: 0.1 },

                { name: "PV6Voltage", scale: 0.1 },
                { name: "PV6Current", scale: 0.1 },

                { name: "PV7Voltage", scale: 0.1 },
                { name: "PV7Current", scale: 0.1 },

                { name: "PV8Voltage", scale: 0.1 },
                { name: "PV8Current", scale: 0.1 },
            ];

            regs.forEach((reg, idx) => {
                const hi = dataBytes[idx * 2];
                const lo = dataBytes[idx * 2 + 1];
                if (hi === undefined || lo === undefined) return;

                let val = (hi << 8) | lo;
                results[reg.name] = val * reg.scale;
            });

            results.PVTotalPower_high =
                (results.PV5Power || 0) +
                (results.PV6Power || 0) +
                (results.PV7Power || 0) +
                (results.PV8Power || 0);

            break;
        }


        case "service": {
            const getReg = (idx) => {
                const hi = dataBytes[idx * 2];
                const lo = dataBytes[idx * 2 + 1];
                if (hi === undefined || lo === undefined) return 0;
                return (hi << 8) | lo;
            };

            const r551 = getReg(0);
            const r552 = getReg(1);
            const r553 = getReg(2);
            const r554 = getReg(3);
            const r555 = getReg(4);
            const r556 = getReg(5);
            const r557 = getReg(6);
            const r558 = getReg(7);

            /* ---------- Power ---------- */
            results.powerOn = (r551 & 0x1) === 1;

            /* ---------- Relays ---------- */
            results.invRelay = !!(r552 & 0x1);
            results.loadRelay = !!(r552 & 0x2);
            results.gridRelay = !!(r552 & 0x4);
            results.genRelay = !!(r552 & 0x8);
            results.gridGivePowerRelay = !!(r552 & 0x10);
            results.dryContact1 = !!(r552 & 0x80);
            results.dryContact2 = !!(r552 & 0x100);

            /* ---------- Warnings ---------- */
            results.fanWarning = !!(r553 & 0x2);
            results.gridPhaseWrong = !!(r553 & 0x4);
            results.batteryLostWarning = !!(r554 & 0x4000);
            results.parallelCommWarning = !!(r554 & 0x8000);

            /* ---------- Faults ---------- */
            results.fault1 = r555;
            results.fault2 = r556;
            results.fault3 = r557;
            results.fault4 = r558;

            break;
        }

        case "settings": {
            const getReg = (idx) => {
                const hi = dataBytes[idx * 2];
                const lo = dataBytes[idx * 2 + 1];
                if (hi === undefined || lo === undefined) return 0;
                return (hi << 8) | lo;
            };

            const regs = [
                { name: "batteryFloatVoltage", scale: 0.1 },
                { name: "batteryCapacityAh", scale: 1 },
                { name: "batteryEmptyVoltage", scale: 0.1 },
                { name: "zeroExportPower", scale: 1 },
                { name: "equalizationDayCycle", scale: 1 },
                { name: "equalizationTime", scale: 0.1 },
                { name: "tempCompensation", scale: 1, signed: true },
                { name: "batteryMaxChargeCurrent", scale: 1 },
                { name: "batteryMaxDischargeCurrent", scale: 1 },
                { name: "parallelBatteryEnable", bool: true },
                { name: "batteryWorkMode", enum: { 0: "voltage", 1: "capacity", 2: "no_battery" }},
                { name: "liBatteryWakeup", bits: { batt1: 0, batt2: 8 }},
                { name: "batteryResistance", scale: 1 },
                { name: "batteryChargeEfficiency", scale: 0.1 },
                { name: "batteryCapacityShutdown", scale: 1 },
                { name: "batteryCapacityRestart", scale: 1 },
                { name: "batteryCapacityLowBatt", scale: 1 },
                { name: "batteryVoltageShutdown", scale: 0.1 },
                { name: "batteryVoltageRestart", scale: 0.1 },
                { name: "batteryVoltageLowBatt", scale: 0.1 },
                { name: "generatorMaxRunTime", scale: 0.1 },
                { name: "generatorCoolingTime", scale: 0.1 },
                { name: "generatorChargeStartVoltage", scale: 0.1 },
                { name: "generatorChargeStartCapacity", scale: 1 },
                { name: "generatorChargeCurrent", scale: 1 },
                { name: "gridChargeStartVoltage", scale: 0.1 },
                { name: "gridChargeStartCapacity", scale: 1 },
                { name: "gridChargeCurrent", scale: 1 },
                { name: "generatorChargeEnable", bool: true },
                { name: "gridChargeEnable", bool: true },
            ];

            regs.forEach((reg, idx) => {
                let val = getReg(idx);

                if (reg.signed && val > 0x7FFF) {
                    val -= 0x10000;
                }

                if (reg.bool) {
                    results[reg.name] = val !== 0;
                } 
                else if (reg.enum) {
                    results[reg.name] = reg.enum[val] ?? val;
                }
                else if (reg.bits) {
                    results[reg.name] = {};
                    for (const [key, bit] of Object.entries(reg.bits)) {
                        results[reg.name][key] = ((val >> bit) & 1) === 1;
                    }
                }
                else {
                    results[reg.name] = val * reg.scale;
                }
            });

            break;
        }
        default:
            console.warn("⚠️ Неизвестная команда:", cmd);
            return null;
    }

    return results;
}






/*  Регистры 622-625 соответствуют регистрам 687-690 , Сеть
    Регистры 633-637 соответствуют регистрам 691-695 , Выход инвертора
    Регистры 640-643 соответствуют регистрам 696-699 , Нагрузка ИБП
    Регистры 616-620 соответствуют регистрам 705-709 , Внешние трансформаторы тока 
    Регистры 604-608 соответствуют регистрам 700-704 , Внутренние трансформаторы тока


Измерение       	Регистры	                    Что измеряет
Inverter Output	    633-637 + 691-695	            Мощность на выходе DC-AC преобразователя
Grid Side	        622-625 + 687-690	            Мощность на стороне сети (после реле)
Load Side	        650-654 + 700-704	            Мощность на стороне нагрузки
UPS Load	        646-649 + 696-699	            Мощность на критической нагрузке
External CT	        655-659 + 705-709	            Мощность через внешние датчики

*/

const toSigned16 = (val) => {
    return val > 0x7FFF ? val - 0x10000 : val;
};

async function readGeneratorRegisters(host, port, slave_id, object_id, protocol) {
    const results = {};
    const startReg = 661;
    const count = 11; // 661–671

    const url =
        `${API_BASE_URL}/api/modbus_tcp/v1_cached/read` +
        `?protocol=${protocol}` +
        `&host=${host}` +
        `&port=${port}` +
        `&slave_id=${slave_id}` +
        `&object_id=${object_id}` +
        `&start=${startReg}` +
        `&count=${count}` +
        `&func_code=3`;

    try {
        console.log(`Чтение регистров генератора ${startReg}–${startReg + count - 1}...`);
        const resp = await fetch(url, { headers: { accept: "application/json" } });
        const data = await resp.json();

       // console.log("Raw Generator data:", data);

        if (data.ok && data.data?.length === count) {

            // --- Phase voltages ---
            results.GenPhaseVoltageA = data.data[0] * 0.1;
            results.GenPhaseVoltageB = data.data[1] * 0.1;
            results.GenPhaseVoltageC = data.data[2] * 0.1;

            // --- LOW words ---
            const pA_low = data.data[3];
            const pB_low = data.data[4];
            const pC_low = data.data[5];
            const pT_low = data.data[6];

            // --- HIGH words ---
            const pA_high = data.data[7];
            const pB_high = data.data[8];
            const pC_high = data.data[9];
            const pT_high = data.data[10];

            // --- 32-bit power calculation ---
            results.GenPhasePowerA = (pA_high << 16) | pA_low;
            results.GenPhasePowerB = (pB_high << 16) | pB_low;
            results.GenPhasePowerC = (pC_high << 16) | pC_low;
            results.GenTotalPower  = (pT_high << 16) | pT_low;

            // --- calculate currents ---
            results.GenPhaseCurrentA = results.GenPhasePowerA / (results.GenPhaseVoltageA || 1);
            results.GenPhaseCurrentB = results.GenPhasePowerB / (results.GenPhaseVoltageB || 1);
            results.GenPhaseCurrentC = results.GenPhasePowerC / (results.GenPhaseVoltageC || 1);

        }

    } catch (err) {
        console.error("Ошибка чтения регистров генератора:", err);
    }

    console.log("Parsed generator results:", results);
    return results;
}


async function readSunPanelRegisters_high(host, port, slave_id, object_id, protocol) {
    const results = {};

    const readBlock = async (start, count) => {
        const url =
            `${API_BASE_URL}/api/modbus_tcp/v1_cached/read` +
            `?protocol=${protocol}` +
            `&host=${host}` +
            `&port=${port}` +
            `&slave_id=${slave_id}` +
            `&object_id=${object_id}` +
            `&start=${start}` +
            `&count=${count}` +
            `&func_code=3`;

        console.log(`Чтение регистров солнечных панелей ${start}–${start + count - 1}...`);    
        const resp = await fetch(url, { headers: { accept: "application/json" } });
        return resp.json();
    };

    // 718–730
    const registers = [
        { name: "PVTotalPowerRaw", start: 718, scale: 10 }, // ⚠️ опционально

        { name: "PV5Voltage", start: 719, scale: 0.1 },
        { name: "PV6Voltage", start: 720, scale: 0.1 },
        { name: "PV7Voltage", start: 721, scale: 0.1 },
        { name: "PV8Voltage", start: 722, scale: 0.1 },

        { name: "PV5Current", start: 723, scale: 0.1 },
        { name: "PV6Current", start: 724, scale: 0.1 },
        { name: "PV7Current", start: 725, scale: 0.1 },
        { name: "PV8Current", start: 726, scale: 0.1 },

        { name: "PV5Power", start: 727, scale: 10 },
        { name: "PV6Power", start: 728, scale: 10 },
        { name: "PV7Power", start: 729, scale: 10 },
        { name: "PV8Power", start: 730, scale: 10 },
    ];

    const startReg = registers[0].start;
    const count = registers.length;

    try {
        const data = await readBlock(startReg, count);

        if (data.ok && data.data?.length === count) {
            registers.forEach((reg, idx) => {
                results[reg.name] = data.data[idx] * reg.scale;
            });

            // ✅ суммарная мощность PV5–PV8
            results.PVTotalPower_high =
                (results.PV5Power || 0) +
                (results.PV6Power || 0) +
                (results.PV7Power || 0) +
                (results.PV8Power || 0);
        }

    } catch (err) {
        console.error("Ошибка чтения расширенных PV регистров:", err);
    }

    console.log("☀️ PV5–PV8 results:", results);
    return results;
}



async function readBatteryRegisters(host, port, slave_id, object_id, protocol) {
    const results = {};

    const registers = [
        { start: 586, name: "battery1Temperature", scale: 0.1 },        // °C
        { start: 587, name: "battery1Voltage", scale: 0.1 },            // V
        { start: 588, name: "battery1SOC", scale: 1 },                   // %
        { start: 589, name: "battery2SOC", scale: 1 },                   // %
        { start: 590, name: "battery1Power", scale: 10, signed: true },  // W
        { start: 591, name: "battery1Current", scale: 0.01, signed: true }, // A
        { start: 592, name: "batteryCorrectedAh", scale: 1 },            // Ah
        { start: 593, name: "battery2Voltage", scale: 0.1 },            // V
        { start: 594, name: "battery2Current", scale: 0.01, signed: true }, // A
        { start: 595, name: "battery2Power", scale: 10, signed: true },  // W
        { start: 596, name: "battery2Temperature", scale: 0.1 }          // °C
    ];

    const startReg = registers[0].start;
    const count = registers.length;

    const url =
        `${API_BASE_URL}/api/modbus_tcp/v1_cached/read` +
        `?protocol=${protocol}` +
        `&host=${host}` +
        `&port=${port}` +
        `&slave_id=${slave_id}` +
        `&object_id=${object_id}` +
        `&start=${startReg}` +
        `&count=${count}` +
        `&func_code=3`;

    try {
        console.log(`Чтение регистров батареи ${startReg}–${startReg + count - 1}...`);
        const resp = await fetch(url, {
            headers: { accept: "application/json" }
        });

        const data = await resp.json();

        if (data.ok && data.data?.length === count) {
            registers.forEach((reg, idx) => {
                let val = data.data[idx];
                if (reg.signed && val > 0x7FFF) val -= 0x10000;
                results[reg.name] = val * reg.scale;
            });

            // 🔹 ОБЩАЯ МОЩНОСТЬ БАТАРЕЙ
            const p1 = results.battery1Power ?? 0;
            const p2 = results.battery2Power ?? 0;
            results.batteryTotalPower = p1 + p2;
        }

    } catch (err) {
        console.error("Ошибка чтения регистров батареи:", err);
    }

    console.log("Battery parsed results:", results);
    return results;
}


async function readSunPanelRegisters_low(host, port, slave_id, object_id, protocol) {
    const results = {};

    const readBlock = async (start, count) => {
        const url =
            `${API_BASE_URL}/api/modbus_tcp/v1_cached/read` +
            `?protocol=${protocol}` +
            `&host=${host}` +
            `&port=${port}` +
            `&slave_id=${slave_id}` +
            `&object_id=${object_id}` +
            `&start=${start}` +
            `&count=${count}` +
            `&func_code=3`;

        console.log(`Чтение регистров солнечных панелей ${start}–${start + count - 1}...`);    
        const resp = await fetch(url, { headers: { accept: "application/json" } });
        return resp.json();
    };

    const registers = [
        { name: "PV1Power", start: 672, scale: 10 },
        { name: "PV2Power", start: 673, scale: 10 },
        { name: "PV3Power", start: 674, scale: 10 },
        { name: "PV4Power", start: 675, scale: 10 },

        { name: "PV1Voltage", start: 676, scale: 0.1 },
        { name: "PV1Current", start: 677, scale: 0.1 },
        { name: "PV2Voltage", start: 678, scale: 0.1 },
        { name: "PV2Current", start: 679, scale: 0.1 },
        { name: "PV3Voltage", start: 680, scale: 0.1 },
        { name: "PV3Current", start: 681, scale: 0.1 },
        { name: "PV4Voltage", start: 682, scale: 0.1 },
        { name: "PV4Current", start: 683, scale: 0.1 },
    ];

    const startReg = registers[0].start;
    const count = registers.length;

    try {
        const data = await readBlock(startReg, count);

        if (data.ok && data.data?.length === count) {

            registers.forEach((reg, idx) => {
                results[reg.name] = data.data[idx] * reg.scale;
            });

            /* ===== PV TOTAL POWER ===== */
            results.PVTotalPower_low =
                (results.PV1Power || 0) +
                (results.PV2Power || 0) +
                (results.PV3Power || 0) +
                (results.PV4Power || 0);
        }

    } catch (err) {
        console.error("Ошибка чтения SunPanel регистров:", err);
    }

    console.log("Обработанные результаты SUN Panel:", results);
    return results;
}





async function readLoadRegisters(host, port, slave_id, object_id, protocol) {
    const results = {};

    const startReg = 644;
    const count = 17; // 644–660

    const url =
        `${API_BASE_URL}/api/modbus_tcp/v1_cached/read` +
        `?protocol=${protocol}` +
        `&host=${host}` +
        `&port=${port}` +
        `&slave_id=${slave_id}` +
        `&object_id=${object_id}` +
        `&start=${startReg}` +
        `&count=${count}` +
        `&func_code=3`;

    try {
        console.log(`Чтение регистров нагрузки ${startReg}–${startReg + count - 1}...`);
        const resp = await fetch(url, { headers: { accept: "application/json" } });
        const data = await resp.json();

      //  console.log("Raw LOAD data:", data);

        if (data.ok && data.data?.length === count) {

            /* ---------- Voltages ---------- */
            results.LoadPhaseVoltageA = data.data[0] * 0.1; // 644
            results.LoadPhaseVoltageB = data.data[1] * 0.1; // 645
            results.LoadPhaseVoltageC = data.data[2] * 0.1; // 646


            /* ---------- Frequency ---------- */
            results.LoadFrequency = data.data[11] * 0.01;  // 655

            /* ---------- LOW words ---------- */
            const pA_low = data.data[6];  // 650
            const pB_low = data.data[7];  // 651
            const pC_low = data.data[8];  // 652
            const pT_low = data.data[9];  // 653

            /* ---------- HIGH words ---------- */
            const pA_high = data.data[12]; // 656
            const pB_high = data.data[13]; // 657
            const pC_high = data.data[14]; // 658
            const pT_high = data.data[15]; // 659

            /* ---------- S32 power calculation ---------- */
            results.LoadPhasePowerA =
                ((pA_high << 16) | pA_low) << 0;
            results.LoadPhasePowerB =
                ((pB_high << 16) | pB_low) << 0;
            results.LoadPhasePowerC =
                ((pC_high << 16) | pC_low) << 0;
            results.LoadTotalPower =
                ((pT_high << 16) | pT_low) << 0;


            results.LoadPhaseCurrentA = results.LoadPhasePowerA / (results.LoadPhaseVoltageA || 1); // 647
            results.LoadPhaseCurrentB = results.LoadPhasePowerB / (results.LoadPhaseVoltageB || 1); // 648
            results.LoadPhaseCurrentC = results.LoadPhasePowerC / (results.LoadPhaseVoltageC || 1); // 649 

        }

    } catch (err) {
        console.error("Ошибка чтения регистров нагрузки:", err);
    }

    console.log("Parsed LOAD results:", results);
    return results;
}




async function readServiceRegisters(host, port, slave_id, object_id, protocol) {
    const results = {};

    // Универсальная функция чтения блоков регистров
    const readBlock = async (start, count) => {
        const url =
            `${API_BASE_URL}/api/modbus_tcp/v1_cached/read` +
            `?protocol=${protocol}` +
            `&host=${host}` +
            `&port=${port}` +
            `&slave_id=${slave_id}` +
            `&object_id=${object_id}` +
            `&start=${start}` +
            `&count=${count}` +
            `&func_code=3`;

        console.log(`Чтение сервисных регистров ${start}–${start + count - 1}...`);
        const resp = await fetch(url, { headers: { accept: "application/json" } });
        return resp.json();
    };

    const startReg = 551;
    const count = 8; // 551–558

    try {
        const data = await readBlock(startReg, count);

        if (data.ok && data.data?.length === count) {
            const regs = data.data;

            // === Turn on/off status ===
            results.powerOn = (regs[0] & 0x1) === 1; // 551, Bit0: 1 = on, 0 = off

            // === AC relay status ===
            results.invRelay = (regs[1] & 0x1) !== 0;      // 552, Bit0
            results.loadRelay = (regs[1] & 0x2) !== 0;     // 552, Bit1
            results.gridRelay = (regs[1] & 0x4) !== 0;     // 552, Bit2
            results.genRelay = (regs[1] & 0x8) !== 0;      // 552, Bit3
            results.gridGivePowerRelay = (regs[1] & 0x10) !== 0; // 552, Bit4
            results.dryContact1 = (regs[1] & 0x80) !== 0;  // 552, Bit7
            results.dryContact2 = (regs[1] & 0x100) !== 0; // 552, Bit8

            // === Warning messages ===
            results.fanWarning = (regs[2] & 0x2) !== 0;          // 553, Bit1
            results.gridPhaseWrong = (regs[2] & 0x4) !== 0;      // 553, Bit2
            results.batteryLostWarning = (regs[3] & 0x4000) !== 0; // 554, Bit14
            results.parallelCommWarning = (regs[3] & 0x8000) !== 0; // 554, Bit15

            // === Fault information ===
            results.fault1 = regs[4]; // 555
            results.fault2 = regs[5]; // 556
            results.fault3 = regs[6]; // 557
            results.fault4 = regs[7]; // 558
        }

    } catch (err) {
        console.error("Ошибка чтения сервисных регистров:", err);
    }

    console.log("Обработанные сервисные результаты:", results);
    return results;
}



async function readInverterGridRegisters(host, port, slave_id, object_id, protocol) {
    const results = {};

    const registers = [
            { start: 621, name: "GridPowerFactor", scale: 0.001 },        // PF
            { start: 622, name: "GridPowerA", scale: 1 },                 // Low_Word
            { start: 623, name: "GridPowerB", scale: 1 },
            { start: 624, name: "GridPowerC", scale: 1 },
            { start: 625, name: "GridTotalPower", scale: 1 },

            // 626 пропускаем
            { start: 626, name: "InverterVoltageA???", scale: 0.1 },
            { start: 627, name: "InverterVoltageA", scale: 0.1 },
            { start: 628, name: "InverterVoltageB", scale: 0.1 },
            { start: 629, name: "InverterVoltageC", scale: 0.1 },

            { start: 630, name: "InverterCurrentA", scale: 0.01, signed: true },
            { start: 631, name: "InverterCurrentB", scale: 0.01, signed: true },
            { start: 632, name: "InverterCurrentC", scale: 0.01, signed: true },

            { start: 633, name: "InverterPowerA", scale: 1, signed: true },
            { start: 634, name: "InverterPowerB", scale: 1, signed: true },
            { start: 635, name: "InverterPowerC", scale: 1, signed: true },

            { start: 636, name: "InverterTotalPower", scale: 0.1, signed: true },
            { start: 637, name: "InverterTotalApparentPower", scale: 0.1, signed: true },  
            { start: 638, name: "InverterFrequency", scale: 0.01 },
        ];
    const startReg = registers[0].start;
    const count = registers.length;

    const url =
        `${API_BASE_URL}/api/modbus_tcp/v1_cached/read` +
        `?protocol=${protocol}` +
        `&host=${host}` +
        `&port=${port}` +
        `&slave_id=${slave_id}` +
        `&object_id=${object_id}` +
        `&start=${startReg}` +
        `&count=${count}` +
        `&func_code=3`;

    try {
        console.log(`Чтение регистров инвертора (сеть) ${startReg}–${startReg + count - 1}...`);
        const resp = await fetch(url, { headers: { accept: "application/json" } });
        const data = await resp.json();
       // console.log("Raw inverter grid data:", data);

        if (data.ok && data.data?.length === count) {
            registers.forEach((reg, idx) => {
                let val = data.data[idx];
                if (reg.signed && val > 0x7FFF) val -= 0x10000;
                results[reg.name] = val * reg.scale;
            });
        }

    } catch (err) {
        console.error("Error reading inverter registers:", err);
    }

    console.log("Processed inverter grid data:", results);
    return results;
}



async function readOutGridRegisters(host, port, slave_id, object_id, protocol) {
    const results = {};

    const readBlock = async (start, count) => {
        const url =
            `${API_BASE_URL}/api/modbus_tcp/v1_cached/read` +
            `?protocol=${protocol}` +
            `&host=${host}` +
            `&port=${port}` +
            `&slave_id=${slave_id}` +
            `&object_id=${object_id}` +
            `&start=${start}` +
            `&count=${count}` +
            `&func_code=3`;

        console.log(`Чтение регистров внешней сети ${start}–${start + count - 1}...`);    
        const resp = await fetch(url, { headers: { accept: "application/json" } });
        return resp.json();
    };

    const startReg = 598;
    const totalCount = 23; // с 598 по 620 включительно

    try {
        const data = await readBlock(startReg, totalCount);

        if (data.ok && data.data?.length === totalCount) {

            // === Первый блок ===
            results.inputVoltageL1 = data.data[0] * 0.1;
            results.inputVoltageL2 = data.data[1] * 0.1;
            results.inputVoltageL3 = data.data[2] * 0.1;

            results.lineVoltageAB = data.data[3] * 0.1;
            results.lineVoltageBC = data.data[4] * 0.1;
            results.lineVoltageCA = data.data[5] * 0.1;

            results.inputPowerL1 = toSigned16(data.data[6]);
            results.inputPowerL2 = toSigned16(data.data[7]);
            results.inputPowerL3 = toSigned16(data.data[8]);
            results.totalApparentPower = toSigned16(data.data[10]);
            results.inputPowerTotal = toSigned16(data.data[9]);
            results.inputFrequency  = data.data[11] * 0.01;

            // === Второй блок ===
            results.inputCurrentL1 = toSigned16(data.data[12]) * 0.01;
            results.inputCurrentL2 = toSigned16(data.data[13]) * 0.01;
            results.inputCurrentL3 = toSigned16(data.data[14]) * 0.01;

            results.outCurrentA = toSigned16(data.data[15]) * 0.01;
            results.outCurrentB = toSigned16(data.data[16]) * 0.01;
            results.outCurrentC = toSigned16(data.data[17]) * 0.01;

            results.outPowerA = toSigned16(data.data[18]);
            results.outPowerB = toSigned16(data.data[19]);
            results.outPowerC = toSigned16(data.data[20]);

            results.outTotalPower = toSigned16(data.data[21]);
            results.outTotalApparentPower = toSigned16(data.data[22]);
        }

    } catch (err) {
        console.error("Ошибка чтения блока регистров:", err);
    }

    console.log("Обработанные GRID результаты:", results);
    return results;
}



async function readPower32_V104(host, port, slave_id, object_id, protocol) {
    const results = {};

    const registers = [
        // --- Grid side ---
        { start: 687, name: "GridPowerA_high", scale: 1 },
        { start: 688, name: "GridPowerB_high", scale: 1 },
        { start: 689, name: "GridPowerC_high", scale: 1 },
        { start: 690, name: "GridTotalPower_high", scale: 1 },

        // --- Inverter output ---
        { start: 691, name: "InverterPowerA_high", scale: 1, signed: true },
        { start: 692, name: "InverterPowerB_high", scale: 1, signed: true },
        { start: 693, name: "InverterPowerC_high", scale: 1, signed: true },
        { start: 694, name: "InverterTotalPower_high", scale: 1, signed: true },
        { start: 695, name: "InverterTotalApparentPower_high", scale: 1, signed: true },

        // --- UPS load ---
        { start: 696, name: "UpsPowerA_high", scale: 1 },
        { start: 697, name: "UpsPowerB_high", scale: 1 },
        { start: 698, name: "UpsPowerC_high", scale: 1 },
        { start: 699, name: "UpsTotalPower_high", scale: 1 },

        // --- Inner grid ---
        { start: 700, name: "InnerGridPowerA_high", scale: 1, signed: true },
        { start: 701, name: "InnerGridPowerB_high", scale: 1, signed: true },
        { start: 702, name: "InnerGridPowerC_high", scale: 1, signed: true },
        { start: 703, name: "InnerGridTotalPower_high", scale: 1, signed: true },
        { start: 704, name: "InnerGridTotalApparentPower_high", scale: 1 }, // reserved

        // --- Out grid ---
        { start: 705, name: "OutGridPowerA_high", scale: 1, signed: true },
        { start: 706, name: "OutGridPowerB_high", scale: 1, signed: true },
        { start: 707, name: "OutGridPowerC_high", scale: 1, signed: true },
        { start: 708, name: "OutGridTotalPower_high", scale: 1, signed: true },
        { start: 709, name: "OutGridTotalApparentPower_high", scale: 1, signed: true },
    ];

    const startReg = registers[0].start;
    const count = registers.length;

    const url =
        `${API_BASE_URL}/api/modbus_tcp/v1_cached/read` +
        `?protocol=${protocol}` +
        `&host=${host}` +
        `&port=${port}` +
        `&slave_id=${slave_id}` +
        `&object_id=${object_id}` +
        `&start=${startReg}` +
        `&count=${count}` +
        `&func_code=3`;

    try {
        const resp = await fetch(url, { headers: { accept: "application/json" } });
        const data = await resp.json();

      //  console.log("Raw HIGH power data:", data);

        if (data.ok && data.data?.length === count) {
            registers.forEach((reg, idx) => {
                let val = data.data[idx];
                if (reg.signed && val > 0x7FFF) val -= 0x10000; // S16
                results[reg.name] = val * reg.scale;
            });
        }

    } catch (err) {
        console.error("Ошибка чтения HIGH power регистров:", err);
    }

  //  console.log("Parsed HIGH power results:", results);
    return results;
}

async function readEnergyServiceRegisters(host, port, slave_id, object_id, protocol) {
    const results = {};

    const registers = [
        { start: 101, name: "batteryFloatVoltage", scale: 0.1 },        // V
        { start: 102, name: "batteryCapacityAh", scale: 1 },             // Ah
        { start: 103, name: "batteryEmptyVoltage", scale: 0.1 },        // V
        { start: 104, name: "zeroExportPower", scale: 1 },
        { start: 105, name: "equalizationDayCycle", scale: 1 },          // days
        { start: 106, name: "equalizationTime", scale: 0.1 },            // hours (MCU 0–100)
        { start: 107, name: "tempCompensation", scale: 1, signed: true },// mV/℃
        { start: 108, name: "batteryMaxChargeCurrent", scale: 1 },       // A
        { start: 109, name: "batteryMaxDischargeCurrent", scale: 1 },    // A
        { start: 110, name: "parallelBatteryEnable", bool: true },
        { start: 111, name: "batteryWorkMode", enum: { 0: "voltage", 1: "capacity", 2: "no_battery" }},
        { start: 112, name: "liBatteryWakeup", bits: { batt1: 0, batt2: 8 } }, // Bit0: battery1, Bit8: battery2
        { start: 113, name: "batteryResistance", scale: 1 },             // mΩ
        { start: 114, name: "batteryChargeEfficiency", scale: 0.1 },     // %
        { start: 115, name: "batteryCapacityShutdown", scale: 1 },       // %
        { start: 116, name: "batteryCapacityRestart", scale: 1 },
        { start: 117, name: "batteryCapacityLowBatt", scale: 1 },
        { start: 118, name: "batteryVoltageShutdown", scale: 0.1 },     // V
        { start: 119, name: "batteryVoltageRestart", scale: 0.1 },
        { start: 120, name: "batteryVoltageLowBatt", scale: 0.1 },
        { start: 121, name: "generatorMaxRunTime", scale: 0.1 },          // hours
        { start: 122, name: "generatorCoolingTime", scale: 0.1 },
        { start: 123, name: "generatorChargeStartVoltage", scale: 0.1 },
        { start: 124, name: "generatorChargeStartCapacity", scale: 1 },  // %
        { start: 125, name: "generatorChargeCurrent", scale: 1 },         // A
        { start: 126, name: "gridChargeStartVoltage", scale: 0.1 },
        { start: 127, name: "gridChargeStartCapacity", scale: 1 },
        { start: 128, name: "gridChargeCurrent", scale: 1 },
        { start: 129, name: "generatorChargeEnable", bool: true },
        { start: 130, name: "gridChargeEnable", bool: true },
    ];

    const startReg = registers[0].start;
    const count = registers.length;

    const url =
        `${API_BASE_URL}/api/modbus_tcp/v1_cached/read` +
        `?protocol=${protocol}` +
        `&host=${host}` +
        `&port=${port}` +
        `&slave_id=${slave_id}` +
        `&object_id=${object_id}` +
        `&start=${startReg}` +
        `&count=${count}` +
        `&func_code=3`;

    try {
        const resp = await fetch(url, { headers: { accept: "application/json" } });
        const data = await resp.json();

        if (data.ok && data.data?.length === count) {
            registers.forEach((reg, idx) => {
                let val = data.data[idx];

                if (reg.signed && val > 0x7FFF) {
                    val -= 0x10000;
                }

                if (reg.bool) {
                    results[reg.name] = val !== 0;
                } 
                else if (reg.enum) {
                    results[reg.name] = reg.enum[val] ?? val;
                }
                else if (reg.bits) {
                    results[reg.name] = {};
                    for (const [key, bit] of Object.entries(reg.bits)) {
                        results[reg.name][key] = ((val >> bit) & 1) === 0;
                    }
                }
                else {
                    results[reg.name] = val * reg.scale;
                }
            });
        }
    } catch (err) {
        console.error("Ошибка чтения регистров энергии (101–130):", err);
    }

   // console.log("⚙️ Energy service registers:", results);
    return results;
}



// Обновленная функция readDeyeFaults
async function readDeyeFaults(host, port, slave_id, object_id, protocol) {
    const startReg = 553;
    const count = 6;

    const url =
        `${API_BASE_URL}/api/modbus_tcp/v1_cached/read` +
        `?protocol=${protocol}` +
        `&host=${host}` +
        `&port=${port}` +
        `&slave_id=${slave_id}` +
        `&object_id=${object_id}` +
        `&start=${startReg}` +
        `&count=${count}` +
        `&func_code=3`;

    try {
        console.log(`Чтение регистров ошибок Deye ${startReg}–${startReg + count - 1}...`);
        const resp = await fetch(url, { headers: { accept: "application/json" } });
        const data = await resp.json();

        if (!data.ok || !Array.isArray(data.data)) {
            console.warn("⚠️ Deye faults read failed:", data);
            return null;
        }

        const [w1, w2, f1, f2, f3, f4] = data.data;

        console.log("🚨 Deye fault raw registers:", {
            553: `0x${w1.toString(16)}`,
            554: `0x${w2.toString(16)}`,
            555: `0x${f1.toString(16)}`,
            556: `0x${f2.toString(16)}`,
            557: `0x${f3.toString(16)}`,
            558: `0x${f4.toString(16)}`
        });

        const warningsDecoded = decodeDeyeWarnings([w1, w2]);
        const faultsDecoded = decodeDeyeFaults([f1, f2, f3, f4]);

        console.log("⚠️ WARNINGS:");
        formatDeyeEvents(warningsDecoded, DEYE_WARNING_INFO, "WARN")
            .forEach(line => console.log(line));

        console.log("❌ FAULTS:");
        formatDeyeEvents(faultsDecoded, DEYE_FAULT_INFO, "FAULT")
            .forEach(line => console.log(line));

        return {
            warnings: warningsDecoded,
            faults: faultsDecoded
        };

    } catch (err) {
        console.error("❌ Error reading Deye faults:", err);
        return null;
    }
}


function decodeDeyeFaults(words) {
    const faults = [];

    words.forEach((word, wordIndex) => {
        for (let bit = 0; bit < 16; bit++) {
            if (!(word & (1 << bit))) continue;

            const faultNumber = wordIndex * 16 + bit + 1; // F01–F64
            const info = DEYE_FAULT_INFO[faultNumber];
            if (!info || info.description === "Reserved") continue;

            faults.push({
                code: info.name,              // Fxx
                name: info.description,
                solution: info.solution
            });
        }
    });

    return faults;
}



// Обновленная функция декодирования предупреждений
function decodeDeyeWarnings(words) {
    const warnings = [];

    words.forEach((word, wordIndex) => {
        for (let bit = 0; bit < 16; bit++) {
            if (!(word & (1 << bit))) continue;
            const warningNumber = wordIndex * 16 + bit + 1; // W01–W32
            const info = DEYE_WARNING_INFO[warningNumber];
            if (!info || info.description === "Reserved") continue;

            warnings.push({
                code: info.name,              // Wxx
                name: info.description,
                solution: info.solution
            });
        }
    });

    return warnings;
}

function formatDeyeEvents(events, type = "WARN") {
    if (!events || events.length === 0) return [`✅ No ${type}`];

    return events.map(ev => {
        return `🚨 ${ev.code} | ${ev.name}\n   🛠 ${ev.solution}`;
    });
}


function calculateBatterySOCVoltage(battData, energyServiceData) {
    if (!battData || !energyServiceData) return null;
    if (energyServiceData.batteryWorkMode !== "voltage") {
        console.log("⚠️ Режим не voltage:", energyServiceData.batteryWorkMode);
        return null;
    }

    // Получаем напряжения
    const v1 = battData.battery1Voltage;
    const v2 = battData.battery2Voltage;
    
    // Определяем, какие батареи есть
    const voltages = [];
    if (typeof v1 === "number" && !isNaN(v1) && v1 > 0) voltages.push(v1);
    if (typeof v2 === "number" && !isNaN(v2) && v2 > 0) voltages.push(v2);
    
    if (voltages.length === 0) {
        console.warn("❌ Нет данных о напряжении батарей");
        return null;
    }
    
    // Среднее напряжение
    const vAvg = voltages.reduce((a, b) => a + b, 0) / voltages.length;
    
    // Настройки (уже в вольтах)
    const vMax = energyServiceData.batteryFloatVoltage;
    const vMin = energyServiceData.batteryVoltageShutdown;
    
    console.log("🔋 HV Battery SOC Calculation:", {
        voltages,
        vAvg: vAvg.toFixed(1) + "V",
        vMax: vMax + "V",
        vMin: vMin + "V",
        range: (vMax - vMin) + "V"
    });
    
    if (!vMax || !vMin || vMax <= vMin) {
        console.warn("❌ Неверные настройки напряжения:", { vMax, vMin });
        return null;
    }
    
    // Расчёт SOC
    let soc = ((vAvg - vMin) / (vMax - vMin)) * 100;
    soc = Math.max(0, Math.min(100, soc));
    const rounded = Math.round(soc * 10) / 10;
    
    console.log(`✅ Calculated SOC: ${rounded}% (${vAvg}V / ${vMax}V max / ${vMin}V min)`);   
    return rounded;
}



