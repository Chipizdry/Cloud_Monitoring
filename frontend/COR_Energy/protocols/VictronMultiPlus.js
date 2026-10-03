



/* ============================================================
   Victron — Modbus TCP monitoring via COR-Bridge
   Регистрация в object.js: MonitoringVictronCorBridge
   ============================================================ */

let vicWS = null;
let vicOfflineTimer = null;
let vicReconnectTimer = null;
let vicFails = 0;
let vicFirstDataReceived = false;

const VIC_FAIL_LIMIT      = 3;
const VIC_OFFLINE_DELAY   = 15000;
const VIC_RECONNECT_DELAY = 3000;

/* ----------------------------------------------------------
   Карта регистров.
   [unit_id, addr, key, type, scale, label]
     type  : 'u16' | 'i16' | 'u32' | 'i32'
     scale : делить raw на это значение
   ---------------------------------------------------------- */
const VIC_REGISTER_MAP = [
  /* ==== vebus, unit 227, 3..28 ==== */
  [227,   3, 'vic_acIn_L1_V',      'u16',   10, 'AC-In L1 напряжение'],
  [227,   4, 'vic_acIn_L2_V',      'u16',   10, 'AC-In L2 напряжение'],
  [227,   5, 'vic_acIn_L3_V',      'u16',   10, 'AC-In L3 напряжение'],
  [227,   6, 'vic_acIn_L1_I',      'i16',   10, 'AC-In L1 ток'],
  [227,   7, 'vic_acIn_L2_I',      'i16',   10, 'AC-In L2 ток'],
  [227,   8, 'vic_acIn_L3_I',      'i16',   10, 'AC-In L3 ток'],
  [227,   9, 'vic_acIn_L1_F',      'i16',  100, 'AC-In частота L1'],
  [227,  10, 'vic_acIn_L2_F',      'i16',  100, 'AC-In частота L2'],
  [227,  11, 'vic_acIn_L3_F',      'i16',  100, 'AC-In частота L3'],
  [227,  12, 'vic_acIn_L1_P',      'i16',   10, 'AC-In L1 мощность'],
  [227,  13, 'vic_acIn_L2_P',      'i16',   10, 'AC-In L2 мощность'],
  [227,  14, 'vic_acIn_L3_P',      'i16',   10, 'AC-In L3 мощность'],
  [227,  15, 'vic_acOut_L1_V',     'u16',   10, 'AC-Out L1 напряжение'],
  [227,  16, 'vic_acOut_L2_V',     'u16',   10, 'AC-Out L2 напряжение'],
  [227,  17, 'vic_acOut_L3_V',     'u16',   10, 'AC-Out L3 напряжение'],
  [227,  18, 'vic_acOut_L1_I',     'i16',   10, 'AC-Out L1 ток'],
  [227,  19, 'vic_acOut_L2_I',     'i16',   10, 'AC-Out L2 ток'],
  [227,  20, 'vic_acOut_L3_I',     'i16',   10, 'AC-Out L3 ток'],
  [227,  21, 'vic_acOut_F',        'i16',  100, 'AC-Out частота'],
  [227,  22, 'vic_acInLimit',      'i16',   10, 'Лимит входного тока'],
  [227,  23, 'vic_acOut_L1_P',     'i16',   10, 'AC-Out L1 мощность'],
  [227,  24, 'vic_acOut_L2_P',     'i16',   10, 'AC-Out L2 мощность'],
  [227,  25, 'vic_acOut_L3_P',     'i16',   10, 'AC-Out L3 мощность'],
  [227,  26, 'vic_bat_V_vebus',    'u16',  100, 'Vbat (vebus)'],
  [227,  27, 'vic_bat_I_vebus',    'i16',   10, 'Ibat (vebus)'],
  [227,  28, 'vic_phaseCount',     'u16',    1, 'Число фаз'],

  /* ==== system, unit 100, 800..851 ==== */
  [100, 806, 'vic_relay1',         'u16',    1, 'CCGX Relay 1'],
  [100, 807, 'vic_relay2',         'u16',    1, 'CCGX Relay 2'],
  [100, 808, 'vic_pvOut_L1_P',     'u16',    1, 'PV AC на выходе L1'],
  [100, 809, 'vic_pvOut_L2_P',     'u16',    1, 'PV AC на выходе L2'],
  [100, 810, 'vic_pvOut_L3_P',     'u16',    1, 'PV AC на выходе L3'],
  [100, 811, 'vic_pvGrid_L1_P',    'u16',    1, 'PV AC на входе L1'],
  [100, 812, 'vic_pvGrid_L2_P',    'u16',    1, 'PV AC на входе L2'],
  [100, 813, 'vic_pvGrid_L3_P',    'u16',    1, 'PV AC на входе L3'],
  [100, 814, 'vic_pvGen_L1_P',     'u16',    1, 'PV AC на генераторе L1'],
  [100, 815, 'vic_pvGen_L2_P',     'u16',    1, 'PV AC на генераторе L2'],
  [100, 816, 'vic_pvGen_L3_P',     'u16',    1, 'PV AC на генераторе L3'],
  [100, 817, 'vic_load_L1_P',      'u16',    1, 'Потребление L1'],
  [100, 818, 'vic_load_L2_P',      'u16',    1, 'Потребление L2'],
  [100, 819, 'vic_load_L3_P',      'u16',    1, 'Потребление L3'],
  [100, 820, 'vic_grid_L1_P',      'i16',    1, 'Сеть L1'],
  [100, 821, 'vic_grid_L2_P',      'i16',    1, 'Сеть L2'],
  [100, 822, 'vic_grid_L3_P',      'i16',    1, 'Сеть L3'],
  [100, 823, 'vic_gen_L1_P',       'i16',    1, 'Генератор L1'],
  [100, 824, 'vic_gen_L2_P',       'i16',    1, 'Генератор L2'],
  [100, 825, 'vic_gen_L3_P',       'i16',    1, 'Генератор L3'],
  [100, 826, 'vic_activeInput',    'i16',    1, 'Активный вход'],
  /* ==== solar, unit 100, 3700..3703 — DC-coupled PV (MPPT) ==== */
  [100, 3700, 'vic_pv_V', 'u16', 10, 'PV DC напряжение'],
  [100, 3701, 'vic_pv_I', 'i16', 10, 'PV DC ток'],
  [100, 3702, 'vic_pv_P', 'u16',  1, 'PV DC мощность (MPPT)'],
  [100, 3703, 'vic_pv_res', 'u16', 1, 'PV резерв'],
  /* ==== battery, unit 225, 256..267 ==== */
  [225, 256, 'vic_bat_P32',        'i32',    1, 'Мощность АКБ (32)'],
  [225, 258, 'vic_bat_P16',        'i16',    1, 'Мощность АКБ (16)'],
  [225, 259, 'vic_bat_V',          'u16',  100, 'Напряжение АКБ'],
  [225, 260, 'vic_bat_Vstarter',   'u16',  100, 'Напряжение старт. АКБ'],
  [225, 261, 'vic_bat_I',          'i16',   10, 'Ток АКБ'],
  [225, 262, 'vic_bat_T',          'i16',   10, 'Температура АКБ'],
  [225, 263, 'vic_bat_midV',       'u16',  100, 'Mid-point V'],
  [225, 264, 'vic_bat_midDev',     'u16',  100, 'Mid-point отклонение'],
  [225, 265, 'vic_bat_consumedAh', 'u16',  -10, 'Потреблено Ah'],
  [225, 266, 'vic_bat_soc',        'u16',   10, 'SOC АКБ'],
];

