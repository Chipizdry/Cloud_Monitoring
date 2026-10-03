

import { buildModals } from "./modalBuilder.js";
import { SETTINGS_SCHEMAS } from "./settingsSchemas.js";    
import { buildSettings } from "./settingsBuilder.js";

const MONITORING_HANDLERS = {
    MonitoringDeyeModbusOverTcp: startMonitoringDeyeModbusOverTcp, 
    MonitoringDeyeCorBridge: startMonitoringDeyeCorBridge,
    MonitoringBlueSunCorBridge: startMonitoringBlueSunCorBridge,
    MonitoringAxiomaCorBridge: startMonitoringAxiomaCorBridge,
    MonitoringAxiomaModbusOverTcp: startMonitoringAxiomaModbusOverTcp,
    MonitoringVictronModbusTcp: startMonitoringVictronModbusTcp,
    MonitoringVictronCorBridge: startMonitoringVictronCorBridge,
    MonitoringDaxtromnCorBridge: startMonitoringDaxtromnCorBridge,
    MonitoringCorCorBridge: startMonitoringCORCorBridge ,
    MonitoringTAC4300CTCorBridge:    startMonitoringTAC4300CTCorBridge,
    MonitoringTAC4300CTModbusOverTcp: startMonitoringTAC4300CTModbusOverTcp
};


// Словарь соответствия layout → URL шаблона
const LAYOUT_TEMPLATES = {
    'flywheel': '/static/COR_Energy/layouts/flywheel.html',
    'hybrid_inverter': '/static/COR_Energy/layouts/hybrid_inverter.html',
    'energy_meter': '/static/COR_Energy/layouts/energy_meter.html',
    'UPS': '/static/COR_Energy/layouts/UPS.html',
    'default': '/static/COR_Energy/layouts/inverter.html'  // fallback
};

async function applyControlLayout(layout) {
    const controlDiv = document.getElementById('control');
    if (!controlDiv) return;

    const templateUrl = LAYOUT_TEMPLATES[layout] || LAYOUT_TEMPLATES['default'];
    console.log(`🔄 Загружаем layout "${layout}" по URL: ${templateUrl}`);

    if (!templateUrl) {
        controlDiv.innerHTML = '<div class="error-message">Нет шаблона для данного устройства</div>';
        return;
    }

    try {
        const response = await fetch(templateUrl);
        if (!response.ok) {
            throw new Error(`HTTP ${response.status} ${response.statusText}`);
        }

        const html = await response.text();

        // ========================
        // Overlay
        // ========================
        let loadingOverlay = document.getElementById('LoadingOverlay');

        if (!loadingOverlay) {
            loadingOverlay = document.createElement('div');
            loadingOverlay.id = 'LoadingOverlay';
            loadingOverlay.className = 'chart-loading-overlay';
            loadingOverlay.style.display = 'none';
            loadingOverlay.innerHTML =
                '<div class="chart-loading-spinner"></div><span class="icon-label">Загрузка...</span>';
        }

        // ========================
        // Clear + inject
        // ========================
        controlDiv.replaceChildren(); // чище чем innerHTML

        controlDiv.appendChild(loadingOverlay);
        controlDiv.insertAdjacentHTML('beforeend', html);

        // ========================
        // UI refresh (after paint)
        // ========================
        requestAnimationFrame(() => {
            try {
                if (window.currentModalSchema) {
                    initIconModalHandlers(window.currentModalSchema);
                }

                if (window.lastData && Object.keys(window.lastData).length > 0) {
                    updateUIByData(window.lastData);
                    refreshPowerIndicators();
                }

                if (typeof isFirstDataReceived !== 'undefined') {
                    isFirstDataReceived ? hideLoading() : showLoading();
                }
            } catch (e) {
                console.error("UI update error:", e);
            }
        });

    } catch (err) {
        console.error("applyControlLayout failed:", err);
        controlDiv.innerHTML =
            '<div class="error-message">Ошибка загрузки layout</div>';
    }
}

