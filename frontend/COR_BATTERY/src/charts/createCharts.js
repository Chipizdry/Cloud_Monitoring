// Фабрики графиков Chart.js: объектный и глобальный.

import {
    Chart,
    commonScales,
    responsiveBase,
    tooltip,
    legend,
    generationLine,
    correctionLine,
} from './chartSetup.js';

function baseOptions() {
    return {
        ...responsiveBase,
        plugins: { legend, tooltip },
        scales: commonScales,
    };
}

// График одного объекта: without_correction_kw vs with_correction_kw.
export function createObjectChart(canvas, chart) {
    return new Chart(canvas.getContext('2d'), {
        type: 'line',
        data: {
            labels: chart.labels,
            datasets: [
                generationLine({ label: 'Без коррекции', data: chart.without }),
                correctionLine({ label: 'С коррекцией', data: chart.withCorrection }),
            ],
        },
        options: baseOptions(),
    });
}

// Глобальный график: сумма без коррекции vs с глобальной коррекцией.
export function createGridChart(canvas, chart) {
    return new Chart(canvas.getContext('2d'), {
        type: 'line',
        data: {
            labels: chart.labels,
            datasets: [
                generationLine({ label: 'Сумма без коррекции', data: chart.without }),
                correctionLine({ label: 'С глобальной коррекцией', data: chart.withGlobal, borderWidth: 3 }),
            ],
        },
        options: baseOptions(),
    });
}