/* ----------------------------------------------------------
   Соответствие suffix → { unit_id, start_address }
   Синхронизировать с polling.json.
   ---------------------------------------------------------- */
const VIC_SUFFIX_CFG = {
  vebus:   { unit_id: 227, start_address:   3 },
  system:  { unit_id: 100, start_address: 800 },
  battery: { unit_id: 225, start_address: 256 },
  solar:   { unit_id: 100, start_address: 3700 }, // DC-coupled PV (MPPT)
  solar_power: { unit_id: 100, start_address: 3724 }, // AC-coupled PV
};

/* Локальное состояние и утилиты */
const vicState = { data: {} };

function vicLog(msg, level) {
  const el = document.getElementById('tacLog');
  if (!el) return;
  const line = document.createElement('div');
  if (level) line.className = level;
  line.innerHTML = '<span class="t">' + new Date().toLocaleTimeString('ru-RU') +
                   '</span>' + String(msg);
  el.appendChild(line);
  while (el.childNodes.length > 200) el.removeChild(el.firstChild);
  el.scrollTop = el.scrollHeight;
}

/* ----------------------------------------------------------
   Проверка кадра Modbus TCP (PDU без MBAP-заголовка):
     [FC] [byteCount] [data...]
   Ни unit_id, ни CRC тут нет — их снимает/добавляет мост.
   ---------------------------------------------------------- */