// Вспомогательная функция для обновления всех индикаторов мощности и SOC

function refreshPowerIndicators() {
    if (!window.lastData || !window.deviceMaxPower) return;

    const powerMap = {
        battery: 'Battery',
        grid: 'Grid',
        load: 'Load',
        solar: 'Solar',
        generator: 'Generator'
    };
    for (const [key, entity] of Object.entries(powerMap)) {
        if (window.lastData[key] !== undefined && window.deviceMaxPower[entity]) {
            const percent = PowerToIndicator(window.lastData[key], window.deviceMaxPower[entity]);
            updatePowerByName(entity, percent);
        }
    }
    if (window.lastData.battery_soc !== undefined) {
        updateBatteryFill(window.lastData.battery_soc);
    }
} 

/*
const HOOK_REGISTRY = {
    startBatteryServicePolling,
    stopBatteryServicePolling
};
*/
export function adaptJsonSchema(json) {
    if (!json) return null;
    const schema = structuredClone(json);
     console.log("🔧 adaptJsonSchema INPUT:", schema);
    console.log("🔧 schema.monitoring:", schema.monitoring);
    console.log("🔧 MONITORING_HANDLERS:", MONITORING_HANDLERS);

    // преобразуем hooks (если нужно)
    // schema.hooks = buildHooks(schema.hooks);

    // 🔹 Преобразуем monitoring из строк → функции
    if (schema.monitoring) {
        schema.monitoringFn = {};
        Object.entries(schema.monitoring).forEach(([protocol, handlerName]) => {
            console.log( `🔍 monitoring: protocol="${protocol}", handler="${handlerName}"` );
            const fn = MONITORING_HANDLERS[handlerName];
            console.log(`🔍 найден handler:`, fn,`для ${handlerName}`);
              if (fn) {schema.monitoringFn[protocol] = fn;
                      } else {
                console.warn( `❌ Handler "${handlerName}" отсутствует в MONITORING_HANDLERS`);
            }


        });
    }

    return schema;
}

/*
function buildHooks(hooksJson) {
    return {
        onOpen(entity, ctx) {
            if (entity === "batterySettings" && hooksJson.batteryOpenHandler) {
                const fn = HOOK_REGISTRY[hooksJson.batteryOpenHandler];
                fn?.(ctx.object);
            }
        },

        onClose(entity) {
            if (entity === "batterySettings" && hooksJson.batteryCloseHandler) {
                const fn = HOOK_REGISTRY[hooksJson.batteryCloseHandler];
                fn?.();
            }
        }
    };
}  */


export async function loadModalSchema(vendor, model) {
    try {
        const res = await fetch(`/api/Vendor_schemas/${vendor}/${model}`);
        if (!res.ok) throw new Error("Schema not found");
        const data = await res.json();

        return data;
    } catch (e) {
        console.error("Ошибка загрузки schema:", e);
        return null;
    }
}

export async function resolveModalSchema(vendor, model) {
    const raw = await loadModalSchema(vendor, model);  
    return adaptJsonSchema(raw);
}

export function resolveSettingsSchema(vendor, model) {
    const vendorSchemas = SETTINGS_SCHEMAS[vendor];
    if (!vendorSchemas) return null;
    return vendorSchemas[model] || vendorSchemas.default || null;
}
// ============================
// Закрытие модалки
// ============================

document.addEventListener("click", (e) => {
    const btn = e.target.closest('[data-action="close"]');
    if (!btn) return;

    const modal = btn.closest(".modal");
    if (!modal) return;
 window.activeModals[entity] = false;
onModalClosed(entity);
});

function onModalClosed(entity) {
    const hooks = window.currentModalSchema?.hooks;
    if (!hooks?.onClose) return;

    hooks.onClose(entity);
}


