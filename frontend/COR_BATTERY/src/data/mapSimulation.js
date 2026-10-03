// Приведение ответа SimulationResponse к рядам для графиков.
// На каждый объект: without_correction_kw / with_correction_kw.
// Глобальный график: grid_timeline → without_correction_kw / with_global_correction_kw.

import { pickIndicesMinMax, sampleByIndices, timeToLabel } from './series.js';

export function mapSimulation(sim, { maxPoints = 720 } = {}) {
    if (!sim) return null;

    const objectCharts = (sim.object_timelines || []).map((ot) => {
        const tl = ot.timeline || [];
        const without = tl.map((p) => p.without_correction_kw);
        const withCorrection = tl.map((p) => p.with_correction_kw);
        const times = tl.map((p) => p.time);
        const idx = pickIndicesMinMax(without, maxPoints);

        return {
            id: ot.id,
            name: ot.name,
            peakPowerKw: ot.peak_power_kw,
            batteryCapacityKwh: ot.battery_capacity_kwh,
            hasAlgorithm: !!ot.algorithm,
            labels: idx.map((i) => timeToLabel(times[i])),
            without: sampleByIndices(without, idx),
            withCorrection: sampleByIndices(withCorrection, idx),
            pointCount: tl.length,
        };
    });

    const grid = sim.grid_timeline || [];
    const gWithout = grid.map((p) => p.without_correction_kw);
    const gWithGlobal = grid.map((p) => p.with_global_correction_kw);
    const gTimes = grid.map((p) => p.time);
    const gidx = pickIndicesMinMax(gWithout, maxPoints);

    const gridChart = {
        labels: gidx.map((i) => timeToLabel(gTimes[i])),
        without: sampleByIndices(gWithout, gidx),
        withGlobal: sampleByIndices(gWithGlobal, gidx),
        pointCount: grid.length,
    };

    return {
        objectCharts,
        gridChart,
        summary: sim.summary || null,
        timezone: sim.timezone || '',
        intervalMinutes: sim.data_interval_minutes ?? null,
    };
}
