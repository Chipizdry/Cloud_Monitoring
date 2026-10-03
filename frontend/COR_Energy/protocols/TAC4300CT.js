/* ============================================================
   TAC4300CT — Modbus-протокол мониторинга
   Регистрация в object.js:  MonitoringTAC4300CTCorBridge
   Использует общие хелперы COR-Energy:
     showLoading/hideLoading, setOfflineState, setErrorText,
     registerFail/resetFails, setDeviceVisibility,
     resolveCORBridgeDeviceId, updateUIByData
   ============================================================ */

let tacWS = null;
let tacOfflineTimer = null;
let tacReconnectTimer = null;
let tacFails = 0;
let tacFirstDataReceived = false;

const TAC_FAIL_LIMIT       = 3;
const TAC_OFFLINE_DELAY    = 15000;
const TAC_RECONNECT_DELAY  = 3000;


/* ============================================================
   Карта регистров TAC4300CT (одна на всё).
   Колонки: [addr, key, type, scale, comment]
     type  : 'f32' | 'u32' | 'i32' | 'i64'
     scale : делить raw на это значение (f32 → 1, u32/i32 → 100, i64 → 1000)
   ============================================================ */
const TAC_REGISTER_MAP = [
  /* ---- Мгновенные параметры (FC 04, Float32) ---- */
  [0x0000, 'tac_v1',       'f32', 1,   'Фазное напряжение L1'],
  [0x0002, 'tac_v2',       'f32', 1,   'Фазное напряжение L2'],
  [0x0004, 'tac_v3',       'f32', 1,   'Фазное напряжение L3'],
  [0x0006, 'tac_i1',       'f32', 1,   'Ток L1'],
  [0x0008, 'tac_i2',       'f32', 1,   'Ток L2'],
  [0x000A, 'tac_i3',       'f32', 1,   'Ток L3'],
  [0x000C, 'tac_p1',       'f32', 1,   'Активная мощность L1'],
  [0x000E, 'tac_p2',       'f32', 1,   'Активная мощность L2'],
  [0x0010, 'tac_p3',       'f32', 1,   'Активная мощность L3'],
  [0x0012, 'tac_q1',       'f32', 1,   'Реактивная мощность L1'],
  [0x0014, 'tac_q2',       'f32', 1,   'Реактивная мощность L2'],
  [0x0016, 'tac_q3',       'f32', 1,   'Реактивная мощность L3'],
  [0x0018, 'tac_s1',       'f32', 1,   'Полная мощность L1'],
  [0x001A, 'tac_s2',       'f32', 1,   'Полная мощность L2'],
  [0x001C, 'tac_s3',       'f32', 1,   'Полная мощность L3'],
  [0x001E, 'tac_pf1',      'f32', 1,   'PF L1'],
  [0x0020, 'tac_pf2',      'f32', 1,   'PF L2'],
  [0x0022, 'tac_pf3',      'f32', 1,   'PF L3'],
  [0x0024, 'tac_ang1',     'f32', 1,   'Угол L1'],
  [0x0026, 'tac_ang2',     'f32', 1,   'Угол L2'],
  [0x0028, 'tac_ang3',     'f32', 1,   'Угол L3'],
  [0x002A, 'tac_u12',      'f32', 1,   'U L1–L2'],
  [0x002C, 'tac_u23',      'f32', 1,   'U L2–L3'],
  [0x002E, 'tac_u31',      'f32', 1,   'U L3–L1'],
  [0x0030, 'tac_freq',     'f32', 1,   'Частота'],
  [0x0032, 'tac_pTotal',   'f32', 1,   'Σ активная'],
  [0x0034, 'tac_qTotal',   'f32', 1,   'Σ реактивная'],
  [0x0036, 'tac_sTotal',   'f32', 1,   'Σ полная'],
  [0x0038, 'tac_pfTotal',  'f32', 1,   'Σ PF'],
  [0x003A, 'tac_angTotal', 'f32', 1,   'Σ угол'],
  [0x003C, 'tac_iSum',     'f32', 1,   'Σ токов'],
  [0x003E, 'tac_vAvgPh',   'f32', 1,   'Ср. фазное U'],
  [0x0040, 'tac_vAvgLine', 'f32', 1,   'Ср. линейное U'],
  [0x0042, 'tac_iAvg',     'f32', 1,   'Ср. ток'],
  [0x0044, 'tac_iN',       'f32', 1,   'Ток нейтрали'],
  [0x004E, 'tac_load1',    'f32', 1,   'Характер нагрузки L1'],
  [0x0050, 'tac_load2',    'f32', 1,   'Характер нагрузки L2'],
  [0x0052, 'tac_load3',    'f32', 1,   'Характер нагрузки L3'],


  /* ---- Энергии Float32 (FC 04, блок 0x0500) — основной источник ---- */
  [0x0500, 'tac_eActImpF',     'f32', 1, 'Σ импорт активной (float)'],
  [0x0502, 'tac_eActExpF',     'f32', 1, 'Σ экспорт активной (float)'],
  [0x0504, 'tac_eActTotF',     'f32', 1, 'Σ активная (float)'],
  // 0x0506 — резерв, не мапим
  [0x0508, 'tac_eReactImpF',   'f32', 1, 'Σ импорт реактивной (float)'],
  [0x050A, 'tac_eReactExpF',   'f32', 1, 'Σ экспорт реактивной (float)'],
  [0x050C, 'tac_eReactTotF',   'f32', 1, 'Σ реактивная (float)'],
  // 0x050E — резерв
  [0x0510, 'tac_eAppTotF',     'f32', 1, 'Σ полная (float)'],
  // 0x0512 — резерв

  [0x0514, 'tac_eActL1ImpF',   'f32', 1, 'L1 актив. импорт (float)'],
  [0x0516, 'tac_eActL2ImpF',   'f32', 1, 'L2 актив. импорт (float)'],
  [0x0518, 'tac_eActL3ImpF',   'f32', 1, 'L3 актив. импорт (float)'],
  [0x051A, 'tac_eActL1ExpF',   'f32', 1, 'L1 актив. экспорт (float)'],
  [0x051C, 'tac_eActL2ExpF',   'f32', 1, 'L2 актив. экспорт (float)'],
  [0x051E, 'tac_eActL3ExpF',   'f32', 1, 'L3 актив. экспорт (float)'],
  [0x0520, 'tac_eActL1TotF',   'f32', 1, 'L1 актив. сумма (float)'],
  [0x0522, 'tac_eActL2TotF',   'f32', 1, 'L2 актив. сумма (float)'],
  [0x0524, 'tac_eActL3TotF',   'f32', 1, 'L3 актив. сумма (float)'],

  /* ---- Энергии float32 (FC 04, блок 0x0526) — fallback ---- */
  [0x0526, 'tac_eReactL1ImpF', 'f32', 1, 'L1 реакт. импорт (float)'],
  [0x0528, 'tac_eReactL2ImpF', 'f32', 1, 'L2 реакт. импорт (float)'],
  [0x052A, 'tac_eReactL3ImpF', 'f32', 1, 'L3 реакт. импорт (float)'],
  [0x052C, 'tac_eReactL1ExpF', 'f32', 1, 'L1 реакт. экспорт (float)'],
  [0x052E, 'tac_eReactL2ExpF', 'f32', 1, 'L2 реакт. экспорт (float)'],
  [0x0530, 'tac_eReactL3ExpF', 'f32', 1, 'L3 реакт. экспорт (float)'],
  [0x0532, 'tac_eReactL1TotF', 'f32', 1, 'L1 реакт. сумма (float)'],
  [0x0534, 'tac_eReactL2TotF', 'f32', 1, 'L2 реакт. сумма (float)'],
  [0x0536, 'tac_eReactL3TotF', 'f32', 1, 'L3 реакт. сумма (float)'],

  /* ---- Энергии int32 (FC 04, блок 0x0400) — основной источник ---- */
  [0x0400, 'tac_eActImp',      'u32', 100, 'Σ импорт активной'],
  [0x0402, 'tac_eActExp',      'u32', 100, 'Σ экспорт активной'],
  [0x0404, 'tac_eActTot',      'i32', 100, 'Σ активная'],
  [0x0408, 'tac_eReactImp',    'u32', 100, 'Σ импорт реактивной'],
  [0x040A, 'tac_eReactExp',    'u32', 100, 'Σ экспорт реактивной'],
  [0x040C, 'tac_eReactTot',    'i32', 100, 'Σ реактивная'],
  [0x0410, 'tac_eAppTot',      'u32', 100, 'Σ полная'],

  [0x0414, 'tac_eActL1Imp',    'u32', 100, 'L1 актив. импорт'],
  [0x0416, 'tac_eActL2Imp',    'u32', 100, 'L2 актив. импорт'],
  [0x0418, 'tac_eActL3Imp',    'u32', 100, 'L3 актив. импорт'],
  [0x041A, 'tac_eActL1Exp',    'u32', 100, 'L1 актив. экспорт'],
  [0x041C, 'tac_eActL2Exp',    'u32', 100, 'L2 актив. экспорт'],
  [0x041E, 'tac_eActL3Exp',    'u32', 100, 'L3 актив. экспорт'],
  [0x0420, 'tac_eActL1Tot',    'i32', 100, 'L1 актив. сумма'],
  [0x0422, 'tac_eActL2Tot',    'i32', 100, 'L2 актив. сумма'],
  [0x0424, 'tac_eActL3Tot',    'i32', 100, 'L3 актив. сумма'],

  [0x0426, 'tac_eReactL1Imp',  'u32', 100, 'L1 реакт. импорт'],
  [0x0428, 'tac_eReactL2Imp',  'u32', 100, 'L2 реакт. импорт'],
  [0x042A, 'tac_eReactL3Imp',  'u32', 100, 'L3 реакт. импорт'],
  [0x042C, 'tac_eReactL1Exp',  'u32', 100, 'L1 реакт. экспорт'],
  [0x042E, 'tac_eReactL2Exp',  'u32', 100, 'L2 реакт. экспорт'],
  [0x0430, 'tac_eReactL3Exp',  'u32', 100, 'L3 реакт. экспорт'],
  [0x0432, 'tac_eReactL1Tot',  'i32', 100, 'L1 реакт. сумма'],
  [0x0434, 'tac_eReactL2Tot',  'i32', 100, 'L2 реакт. сумма'],
  [0x0436, 'tac_eReactL3Tot',  'i32', 100, 'L3 реакт. сумма'],

  /* ---- Энергии int64 (FC 03, блок 0x1D00) — не поддерживается ----
     Оставлено на случай обновления прошивки счётчика.
     Все ключи с суффиксом "64", чтобы не конфликтовать с int32-версиями.
     Пока счётчик отвечает exception 02 на 0x1D00 — данные не придут. */
  [0x1D00, 'tac_eActImp64',       'i64', 1000, 'Σ импорт активной (64)'],
  [0x1D04, 'tac_eActExp64',       'i64', 1000, 'Σ экспорт активной (64)'],
  [0x1D08, 'tac_eActTot64',       'i64', 1000, 'Σ активная (64)'],
  [0x1D10, 'tac_eReactImp64',     'i64', 1000, 'Σ импорт реактивной (64)'],
  [0x1D14, 'tac_eReactExp64',     'i64', 1000, 'Σ экспорт реактивной (64)'],
  [0x1D18, 'tac_eReactTot64',     'i64', 1000, 'Σ реактивная (64)'],
  [0x1D20, 'tac_eAppTot64',       'i64', 1000, 'Σ полная (64)'],

  [0x1D24, 'tac_eActL1Imp64',     'i64', 1000, 'L1 актив. импорт (64)'],
  [0x1D28, 'tac_eActL2Imp64',     'i64', 1000, 'L2 актив. импорт (64)'],
  [0x1D2C, 'tac_eActL3Imp64',     'i64', 1000, 'L3 актив. импорт (64)'],
  [0x1D30, 'tac_eActL1Exp64',     'i64', 1000, 'L1 актив. экспорт (64)'],
  [0x1D34, 'tac_eActL2Exp64',     'i64', 1000, 'L2 актив. экспорт (64)'],
  [0x1D38, 'tac_eActL3Exp64',     'i64', 1000, 'L3 актив. экспорт (64)'],
  [0x1D3C, 'tac_eActL1Tot64',     'i64', 1000, 'L1 актив. сумма (64)'],
  [0x1D40, 'tac_eActL2Tot64',     'i64', 1000, 'L2 актив. сумма (64)'],
  [0x1D44, 'tac_eActL3Tot64',     'i64', 1000, 'L3 актив. сумма (64)'],

  [0x1D48, 'tac_eReactL1Imp64',   'i64', 1000, 'L1 реакт. импорт (64)'],
  [0x1D4C, 'tac_eReactL2Imp64',   'i64', 1000, 'L2 реакт. импорт (64)'],
  [0x1D50, 'tac_eReactL3Imp64',   'i64', 1000, 'L3 реакт. импорт (64)'],
  [0x1D54, 'tac_eReactL1Exp64',   'i64', 1000, 'L1 реакт. экспорт (64)'],
  [0x1D58, 'tac_eReactL2Exp64',   'i64', 1000, 'L2 реакт. экспорт (64)'],
  [0x1D5C, 'tac_eReactL3Exp64',   'i64', 1000, 'L3 реакт. экспорт (64)'],
  [0x1D60, 'tac_eReactL1Tot64',   'i64', 1000, 'L1 реакт. сумма (64)'],
  [0x1D64, 'tac_eReactL2Tot64',   'i64', 1000, 'L2 реакт. сумма (64)'],
  [0x1D68, 'tac_eReactL3Tot64',   'i64', 1000, 'L3 реакт. сумма (64)'],
];


