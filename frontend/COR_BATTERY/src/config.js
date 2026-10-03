// Общая конфигурация проекта.
// ВНИМАНИЕ: токен временный (истекает). На этапе разработки работаем на моках,
// реальные запросы включатся, когда решится вопрос с CORS/доступом.
export const API_URL = `${window.location.origin}/api`;
// export const API_URL = `https://dev.monitoring.cor-int.com/api`;

// Временный access token (из index.js). Заменить на актуальный при боевых запросах.
export const ACCESS_TOKEN = localStorage.getItem("access_token")

// Палитра COR_ID (дублирует CSS-переменные для использования в Chart.js).
export const palette = {
    primary: '#5B4296',
    primaryStrong: '#7527B2',
    primaryDark: '#291161',
    primaryLight: '#B1A1DA',
    generation: '#E5484D',   // линия «без коррекции» — красная
    correction: '#7527B2',   // линия «с коррекцией» — наш основной цвет
    axis: '#ece8f6',
    axisBorder: '#d8d1ea',
    textMuted: '#6b6480',
};

export const chartFont =
    "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif";