function vicCheckFrame(hex) {
  if (!hex || typeof hex !== 'string') return false;
  const clean = hex.replace(/\s+/g, '');
  if (clean.length < 8 || clean.length % 2 !== 0) return false;

  const bytesLen = clean.length / 2;
  const fc = parseInt(clean.substr(0, 2), 16);
  if (fc !== 0x03 && fc !== 0x04) return false;

  const bc = parseInt(clean.substr(2, 2), 16);
  return (2 + bc === bytesLen);
}



function vicDecodeModbusHex(hexResponse, unitId, startAddr) {
  if (!hexResponse || typeof hexResponse !== 'string') return null;
  const clean = hexResponse.replace(/\s+/g, '');
  if (clean.length < 8 || clean.length % 2 !== 0) return null;

  /* --- FC в TCP-кадре идёт СРАЗУ, без unit_id перед ним --- */
  const fc = parseInt(clean.substr(0, 2), 16);

  /* Modbus exception: старший бит FC установлен */
  if (fc & 0x80) {
    const exc = parseInt(clean.substr(2, 2), 16);
    const text = {
      1:'Illegal Function', 2:'Illegal Data Address', 3:'Illegal Data Value',
      4:'Slave Device Failure', 6:'Slave Device Busy',
      11:'Gateway Target Device Failed to Respond'
    }[exc] || ('Exception ' + exc);
    console.warn('[VIC] ⚠️ Modbus exception: unit=' + unitId +
                 ' FC=0x' + fc.toString(16) + ' code=' + exc + ' (' + text + ')');
    return null;
  }
  if (fc !== 0x03 && fc !== 0x04) return null;

  /* --- byteCount — следующий байт --- */
  const byteCount = parseInt(clean.substr(2, 2), 16);
  if (!byteCount) return null;

  /* --- данные начинаются с 4-го hex-символа (позиция 4) --- */
  const dataHex = clean.substr(4, byteCount * 2);
  const bytes = new Uint8Array(byteCount);
  for (let i = 0; i < byteCount; i++) {
    bytes[i] = parseInt(dataHex.substr(i * 2, 2), 16);
  }

  const dv = new DataView(bytes.buffer);
  const out = {};
  const endReg = startAddr + byteCount / 2;

  /* unit_id в ответе нет → фильтруем только по диапазону адресов.
     Диапазоны vebus (3…28), system (800…851), battery (256…267)
     не пересекаются, коллизий не будет. */
  for (const [/*u*/, addr, key, type, scale] of VIC_REGISTER_MAP) {
    const size = (type === 'u32' || type === 'i32') ? 2 : 1;
    if (addr < startAddr || addr + size > endReg) continue;

    const off = addr - startAddr;
    let raw;
    try {
      switch (type) {
        case 'u16': raw = dv.getUint16(off * 2, false); break;
        case 'i16': raw = dv.getInt16 (off * 2, false); break;
        case 'u32': raw = dv.getUint32(off * 2, false); break;
        case 'i32': raw = dv.getInt32 (off * 2, false); break;
        default: continue;
      }
    } catch (e) { continue; }

    out[key] = (scale === -10) ? raw / -10 : raw / (scale || 1);
  }
  return out;
}