const TAC_LOAD_NATURE = {
  0: 'Нет нагрузки',
  1: 'Активная (R)',
  2: 'Индуктивная (L)',
  3: 'Ёмкостная (C)',
  4: 'Нет нагрузки'
};

const TAC_QUADRANTS = {
  1: { name: 'Квадрант I',   desc: 'Импорт P · Импорт Q — индуктивная нагрузка' },
  2: { name: 'Квадрант II',  desc: 'Экспорт P · Импорт Q' },
  3: { name: 'Квадрант III', desc: 'Экспорт P · Экспорт Q' },
  4: { name: 'Квадрант IV',  desc: 'Импорт P · Экспорт Q — ёмкостная нагрузка' }
};

/* Локальное состояние TAC-метра. Данные также уходят в window.lastData. */
const tacState = { data: {} };

/* ============================================================
   УТИЛИТЫ
   ============================================================ */
function tacFmt(v, dec) {
  if (v === null || v === undefined || v === '') return '—';
  if (typeof v !== 'number' || !isFinite(v)) return String(v);
  if (dec === undefined || dec === null) {
    const a = Math.abs(v);
    dec = a >= 1000 ? 1 : a >= 100 ? 2 : 3;
  }
  return v.toLocaleString('ru-RU', {
    minimumFractionDigits: dec,
    maximumFractionDigits: dec
  });
}

