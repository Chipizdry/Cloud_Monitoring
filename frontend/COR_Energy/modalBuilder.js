
// ===============================
// Конструктор модалок по schema
// ===============================

export function buildModals(schema) {
    if (!schema) return;

    Object.values(schema).forEach(section => {
        if (!section?.enabled || !section.modalId) return;

        const modal = document.getElementById(section.modalId);
        if (!modal) return;

        // Заголовок
        const title = modal.querySelector("h3");
        if (title && section.title) title.textContent = section.title;

        // Тело модалки
        let body = modal.querySelector(".modal-body");
        if (!body) {
            body = document.createElement("div");
            body.className = "modal-body";
            modal.appendChild(body);
        }
        body.innerHTML = "";

           // Динамические солнечные зарядки
        if (section.dynamic === "solarChargers") {
    // ⚡ передаем актуальные данные
            body.appendChild(renderSolarChargersTemplate(window.lastData || { chargers: [] }));
        }
        // Сначала blocks
        section.blocks?.forEach(block => body.appendChild(renderBlock(block)));

        // Затем поля
        if (section.fields?.length) {
            body.appendChild(renderBlock({ type: "fieldList", fields: section.fields, title: section.fieldsTitle }));
        }

       
        // Затем controls
        section.controls?.forEach(ctrl => body.appendChild(renderBlock(ctrl)));
    });
}

// ===============================
// Фабрика блоков
// ===============================


function renderBlock(block) {
    const container = document.createElement("div");
    container.className = "modal-block";

    let content;

    switch (block.type) {
        case "fieldList":
            content = renderFieldList(block.fields || [], block.title);
            break;

        case "phaseTable":
            content = renderPhaseTable(block);
            break;

        case "singlePhase":
            content = renderSinglePhase(block.fields || []);
            break;
            
        case "stringTable":
            content = renderStringTable(block);
            break;

        case "slider":
            content = renderSlider(block);
            break;

        case "text":
            content = renderText(block.text || "");
            break;

        default:
            console.warn("Неизвестный тип блока:", block.type);
            content = document.createElement("div");
            content.textContent = "Неподдерживаемый блок: " + block.type;
            content.style.color = "red";
    }

    container.appendChild(content);
    return container;
}
// ===============================
// Блок: список параметров
// ===============================


function renderFieldList(fields = [], title = null) {
    const wrap = document.createElement("div");
    wrap.className = "single-phase-grid";

    if (title) {
        const t = document.createElement("div");
        t.className = "single-block-title";
        t.textContent = title;
        wrap.appendChild(t);
    }

    fields.forEach(f => {
        const row = document.createElement("div");
        row.className = "modal-row";

        row.innerHTML = `
            <span class="data-label">${f.label}</span>
            <span class="value-wrapper">
                <span class="data-value" data-source="${f.source}">—</span>
                <span class="unit">${f.unit || ""}</span>
            </span>
        `;
        console.log("🟢 Создано поле:", f.label, "data-source:", f.source, "unit:", f.unit);

        wrap.appendChild(row);
    });

    return wrap;
}
// ===============================
// Блок: фазовая таблица (1 / 3)
// ===============================

function renderPhaseTable(block) {
    const { phases = 3, rows = [] } = block;

    const table = document.createElement("table");
    table.className = "phase-table";

    table.innerHTML = `
        <thead>
            <tr>
                <th>Параметр</th>
                ${Array.from({ length: phases }, (_, i) => `<th>L${i + 1}</th>`).join("")}
            </tr>
        </thead>
        <tbody>
            ${rows.map(row => `
                <tr>
                    <td>${row.label} (${row.unit})</td>
                    ${row.source.map(src => `<td data-source="${src}">—</td>`).join("")}
                </tr>
            `).join("")}
        </tbody>
    `;

    return table;
}


function renderPhaseRow(label, unit, base, phases) {
    return `
        <tr>
            <td>${label} (${unit})</td>
            ${Array.from({ length: phases }, (_, i) =>
                `<td data-source="${base}L${i + 1}">—</td>`
            ).join("")}
        </tr>
    `;
}

// ===============================
// Блок: слайдер
// ===============================
function renderSlider(cfg) {
    const wrap = document.createElement("div");
    wrap.className = "modal-control";

    wrap.innerHTML = `
        <label>${cfg.label}</label>
        <div style="display:flex; gap:10px; align-items:center;">
            <input type="range"
                min="${cfg.min}"
                max="${cfg.max}"
                step="${cfg.step || 1}"
                data-source="${cfg.source}">
            <span class="slider-value">—</span>
        </div>
        ${cfg.saveAction ? `<button onclick="${cfg.saveAction}()">Сохранить</button>` : ""}
    `;
    return wrap;
}

// ===============================
// Блок: текст
// ===============================
function renderText(text) {
    const p = document.createElement("p");
    p.textContent = text;
    return p;
}


function renderSinglePhase(fields = []) {
    const wrap = document.createElement("div");
    wrap.className = "single-phase-grid";

    fields.forEach(f => {
        const card = document.createElement("div");
        card.className = "modal-row";
        card.innerHTML = `
            <span class="data-label">${f.label}</span>
            <span class="value-wrapper">
                <span class="data-value" data-source="${f.source}">—</span>
                <span class="unit">${f.unit}</span>
            </span>
        `;
        wrap.appendChild(card);
    });

    return wrap;
}

function renderStringTable(block) {
    const table = document.createElement("table");
    table.className = "phase-table";

    table.innerHTML = `
        <thead>
            <tr>
                <th>String</th>
                <th>Напряжение (В)</th>
                <th>Ток (А)</th>
                <th>Мощность (Вт)</th>
            </tr>
        </thead>
        <tbody>
            ${block.rows.map(r => `
                <tr>
                    <td>${r.label}</td>
                    <td data-source="${r.voltage}">—</td>
                    <td data-source="${r.current}">—</td>
                    <td data-source="${r.power}">—</td>
                </tr>
            `).join("")}
        </tbody>
    `;
    return table;
}

function renderSolarChargersTemplate(solarData) {
    const container = document.createElement("div");
    container.className = "solar-dynamic";
    if (!solarData?.chargers) return container;

    solarData.chargers.forEach(charger => {
        const block = document.createElement("div");
        block.className = "modal-block";
        const title = document.createElement("h4");
        title.textContent = `MPPT ${charger.slave}`;
        block.appendChild(title);

        if (charger.error) {
            const errorDiv = document.createElement("div");
            errorDiv.style.color = "red";
            errorDiv.textContent = `Ошибка: ${charger.error}`;
            block.appendChild(errorDiv);
        } else {
            const table = document.createElement("table");
            table.className = "phase-table";

            const tbodyRows = charger.strings.map((s, i) => `
                <tr>
                    <td>String ${i+1}</td>
                    <td data-source="solar_${charger.slave}_${i+1}_voltage">—</td>
                    <td data-source="solar_${charger.slave}_${i+1}_current">—</td>
                    <td data-source="solar_${charger.slave}_${i+1}_power">—</td>
                </tr>
            `);

            table.innerHTML = `
                <thead>
                    <tr><th>String</th><th>Напряжение</th><th>Ток</th><th>Мощность</th></tr>
                </thead>
                <tbody>
                    ${tbodyRows.join("")}
                </tbody>
            `;

            block.appendChild(table);
        }

        container.appendChild(block);
    });

    return container;
}