/* ----------------------------------------------------------
   Определение unit_id + start_address по suffix из команды
   ---------------------------------------------------------- */
function vicResolveCfg(cmd) {
  if (!cmd) return null;
  const key = String(cmd).trim().toLowerCase();
  return VIC_SUFFIX_CFG[key] || null;
}

/* ----------------------------------------------------------
   Производные значения → window.lastData для hybrid_inverter.html
   ---------------------------------------------------------- */
const _vicDerived = new Set();

function vicMarkDirect(keys) { for (const k of keys) _vicDerived.delete(k); }

function vicDerive() {
  const d = vicState.data;
  const num = v => (typeof v === 'number' && isFinite(v)) ? v : null;
  const sum = (...arr) => {
    const vs = arr.map(num).filter(v => v !== null);
    return vs.length ? vs.reduce((a, b) => a + b, 0) : null;
  };
  const set = (key, val) => {
    if (val === null || val === undefined) return;
    d[key] = val;
    _vicDerived.add(key);
  };

  /* ============================================================
     A. Короткие ключи для SVG-индикаторов в hybrid_inverter.html
     (их читает refreshPowerIndicators в object.js)
     ============================================================ */
  const batP = num(d.vic_bat_P32) ?? num(d.vic_bat_P16);
  if (batP !== null) set('battery', -batP);           // знак развёрнут: +разряд

  const gridP = sum(d.vic_grid_L1_P, d.vic_grid_L2_P, d.vic_grid_L3_P);
  if (gridP !== null) set('grid', gridP);

  const loadP = sum(d.vic_load_L1_P, d.vic_load_L2_P, d.vic_load_L3_P);
  if (loadP !== null) set('load', loadP);

  const solarParts = [
    num(d.vic_pvDc_P),
    num(d.vic_pvOut_L1_P),  num(d.vic_pvOut_L2_P),  num(d.vic_pvOut_L3_P),
    num(d.vic_pvGrid_L1_P), num(d.vic_pvGrid_L2_P), num(d.vic_pvGrid_L3_P),
    num(d.vic_pvGen_L1_P),  num(d.vic_pvGen_L2_P),  num(d.vic_pvGen_L3_P)
  ].filter(v => v !== null);
  set('solar', solarParts.length ? solarParts.reduce((a, b) => a + b, 0) : 0);

  const genP = sum(d.vic_gen_L1_P, d.vic_gen_L2_P, d.vic_gen_L3_P);
  if (genP !== null) set('generator', genP);

  if (d.vic_bat_soc !== undefined) set('battery_soc', d.vic_bat_soc);

  /* ============================================================
     B. Имена для модалок (совпадают с batteryModal/GridSettingsModal/
        loadSettingsModal/SolarModal/GeneratorModal в схеме)
     ============================================================ */

  /* --- Батарея --- */
  set('batterySOC',     num(d.vic_bat_soc));
  set('batteryVoltage', num(d.vic_bat_V));
  set('batteryCurrent', num(d.vic_bat_I));
  set('batteryPower',   batP);          // без смены знака — как отдаёт Victron

  /* --- Сеть (AC-In) --- */
  set('inputVoltageL1', num(d.vic_acIn_L1_V));
  set('inputVoltageL2', num(d.vic_acIn_L2_V));
  set('inputVoltageL3', num(d.vic_acIn_L3_V));
  set('inputCurrentL1', num(d.vic_acIn_L1_I));
  set('inputCurrentL2', num(d.vic_acIn_L2_I));
  set('inputCurrentL3', num(d.vic_acIn_L3_I));
  set('inputPowerL1',   num(d.vic_acIn_L1_P));
  set('inputPowerL2',   num(d.vic_acIn_L2_P));
  set('inputPowerL3',   num(d.vic_acIn_L3_P));
  set('inputPowerTotal', sum(d.vic_acIn_L1_P, d.vic_acIn_L2_P, d.vic_acIn_L3_P));
  set('inputFrequency',  num(d.vic_acIn_L1_F));

  /* --- Нагрузка (AC-Out) --- */
  set('LoadPhaseVoltageA', num(d.vic_acOut_L1_V));
  set('LoadPhaseVoltageB', num(d.vic_acOut_L2_V));
  set('LoadPhaseVoltageC', num(d.vic_acOut_L3_V));
  set('LoadPhaseCurrentA', num(d.vic_acOut_L1_I));
  set('LoadPhaseCurrentB', num(d.vic_acOut_L2_I));
  set('LoadPhaseCurrentC', num(d.vic_acOut_L3_I));
  set('LoadPhasePowerA',   num(d.vic_acOut_L1_P));
  set('LoadPhasePowerB',   num(d.vic_acOut_L2_P));
  set('LoadPhasePowerC',   num(d.vic_acOut_L3_P));
  /* Итог берём из system/Load (consumption), если есть; иначе — из AC-Out */
  set('LoadTotalPower', (loadP !== null)
        ? loadP
        : sum(d.vic_acOut_L1_P, d.vic_acOut_L2_P, d.vic_acOut_L3_P));
  set('LoadFrequency', num(d.vic_acOut_F));

  /* --- Солнце --- */
  set('TotalPVPower', solarParts.length ? solarParts.reduce((a, b) => a + b, 0) : 0);

  /* --- Генератор --- */
  /* Напряжение/ток генератора берём из AC-In только когда активен вход 2 */
  const isGenActive = num(d.vic_activeInput) === 2;
  if (isGenActive) {
    set('GenPhaseVoltageA', num(d.vic_acIn_L1_V));
    set('GenPhaseVoltageB', num(d.vic_acIn_L2_V));
    set('GenPhaseVoltageC', num(d.vic_acIn_L3_V));
    set('GenPhaseCurrentA', num(d.vic_acIn_L1_I));
    set('GenPhaseCurrentB', num(d.vic_acIn_L2_I));
    set('GenPhaseCurrentC', num(d.vic_acIn_L3_I));
  }
  set('GenPhasePowerA', num(d.vic_gen_L1_P));
  set('GenPhasePowerB', num(d.vic_gen_L2_P));
  set('GenPhasePowerC', num(d.vic_gen_L3_P));
  set('GenTotalPower',  genP);
}