function tacQuadrantOf(p, q) {
  if (p >= 0 && q >= 0) return 1;
  if (p <  0 && q >= 0) return 2;
  if (p <  0 && q <  0) return 3;
  return 4;
}

function tacNiceScale(max) {
  const steps = [0.01,0.025,0.05,0.1,0.25,0.5,1,2.5,5,10,25,50,100,250,500,
                 1000,2500,5000,10000,25000,50000,100000];
  for (const s of steps) if (max <= s) return s;
  return Math.pow(10, Math.ceil(Math.log10(max)));
}

/* ----------------------------------------------------------
   Лог в панели «Связь»
   ---------------------------------------------------------- */
function tacLog(msg, level) {
  const el = document.getElementById('tacLog');
  if (!el) return;
  const line = document.createElement('div');
  if (level) line.className = level;
  line.innerHTML = '<span class="t">' + new Date().toLocaleTimeString('ru-RU') + '</span>' + String(msg);
  el.appendChild(line);
  while (el.childNodes.length > 200) el.removeChild(el.firstChild);
  el.scrollTop = el.scrollHeight;
}

/* ----------------------------------------------------------
   Статус-пилюля соединения
   ---------------------------------------------------------- */
function tacSetConn(stateName, text) {
  const pill = document.getElementById('tacConnPill');
  const txt  = document.getElementById('tacConnText');
  if (pill) pill.dataset.state = stateName;
  if (txt)  txt.textContent = text;
}



/* ============================================================
   Снапшот «polling_snapshot»
   data может быть:
     - { phases:"01043C...", angles:"01043C...", ... }  (hex-строки)
     - { tac_v1:230.2, tac_i1:12.5, ... }               (готовые числа)
   ============================================================ */
