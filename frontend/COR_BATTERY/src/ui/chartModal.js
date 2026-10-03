// Модалка с графиком на весь экран. Пересоздаёт график из данных модели
// (createObjectChart / createGridChart) и проигрывает анимацию отрисовки.

import { createObjectChart, createGridChart } from '../charts/createCharts.js';
import { createPlayback } from '../charts/playback.js';

export function openChartModal({ title, kind, chartData }) {
    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay';
    overlay.innerHTML = `
        <div class="modal modal--chart" role="dialog" aria-modal="true">
            <div class="modal__header">
                <h2 class="modal__title">${title}</h2>
                <button type="button" class="modal__close" aria-label="Закрыть">&times;</button>
            </div>
            <div class="chart-modal__body">
                <div class="chart-modal__canvas-wrap">
                    <canvas id="expandedChart"></canvas>
                </div>
            </div>
        </div>
    `;

    let chart = null;
    let playback = null;

    function close() {
        document.removeEventListener('keydown', onKeydown);
        if (playback) playback.stop();
        if (chart) { try { chart.destroy(); } catch (_) { /* noop */ } }
        overlay.remove();
    }
    function onKeydown(e) {
        if (e.key === 'Escape') close();
    }

    overlay.addEventListener('mousedown', (e) => {
        if (e.target === overlay) close();
    });
    overlay.querySelector('.modal__close').addEventListener('click', close);
    document.addEventListener('keydown', onKeydown);
    document.body.appendChild(overlay);

    const canvas = overlay.querySelector('#expandedChart');
    chart = kind === 'grid'
        ? createGridChart(canvas, chartData)
        : createObjectChart(canvas, chartData);

    playback = createPlayback({ charts: [chart], durationMs: 2200 });
    playback.start();

    return { close };
}