/* ============================================================
   WS-цикл
   ============================================================ */
async function startMonitoringVictronCorBridge(objectData) {
  showLoading();
  setDeviceVisibility('Generator', 'hidden');
  vicLog('🚀 Старт мониторинга Victron (COR-Bridge)');

  const corBridgeId = objectData.cor_bridges?.[0];
  if (!corBridgeId) {
    vicLog('❌ У объекта нет cor_bridges', 'err');
    return;
  }

  const deviceId = await resolveCORBridgeDeviceId(corBridgeId);
  if (!deviceId) {
    vicLog('❌ Не удалось получить device_id', 'err');
    return;
  }

  vicLog('🔍 device_id = ' + deviceId);
  startVictronCorBridgeWS(deviceId);
}

function startVictronCorBridgeWS(deviceId) {
  if (vicWS && vicWS.readyState === WebSocket.OPEN) return;

  const wsUrl = buildAuthenticatedWebSocketUrl(
    'wss://dev.monitoring.cor-int.com/dev-modbus/responses?device_id=' +
    encodeURIComponent(deviceId)
  );
  vicLog('🌐 WS: ' + maskWebSocketUrlForLog(wsUrl));

  try { vicWS = new WebSocket(wsUrl); }
  catch (e) {
    vicLog('❌ Ошибка создания WS: ' + e.message, 'err');
    return;
  }

  vicWS.onopen = () => {
    vicLog('✅ WS подключён');
    vicResetOfflineTimer();
  };

  vicWS.onmessage = (event) => {
    vicResetOfflineTimer();
    try {
      const raw = JSON.parse(event.data);
      if (raw?.type === 'connection_established' ||
          raw?.type === 'subscription_changed' ||
          raw?.type === 'ping') return;

      /* Снапшот polling */
      if (raw?.type === 'polling_snapshot' && raw?.data) {
        vicHandlePollingSnapshot(raw.data);
        return;
      }

      /* Снапшот очереди агента */
      if (raw?.type === 'cor_agent_snapshot' && Array.isArray(raw.events)) {
        vicHandleAgentSnapshot(raw.events);
        return;
      }

      /* Прямое сообщение */
      const cmd = raw?.command_name ?? raw?.data?.command_name ?? raw?.cmd;
      const hex = raw?.hex_response ?? raw?.data?.hex_response
               ?? raw?.hex_data     ?? raw?.data?.hex_data;
      if (!cmd || !hex) return;

      vicApplyOne(cmd, hex);
    } catch (e) {
      console.error('[VIC] ❌ Ошибка WS:', e, event.data);
    }
  };

  vicWS.onerror = () => { vicLog('❌ Ошибка WS', 'err'); };

  vicWS.onclose = () => {
    vicLog('🔌 WS закрыт — реконнект', 'warn');
    vicWS = null;
    clearTimeout(vicReconnectTimer);
    vicReconnectTimer = setTimeout(
      () => startVictronCorBridgeWS(deviceId), VIC_RECONNECT_DELAY);
  };
}