async function loadObjectSettings(objectId) {
    await checkToken();
    const token = getToken();
       
    try {
        const response = await fetch(`${API_BASE_URL}/api/energetic_objects/${objectId}`, {
            method: "GET",
            headers: { "Accept": "application/json",Authorization: `Bearer ${token}` }
        });

        if (!response.ok) {
            throw new Error("Ошибка загрузки объекта");
        }

        const data = await response.json();
        console.log("Объект:", data);
        // Установка заголовка
        document.getElementById("objectTitle").textContent = data.name || "    ";
        
        const modalSchema = await resolveModalSchema(data.vendor, data.model_name);
        const settingsSchema = resolveSettingsSchema(data.vendor, data.model_name);
        console.log("Schema:", modalSchema);
        window.currentModalSchema = modalSchema;
        window.currentLayout = modalSchema.layout || "default";
        console.log("🎨 Layout:", window.currentLayout);
        await applyControlLayout(window.currentLayout);

        window.deviceMaxPower = {
            Battery: modalSchema?.battery?.maxPower,
            Grid: modalSchema?.grid?.maxPower,
            Load: modalSchema?.load?.maxPower,
            Solar: modalSchema?.solar?.maxPower,
            Generator: modalSchema?.generator?.maxPower,
            Inverter: modalSchema?.inverter?.maxPower
        };
        console.log("⚡ deviceMaxPower:", window.deviceMaxPower);

        // 🔥 СТРОИМ МОДАЛКИ ПО СХЕМЕ
        buildModals(modalSchema);
        buildSettings(settingsSchema);
      //  initIconModalHandlers(modalSchema);

        window.currentObject = data;
        handleObjectByProtocol(data);

        if (window.lastData) {
            updateUIByData(window.lastData);
        }

        // Инициализируем таблицу расписаний после загрузки объекта
        if (typeof window.initScheduleTable === 'function') {
            window.initScheduleTable();
        }

    } catch (err) {
        console.error("Ошибка:", err);
    }
}

function handleObjectByProtocol(objectData) {

    const protocol = objectData?.protocol;

    console.group("🎯 handleObjectByProtocol");

    console.log("protocol:", protocol);
    console.log("currentModalSchema:", window.currentModalSchema);
    console.log("monitoring:", window.currentModalSchema?.monitoring);
    console.log("monitoringFn:", window.currentModalSchema?.monitoringFn);

    const handler = window.currentModalSchema?.monitoringFn?.[protocol];

    console.log("handler:", handler);
    console.log("typeof handler:", typeof handler);

    if (typeof handler !== "function") {
        console.warn(`❌ Нет monitoring handler для протокола "${protocol}"` );
        console.groupEnd();
        return;
    }

    console.log(`🚀 Запускаем monitoring handler для "${protocol}"`);

    try {
        handler(objectData);
    } catch (err) {
        console.error(
            `❌ Ошибка выполнения monitoring handler "${protocol}":`,
            err
        );
    }

    console.groupEnd();
}


async function resolveCORBridgeDeviceId(corBridgeId) {
      await checkToken();
      const token = getToken();
   
    if (!corBridgeId) return null;

    try {

          const response = await fetch(
            `${API_BASE_URL}/api/energetic/devices`,
            {
                method: 'GET',
                headers: {
                    'Accept': 'application/json',
                    'Authorization': `Bearer ${token}`
                }
            }
        );

        if (!response.ok) {
            throw new Error("Не удалось загрузить COR-Bridge список");
        }

        const devices = await response.json();
        console.log("📦 devices:", devices);
        const bridge = devices.find(d => d.id === corBridgeId || d.device_id === corBridgeId);

        if (bridge?.device_id) {
            return bridge.device_id;
        }

        if (typeof corBridgeId === "string" && corBridgeId.startsWith("COR-")) {
            return corBridgeId;
        }

        console.warn("❌ COR-Bridge не найден:", corBridgeId);
        return null;
    } catch (err) {
        console.error("❌ Ошибка resolveCORBridgeDeviceId:", err);
        return null;
    }
}


