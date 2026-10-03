

const SECTION_BUILDERS = {
    relay: buildRelayBlock,
    gridLimit: buildGridLimitBlock,
    essAdvanced: buildEssBlock,
    modbusTools: buildModbusBlock
};

export function buildSettings(schema) {

    const container = document.getElementById("settingsContainer");
    if (!container) {
        console.warn("settingsContainer не найден");
        return;
    }

    container.innerHTML = "";

    if (!schema) {
    container.innerHTML = `<div class="empty-state">Нет доступных настроек</div>`;
    return;
    }

    // 🔥 Если у модели полностью кастомный UI
    if (typeof schema.customBuilder === "function") {
        schema.customBuilder(container, {
            object: window.currentObject
        });
        return;
    }

    if (!Array.isArray(schema.sections)) {
        console.warn("sections не определены в схеме");
        return;
    }

    schema.sections.forEach(section => {

        const builder = SECTION_BUILDERS[section.type];

        if (!builder) {
            console.warn(`Билдер для типа "${section.type}" не найден`);
            return;
        }

        try {
            const element = builder(section, {
                object: window.currentObject
            });

            if (element instanceof HTMLElement) {
                container.appendChild(element);
            }

        } catch (err) {
            console.error(`Ошибка построения секции ${section.type}:`, err);
        }

    });
}



function buildRelayBlock(section, ctx) {

    const div = document.createElement("div");
    div.className = "relay-block";

    div.innerHTML = `
        <div class="relay-control">
            <label class="ios-switch">
                <input type="checkbox" id="relayStart">
                <span class="slider"></span>
            </label>
            <span>Блокировка генератора</span>
        </div>

        <div class="relay-control">
            <label class="ios-switch">
                <input type="checkbox" id="relayBlock">
                <span class="slider"></span>
            </label>
            <span>Пуск генератора</span>
        </div>
    `;

    return div;
}



function buildGridLimitBlock(section = {}, ctx = {}) {

    const maxPower = section.maxPower || 10000;
    const currentValue = ctx.object?.grid_limit || 0;

    const wrapper = document.createElement("div");
    wrapper.className = "grid-limit-block settings-block";

    wrapper.innerHTML = `
        <h3>Ограничение мощности сети</h3>
        
        <div class="form-row">
            <label>Макс. мощность (Вт)</label>
            <input 
                type="number" 
                class="input"
                min="0"
                max="${maxPower}"
                value="${currentValue}"
                data-setting="grid_limit"
            >
        </div>
    `;

    return wrapper;
}


function buildEssBlock(section = {}, ctx = {}) {

    const mode = ctx.object?.ess_mode || "self_use";

    const wrapper = document.createElement("div");
    wrapper.className = "ess-block settings-block";

    wrapper.innerHTML = `
        <h3>ESS Настройки</h3>

        <div class="form-row">
            <label>Режим работы</label>
            <select class="input" data-setting="ess_mode">
                <option value="self_use" ${mode === "self_use" ? "selected" : ""}>Self Use</option>
                <option value="backup" ${mode === "backup" ? "selected" : ""}>Backup</option>
                <option value="feed_in" ${mode === "feed_in" ? "selected" : ""}>Feed In</option>
            </select>
        </div>

        <div class="form-row">
            <label>Мин. SOC (%)</label>
            <input 
                type="number"
                min="0"
                max="100"
                class="input"
                value="${ctx.object?.min_soc ?? 20}"
                data-setting="min_soc"
            >
        </div>
    `;

    return wrapper;
}


function buildModbusBlock(section = {}, ctx = {}) {

    const wrapper = document.createElement("div");
    wrapper.className = "modbus-block settings-block";

    wrapper.innerHTML = `
        <h3>Modbus Инструменты</h3>

        <div class="form-row">
            <label>Slave ID</label>
            <input 
                type="number"
                min="1"
                max="247"
                class="input"
                value="${ctx.object?.slave_id ?? 1}"
                data-setting="slave_id"
            >
        </div>

        <div class="button-row">
            <button class="btn" data-action="read-modbus">
                Прочитать регистры
            </button>

            <button class="btn danger" data-action="write-modbus">
                Записать настройки
            </button>
        </div>
    `;

    return wrapper;
}


