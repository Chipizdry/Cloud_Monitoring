// Переиспользуемые модалки в стиле COR_ID.
// createModal — базовый каркас (оверлей, заголовок, тело, футер, закрытие).
// openCreateObjectModal        — POST objects
// openObjectAlgorithmModal     — POST objects/{id}/algorithm
// openGlobalAlgorithmModal     — PATCH global-algorithm

// Базовая модалка. Возвращает { close }.
// onSubmit(values, helpers) — вызывается по «Сохранить»; может вернуть промис.
// helpers: { setError, setBusy, close }.
export function createModal({ title, bodyHTML, submitText = 'Сохранить', submitVariant = 'primary', onSubmit }) {
    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay';
    overlay.innerHTML = `
        <div class="modal" role="dialog" aria-modal="true">
            <div class="modal__header">
                <h2 class="modal__title">${title}</h2>
                <button type="button" class="modal__close" aria-label="Закрыть">&times;</button>
            </div>
            <form class="modal__form">
                <div class="modal__body">${bodyHTML}</div>
                <p class="modal__error" hidden></p>
                <div class="modal__footer">
                    <button type="button" class="btn btn--ghost" data-role="cancel">Отмена</button>
                    <button type="submit" class="btn btn--${submitVariant}" data-role="submit">${submitText}</button>
                </div>
            </form>
        </div>
    `;

    const form = overlay.querySelector('.modal__form');
    const errorEl = overlay.querySelector('.modal__error');
    const submitBtn = overlay.querySelector('[data-role="submit"]');

    function close() {
        document.removeEventListener('keydown', onKeydown);
        overlay.remove();
    }
    function onKeydown(e) {
        if (e.key === 'Escape') close();
    }
    function setError(msg) {
        errorEl.textContent = msg || '';
        errorEl.hidden = !msg;
    }
    function setBusy(busy) {
        submitBtn.disabled = busy;
        submitBtn.textContent = busy ? 'Сохранение…' : submitText;
    }

    overlay.addEventListener('mousedown', (e) => {
        if (e.target === overlay) close();
    });
    overlay.querySelector('.modal__close').addEventListener('click', close);
    overlay.querySelector('[data-role="cancel"]').addEventListener('click', close);
    document.addEventListener('keydown', onKeydown);

    form.addEventListener('submit', (e) => {
        e.preventDefault();
        setError('');
        const values = Object.fromEntries(new FormData(form).entries());
        const result = onSubmit?.(values, { setError, setBusy, close });
        if (result && typeof result.then === 'function') {
            setBusy(true);
            result
                .then(() => close())
                .catch((err) => setError(err.message || 'Ошибка сохранения'))
                .finally(() => setBusy(false));
        }
    });

    document.body.appendChild(overlay);
    overlay.querySelector('input, select, textarea')?.focus();
    return { close };
}

// Универсальное поле формы (text/number).
function field({
    name, label, type = 'number', value = '', min, max, step = 'any',
    required = true, hint, placeholder = '', maxlength,
}) {
    const attrs = [
        `type="${type}"`,
        `name="${name}"`,
        value !== '' && value !== null && value !== undefined ? `value="${value}"` : '',
        placeholder ? `placeholder="${placeholder}"` : '',
        required ? 'required' : '',
        maxlength ? `maxlength="${maxlength}"` : '',
    ];
    if (type === 'number') {
        attrs.push(`step="${step}"`);
        if (min !== undefined) attrs.push(`min="${min}"`);
        if (max !== undefined) attrs.push(`max="${max}"`);
    }
    return `
        <label class="field">
            <span class="field__label">${label}</span>
            <input class="field__input" ${attrs.filter(Boolean).join(' ')}>
            ${hint ? `<span class="field__hint">${hint}</span>` : ''}
        </label>
    `;
}

// '' → null, иначе число (или null, если не парсится).
function numOrNull(v) {
    const s = String(v ?? '').trim();
    if (s === '') return null;
    const n = Number(s);
    return Number.isNaN(n) ? null : n;
}

// --- Модалка подтверждения (например, удаление) ---
// onConfirm() может вернуть промис (кнопка уйдёт в состояние загрузки).
export function openConfirmModal({ title, message, confirmText = 'Подтвердить', danger = false, onConfirm }) {
    return createModal({
        title,
        submitText: confirmText,
        submitVariant: danger ? 'danger' : 'primary',
        bodyHTML: `<p class="modal__message">${message}</p>`,
        onSubmit: () => onConfirm?.(),
    });
}