// ============================
// Открытие модалки
// ============================
function openEntityModal(entity, modalSchema) {
    console.group(`🪟 openEntityModal: ${entity}`);

    if (!modalSchema) {
        console.warn("❌ Нет схемы модалок");
        console.groupEnd();
        return;
    }

    const entitySchema = modalSchema[entity];
    if (!entitySchema || !entitySchema.modalId) {
        console.warn(`❌ Сущность '${entity}' отсутствует или modalId не задан`);
        console.groupEnd();
        return;
    }

    const modal = document.getElementById(entitySchema.modalId);
    if (!modal) {
        console.error(`❌ Модалка '${entitySchema.modalId}' не найдена`);
        console.groupEnd();
        return;
    }

    modal.style.display = "block";

      // 🔥 АКТИВИРУЕМ ФЛАГ
   window.activeModals[entity] = true;
   onModalOpened(entity);

    // 🔄 обновляем модалку сразу актуальными данными
    updateUIByData(window.lastData);
    console.groupEnd();
}

function onModalOpened(entity) {
    const hooks = window.currentModalSchema?.hooks;
    if (!hooks?.onOpen) return;
    hooks.onOpen(entity, {
        object: window.currentObject,
        lastData: window.lastData
    });
}

function initIconModalHandlers(modalSchema) {
    console.group("🧷 initIconModalHandlers");
    if (!modalSchema) {
        console.error("❌ modalSchema отсутствует");
        console.groupEnd();
        return;
    }

    const icons = document.querySelectorAll(".icon[data-entity]");
    console.log("Найдено иконок:", icons.length);
    icons.forEach(icon => {
        const entity = icon.dataset.entity;
        console.log("→ иконка entity:", entity);
        icon.addEventListener("click", () => {
            console.log(`🖱️ click по entity: ${entity}`);
            openEntityModal(entity, modalSchema);
        });
    });

    console.groupEnd();
}


function getGradientColor(value) {
const x = Math.max(0, Math.min(100, value));
let r, g;
if (x <= 50) {
const k = x / 50;
r = Math.round(255 * k);
g = 255;
} else {
const k = (x - 50) / 50;
r = 255;
g = Math.round(255 * (1 - k));
}
return `rgb(${r}, ${g}, 0)`;
}
function updatePowerElement(id, value) {
const el = document.getElementById(id);
if (!el) return;

const v = Math.max(0, Math.min(100, Math.abs(value))); // используем модуль для цвета

if (el.tagName.toLowerCase() === "path") {
    const animate = el.querySelector("animate");
    if (v === 0) {
        el.style.stroke = "rgba(120,120,120,0.3)";
        el.style.opacity = "0.3";
        if (animate) animate.setAttribute("from", "0"), animate.setAttribute("to", "0");
        return;
    }
    el.style.stroke = getGradientColor(v);
    el.style.opacity = "1";
    if (animate) animate.setAttribute("from", "0"), animate.setAttribute("to", value > 0 ? "-50" : "50");
} else if (el.tagName.toLowerCase() === "rect") {
    const svg = el.ownerSVGElement;
    if (!svg) return;
    const vb = svg.viewBox.baseVal;
    const fullWidth = vb.width || svg.getBoundingClientRect().width;
    const centerBars = ["BatteryBar", "gridBar"];
    const isCenter = centerBars.includes(id);
    el.setAttribute("fill", v === 0 ? "rgba(120,120,120,0.4)" : getGradientColor(v));

    if (isCenter) {
        const centerX = fullWidth / 2;
        const halfWidth = (fullWidth / 2) * (v / 100);
        if (value >= 0) {
            // вправо от центра
            el.setAttribute("x", centerX);
            el.setAttribute("width", halfWidth);
        } else {
            // влево от центра
            el.setAttribute("x", centerX - halfWidth);
            el.setAttribute("width", halfWidth);
        }
    } else {
        const padding = 2;
        const width = Math.round((fullWidth - padding * 2) * (v / 100));
        el.setAttribute("x", padding);
        el.setAttribute("width", width);
    }
}

}