function tacHandlePollingSnapshot(data) {
    let applied = 0;
    const directKeys = [];             

    for (const [key, value] of Object.entries(data)) {
        if (typeof value === 'string' && /^[0-9A-Fa-f]{10,}$/.test(value.replace(/\s/g, ''))) {
            if (!tacCheckCRC16(value)) continue;
            const startAddr = tacResolveStartAddr(key);
            if (startAddr === null) continue;
            const decoded = tacDecodeModbusHex(value, startAddr);
            if (decoded) {
                Object.assign(tacState.data, decoded);
                directKeys.push(...Object.keys(decoded));  
                applied++;
            }
        } else if (typeof value === 'number' && key.startsWith('tac_')) {
            tacState.data[key] = value;
            directKeys.push(key);                          
            applied++;
        }
    }

    if (applied) {
        tacMarkDirect(directKeys);                         
        tacDeriveTotals(tacState.data);
        Object.assign(window.lastData, tacState.data);
        tacAfterUpdate();
        tacLog('📦 polling_snapshot (' + applied + ' полей)');
    } else {
        console.warn('[TAC] polling_snapshot — нет распознанных полей:', Object.keys(data));
    }
}

/* ============================================================
   Снапшот очереди событий (array of {command_name, hex_response})
   ============================================================ */
function tacHandleAgentSnapshot(events) {
    let applied = 0;
    const directKeys = [];                

    for (const ev of events) {
        const cmd = ev?.command_name ?? ev?.data?.command_name;
        const hex = ev?.hex_response ?? ev?.data?.hex_response
                 ?? ev?.hex_data     ?? ev?.data?.hex_data;

        if (!cmd || !hex || hex === 'No response from RS485') continue;
        if (!tacCheckCRC16(hex)) continue;

        const startAddr = tacResolveStartAddr(cmd);
        if (startAddr === null) continue;

        const decoded = tacDecodeModbusHex(hex, startAddr);
        if (decoded && Object.keys(decoded).length) {
            Object.assign(tacState.data, decoded);
            directKeys.push(...Object.keys(decoded));   
            applied++;
        }
    }

    if (applied) {
        tacMarkDirect(directKeys);                  
        tacDeriveTotals(tacState.data);
        Object.assign(window.lastData, tacState.data);
        tacAfterUpdate();
        tacLog('📦 cor_agent_snapshot (' + applied + ' из ' + events.length + ')');
    }
}

/* CRC16 Modbus RTU — контроль целостности.
   Возвращает false, если строку нельзя распарсить. */
function tacCheckCRC16(hex) {
    if (!hex || typeof hex !== 'string') return false;
    const clean = hex.replace(/\s+/g, '');
    if (clean.length < 8 || clean.length % 2 !== 0) return false;

    const bytes = [];
    for (let i = 0; i < clean.length; i += 2) {
        const b = parseInt(clean.substr(i, 2), 16);
        if (isNaN(b)) return false;
        bytes.push(b);
    }

    const data = bytes.slice(0, -2);
    const crcReceived = (bytes[bytes.length - 1] << 8) | bytes[bytes.length - 2];

    let crc = 0xFFFF;
    for (const b of data) {
        crc ^= b;
        for (let i = 0; i < 8; i++) {
            crc = (crc & 1) ? (crc >> 1) ^ 0xA001 : (crc >> 1);
        }
    }
    return crc === crcReceived;
}


/* ============================================================
   ПЕРЕКЛЮЧЕНИЕ ПОД-ВКЛАДОК (вызывается из onclick в layout)
   ============================================================ */
function tacSwitchTab(ev, tab) {
  const root = ev.currentTarget.closest('.tac-meter');
  if (!root) return;
  root.querySelectorAll('.tac-tab').forEach(b => b.classList.remove('active'));
  root.querySelectorAll('.tac-panel').forEach(p => p.classList.remove('active'));
  ev.currentTarget.classList.add('active');
  const panel = root.querySelector('#tac-panel-' + tab);
  if (panel) panel.classList.add('active');
}
window.tacSwitchTab = tacSwitchTab;

/* ============================================================
   ДЕКОДЕР MODBUS
   hex_response — строка байт ответа Modbus RTU (без CR):
     [addr] [FC] [byteCount] [data...] [crcLo] [crcHi]
   Возвращает объект { data-source: value, ... }
   ============================================================ */
function tacDecodeModbusHex(hexResponse, startAddr) {
  startAddr = startAddr || 0;
  if (!hexResponse || typeof hexResponse !== 'string') return null;

  const clean = hexResponse.replace(/\s+/g, '');
  if (clean.length < 10 || clean.length % 2 !== 0) return null;

  const fc = parseInt(clean.substr(2, 2), 16);

  if (fc & 0x80) {
    const exc = parseInt(clean.substr(4, 2), 16);
    const text = {
      1:'Illegal Function', 2:'Illegal Data Address', 3:'Illegal Data Value',
      4:'Slave Device Failure', 6:'Slave Device Busy',
      11:'Gateway Target Device Failed to Respond'
    }[exc] || ('Exception ' + exc);
    console.warn('[TAC] ⚠️ Modbus exception: FC=0x' + fc.toString(16) +
                 ' code=' + exc + ' (' + text + ')');
    return null;
  }

  if (fc !== 0x03 && fc !== 0x04) return null;

  const byteCount = parseInt(clean.substr(4, 2), 16);
  if (!byteCount) return null;

  const dataHex = clean.substr(6, byteCount * 2);
  const bytes = new Uint8Array(byteCount);
  for (let i = 0; i < byteCount; i++) {
    bytes[i] = parseInt(dataHex.substr(i * 2, 2), 16);
  }

  const dv = new DataView(bytes.buffer);
  const out = {};
  const startReg = startAddr;
  const endReg   = startAddr + byteCount / 2;

  for (const [addr, key, type, scale] of TAC_REGISTER_MAP) {
    const sizeRegs = (type === 'i64') ? 4 : 2;          // в регистрах
    if (addr < startReg || addr + sizeRegs > endReg) continue;

    const off = (addr - startReg) * 2;                  // ✅ байты, не регистры
    let raw;
    try {
      switch (type) {
        case 'f32': raw = dv.getFloat32(off, false); break;
        case 'u32': raw = dv.getUint32 (off, false); break;
        case 'i32': raw = dv.getInt32  (off, false); break;
        case 'i64': raw = Number(dv.getBigInt64(off, false)); break;
        default: continue;
      }
    } catch (e) {
      console.warn('[TAC] decode fail @0x' + addr.toString(16), e);
      continue;
    }
    out[key] = raw / (scale || 1);
  }

  return out;
}

