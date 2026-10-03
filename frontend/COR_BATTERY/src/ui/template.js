// Разметка оболочки приложения: блок глобального алгоритма + секция графиков + футер.

export function renderApp(root) {
    root.innerHTML = `
        <div class="app-shell">
            <main class="app-main" id="appMain">
                <section id="globalAlgorithmSection"></section>
                <section id="chartsSection" class="app-main__charts"></section>
            </main>

            <footer class="app-footer">
                <span>CorSolar · demo-solar-battery-v2</span>
            </footer>
        </div>
    `;
}

// Скелетон-загрузка секции (карточки-заглушки).
export function renderSectionSkeleton(container, cards = 3) {
    const items = Array.from({ length: cards }, () => '<div class="skeleton-card"></div>').join('');
    container.innerHTML = `<div class="section"><div class="skeleton-grid">${items}</div></div>`;
}
