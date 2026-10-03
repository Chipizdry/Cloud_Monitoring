// Прореживание плотных суточных рядов (до ~720 точек) с сохранением пиков/провалов.
// Выбираем ИНДЕКСЫ по опорному ряду (min/max в каждой корзине), затем сэмплируем
// все ряды и метки по этим же индексам — так линии остаются выровненными.

export function pickIndicesMinMax(reference, maxPoints = 720) {
    const n = reference.length;
    if (n <= maxPoints) return reference.map((_, i) => i);

    const bucketCount = Math.max(1, Math.floor(maxPoints / 2));
    const bucketSize = n / bucketCount;
    const set = new Set([0, n - 1]);

    for (let b = 0; b < bucketCount; b += 1) {
        const start = Math.floor(b * bucketSize);
        const end = Math.min(n, Math.floor((b + 1) * bucketSize));
        let minI = start;
        let maxI = start;
        for (let i = start; i < end; i += 1) {
            if (reference[i] < reference[minI]) minI = i;
            if (reference[i] > reference[maxI]) maxI = i;
        }
        set.add(minI);
        set.add(maxI);
    }
    return [...set].sort((a, b) => a - b);
}

export function sampleByIndices(series, indices) {
    return indices.map((i) => series[i]);
}

// "2026-09-20T05:00:00" → "05:00"
export function timeToLabel(iso) {
    if (typeof iso !== 'string') return '';
    return iso.length >= 16 ? iso.slice(11, 16) : iso;
}
