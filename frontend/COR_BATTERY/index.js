// Точка входа CorSolar.
// Клиент НИЧЕГО не вычисляет: после любой мутации (создание/правка/удаление
// объекта, алгоритм объекта, глобальный алгоритм) вызывается refreshAll() —
// заново запрашиваем ВСЕ данные с бэкенда и перерисовываем. Ошибки и пустые
// состояния показываем соответствующими блоками.

import { renderApp, renderSectionSkeleton } from './src/ui/template.js';
import { applyChartDefaults, Chart } from './src/charts/chartSetup.js';
import { api } from './src/api/client.js';
import { getState, setState } from './src/state/store.js';
import { renderCharts, destroyCharts } from './src/ui/chartsView.js';
import { renderGlobalAlgorithm } from './src/ui/globalAlgorithmView.js';
import { openChartModal } from './src/ui/chartModal.js';
import { mapSimulation } from './src/data/mapSimulation.js';
import { createPlayback } from './src/charts/playback.js';
import {
    openCreateObjectModal,
    openEditObjectModal,
    openObjectAlgorithmModal,
    openGlobalAlgorithmModal,
    openConfirmModal,
} from './src/ui/modals.js';

function bootstrap() {
    const root = document.querySelector('#app');
    renderApp(root);
    applyChartDefaults();

    if (!Chart) {
        console.error('[CorSolar] Chart.js не загружен');
        return;
    }

    const globalMount = document.querySelector('#globalAlgorithmSection');
    const chartsMount = document.querySelector('#chartsSection');

    let playback = null;

    function renderGlobalBlock() {
        renderGlobalAlgorithm(globalMount, getState().globalAlgorithm, {
            error: getState().globalError,
            errorDetail: getState().globalErrorDetail,
            saveError: getState().globalSaveError,
            onEdit: handleGlobalAlgorithm,
            onDismissSaveError: () => {
                setState({ globalSaveError: null });
                renderGlobalBlock();
            },
        });
    }

    function renderChartsFromState() {
        if (playback) playback.stop();
        destroyCharts(getState().charts);

        const s = getState();
        let state = 'ok';
        let model = null;
        if (s.objectsError) {
            state = 'error';
        } else if (!s.objects.length) {
            state = 'empty';
        } else {
            model = mapSimulation(s.simulation);
            if (!model) state = 'error';
        }

        const { charts } = renderCharts(chartsMount, model, {
            state,
            onRetry: refreshAll,
            onCreateObject: handleCreateObject,
            onObjectAlgorithm: handleObjectAlgorithm,
            onEditObject: handleEditObject,
            onDeleteObject: handleDeleteObject,
            onExpand: handleExpandChart,
        });
        setState({ chartModel: model, charts });

        if (charts.length) {
            playback = createPlayback({ charts, durationMs: 2600 });
            playback.start();
            chartsMount.querySelector('#replayBtn')?.addEventListener('click', () => playback.start());
            chartsMount.querySelector('#refreshBtn')?.addEventListener('click', () => refreshAll());
        }
    }

    // Единая точка обновления: тянем ВСЁ с бэкенда и перерисовываем.
    function refreshAll() {
        return Promise.allSettled([
            api.getObjects(),
            api.simulate(),
            api.getGlobalAlgorithm(),
        ]).then(([objectsR, simR, globalR]) => {
            setState({
                objects: objectsR.status === 'fulfilled' ? (objectsR.value || []) : [],
                objectsError: objectsR.status === 'rejected',
                simulation: simR.status === 'fulfilled' ? simR.value : null,
                globalAlgorithm: globalR.status === 'fulfilled' ? globalR.value : null,
                globalError: globalR.status === 'rejected',
                globalErrorDetail: globalR.status === 'rejected' ? globalR.reason?.detail || null : null,
            });
            renderGlobalBlock();
            renderChartsFromState();
        });
    }

    function handleExpandChart({ kind, id }) {
        const model = getState().chartModel;
        if (!model) return;
        if (kind === 'grid') {
            openChartModal({ title: 'Глобально по всем объектам', kind: 'grid', chartData: model.gridChart });
        } else {
            const oc = model.objectCharts.find((c) => c.id === id);
            if (oc) openChartModal({ title: oc.name, kind: 'object', chartData: oc });
        }
    }

    // --- Мутации: вызвать API, затем refreshAll() ---

    function handleCreateObject() {
        openCreateObjectModal({
            onCreate: (body) => api.createObject(body).then(() => refreshAll()),
        });
    }

    function handleEditObject(objectId) {
        const obj = getState().objects.find((o) => o.id === objectId);
        if (!obj) return;
        openEditObjectModal({
            object: obj,
            onSubmit: (body) => api.updateObject(objectId, body).then(() => refreshAll()),
        });
    }

    function handleDeleteObject(objectId) {
        const obj = getState().objects.find((o) => o.id === objectId);
        if (!obj) return;
        openConfirmModal({
            title: 'Удалить объект',
            message: `Удалить «${obj.name}»? Действие необратимо.`,
            confirmText: 'Удалить',
            danger: true,
            onConfirm: () => api.deleteObject(objectId).then(() => refreshAll()),
        });
    }

    function handleObjectAlgorithm(objectId) {
        const obj = getState().objects.find((o) => o.id === objectId);
        if (!obj) return;
        // Текущий алгоритм для префилла берём из симуляции (её отдаёт бэкенд).
        const timeline = getState().simulation?.object_timelines?.find((ot) => ot.id === objectId);
        openObjectAlgorithmModal({
            objectName: obj.name,
            current: timeline?.algorithm || null,
            onSubmit: (body) => api.addObjectAlgorithm(objectId, body).then(() => refreshAll()),
        });
    }

    function handleGlobalAlgorithm() {
        openGlobalAlgorithmModal({
            current: getState().globalAlgorithm,
            onSubmit: (body) =>
                api
                    .patchGlobalAlgorithm(body)
                    .then(() => {
                        setState({ globalSaveError: null });
                        return refreshAll();
                    })
                    .catch((err) => {
                        // Показываем ошибку и в блоке алгоритма, и в модалке (rethrow).
                        setState({ globalSaveError: err.detail || err.message || 'Ошибка сохранения' });
                        renderGlobalBlock();
                        throw err;
                    }),
        });
    }

    // Первичная загрузка
    renderSectionSkeleton(globalMount, 1);
    renderSectionSkeleton(chartsMount, 2);
    refreshAll();
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bootstrap);
} else {
    bootstrap();
}