// --- Модалка «Создать объект» (POST objects) ---
export function openCreateObjectModal({ onCreate }) {
    return createModal({
        title: 'Новый объект',
        submitText: 'Создать',
        bodyHTML: `
            ${field({ name: 'name', label: 'Название', type: 'text', maxlength: 120, placeholder: 'Например, СЭС Южная' })}
            ${field({ name: 'peak_power_kw', label: 'Пиковая мощность, кВт', min: 0.01 })}
            ${field({ name: 'battery_capacity_kwh', label: 'Ёмкость АКБ, кВт·ч', min: 0.01 })}
        `,
        onSubmit(values, { setError }) {
            const name = String(values.name || '').trim();
            const peak = numOrNull(values.peak_power_kw);
            const capacity = numOrNull(values.battery_capacity_kwh);

            if (!name) return setError('Укажите название объекта');
            if (peak === null || !(peak > 0)) return setError('Пиковая мощность должна быть больше 0');
            if (capacity === null || !(capacity > 0)) return setError('Ёмкость АКБ должна быть больше 0');

            return onCreate({ name, peak_power_kw: peak, battery_capacity_kwh: capacity });
        },
    });
}

// --- Модалка «Редактировать объект» (PATCH objects/{id}) ---
// По схеме ObjectUpdate все поля опциональны — здесь предзаполнены текущими значениями.
export function openEditObjectModal({ object, onSubmit }) {
    const o = object || {};
    return createModal({
        title: `Редактировать · ${o.name || 'объект'}`,
        submitText: 'Сохранить',
        bodyHTML: `
            ${field({ name: 'name', label: 'Название', type: 'text', maxlength: 120, value: o.name ?? '' })}
            ${field({ name: 'peak_power_kw', label: 'Пиковая мощность, кВт', min: 0.01, value: o.peak_power_kw ?? '' })}
            ${field({ name: 'battery_capacity_kwh', label: 'Ёмкость АКБ, кВт·ч', min: 0.01, value: o.battery_capacity_kwh ?? '' })}
        `,
        onSubmit(values, { setError }) {
            const name = String(values.name || '').trim();
            const peak = numOrNull(values.peak_power_kw);
            const capacity = numOrNull(values.battery_capacity_kwh);

            if (!name) return setError('Укажите название объекта');
            if (peak === null || !(peak > 0)) return setError('Пиковая мощность должна быть больше 0');
            if (capacity === null || !(capacity > 0)) return setError('Ёмкость АКБ должна быть больше 0');

            // Шлём только изменённые поля (PATCH).
            const body = {};
            if (name !== o.name) body.name = name;
            if (peak !== o.peak_power_kw) body.peak_power_kw = peak;
            if (capacity !== o.battery_capacity_kwh) body.battery_capacity_kwh = capacity;
            if (Object.keys(body).length === 0) return setError('Нет изменений');

            return onSubmit(body);
        },
    });
}

// --- Модалка «Алгоритм объекта» (POST objects/{id}/algorithm) ---
// По схеме AlgorithmCreate: support_peak_minutes обязателен, остальные с дефолтами.
export function openObjectAlgorithmModal({ objectName, current, onSubmit }) {
    const c = current || {};
    return createModal({
        title: `Алгоритм · ${objectName}`,
        submitText: 'Сохранить алгоритм',
        bodyHTML: `
            ${field({ name: 'support_peak_minutes', label: 'Удержание пика, мин', min: 1, step: 1, value: c.support_peak_minutes ?? '', hint: 'Целое число минут (обязательно)' })}
            ${field({ name: 'correction_threshold_percent', label: 'Порог коррекции, %', min: 0, max: 100, required: false, value: c.correction_threshold_percent ?? 10, hint: 'По умолчанию 10' })}
            ${field({ name: 'min_soc_percent', label: 'Мин. SOC, %', min: 0, max: 99.9, required: false, value: c.min_soc_percent ?? 20, hint: '0…<100, по умолчанию 20' })}
            ${field({ name: 'recalculation_period_minutes', label: 'Период пересчёта, мин', min: 1, step: 1, required: false, value: c.recalculation_period_minutes ?? 16, hint: 'Целое, по умолчанию 16' })}
        `,
        onSubmit(values, { setError }) {
            const spm = numOrNull(values.support_peak_minutes);
            if (spm === null) return setError('Укажите удержание пика (мин)');
            if (!Number.isInteger(spm) || spm < 1) return setError('Удержание пика: целое число ≥ 1');
            const body = { support_peak_minutes: spm };

            const ct = numOrNull(values.correction_threshold_percent);
            if (ct !== null) {
                if (ct < 0 || ct > 100) return setError('Порог коррекции: 0…100');
                body.correction_threshold_percent = ct;
            }
            const soc = numOrNull(values.min_soc_percent);
            if (soc !== null) {
                if (soc < 0 || soc >= 100) return setError('Мин. SOC: 0…<100');
                body.min_soc_percent = soc;
            }
            const rp = numOrNull(values.recalculation_period_minutes);
            if (rp !== null) {
                if (!Number.isInteger(rp) || rp < 1) return setError('Период пересчёта: целое число ≥ 1');
                body.recalculation_period_minutes = rp;
            }
            return onSubmit(body);
        },
    });
}