// --- Обновление по логическому имени ---
function updatePowerByName(name, value) {
    const map = {
        Battery: { line: "batteryLine", bar: "BatteryBar" },
        Generator: { line: "generatorLine", bar: "GeneratorBar" },
        Load: { line: "loadLine", bar: "PowerBar" },
        Grid: { line: "gridLine", bar: "gridBar" },
        Solar: { line: "solarLine", bar: "SunPanelBar" }
    };
    const ids = map[name];
    if (!ids) return;
    updatePowerElement(ids.line, value);
    updatePowerElement(ids.bar, value);
}

function updateBatteryFill(value) {
    const fill = document.getElementById("batteryFill");
    if (!fill) return;

    const v = Math.max(0, Math.min(100, value)); // SOC 0..100
    const x_left  = 14.7964;
    const x_right = 73.0562;
    const maxWidth = x_right - x_left;
    const newWidth = maxWidth * (v / 100);
    const newX = x_right - newWidth;

    fill.setAttribute("x", newX.toFixed(2));
    fill.setAttribute("width", newWidth.toFixed(2));

    // 🔴⬅️🟢 ИНВЕРСИЯ ЦВЕТА
    fill.setAttribute("fill", getGradientColor(100 - v));
}

function PowerToIndicator(powerW, maxPowerW) {
    if (typeof powerW !== "number" || !isFinite(powerW)) return 0;
    const percent = (powerW / maxPowerW) * 100;
    // ограничиваем, но сохраняем знак
    return Math.max(-100, Math.min(100, percent));
}


function formatPowerLabel(powerW, type) {
    if (typeof powerW !== "number" || !isFinite(powerW)) {
        return undefined; // ← textContent НЕ меняется
    }

    const absW = Math.abs(powerW);

    //НЕТ ПОТОКА
    if (absW === 0) {
        return "Нет потока";
    }

    const formatValue = (w) => {
        if (w < 10100) {
            return `${Math.round(w)} Вт`;
        }
        return `${(w / 1000).toFixed(1)} КВт`;
    };

    switch (type) {
        case "battery":
            return powerW >= 0
                ? `Разряд: ${formatValue(absW)}`
                : `Заряд: ${formatValue(absW)}`;

        case "grid":
            return powerW >= 0
                ? `Потребление: ${formatValue(absW)}`
                : `Отдача: ${formatValue(absW)}`;

        case "load":
            return `Нагрузка: ${formatValue(absW)}`;

        case "solar":
        case "generator":
            return `Генерация: ${formatValue(absW)}`;

        default:
            return formatValue(absW);
    }
}


/**
 * Показывает или скрывает элементы устройства по имени
 * @param {string} name - имя сущности: "Grid", "Battery", "Generator", "Load", "Sun"
 * @param {string} state - "visible" или "hidden"
 */
function setDeviceVisibility(name, state) {

  //  console.log("⚙️ setDeviceVisibility вызвана:", { name, state });

    if (!name || !state) {
        console.warn("❌ Ошибка: name или state не переданы");
        return;
    }

    const show = state === "visible";

   // console.log("👉 Режим отображения:", show ? "ПОКАЗАТЬ" : "СКРЫТЬ");

    // Привязываем к реальным ID элементов
    const idMap = {
        Battery: ["batteryIcon", "batteryLine", "batteryFill"],
        Generator: ["GeneratorIcon", "generatorLine"],
        Load: ["loadIcon", "loadLine"],
        Grid: ["power-grid-icon", "gridLine"],
        Sun: ["SolarBatteryIcon", "solarLine"],
        Inverter: ["inverterIcon"],
        ErrorIcon: ["ErrorIcon"],
        WarningIcon: ["WarningIcon"],
        FaultIcon: ["FaultIcon"],
        LoadingOverlay: ["LoadingOverlay"],
        BlockIcon: ["BlockIcon"]
    };

    const elements = idMap[name];

    if (!elements) {
        console.warn("❌ Неизвестное устройство:", name);
        return;
    }

   // console.log("📌 Найдены элементы:", elements);

    elements.forEach(id => {
        const el = document.getElementById(id);

        if (el) {
            el.style.display = show ? "block" : "none";
          //  console.log(`✅ ${id} → display: ${el.style.display}`);
        } else {
            console.warn(`⚠️ Элемент не найден в DOM: ${id}`);
        }
    });

   // console.log("✅ setDeviceVisibility завершена\n");
}