/* ============================================================
   ОФФЛАЙН-ТАЙМЕР
   ============================================================ */
function tacResetOfflineTimer() {
  if (tacOfflineTimer) clearTimeout(tacOfflineTimer);
  tacOfflineTimer = setTimeout(() => {
    tacLog('⚠️ Нет данных > ' + (TAC_OFFLINE_DELAY / 1000) + ' сек', 'warn');
    tacSetConn('offline', 'Нет связи');

    // Если данных ещё ни разу не было — не «убиваем» UI,
    // просто показываем, что связь не установлена.
    if (tacFirstDataReceived) {
      setOfflineState();
    } else {
      console.warn('[TAC] Оффлайн до первых данных — UI не трогаем');
    }
  }, TAC_OFFLINE_DELAY);
}

/* ============================================================
   FAIL/RESET
   ============================================================ */
function tacRegisterFail(message) {
  tacFails++;
  tacLog('❌ ' + message + ' (' + tacFails + '/' + TAC_FAIL_LIMIT + ')', 'err');
  if (tacFails >= TAC_FAIL_LIMIT) {
    setDeviceVisibility('ErrorIcon', 'visible');
    setErrorText(message);
    setOfflineState();
  }
}

function tacResetFails() {
  tacFails = 0;
  setDeviceVisibility('ErrorIcon', 'hidden');
}

/* ============================================================
   ГЛАВНАЯ ТОЧКА ВХОДА (регистрируется в object.js)
   ============================================================ */
async function startMonitoringTAC4300CTCorBridge(objectData) {
  showLoading();
  setDeviceVisibility('Generator', 'hidden');
  tacSetConn('offline', 'Подключение…');
  tacLog('🚀 Старт мониторинга TAC4300CT (COR-Bridge)');

  const corBridgeId = objectData.cor_bridges?.[0];
  if (!corBridgeId) {
    tacLog('❌ У объекта нет cor_bridges', 'err');
    tacRegisterFail('Нет cor_bridges у объекта');
    return;
  }

  const deviceId = await resolveCORBridgeDeviceId(corBridgeId);
  if (!deviceId) {
    tacLog('❌ Не удалось получить device_id', 'err');
    tacRegisterFail('Не удалось получить device_id');
    return;
  }

  tacLog('🔍 device_id = ' + deviceId);
  startTAC4300CTCorBridgeWS(deviceId);
}

/* Заглушка, если у пользователя в будущем будет Modbus-over-TCP */
async function startMonitoringTAC4300CTModbusOverTcp(objectData) {
  tacLog('⚠️ Modbus over TCP пока не реализован', 'warn');
}

/* ============================================================
   WEBSOCKET COR-BRIDGE
   ============================================================ */
function startTAC4300CTCorBridgeWS(deviceId) {
  if (tacWS && tacWS.readyState === WebSocket.OPEN) {
    tacLog('⚠️ WS уже подключён', 'warn');
    return;
  }

  const wsUrl = buildAuthenticatedWebSocketUrl(
    'wss://dev.monitoring.cor-int.com/dev-modbus/responses?device_id=' +
    encodeURIComponent(deviceId)
  );
  tacLog('🌐 WS: ' + maskWebSocketUrlForLog(wsUrl));

  try {
    tacWS = new WebSocket(wsUrl);
  } catch (e) {
    tacLog('❌ Ошибка создания WS: ' + e.message, 'err');
    tacSetConn('error', 'Ошибка');
    return;
  }

   tacWS.onopen = () => {
    console.log('[TAC] ✅ WS open:', wsUrl);
    tacLog('✅ WS подключён: ' + maskWebSocketUrlForLog(wsUrl));
    tacSetConn('online', 'Онлайн');
    tacResetOfflineTimer();
  };

  tacWS.onmessage = (event) => {
    tacResetOfflineTimer();
    try {
        const raw = JSON.parse(event.data);

        // 1) Полный лог — как у Deye
        console.log('[TAC] 📩 RAW WS:', raw);

        // 2) Служебные сообщения — сразу мимо
        if (raw?.type === 'connection_established' ||
            raw?.type === 'subscription_changed'  ||
            raw?.type === 'ping') {
            return;
        }

        // 3) Снапшот «текущее состояние всех регистров»
        //    Формат: { type:"polling_snapshot", data:{ phases:"01043C...", angles:"01043C..." } }
        if (raw?.type === 'polling_snapshot' && raw?.data) {
            tacHandlePollingSnapshot(raw.data);
            return;
        }

        // 4) Снапшот очереди событий
        if (raw?.type === 'cor_agent_snapshot' && Array.isArray(raw.events)) {
            tacHandleAgentSnapshot(raw.events);
            return;
        }

        // 5) Прямое сообщение — один ответ Modbus
        const cmd = raw?.command_name ?? raw?.data?.command_name;
        const hex = raw?.hex_response ?? raw?.data?.hex_response
                 ?? raw?.hex_data     ?? raw?.data?.hex_data;

        if (!cmd || !hex) {
            console.warn('[TAC] ⚠️ Нет cmd/hex в сообщении:', raw);
            return;
        }



          console.log('[TAC] 📥 cmd=' + cmd
          + '  start=' + tacResolveStartAddr(cmd)
          + '  hex=' + (typeof hex === 'string' ? hex.slice(0, 20) + '…' : hex));



        if (hex === 'No response from RS485') {
            tacRegisterFail('Нет связи с TAC4300CT (RS485)');
            return;
        }

        if (!tacCheckCRC16(hex)) {
            console.warn('[TAC] ❌ CRC не прошла:', { cmd, hex });
            return;
        }

        const startAddr = tacResolveStartAddr(cmd);
        if (startAddr === null) {
            tacLog('⚠️ Неизвестный suffix: ' + cmd, 'warn');
            return;
        }

        const decoded = tacDecodeModbusHex(hex, startAddr);
        if (!decoded || !Object.keys(decoded).length) {
            tacLog('⚠️ Не распознан ответ cmd=' + cmd, 'warn');
            return;
        }

        Object.assign(tacState.data, decoded);
          tacMarkDirect(Object.keys(decoded));       
          tacDeriveTotals(tacState.data);
          Object.assign(window.lastData, tacState.data);
          tacAfterUpdate();
          tacLog('📊 ' + Object.keys(decoded).length + ' полей (cmd=' + cmd + ')');

    } catch (e) {
        console.error('[TAC] ❌ Ошибка обработки WS:', e, event.data);
    }
};

  tacWS.onerror = (e) => {
    console.error('[TAC] ❌ WS error:', e);
    tacLog('❌ Ошибка WS', 'err');
    tacSetConn('error', 'Ошибка связи');
  };

  tacWS.onclose = (e) => {
    console.warn('[TAC] 🔌 WS closed:', { code: e.code, reason: e.reason, wasClean: e.wasClean });
    tacLog('🔌 WS закрыт (code=' + e.code + ')', 'warn');
    tacWS = null;
    tacSetConn('offline', 'Нет связи');
    clearTimeout(tacReconnectTimer);
    tacReconnectTimer = setTimeout(() => startTAC4300CTCorBridgeWS(deviceId), TAC_RECONNECT_DELAY);
  };
}

