// Секция графиков: глобальный график + по одному на каждый объект.
// На карточках объектов — действия (алгоритм, удалить) и бейдж алгоритма.
// Возвращает список инстансов Chart.js (нужно для анимации в Stage 5).

import { createObjectChart, createGridChart } from '../charts/createCharts.js';
import { icons } from './icons.js';

const fmt = (n) => Number(n).toLocaleString('ru-RU');

// Уничтожает ранее созданные графики (перед перерисовкой).
export function destroyCharts(charts) {
    (charts || []).forEach((c) => {
        try { c.destroy(); } catch (_) { /* noop */ }
    });
}

function chartCard({ id, title, subtitle, modifier = '', actions = '' }) {
    return `
        <div class="chart-card ${modifier}">
            <div class="chart-card__head">
                <h3 class="chart-card__title">${title}</h3>
                ${subtitle ? `<span class="chart-card__subtitle">${subtitle}</span>` : ''}
                ${actions}
            </div>
            <div class="chart-canvas-wrap">
                <canvas id="${id}"></canvas>
            </div>
        </div>
    `;
}

// model — результат mapSimulation() (для state==='ok').
// opts: { state:'ok'|'empty'|'error', onCreateObject, onObjectAlgorithm, onEditObject,
//         onDeleteObject, onExpand, onRetry }.
// oc.hasAlgorithm (из симуляции) определяет надпись кнопки: «Добавить/Изменить алгоритм».
// Возвращает { charts: Chart[] }.
export function renderCharts(container, model, opts = {}) {
    const {
        state = 'ok',
        onCreateObject, onObjectAlgorithm, onDeleteObject, onEditObject, onExpand, onRetry,
    } = opts;

    const expandBtn = (kind, id = '') =>
        `<button type="button" class="btn btn--icon" data-action="expand" data-kind="${kind}" data-id="${id}" title="Развернуть график" aria-label="Развернуть график">${icons.expand}</button>`;

    // Ошибка загрузки
    if (state === 'error') {
        container.innerHTML = `
            <div class="section">
                <div class="section__header"><h2 class="section__title">Графики</h2></div>
                <div class="state-block state-block--error">
                    <p class="state-block__text">Не удалось загрузить данные с сервера.</p>
                    <button type="button" class="btn btn--primary" id="retryBtn">${icons.refresh} Повторить</button>
                </div>
            </div>`;
        container.querySelector('#retryBtn')?.addEventListener('click', () => onRetry?.());
        return { charts: [] };
    }

    // Пусто: нет ни одного объекта
    if (state === 'empty') {
        container.innerHTML = `
            <div class="section">
                <div class="section__header"><h2 class="section__title">Объекты</h2></div>
                <div class="state-block">
                    <p class="state-block__text">У вас ещё нет ни одного объекта. Создайте первый, чтобы увидеть графики.</p>
                    <button type="button" class="btn btn--primary" id="createObjectBtn">${icons.plus} Создать объект</button>
                </div>
            </div>`;
        container.querySelector('#createObjectBtn')?.addEventListener('click', () => onCreateObject?.());
        return { charts: [] };
    }

    const gridSub = model.summary
        ? `${model.summary.objects_count} объектов · ${fmt(model.summary.installed_power_kw)} кВт установл.`
        : '';

    const objectCardsHTML = model.objectCharts
        .map((oc) => {
            const hasAlg = !!oc.hasAlgorithm;
            const actions = `
                <div class="chart-card__actions">
                    <button type="button" class="btn btn--ghost btn--sm" data-action="algorithm" data-id="${oc.id}">
                        ${icons.tune} ${hasAlg ? 'Изменить алгоритм' : 'Добавить алгоритм'}
                    </button>
                    ${expandBtn('object', oc.id)}
                    <button type="button" class="btn btn--icon" data-action="edit" data-id="${oc.id}" title="Редактировать объект" aria-label="Редактировать объект">
                        ${icons.pencil}
                    </button>
                    <button type="button" class="btn btn--icon" data-action="delete" data-id="${oc.id}" title="Удалить объект" aria-label="Удалить объект">
                        ${icons.trash}
                    </button>
                </div>`;
            return chartCard({
                id: `chart-obj-${oc.id}`,
                title: oc.name,
                subtitle: `${fmt(oc.peakPowerKw)} кВт · пик · АКБ ${fmt(oc.batteryCapacityKwh)} кВт·ч`,
                actions,
            });
        })
        .join('');

    container.innerHTML = `
        <div class="section">
            <div class="section__header">
                <h2 class="section__title">Графики</h2>
                <div class="section__actions">
                    <button type="button" class="btn btn--ghost btn--sm" id="refreshBtn">
                        ${icons.refresh} Пересчитать
                    </button>
                    <button type="button" class="btn btn--ghost btn--sm" id="replayBtn">
                        ${icons.play} Проиграть
                    </button>
                    <button type="button" class="btn btn--primary btn--sm" id="createObjectBtn">
                        ${icons.plus} Создать объект
                    </button>
                </div>
            </div>
            <div class="charts-grid">
                ${chartCard({ id: 'chart-grid', title: 'Глобально по всем объектам', subtitle: gridSub, modifier: 'chart-card--wide', actions: `<div class="chart-card__actions">${expandBtn('grid')}</div>` })}
                ${objectCardsHTML}
            </div>
        </div>
    `;

    // Действия
    container.querySelector('#createObjectBtn')?.addEventListener('click', () => onCreateObject?.());
    container.querySelectorAll('[data-action="algorithm"]').forEach((b) => {
        b.addEventListener('click', () => onObjectAlgorithm?.(b.dataset.id));
    });
    container.querySelectorAll('[data-action="edit"]').forEach((b) => {
        b.addEventListener('click', () => onEditObject?.(b.dataset.id));
    });
    container.querySelectorAll('[data-action="delete"]').forEach((b) => {
        b.addEventListener('click', () => onDeleteObject?.(b.dataset.id));
    });
    container.querySelectorAll('[data-action="expand"]').forEach((b) => {
        b.addEventListener('click', () => onExpand?.({ kind: b.dataset.kind, id: b.dataset.id }));
    });

    // Графики
    const charts = [];
    const gridCanvas = container.querySelector('#chart-grid');
    if (gridCanvas) charts.push(createGridChart(gridCanvas, model.gridChart));

    model.objectCharts.forEach((oc) => {
        const canvas = container.querySelector(`#chart-obj-${oc.id}`);
        if (canvas) charts.push(createObjectChart(canvas, oc));
    });

    return { charts };
}
