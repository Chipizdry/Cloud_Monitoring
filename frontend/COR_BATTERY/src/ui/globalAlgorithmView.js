// Блок глобального алгоритма: сводка параметров, либо пустое/ошибочное состояние.

import { icons } from './icons.js';

const fmt = (v) => (v === null || v === undefined || v === '' ? '—' : Number(v).toLocaleString('ru-RU'));

const escapeHtml = (str) =>
    String(str).replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch]);

// Плашка с ошибкой бэкенда (detail) — например, 400 на сохранение.
const errorAlert = (detail) => `
    <div class="ga-alert" role="alert">
        <div class="ga-alert__body">
            <span class="ga-alert__title">Не удалось применить алгоритм</span>
            <span class="ga-alert__text">${escapeHtml(detail)}</span>
        </div>
        <button type="button" class="ga-alert__close" id="dismissGlobalErrorBtn" aria-label="Скрыть">&times;</button>
    </div>`;

const header = (extra = '') => `
    <div class="section__header">
        <h2 class="section__title">${icons.gear} Глобальный алгоритм</h2>
        ${extra}
    </div>`;

export function renderGlobalAlgorithm(container, algo, { onEdit, error, errorDetail, saveError, onDismissSaveError } = {}) {
    // Ошибка загрузки
    if (error) {
        container.innerHTML = `
            <div class="section">
                ${header()}
                <div class="state-block state-block--error">
                    <p class="state-block__text">Не удалось загрузить глобальный алгоритм.</p>
                    ${errorDetail ? `<p class="state-block__text">${escapeHtml(errorDetail)}</p>` : ''}
                </div>
            </div>`;
        return;
    }

    // Не задан
    if (!algo) {
        container.innerHTML = `
            <div class="section">
                ${header()}
                ${saveError ? errorAlert(saveError) : ''}
                <div class="state-block">
                    <p class="state-block__text">У вас нет глобального алгоритма. Вы можете его добавить.</p>
                    <button type="button" class="btn btn--primary" id="addGlobalBtn">${icons.plus} Добавить</button>
                </div>
            </div>`;
        container.querySelector('#addGlobalBtn')?.addEventListener('click', () => onEdit?.());
        container.querySelector('#dismissGlobalErrorBtn')?.addEventListener('click', () => onDismissSaveError?.());
        return;
    }

    // Есть алгоритм
    const rows = [
        ['Ёмкость внешней АКБ', algo.external_battery_capacity_kwh, 'кВт·ч'],
        ['Мощность поддержки', algo.support_power_kw, 'кВт'],
        ['Удержание пика', algo.support_peak_minutes, 'мин'],
        ['Порог коррекции', algo.correction_threshold_percent, '%'],
        ['Мин. SOC', algo.min_soc_percent, '%'],
        ['Период пересчёта', algo.recalculation_period_minutes, 'мин'],
    ];

    container.innerHTML = `
        <div class="section">
            ${header('<button type="button" class="btn btn--primary btn--sm" id="editGlobalBtn">Редактировать</button>')}
            ${saveError ? errorAlert(saveError) : ''}
            <div class="ga-grid">
                ${rows
                    .map(
                        ([label, value, unit]) => `
                    <div class="ga-item">
                        <span class="ga-item__label">${label}</span>
                        <span class="ga-item__value">${fmt(value)} <small>${unit}</small></span>
                    </div>`,
                    )
                    .join('')}
            </div>
        </div>
    `;

    container.querySelector('#editGlobalBtn')?.addEventListener('click', () => onEdit?.());
    container.querySelector('#dismissGlobalErrorBtn')?.addEventListener('click', () => onDismissSaveError?.());
}