function stopTAC4300CTWS() {
  clearTimeout(tacReconnectTimer);
  clearTimeout(tacOfflineTimer);
  if (tacWS) { tacWS.close(); tacWS = null; }
  tacLog('🛑 WS остановлен', 'warn');
}

/* ============================================================
   ОБРАБОТКА СООБЩЕНИЙ WS
   Формат совпадает с Axioma: { cmd, hex_response } или
   снапшот { type: "cor_agent_snapshot", events: [...] }.
   ============================================================ */
function tacHandleMessage(rawData) {
    // Оставлено для обратной совместимости — вызывается только если
    // кто-то (внешний код) передаёт строку напрямую.
    try {
        const raw = JSON.parse(rawData);
        if (raw?.type === 'polling_snapshot' && raw?.data) return tacHandlePollingSnapshot(raw.data);
        if (raw?.type === 'cor_agent_snapshot' && Array.isArray(raw.events)) return tacHandleAgentSnapshot(raw.events);
    } catch (e) { /* noop */ }
}

/* Извлекает cmd + hex, декодирует, кладёт в tacState */
function tacApplyPayload(payload) {
   const nested = payload?.data && typeof payload.data === 'object' ? payload.data : payload;

  // suffix ("phases", "angles", "aggregates", "load") приходит в command_name.
  // "cmd" = "modbus_read" — generic, для резолвера бесполезен.
  const cmd = nested?.command_name ?? payload?.command_name
           ?? nested?.cmd          ?? payload?.cmd
           ?? null;

  const hex = nested?.hex_response ?? payload?.hex_response
           ?? nested?.hex_data     ?? payload?.hex_data
           ?? null;

  console.log('[TAC] payload:', {
    cmd,
    hex_len: typeof hex === 'string' ? hex.length : null,
    hex_head: typeof hex === 'string' ? hex.slice(0, 24) : hex
  });

  if (!cmd || !hex) return false;

  // Нет ответа от RS485
  if (hex === 'No response from RS485') {
    tacRegisterFail('Нет связи с TAC4300CT (RS485)');
    return false;
  }

  // NAK
  const ascii = tacHexToAscii(hex);
  if (ascii.includes('NAK')) {
    tacRegisterFail('Инвертор вернул NAK');
    return false;
  }

   // Стартовый адрес — либо явно в cmd (hex), либо по suffix из polling.json
  const startAddr = tacResolveStartAddr(cmd);

  if (startAddr === null) {
    console.warn('[TAC] ⚠️ Неизвестный suffix:', cmd);
    tacLog('⚠️ Неизвестный suffix: ' + cmd, 'warn');
    return false;
  }

  console.log('[TAC] resolve:', cmd, '→ 0x' + startAddr.toString(16).toUpperCase());

  const decoded = tacDecodeModbusHex(hex, startAddr);
  console.log('[TAC] decoded keys:', decoded ? Object.keys(decoded).length : 0);
  if (!decoded || !Object.keys(decoded).length) {
    tacLog('⚠️ Не распознан ответ cmd=' + cmd, 'warn');
    return false;
  }

  Object.assign(tacState.data, decoded);
  tacMarkDirect(Object.keys(decoded)); 
  tacDeriveTotals(tacState.data);

  // Зеркалим в window.lastData для совместимости с остальной системой
  Object.assign(window.lastData, tacState.data);

  tacLog('📊 ' + Object.keys(decoded).length + ' полей обновлено (cmd=' + cmd + ')');
  return true;
}

/* ----------------------------------------------------------
   Соответствие suffix → start_address.
   ОБЯЗАТЕЛЬНО синхронизировать с tac4300ct_polling.json.
   При изменении шаблона опроса — править обе стороны.
   ---------------------------------------------------------- */

