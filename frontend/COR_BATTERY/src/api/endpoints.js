// Пути API demo-solar-battery-v2 и сборка URL.

import { API_URL } from '../config.js';

const BASE = `${API_URL}/demo-solar-battery-v2`;

export const endpoints = {
    objects: () => `${BASE}/objects`,
    object: (id) => `${BASE}/objects/${id}`,
    objectAlgorithm: (id) => `${BASE}/objects/${id}/algorithm`,
    globalAlgorithm: () => `${BASE}/global-algorithm`,
    simulate: (query = {}) => {
        const url = new URL(`${BASE}/simulate`);
        // days_back?, start_time?, end_time? — все опциональны
        Object.entries(query).forEach(([key, value]) => {
            if (value !== undefined && value !== null && value !== '') {
                url.searchParams.set(key, String(value));
            }
        });
        return url.toString();
    },
};
