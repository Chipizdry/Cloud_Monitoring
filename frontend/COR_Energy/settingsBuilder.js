


// ============================================================
// 1. Словарь билдеров
// ============================================================
const SECTION_BUILDERS = {
    relay:       buildRelayBlock,
    gridLimit:   buildGridLimitBlock,
    essAdvanced: buildEssBlock,
    modbusTools: buildModbusBlock
};

// ============================================================
// 2. Словарь layout-шаблонов настроек
// ============================================================
const SETTINGS_LAYOUT_TEMPLATES = {
    'hybrid_inverter_settings': '/static/COR_Energy/layouts/settings/hybrid_inverter_settings.html',
    'energy_meter_settings':    '/static/COR_Energy/layouts/settings/energy_meter_settings.html',
    'flywheel_settings':        '/static/COR_Energy/layouts/settings/flywheel_settings.html',
    'UPS_settings':             '/static/COR_Energy/layouts/settings/UPS_settings.html',
    'default_settings':         '/static/COR_Energy/layouts/settings/default_settings.html'
};

const _layoutCache = new Map();

async function loadSettingsLayout(layoutName) {
    const url = SETTINGS_LAYOUT_TEMPLATES[layoutName]
             || SETTINGS_LAYOUT_TEMPLATES['default_settings'];

    if (_layoutCache.has(url)) return _layoutCache.get(url);

    const html = await fetch(url, { cache: 'no-cache' }).then(r => {
        if (!r.ok) throw new Error(`Layout not found: ${url}`);
        return r.text();
    });

    _layoutCache.set(url, html);
    return html;
}

// ============================================================
// 3. Главная точка входа
// ============================================================
export async function buildSettings(schema, layoutName = null) {

    const root = document.getElementById("settingsContainer");
    if (!root) {
        console.warn("settingsContainer не найден");
        return;
    }

    // 3.1. Совсем нет схемы — заглушка
    if (!schema) {
        root.innerHTML = `<div class="empty-state">Нет доступных настроек</div>`;
        return;
    }

    // 3.2. Кастомный UI
    if (typeof schema.customBuilder === "function") {
        root.innerHTML = "";
        schema.customBuilder(root, { object: window.currentObject });
        return;
    }

    // 3.3. Резолвим имя layout
    const layout = layoutName
        || schema.settings_layout
        || 'default_settings';

    // 3.4. Грузим layout в #settingsContainer
    try {
        root.innerHTML = await loadSettingsLayout(layout);
    } catch (err) {
        console.error("Ошибка загрузки layout настроек:", err);
        root.innerHTML = `<div class="empty-state">Не удалось загрузить интерфейс настроек</div>`;
        return;
    }

    // 3.5. Навигация (табы/аккордеон)
    bindSettingsNav();

    // 3.6. Секций нет — layout уже отрисован
    if (!Array.isArray(schema.sections)) {
        console.warn("sections не определены в схеме");
        return;
    }

    // 3.7. Рендерим секции
    renderSections(schema.sections, schema);
}

// ============================================================
// 4. Раскладка секций по контейнерам
// ============================================================
function renderSections(sections, schema) {
    const fallback = document.querySelector('.settings-layout.default-settings')
                  || document.getElementById('settingsContainer');

    sections.forEach(section => {
        const builder = SECTION_BUILDERS[section.type];
        if (!builder) {
            console.warn(`Билдер для типа "${section.type}" не найден`);
            return;
        }

        const targetId = section.container
                      || (section.id ? `section-${section.id}` : null);
        const target = (targetId && document.getElementById(targetId)) || fallback;

        if (!target) {
            console.warn(`Контейнер для секции "${section.type}" не найден`);
            return;
        }

        try {
            const element = builder(section, {
                object: window.currentObject,
                schema
            });
            if (element instanceof HTMLElement) {
                if (target !== fallback) target.innerHTML = "";
                target.appendChild(element);
            }
        } catch (err) {
            console.error(`Ошибка построения секции ${section.type}:`, err);
        }
    });
}

// ============================================================
// 5. Навигация по секциям
// ============================================================
function bindSettingsNav() {
    const navItems = document.querySelectorAll('.settings-nav [data-section]');
    const panels   = document.querySelectorAll('.settings-section');

    if (!navItems.length || !panels.length) return;

    navItems.forEach(item => {
        item.addEventListener('click', () => {
            const targetId = 'section-' + item.dataset.section;

            panels.forEach(p => p.classList.remove('active'));
            document.getElementById(targetId)?.classList.add('active');

            navItems.forEach(x => x.classList.remove('active'));
            item.classList.add('active');
        });
    });

    navItems[0]?.click();
}

// ============================================================
// 6. Билдеры секций
// ============================================================
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
    const maxPower     = section.maxPower
                      || ctx.schema?.grid?.maxPower
                      || 10000;
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
                <option value="backup"   ${mode === "backup"   ? "selected" : ""}>Backup</option>
                <option value="feed_in"  ${mode === "feed_in"  ? "selected" : ""}>Feed In</option>
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