const TAC_SUFFIX_START = {
  phases:         0x0000,   // V1..V3, I1..I3, P1..P3, Q1..Q3, S1..S3      // start_address: 0
  angles:         0x001E,   // PF1..PF3, ANG1..ANG3, U12, U23, U31, FREQ   // start_address: 30
  aggregates:     0x003C,   // ISum, VAvgPh, VAvgLine, IAvg, INeut         // start_address: 60
  load:           0x004E,   // load1, load2, load3                         // start_address: 78
  energy32:       0x0400,   // int32-энергии
  energy_float:   0x0500,   
  active:         0x1D00,   // int64 — оставить только на случай включения
};

/* Определяет стартовый адрес ответа Modbus.
   Приоритет:
     1) Явный hex в имени команды — "read_0x001E", "FC04_0x003C"
     2) Известный suffix — "phases", "angles", "aggregates", "load"
     3) Фолбэк — 0x0000
*/
function tacResolveStartAddr(cmd) {
  if (!cmd) return null;

  // 1) Явный hex в имени команды
  const m = String(cmd).match(/0x([0-9A-Fa-f]{1,4})|_([0-9A-Fa-f]{4})\b/);
  if (m) return parseInt(m[1] || m[2], 16);

  // 2) Точное совпадение suffix
  const key = String(cmd).trim().toLowerCase();
  if (Object.prototype.hasOwnProperty.call(TAC_SUFFIX_START, key)) {
    return TAC_SUFFIX_START[key];
  }

  // 3) Подстрока
  for (const [suffix, addr] of Object.entries(TAC_SUFFIX_START)) {
    if (key.includes(suffix)) return addr;
  }

  return null;
}

/* hex → ASCII (для детекта NAK и т.п.) */
function tacHexToAscii(hex) {
  if (!hex || typeof hex !== 'string') return '';
  let s = '';
  for (let i = 0; i < hex.length; i += 2) {
    const b = parseInt(hex.substr(i, 2), 16);
    if (!isNaN(b)) s += String.fromCharCode(b);
  }
  return s;
}

/* ============================================================
   ПРОИЗВОДНЫЕ ВЕЛИЧИНЫ
   ============================================================ */

const _tacDerivedKeys = new Set();
function tacMarkDirect(keys) { for (const k of keys) _tacDerivedKeys.delete(k); }


function tacDeriveTotals(d) {
  const num = v => (typeof v === 'number' && isFinite(v)) ? v : null;
  const sumOf = (...arr) => {
    const vs = arr.map(num);
    return vs.every(v => v !== null) ? vs.reduce((a, b) => a + b, 0) : null;
  };
  const derive = (key, value) => {
    if (value === null || value === undefined) return;
    if (d[key] !== undefined && !_tacDerivedKeys.has(key)) return;
    d[key] = value;
    _tacDerivedKeys.add(key);
  };

  const pick = (base) =>
  num(d[base + '64']) ??   // 64-бит (нет на этой прошивке)
  num(d[base + 'F'])  ??   // Float32 (новый блок 0x0500)
  num(d[base]);            // 32-бит (нулевой)

  /* -------- Мгновенные (без изменений) -------- */
  derive('tac_pTotal', sumOf(d.tac_p1, d.tac_p2, d.tac_p3));
  derive('tac_qTotal', sumOf(d.tac_q1, d.tac_q2, d.tac_q3));
  derive('tac_sTotal', sumOf(d.tac_s1, d.tac_s2, d.tac_s3));

  const P = num(d.tac_pTotal), Q = num(d.tac_qTotal), S = num(d.tac_sTotal);
  if (P !== null && S !== null && S !== 0)     derive('tac_pfTotal', P / S);
  if (P !== null && Q !== null && (P || Q))    derive('tac_angTotal', Math.atan2(Q, P) * 180 / Math.PI);
  derive('tac_iSum', sumOf(d.tac_i1, d.tac_i2, d.tac_i3));

   /* -------- Глобальные энергии: приоритет 64 → F → 32-бит -------- */
  const pickGlobal = (base) => {
    const val = num(d[base + '64']) ?? num(d[base + 'F']) ?? num(d[base]);
    if (val !== null) {
      d[base] = val;                 // ← перезаписываем безусловно
      _tacDerivedKeys.add(base);
    }
  };
  pickGlobal('tac_eActImp');
  pickGlobal('tac_eActExp');
  pickGlobal('tac_eActTot');
  pickGlobal('tac_eAppTot');
  pickGlobal('tac_eReactImp');
  pickGlobal('tac_eReactExp');
  pickGlobal('tac_eReactTot');

  /* -------- Глобальные Σ = Импорт − Экспорт (Note 3 PDF) --------
     Если счётчик прислал готовый F-«Tot» — используем его,
     иначе считаем сами.                                              */
  if (num(d.tac_eActTotF) === null) {
    const imp = num(d.tac_eActImp), exp = num(d.tac_eActExp);
    if (imp !== null && exp !== null) {
      d.tac_eActTot = imp - exp;
      _tacDerivedKeys.add('tac_eActTot');
    }
  }
  if (num(d.tac_eReactTotF) === null) {
    const imp = num(d.tac_eReactImp), exp = num(d.tac_eReactExp);
    if (imp !== null && exp !== null) {
      d.tac_eReactTot = imp - exp;
      _tacDerivedKeys.add('tac_eReactTot');
    }
  }

  /* -------- Пофазные базовые: подстановка F-значений (0x0500) -------- */
  for (const ph of ['1','2','3']) {
    for (const kind of ['Imp','Exp','Tot']) {
      const aBase = 'tac_eActL' + ph + kind;
      const aF = num(d[aBase + 'F']);
      if (aF !== null) { d[aBase] = aF; _tacDerivedKeys.add(aBase); }

      const rBase = 'tac_eReactL' + ph + kind;
      const rF = num(d[rBase + 'F']);
      if (rF !== null) { d[rBase] = rF; _tacDerivedKeys.add(rBase); }
    }
  }

  /* -------- Пофазные Σ = Импорт − Экспорт (если F-«Tot» не пришёл) -------- */
  for (const ph of ['1','2','3']) {
    const aTot = 'tac_eActL' + ph + 'Tot';
    if (num(d[aTot + 'F']) === null) {
      const imp = num(d['tac_eActL' + ph + 'Imp']);
      const exp = num(d['tac_eActL' + ph + 'Exp']);
      if (imp !== null && exp !== null) { d[aTot] = imp - exp; _tacDerivedKeys.add(aTot); }
    }
    const rTot = 'tac_eReactL' + ph + 'Tot';
    if (num(d[rTot + 'F']) === null) {
      const imp = num(d['tac_eReactL' + ph + 'Imp']);
      const exp = num(d['tac_eReactL' + ph + 'Exp']);
      if (imp !== null && exp !== null) { d[rTot] = imp - exp; _tacDerivedKeys.add(rTot); }
    }
  }
}



