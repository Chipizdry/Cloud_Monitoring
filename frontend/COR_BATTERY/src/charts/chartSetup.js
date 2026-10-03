// Общие настройки Chart.js. Chart грузится глобально через CDN (window.Chart).
// Встроенную анимацию отключаем — свою «прорисовку» сделаем в Stage 5 (playback.js).

import { chartFont, palette } from '../config.js';

// Chart подключён как UMD-глобал в index.html.
export const Chart = window.Chart;

export function applyChartDefaults() {
    if (!Chart) {
        console.error('[CorSolar] Chart.js не загружен (проверьте CDN в index.html)');
        return;
    }
    Chart.defaults.font.family = chartFont;
    Chart.defaults.color = palette.textMuted;
}

export const commonScales = {
    x: {
        grid: { color: palette.axis, drawTicks: false },
        border: { color: palette.axisBorder },
        ticks: { maxTicksLimit: 9, padding: 10, font: { size: 9 } },
    },
    y: {
        min: 0,
        beginAtZero: true,
        grid: { color: palette.axis, drawTicks: false },
        border: { display: false },
        ticks: {
            padding: 10,
            font: { size: 9 },
            callback: (value) => `${Number(value).toLocaleString('ru-RU')} кВт`,
        },
    },
};

// Отключаем встроенные анимации — фронт линии рисуем сами (Stage 5).
export const responsiveBase = {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    animations: { colors: false, x: false, y: false },
    transitions: { active: { animation: { duration: 0 } } },
    interaction: { intersect: false, mode: 'index' },
    layout: { autoPadding: false },
};

export const tooltip = {
    backgroundColor: palette.primaryDark,
    borderColor: palette.primary,
    borderWidth: 1,
    titleColor: '#ffffff',
    bodyColor: '#f2f0f8',
    padding: 11,
    displayColors: true,
    callbacks: {
        label: (ctx) =>
            ` ${ctx.dataset.label}: ${Number(ctx.parsed.y).toLocaleString('ru-RU', {
                maximumFractionDigits: 1,
            })} кВт`,
    },
};

export const legend = {
    display: true,
    position: 'top',
    align: 'end',
    labels: { boxWidth: 12, boxHeight: 12, usePointStyle: true, padding: 14, font: { size: 11 } },
};

// Плотная сетка точек делает радиус 0 обязательным, иначе Chart.js
// рисует сотни маркеров и теряет кадры.
const lineBase = {
    pointRadius: 0,
    pointHoverRadius: 0,
    borderCapStyle: 'round',
    borderJoinStyle: 'round',
    tension: 0.42,
    cubicInterpolationMode: 'monotone',
    spanGaps: false, // проигрывание гасит «будущее» через null (Stage 5)
};

export function generationLine(overrides = {}) {
    return {
        ...lineBase,
        label: 'Без коррекции',
        borderColor: palette.generation,
        backgroundColor: 'rgba(229, 72, 77, 0.08)',
        fill: true,
        borderWidth: 3,
        ...overrides,
    };
}

export function correctionLine(overrides = {}) {
    return {
        ...lineBase,
        label: 'С коррекцией',
        borderColor: palette.correction,
        backgroundColor: 'transparent',
        borderWidth: 2.6,
        borderDash: [],
        ...overrides,
    };
}
