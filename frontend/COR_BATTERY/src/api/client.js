// HTTP-слой: чистый fetch к бэкенду. Методы резолвятся данными или реджектятся
// ошибкой — обработку (ошибки/пустые состояния) делает UI.

import { ACCESS_TOKEN } from '../config.js';
import { endpoints } from './endpoints.js';

// Текст ошибки из тела ответа: { detail: "..." } или { detail: [{ msg }, ...] } (валидация).
function extractDetail(body) {
    const d = body?.detail;
    if (typeof d === 'string') return d;
    if (Array.isArray(d)) return d.map((e) => e?.msg || JSON.stringify(e)).join('; ');
    return null;
}

function request(url, options = {}) {
    return fetch(url, {
        ...options,
        headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${ACCESS_TOKEN}`,
            ...(options.headers || {}),
        },
    }).then(async (res) => {
        if (!res.ok) {
            const detail = await res.json().then(extractDetail, () => null);
            const err = new Error(detail || `API ответил ${res.status}`);
            err.status = res.status;
            err.detail = detail;
            throw err;
        }
        if (res.status === 204) return null; // например, DELETE без тела
        return res.json();
    });
}

export const api = {
    // Объекты
    getObjects() {
        return request(endpoints.objects(), { method: 'GET' });
    },
    createObject(body) {
        return request(endpoints.objects(), { method: 'POST', body: JSON.stringify(body) });
    },
    updateObject(id, body) {
        return request(endpoints.object(id), { method: 'PATCH', body: JSON.stringify(body) });
    },
    deleteObject(id) {
        return request(endpoints.object(id), { method: 'DELETE' });
    },

    // Алгоритм объекта
    addObjectAlgorithm(objectId, body) {
        return request(endpoints.objectAlgorithm(objectId), { method: 'POST', body: JSON.stringify(body) });
    },

    // Глобальный алгоритм (404 → null: ещё не задан)
    getGlobalAlgorithm() {
        return request(endpoints.globalAlgorithm(), { method: 'GET' }).catch((e) => {
            if (e.status === 404) return null;
            throw e;
        });
    },
    patchGlobalAlgorithm(body) {
        return request(endpoints.globalAlgorithm(), { method: 'PUT', body: JSON.stringify(body) });
    },

    // Симуляция (данные графиков). query: { days_back?, start_time?, end_time? }
    simulate(query = {}) {
        return request(endpoints.simulate(query), { method: 'GET' });
    },
};