/* ============================================================
   ОТРИСОВКА
   Значения масштабируем: Вт → кВт, вар → квар, ВА → кВА
   ============================================================ */
function tacAfterUpdate() {
  try { updateUIByData(window.lastData); } catch (e) { /* noop */ }
  tacRenderAll();
  if (!tacFirstDataReceived) {
    tacFirstDataReceived = true;
    hideLoading();
  }
  tacResetFails();
}

function tacRenderAll() {
  const root = document.querySelector('.tac-meter');
  if (!root) return;
  const data = tacState.data;

  root.querySelectorAll('[data-source]').forEach(el => {
  const key = el.dataset.source;
  const raw = data[key];

  // Характер нагрузки рендерим всегда, даже если raw === undefined
  if (/(^|_)load[123]$/.test(key)) {
    let code;
    if (raw === undefined || raw === null || raw === '') {
      code = 4;                                // нет данных — считаем, что нагрузки нет
    } else {
      code = Math.round(Number(raw));
      if (!isFinite(code)) code = 4;
    }
    // Любое неизвестное значение тоже трактуем как «Нет нагрузки»
    el.textContent = TAC_LOAD_NATURE[code] ?? 'Нет нагрузки';
    return;
  }

  if (raw === undefined) return;   // остальным полям «—» оставляем как есть
  if (raw === null || raw === '') { el.textContent = '—'; return; }
  if (typeof raw !== 'number' || !isFinite(raw)) { el.textContent = String(raw); return; }

  let v = raw;
  if (/_p[123]?$|_q[123]?$|_s[123]?$|pTotal|qTotal|sTotal/.test(key)) v = raw / 1000;
  const dec = el.dataset.dec !== undefined ? Number(el.dataset.dec) : undefined;
  el.textContent = tacFmt(v, dec);
  el.classList.toggle('neg', v < 0);
});

  // Квадрант
  const p = data.tac_pTotal;
  const q = data.tac_qTotal;
  if (typeof p === 'number' && typeof q === 'number') {
    tacUpdateQuadrant(p, q);
  }
}

function tacUpdateQuadrant(p, q) {
  const root = document.querySelector('.tac-meter');
  if (!root) return;

  const qNum = tacQuadrantOf(p, q);
  for (let i = 1; i <= 4; i++) {
    const s = root.querySelector('#tacSecQ' + i);
    const l = root.querySelector('#tacLblQ' + i);
    if (s) s.classList.toggle('active', i === qNum);
    if (l) l.classList.toggle('active', i === qNum);
  }

  const pKw = p / 1000, qKvar = q / 1000;
  const CX = 150, CY = 150, R = 125;
  const scale = tacNiceScale(Math.max(Math.abs(pKw), Math.abs(qKvar), 1e-6));
  const px = CX + (pKw / scale) * R;
  const py = CY - (qKvar / scale) * R;

  const v = root.querySelector('#tacQuadVector');
  const pt = root.querySelector('#tacQuadPoint');
  if (v)  { v.setAttribute('x2', px.toFixed(2)); v.setAttribute('y2', py.toFixed(2)); }
  if (pt) { pt.setAttribute('cx', px.toFixed(2)); pt.setAttribute('cy', py.toFixed(2)); }

  const info = TAC_QUADRANTS[qNum];
  const sign = x => x >= 0 ? '+' : '−';
  const stateEl = root.querySelector('#tacQuadState');
  if (stateEl) {
    stateEl.innerHTML =
      info.name +
      '<small>' + info.desc + '</small>' +
      '<small>P = ' + sign(pKw) + tacFmt(Math.abs(pKw), 3) + ' кВт · ' +
      'Q = ' + sign(qKvar) + tacFmt(Math.abs(qKvar), 3) + ' квар</small>';
  }
}

/* ============================================================
   ЭКСПОРТ В ГЛОБАЛЬНУЮ ОБЛАСТЬ
   (чтобы object.js мог сослаться на неё из MONITORING_HANDLERS)
   ============================================================ */
window.startMonitoringTAC4300CTCorBridge = startMonitoringTAC4300CTCorBridge;
window.startMonitoringTAC4300CTModbusOverTcp = startMonitoringTAC4300CTModbusOverTcp;
window.stopTAC4300CTWS = stopTAC4300CTWS;

/* Опционально: API для ручного теста из консоли */
window.TAC4300CT = {
  state: tacState,
  decode: tacDecodeModbusHex,
  stop: stopTAC4300CTWS
};