function setIconStatus(type, status) {

    const statusColors = {
        offline: "rgba(160,160,160,0.3)",
        normal: null,
        warning: "orange",
        alarm: "red"
    };

    // SVG иконки
    const iconMap = {
        Battery: "batteryIcon",
        Grid: "power-grid-icon",
        Load: "loadIcon",
        Generator: "GeneratorIcon",
        Solar: "SolarBatteryIcon",
        Inverter: "inverterIcon"
    };

    // HTML подписи
    const labelMap = {
        Battery: "batteryFlowLabel",
        Grid: "networkFlowLabel",
        Load: "loadIndicatorLabel",
        Generator: "generatorFlowLabel",
        Solar: "solarPowerLabel",
        Inverter: "inverterFlowLabel"
    };

    const color = statusColors[status];

    // ==========================
    // ✅ 1) Меняем SVG иконку
    // ==========================
    const iconId = iconMap[type];
    const icon = document.getElementById(iconId);

    if (icon) {

        if (status === "normal") {
            icon.querySelectorAll("*").forEach(el => {
                el.style.stroke = "";
                el.style.fill = "";
                el.style.opacity = "";
            });
        } else {
            icon.querySelectorAll("path, rect, circle, text, tspan").forEach(el => {
                if (el.hasAttribute("stroke")) el.style.stroke = color;
                el.style.fill = color;
                el.style.opacity = "1";
            });
        }
    }

    // ==========================
    // ✅ 2) Меняем HTML подпись
    // ==========================
    const labelId = labelMap[type];
    const label = document.getElementById(labelId);

    if (label) {

        if (status === "normal") {
            label.style.color = ""; // вернуть оригинальный
            label.style.opacity = "";
        } else {
            label.style.color = color;
            label.style.opacity = "1";
        }
    }

  //  console.log(`✅ ${type} status=${status}`);
}



export function updateUIByData(data = {}) {
    if (!data || typeof data !== "object") {
        console.warn("updateUIByData: пустые или некорректные данные", data);
        return;
    }

    Object.assign(window.lastData, data);

    console.group("🔄 updateUIByData");
    Object.entries(data).forEach(([key, value]) => {
        const nodes = document.querySelectorAll(`[data-source="${key}"]`);

        if (!nodes.length) {
          //  console.warn(`❌ Поле с data-source="${key}" не найдено в DOM`, value);
            return;
        }


        nodes.forEach(node => {
            if (node.classList.contains("data-value")) {
                // Обновляем только текст значения, не трогая структуру
                node.textContent = formatValue(value);
            } else if (node.tagName === "TD") {
                node.textContent = formatValue(value);
            } else if (node.tagName === "INPUT" || node.tagName === "SELECT") {
                node.value = value;
            }
        });
    });
    console.groupEnd();
}


function formatValue(val) {
    if (val == null || Number.isNaN(val)) return "—";
    if (typeof val === "number") return Math.abs(val) >= 1000 ? val.toFixed(0) : val.toFixed(1);
    return val;
}

function setErrorText(text) {
    const label = document.getElementById("errorIndicatorLabel");

    if (!label) {
        console.warn("❌ errorIndicatorLabel не найден");
        return;
    }
    label.textContent = text;
}


function setWarningText(text) {
    const label = document.getElementById("warningIndicatorLabel");
    if (!label) {
        console.warn("❌ warningIndicatorLabel не найден");
        return;
    }
    label.textContent = text;
}


