// «Прорисовка» линий: график не появляется целиком, а рисуется слева направо.
// Плавность держится на двух вещах (как в example):
// 1) кадр считается по времени, а не по номеру точки;
// 2) головная точка интерполируется между соседями → фронт движется непрерывно,
//    а не прыгает от узла к узлу сетки.
// «Будущее» гасится через null (spanGaps:false в пресетах линий).

const clamp01 = (v) => Math.max(0, Math.min(1, v));

export function createPlayback({ charts, durationMs = 2600, onFrame }) {
    let sources = [];
    let raf = null;
    let progress = 1;
    let startedAt = 0;

    // Снимок полных данных каждого графика (чтобы восстанавливать при перемотке).
    function syncSources() {
        sources = (charts || []).map((chart) => ({
            chart,
            labels: [...chart.data.labels],
            datasets: chart.data.datasets.map((d) => [...d.data]),
        }));
    }

    // Ряд, обрезанный по позиции фронта; последняя видимая точка подтянута
    // к дробной позиции — это и даёт «текучесть» вместо ступенек.
    function revealSeries(source, head) {
        const last = Math.floor(head);
        const frac = head - last;
        return source.map((value, index) => {
            if (index < last) return value;
            if (index > last) return null;
            if (frac <= 0 || index + 1 >= source.length) return value;
            const next = source[index + 1];
            if (typeof value !== 'number' || typeof next !== 'number') return value;
            return value + (next - value) * frac;
        });
    }

    function applyFrame(nextProgress, force = false) {
        progress = clamp01(nextProgress);

        sources.forEach(({ chart, labels, datasets }) => {
            const maxIndex = labels.length - 1;
            if (maxIndex < 0) return;
            const head = progress >= 1 ? maxIndex : progress * maxIndex;

            // Пересчитываем только при заметном сдвиге фронта — экономит работу.
            const changed = force || chart.$head === undefined || Math.abs(chart.$head - head) > 0.01;
            if (!changed) return;

            chart.data.labels = labels;
            chart.data.datasets.forEach((dataset, di) => {
                dataset.data = revealSeries(datasets[di] || [], head);
            });
            chart.$head = head;
            chart.update('none');
        });

        onFrame?.({ progress });
    }

    function finish() {
        raf = null;
        applyFrame(1, true);
        onFrame?.({ progress: 1, finished: true });
    }

    function tick(now) {
        const elapsed = (now - startedAt) / durationMs;
        if (elapsed >= 1) {
            finish();
            return;
        }
        applyFrame(elapsed);
        raf = requestAnimationFrame(tick);
    }

    function start() {
        syncSources();
        if (!sources.length) return;

        // Уважаем настройку пользователя: без анимации — сразу финальный кадр.
        const reduce = window.matchMedia
            && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        if (reduce) {
            finish();
            return;
        }

        if (raf) cancelAnimationFrame(raf);
        startedAt = performance.now();
        applyFrame(0, true);
        raf = requestAnimationFrame(tick);
    }

    function stop() {
        if (raf) cancelAnimationFrame(raf);
        raf = null;
    }

    return { start, stop, syncSources, applyFrame, getProgress: () => progress };
}