function stopVictronWS() {
  clearTimeout(vicReconnectTimer);
  clearTimeout(vicOfflineTimer);
  if (vicWS) { vicWS.close(); vicWS = null; }
}

/* ============================================================
   Обработчики снапшотов и прямых сообщений
   ============================================================ */
function vicHandlePollingSnapshot(data) {
  let applied = 0;
  const directKeys = [];

  for (const [suffix, value] of Object.entries(data)) {
    if (typeof value !== 'string') continue;
    if (!vicCheckFrame(value)) continue;
    const cfg = vicResolveCfg(suffix);
    if (!cfg) continue;
    const decoded = vicDecodeModbusHex(value, cfg.unit_id, cfg.start_address);
    if (decoded) {
      Object.assign(vicState.data, decoded);
      directKeys.push(...Object.keys(decoded));
      applied++;
    }
  }
  if (applied) vicPublish(directKeys, 'polling_snapshot');
}

function vicHandleAgentSnapshot(events) {
  let applied = 0;
  const directKeys = [];
  for (const ev of events) {
    const cmd = ev?.command_name ?? ev?.data?.command_name;
    const hex = ev?.hex_response ?? ev?.data?.hex_response
             ?? ev?.hex_data     ?? ev?.data?.hex_data;
    if (!cmd || !hex || hex === 'No response from RS485') continue;
    if (!vicCheckFrame(hex)) continue; 
    const cfg = vicResolveCfg(cmd);
    if (!cfg) continue;
    const decoded = vicDecodeModbusHex(hex, cfg.unit_id, cfg.start_address);
    if (decoded && Object.keys(decoded).length) {
      Object.assign(vicState.data, decoded);
      directKeys.push(...Object.keys(decoded));
      applied++;
    }
  }
  if (applied) vicPublish(directKeys, 'cor_agent_snapshot (' + applied + '/' + events.length + ')');
}