function setFaultText(text) {
    const label = document.getElementById("faultIndicatorLabel");
    if (!label) {
        console.warn("❌ faultIndicatorLabel не найден");
        return;
    }
    label.textContent = text;
}


function setOfflineState() {

    console.warn("⚠️ Нет данных > 4 сек → OFFLINE");
    setIconStatus("Grid", "offline");
    setIconStatus("Battery", "offline");
    setIconStatus("Inverter", "offline");
    setIconStatus("Load", "offline");
    setIconStatus("Solar", "offline");
    setDeviceVisibility("Generator", "hidden");
    setDeviceVisibility("ErrorIcon", "visible");
    setDeviceVisibility("WarningIcon", "hidden");
    setDeviceVisibility("FaultIcon", "hidden");
    setDeviceVisibility("BlockIcon", "hidden");
    hideLoading();
}


function resetOfflineTimer() {

    // если таймер уже был → убираем
    if (offlineTimer) clearTimeout(offlineTimer);

    // запускаем новый таймер
    offlineTimer = setTimeout(() => {
        setOfflineState();
    }, OFFLINE_DELAY);
}

function showLoading() {
    isFirstDataReceived = false;
   console.log("⏳ Показать загрузку...");
    setDeviceVisibility("LoadingOverlay", "visible");

    // Пока нет данных — ошибки не показываем
    setDeviceVisibility("FaultIcon", "hidden");
    setDeviceVisibility("WarningIcon", "hidden");
    setDeviceVisibility("ErrorIcon", "hidden");
    setDeviceVisibility("BlockIcon", "hidden")
}

function hideLoading() {
    isFirstDataReceived = true;
    setDeviceVisibility("LoadingOverlay", "hidden");
}


function registerFail(type, message) {

    if (type === "rs485") {
        rs485FailCount++;

        if (rs485FailCount >= FAIL_RS_BUS) {
            setDeviceVisibility("ErrorIcon", "visible");
            setDeviceVisibility("WarningIcon", "hidden");
            setDeviceVisibility("FaultIcon", "hidden");
            setDeviceVisibility("BlockIcon", "hidden");
            setErrorText(message);
            setOfflineState();
        }
    }

    if (type === "cor") {
        corFailCount++;

        if (corFailCount >= FAIL_COR_BRIDGE) {
            setDeviceVisibility("ErrorIcon", "visible");
            setDeviceVisibility("WarningIcon", "hidden");
            setDeviceVisibility("FaultIcon", "hidden");
            setDeviceVisibility("BlockIcon", "hidden");
            setErrorText(message);
            setOfflineState()
        }
    }
    if (type === "nakk") {
        corFailCount++;

        if (corFailCount >= FAIL_NAKK) {
            setDeviceVisibility("ErrorIcon", "visible");
            setDeviceVisibility("WarningIcon", "hidden");
            setDeviceVisibility("FaultIcon", "hidden");
            setDeviceVisibility("BlockIcon", "hidden");
            setErrorText(message);
            setOfflineState();
        }
    }
}


function resetFails() {
    rs485FailCount = 0;
    corFailCount = 0;

    setDeviceVisibility("ErrorIcon", "hidden");
}


window.resolveModalSchema = resolveModalSchema;
window.loadObjectSettings = loadObjectSettings;
window.updatePowerByName = updatePowerByName;
window.updateBatteryFill = updateBatteryFill;
window.PowerToIndicator = PowerToIndicator;
window.formatPowerLabel = formatPowerLabel;
window.setDeviceVisibility = setDeviceVisibility;
window.setIconStatus = setIconStatus;
window.updateUIByData = updateUIByData;
window.resolveCORBridgeDeviceId = resolveCORBridgeDeviceId;
window.setOfflineState = setOfflineState;
window.resetOfflineTimer = resetOfflineTimer;
window.setErrorText = setErrorText;
window.setWarningText = setWarningText;
window.setFaultText = setFaultText;
window.registerFail = registerFail;
window.resetFails = resetFails;
window.showLoading = showLoading;
window.hideLoading = hideLoading;
