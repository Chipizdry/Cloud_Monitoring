// Простое состояние приложения + подписки. Наполним на следующих этапах.

const state = {
    objects: [],           // список объектов (с бэкенда)
    objectsError: false,   // не удалось загрузить объекты/симуляцию
    globalAlgorithm: null, // глобальный алгоритм (null = не задан)
    globalError: false,    // не удалось загрузить глобальный алгоритм
    globalErrorDetail: null, // detail ошибки загрузки глобального алгоритма
    globalSaveError: null, // detail ошибки сохранения глобального алгоритма (напр. 400)
    simulation: null,      // ответ simulate (с бэкенда)
    chartModel: null,      // результат mapSimulation (для отрисовки)
    charts: [],            // инстансы Chart.js (для анимации)
};

const listeners = new Set();

export function getState() {
    return state;
}

export function setState(patch) {
    Object.assign(state, patch);
    listeners.forEach((fn) => fn(state));
}

export function subscribe(fn) {
    listeners.add(fn);
    return () => listeners.delete(fn);
}