function vicApplyOne(cmd, hex) {
  if (!vicCheckFrame(hex)) {
    console.warn('[VIC] ❌ Битый кадр:', { cmd, hex });
    return;
  }
  const cfg = vicResolveCfg(cmd);
  if (!cfg) { vicLog('⚠️ Неизвестный suffix: ' + cmd, 'warn'); return; }

  const decoded = vicDecodeModbusHex(hex, cfg.unit_id, cfg.start_address);
  if (!decoded || !Object.keys(decoded).length) {
    vicLog('⚠️ Не распознан ответ cmd=' + cmd, 'warn');
    return;
  }
  Object.assign(vicState.data, decoded);
  vicPublish(Object.keys(decoded), cmd);
}

/* Общая точка выхода: mark direct, derive, publish, render */
function vicPublish(directKeys, sourceTag) {
  vicMarkDirect(directKeys);
  vicDerive();
  Object.assign(window.lastData, vicState.data);
  vicAfterUpdate();
  vicLog('📊 ' + directKeys.length + ' полей (' + sourceTag + ')');
}

/* ============================================================
   UI-обновление после парсинга.
   Дублирует логику Daxtromn: статусы иконок, заливка батареи,
   прогресс-бары, анимация линий + текстовые подписи.
   ============================================================ */
function vicAfterUpdate() {
  const d   = vicState.data;
  const max = window.deviceMaxPower || {};

  /* 1) Общий апдейтер — он разберётся с модалками и чужими data-source */
  try { updateUIByData(window.lastData); } catch (e) { /* noop */ }

  /* 2) Статусы иконок — раз данные пришли, всё "normal" */
  try {
    setIconStatus("Grid",     "normal");
    setIconStatus("Battery",  "normal");
    setIconStatus("Inverter", "normal");
    setIconStatus("Load",     "normal");
    setIconStatus("Solar",    "normal");
  } catch (e) { /* noop */ }

  /* 3) Батарея: заливка SOC */
  if (typeof d.battery_soc === "number") {
    try { updateBatteryFill(d.battery_soc); } catch (e) { /* noop */ }
  }

  /* 4) Прогресс-бары + линии + подписи.
        Хелпер: обновляет indicator через updatePowerByName,
        а label — через formatPowerLabel (как в Daxtromn). */
  const paint = (entity, value, labelId, labelKind) => {
    if (typeof value !== "number" || !isFinite(value)) return;
    const maxPower = max[entity];
    if (!maxPower) return;

    try {
      updatePowerByName(entity, PowerToIndicator(value, maxPower));
    } catch (e) { /* noop */ }

    const el = document.getElementById(labelId);
    if (!el) return;

    let txt;
    try { txt = formatPowerLabel(value, labelKind); }
    catch (e) { txt = undefined; }

    if (txt !== undefined) el.textContent = txt;
  };

  paint("Battery",   d.battery,   "batteryFlowLabel",   "battery");
  paint("Grid",      d.grid,      "networkFlowLabel",   "grid");
  paint("Load",      d.load,      "loadIndicatorLabel", "load");
  paint("Solar",     d.solar,     "solarPowerLabel",    "solar");
  paint("Generator", d.generator, "generatorFlowLabel", "generator");

  /* 5) Убрать loading при первом пакете */
  if (!vicFirstDataReceived) {
    vicFirstDataReceived = true;
    try { hideLoading(); } catch (e) { /* noop */ }
  }
  vicFails = 0;
}

/* ============================================================
   Оффлайн-таймер
   ============================================================ */
function vicResetOfflineTimer() {
  if (vicOfflineTimer) clearTimeout(vicOfflineTimer);
  vicOfflineTimer = setTimeout(() => {
    vicLog('⚠️ Нет данных > ' + (VIC_OFFLINE_DELAY / 1000) + ' сек', 'warn');
    if (vicFirstDataReceived) {
      try { setOfflineState(); } catch (e) { /* noop */ }
    }
  }, VIC_OFFLINE_DELAY);
}

/* ============================================================
   Экспорт
   ============================================================ */
window.startMonitoringVictronCorBridge = startMonitoringVictronCorBridge;
window.stopVictronWS = stopVictronWS;
window.VictronVIC = {
  state: vicState,
  decode: vicDecodeModbusHex,
  stop: stopVictronWS
};