// --- Модалка «Глобальный алгоритм» (PATCH global-algorithm) ---
// Все поля опциональны/nullable — отправляем только заполненные.
export function openGlobalAlgorithmModal({ current, onSubmit }) {
    const c = current || {};
    return createModal({
        title: 'Глобальный алгоритм',
        submitText: 'Применить',
        bodyHTML: `
            <p class="modal__note">Все поля необязательны — отправятся только заполненные.</p>
            ${field({ name: 'external_battery_capacity_kwh', label: 'Ёмкость внешней АКБ, кВт·ч', min: 0.01, required: false, value: c.external_battery_capacity_kwh ?? '', hint: 'Больше 0' })}
            ${field({ name: 'support_power_kw', label: 'Мощность поддержки, кВт', min: 0.01, required: false, value: c.support_power_kw ?? '', hint: 'Больше 0' })}
            ${field({ name: 'support_peak_minutes', label: 'Удержание, мин', min: 1, max: 120, step: 1, required: false, value: c.support_peak_minutes ?? '', hint: '1…120' })}
            ${field({ name: 'correction_threshold_percent', label: 'Порог коррекции, %', min: 0, max: 100, required: false, value: c.correction_threshold_percent ?? '', hint: '0…100' })}
            ${field({ name: 'min_soc_percent', label: 'Мин. SOC, %', min: 0, max: 99.9, required: false, value: c.min_soc_percent ?? '', hint: '0…<100' })}
            ${field({ name: 'recalculation_period_minutes', label: 'Период пересчёта, мин', min: 1, max: 120, step: 1, required: false, value: c.recalculation_period_minutes ?? '', hint: '1…120' })}
        `,
        onSubmit(values, { setError }) {
            const body = {};

            const ebc = numOrNull(values.external_battery_capacity_kwh);
            if (ebc !== null) {
                if (!(ebc > 0)) return setError('Ёмкость внешней АКБ: больше 0');
                body.external_battery_capacity_kwh = ebc;
            }
            const sp = numOrNull(values.support_power_kw);
            if (sp !== null) {
                if (!(sp > 0)) return setError('Мощность поддержки: больше 0');
                body.support_power_kw = sp;
            }
            const spm = numOrNull(values.support_peak_minutes);
            if (spm !== null) {
                if (!Number.isInteger(spm) || spm < 1 || spm > 120) return setError('Удержание: целое 1…120');
                body.support_peak_minutes = spm;
            }
            const ct = numOrNull(values.correction_threshold_percent);
            if (ct !== null) {
                if (ct < 0 || ct > 100) return setError('Порог коррекции: 0…100');
                body.correction_threshold_percent = ct;
            }
            const soc = numOrNull(values.min_soc_percent);
            if (soc !== null) {
                if (soc < 0 || soc >= 100) return setError('Мин. SOC: 0…<100');
                body.min_soc_percent = soc;
            }
            const rp = numOrNull(values.recalculation_period_minutes);
            if (rp !== null) {
                if (!Number.isInteger(rp) || rp < 1 || rp > 120) return setError('Период пересчёта: целое 1…120');
                body.recalculation_period_minutes = rp;
            }

            if (Object.keys(body).length === 0) return setError('Заполните хотя бы одно поле');
            return onSubmit(body);
        },
    });
}
