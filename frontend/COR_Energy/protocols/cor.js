

let pwmInitialized = false;
let currentDeviceId = null;
let startStopInitialized = false;
let startStopPending = false;      // флаг ожидания ответа на команду START/STOP
let modeChangePending = false;     // флаг ожидания смены режима
let modeButtonsInitialized = false;
let resetInitialized = false;
let pwmChangePending = false;    // флаг ожидания ответа на команду изменения ШИМ
let pendingPwmValue = null;      // отправленное значение (для проверки)

// Момент инерции маховика (кг·м²) – рассчитан выше
const FLYWHEEL_J = 1.9075; // можно уточнить под свою геометрию

// Коэффициент перевода (об/мин)² → Вт·ч
const RPM2_WH = (FLYWHEEL_J * Math.PI ** 2) / (2 * 900 * 3600);

// /static/COR_Energy/protocols/cor.js
startMonitoringCORCorBridge = async function startMonitoringCORCorBridge(objectData) {
    console.log("🚀 Запуск мониторинга COR Bridge для маховика", objectData);
    
  

    // Берём первый cor_bridge из массива (как в axioma.js)
    const corBridgeId = objectData.cor_bridges?.[0];
    if (!corBridgeId) {
        console.error("❌ У объекта нет cor_bridges", objectData);
        return;
    }

    const deviceId = await resolveCORBridgeDeviceId(corBridgeId);
    currentDeviceId = deviceId;
    console.log("🔍 Полученный device_id:", deviceId);
    if (!deviceId) {
        console.error("❌ Не удалось определить device_id для COR Bridge");
        return;
    }

    const wsUrl = buildAuthenticatedWebSocketUrl(
        `/dev-modbus/responses?device_id=${encodeURIComponent(deviceId)}`
    );
    console.log(`🌐 Подключение к WebSocket: ${maskWebSocketUrlForLog(wsUrl)}`);
    const ws = new WebSocket(wsUrl);

    ws.onopen = () => console.log('✅ COR-Bridge WS connected');

    ws.onmessage = (event) => {
        try {
            const msg = JSON.parse(event.data);

            // =========================================
            // SNAPSHOT
            // =========================================
            if (msg?.type === "cor_agent_snapshot" && Array.isArray(msg.events)) {
                console.log("📦 Получен snapshot:", msg.events);
                let snapshotData = {};
                for (const snapshotEvent of msg.events) {
                    const payload = snapshotEvent.data || snapshotEvent;
                    if (!payload.command_name || !payload.hex_data) continue;
                    const parsed = parsePI30Response(payload.command_name, payload.hex_data);
                    if (!parsed) continue;
                    snapshotData = { ...snapshotData, ...parsed };
                }
                console.log("✅ Snapshot применён:", snapshotData);
                updateUIByData(snapshotData);
                syncPWMControl(snapshotData);
                updateStartStopButtonState(snapshotData);  
                updateModeButtonsState(snapshotData);      
                updateEnergyDisplay(snapshotData);       
                hideLoading();
                return;
            }

            // =========================================
            // LIVE DATA
            // =========================================

            const payload = msg.data || msg;

            if (payload.command_name && payload.hex_data) {

                const parsed = parsePI30Response(
                    payload.command_name,
                    payload.hex_data
                );

                if (parsed) {
                    console.log(`📊 ${payload.command_name} распознан:`, parsed);

                    updateUIByData(parsed);
                    syncPWMControl(parsed);
                    updateStartStopButtonState(parsed);
                    updateModeButtonsState(parsed);
                    updateEnergyDisplay(parsed);
                    hideLoading();
                }

            } else {

                console.log("📨 Получены данные маховика:", msg);
            }

        } catch (e) {
            console.error("❌ Ошибка парсинга сообщения WebSocket:", e);
        }
    };

    ws.onerror = (err) => console.error('❌ COR WS error', err);
    ws.onclose = () => console.warn('⚠️ COR WS closed, переподключение через 3 сек...');

    if (window.lastData) {
        setTimeout(() => {
            updateStartStopButtonState(window.lastData);
            updateModeButtonsState(window.lastData);
            if (window.lastData.pwmPercent !== undefined) {
                syncPWMControl(window.lastData);
            }
        }, 500);
    
};
}
/**
 * Преобразует HEX-строку в Uint8Array
 */
function hexToBytes(hex) {
    // Удаляем возможные пробелы
    hex = hex.replace(/\s/g, '');
    if (hex.length % 2 !== 0) {
        console.error('Неверная длина hex-строки');
        return null;
    }
    const bytes = new Uint8Array(hex.length / 2);
    for (let i = 0; i < bytes.length; i++) {
        bytes[i] = parseInt(hex.substr(i * 2, 2), 16);
    }
    return bytes;
}

/**
 * Парсит PI30 ответ из hex_data в зависимости от command_name.
 * Возвращает объект с полями или null при ошибке.
 */
function parsePI30Response(commandName, hexData) {
    const bytes = hexToBytes(hexData);
    if (!bytes || bytes.length < 3) {
        console.error('Слишком короткий PI30 ответ');
        return null;
    }

    // Последний байт должен быть 0x0D (CR)
    if (bytes[bytes.length - 1] !== 0x0D) {
        console.error('Отсутствует завершающий CR (0x0D) в PI30 ответе');
        return null;
    }

    // Извлекаем ASCII часть (до двух байт CRC)
    const asciiBytes = bytes.slice(0, bytes.length - 3);
    const crc = (bytes[bytes.length - 3] << 8) | bytes[bytes.length - 2];
    const asciiStr = new TextDecoder().decode(asciiBytes);

    // Проверяем, что строка начинается с '('
    if (!asciiStr.startsWith('(')) {
        console.error('Ответ PI30 не начинается с "("');
        return null;
    }

    // Убираем скобку и разбиваем по пробелам
    const values = asciiStr.substring(1).trim().split(/\s+/);

    let result = {
        command: commandName,
        crc: crc,
        rawAscii: asciiStr,
    };

    // Парсинг в зависимости от команды
    switch (commandName) {
        case 'FWSTATUS':

    if (values.length >= 5) {

        const temperatures = values.slice(0, 4).map(Number);
        const statusByte = parseInt(values[4], 10);

        // температуры отдельно
        result.temp1 = temperatures[0];
        result.temp2 = temperatures[1];
        result.temp3 = temperatures[2];
        result.temp4 = temperatures[3];

        // массив тоже сохраняем
        result.temperatures = temperatures;

        // статусные биты
        result.startActive  = !!(statusByte & 1); // бит 0
        result.delayExpired = !!(statusByte & 2); // бит 1
        result.genMode      = !!(statusByte & 4); // бит 2

        result.statusByte = statusByte;

        // текст режима
        result.modeText = result.genMode ? "GENERATOR" : "MOTOR";

        // ===== ЛОГИ =====

        console.log(
            `🌡 Температуры статора: ` +
            `T1=${result.temp1}°C, ` +
            `T2=${result.temp2}°C, ` +
            `T3=${result.temp3}°C, ` +
            `T4=${result.temp4}°C`
        );

        console.log(
            `⚙️ Статус: ` +
            `startActive=${result.startActive}, ` +
            `delayExpired=${result.delayExpired}, ` +
            `genMode=${result.genMode} (${result.modeText}), ` +
            `statusByte=${statusByte}`
        );

    } else {

        console.error(
            'Неверное количество полей в FWSTATUS. ' +
            'Ожидается 5 значений (4 температуры + статусный байт)'
        );
    }

    break;

        case 'FWMSTATUS':
            // Ожидается: (speed pwmPercent freqHz timerArr pwmRaw)
            if (values.length >= 5) {
                result.speed = parseInt(values[0], 10);
                result.pwmPercent = parseInt(values[1], 10);
                result.freqHz = parseInt(values[2], 10);
                result.timerArr = parseInt(values[3], 10);
                result.pwmRaw = parseInt(values[4], 10);
                console.log(`🔄 Мотор: скорость=${result.speed} об/мин, ШИМ=${result.pwmPercent}%, частота=${result.freqHz} Гц`);
                console.log(`🔧 ARR=${result.timerArr}, PWM_raw=${result.pwmRaw}`);
            } else {
                console.warn('Неверное количество полей в FWMSTATUS, ожидается 5 значений (speed, pwm%, freqHz, ARR, PWM_raw)');
            }
            break;

        case 'FWDCSTATUS':
            // Ожидается: (U I P)
            if (values.length >= 3) {
                result.voltage = parseFloat(values[0]);
                result.current = parseFloat(values[1]);
                result.power   = parseInt(values[2], 10);
                console.log(`⚡ DC: U=${result.voltage}В, I=${result.current}A, P=${result.power}Вт`);
            } else {
                console.warn('Неверное количество полей в FWDCSTATUS, ожидается 3 значения (U, I, P)');
            }
            break;

        case 'FWGSTATUS':
            // Заглушка
            console.log('🔧 Получен FWGSTATUS, содержимое:', asciiStr);
            result.data = asciiStr;
            break;

        case 'FWSET':
            // Заглушка
            console.log('⚙️ Получен FWSET, содержимое:', asciiStr);
            result.data = asciiStr;
            break;

        default:
            console.warn(`Неизвестная команда PI30: ${commandName}`);
            result.data = asciiStr;
    }

    return result;
}



// ======================================================
// PI30 COMMAND SENDER
// ======================================================
async function sendPI30Command(pi30, pi30Value = "") {
    // Проверяем токен
    if (typeof checkToken === 'function') await checkToken();
    const token = typeof getToken === 'function' ? getToken() : localStorage.getItem("access_token");

    if (!token) {
        if (typeof showPopup === 'function') {
            showPopup({
                type: "error",
                title: "Нет авторизации",
                message: "Токен отсутствует"
            });
        }
        return false;
    }

    // Проверяем deviceId
    if (!currentDeviceId) {
        console.error("❌ Нет device_id для отправки команды");
        if (typeof showPopup === 'function') {
            showPopup({
                type: "error",
                title: "Ошибка",
                message: "Устройство не инициализировано"
            });
        }
        return false;
    }

    try {
        const response = await fetch("/api/energetic/pi30/send_command", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "Authorization": `Bearer ${token}`
            },
            body: JSON.stringify({
                session_token: currentDeviceId, 
                pi30: pi30,
                pi30_value: pi30Value,
                pause_background_polling: true,
                response_timeout_seconds: 4
            })
        });

        // Обработка 401 Unauthorized
        if (response.status === 401) {
            localStorage.removeItem("access_token");
            if (typeof showPopup === 'function') {
                showPopup({
                    type: "error",
                    title: "Сессия истекла",
                    message: "Требуется повторный вход"
                });
            }
            return false;
        }

        // Парсим ответ
        let json = {};
        try {
            json = await response.json();
        } catch (_) {}

        if (!response.ok) {
            console.error("❌ PI30 command failed:", json);
            if (typeof showPopup === 'function') {
                showPopup({
                    type: "error",
                    title: "Ошибка",
                    message: json.detail || "Ошибка отправки команды"
                });
            }
            return false;
        }

        console.log("✅ PI30 command success:", json);
        return json;

    } catch (err) {
        console.error("❌ PI30 request error:", err);
        if (typeof showPopup === 'function') {
            showPopup({
                type: "error",
                title: "Нет соединения",
                message: "Не удалось отправить PI30 команду"
            });
        }
        return false;
    }
}

// ======================================================
// START / STOP BUTTON - динамическая инициализация
// ======================================================


function initStartStopButton() {
    const startStopBtn = document.getElementById("startStopBtn");
    if (!startStopBtn) {
        console.warn("⚠️ Кнопка startStopBtn пока не найдена");
        return false;
    }

    if (startStopInitialized) {
        console.log("✅ Кнопка уже инициализирована");
        return true;
    }

    console.log("✅ Кнопка найдена, навешиваем обработчик");
    startStopBtn.addEventListener("click", async () => {
        if (startStopPending) {
            console.log("⏳ Уже ожидается ответ, повторная команда игнорируется");
            return;
        }

        const isRunning = startStopBtn.dataset.state === "running";
        const command = isRunning ? "OFF" : "ON";

        // Переводим кнопку в режим ожидания
        startStopPending = true;
        startStopBtn.disabled = true;
        const originalText = startStopBtn.textContent;
        startStopBtn.textContent = "Ожидание...";

        console.log("📤 Отправка команды:", command);
        const ok = await sendPI30Command("FWSTART", command);

        if (!ok) {
            // Ошибка – возвращаем кнопку в исходное состояние
            startStopPending = false;
            startStopBtn.disabled = false;
            startStopBtn.textContent = originalText;
            console.error("❌ Команда не отправлена");
            return;
        }

        // Успех – теперь ждём обновления от WebSocket (startActive)
        // Кнопка остаётся disabled до прихода нового статуса
        console.log("✅ Команда отправлена, ожидаем подтверждения от устройства");
    });

    startStopInitialized = true;
    return true;
}




function initPWMControls() {
    if (pwmInitialized) return true;

    const plusBtn = document.getElementById("pwmPlusBtn");
    const minusBtn = document.getElementById("pwmMinusBtn");
    const pwmValueSpan = document.getElementById("pwmControlValue");

    if (!plusBtn || !minusBtn || !pwmValueSpan) {
        console.warn("⚠️ PWM элементы не найдены, повторная попытка позже");
        return false;
    }

const updatePWM = async (delta) => {
    if (pwmChangePending) {
        console.log("⏳ Уже ожидается ответ на предыдущую команду ШИМ");
        return;
    }

    let current = parseInt(pwmValueSpan.textContent, 10);
    if (isNaN(current)) {
        const lastPwm = window.lastData?.pwmPercent;
        current = (typeof lastPwm === 'number') ? lastPwm : 0;
        pwmValueSpan.textContent = current;
    }
    let newValue = current + delta;
    if (newValue < 0) newValue = 0;
    if (newValue > 100) newValue = 100;
    if (newValue === current) return;

    // Устанавливаем флаг ожидания
    pwmChangePending = true;
    pendingPwmValue = newValue;

    // Оптимистичное обновление UI
    pwmValueSpan.textContent = newValue;
    const dataValueSpan = document.querySelector('.data-value[data-source="pwmPercent"]');
    if (dataValueSpan) dataValueSpan.textContent = newValue;

    const ok = await sendPI30Command("FWPWMSET", String(newValue));

    if (!ok) {
        // Ошибка: откатываем UI и снимаем флаг
        pwmChangePending = false;
        pendingPwmValue = null;
        pwmValueSpan.textContent = current;
        if (dataValueSpan) dataValueSpan.textContent = current;
        console.error("❌ Ошибка установки PWM");
    } else {
        // Ждём подтверждения от устройства, но на всякий случай снимаем флаг через 2 секунды
        setTimeout(() => {
            if (pwmChangePending) {
                console.warn("⚠️ Таймаут ожидания подтверждения ШИМ, снимаем блокировку");
                pwmChangePending = false;
                pendingPwmValue = null;
            }
        }, 2000);
    }
};

    // Удаляем старые обработчики (если были) через клонирование
    const newPlusBtn = plusBtn.cloneNode(true);
    const newMinusBtn = minusBtn.cloneNode(true);
    plusBtn.parentNode.replaceChild(newPlusBtn, plusBtn);
    minusBtn.parentNode.replaceChild(newMinusBtn, minusBtn);

    newPlusBtn.addEventListener("click", () => updatePWM(1));
    newMinusBtn.addEventListener("click", () => updatePWM(-1));

    pwmInitialized = true;
    console.log("✅ PWM controls initialized");
    return true;
}



function initModeButtons() {
    const modeBtns = document.querySelectorAll(".mode-btn");
    if (modeBtns.length !== 2) return false;

    if (modeButtonsInitialized) return true;

    const chargeBtn = Array.from(modeBtns).find(btn => btn.textContent.trim() === "Заряд");
    const dischargeBtn = Array.from(modeBtns).find(btn => btn.textContent.trim() === "Разряд");

    if (!chargeBtn || !dischargeBtn) return false;

    const switchMode = async (targetMode) => {
        if (modeChangePending) return;
        // targetMode: "MOTOR" (заряд) или "GENERATOR" (разряд)
        const newGenMode = (targetMode === "GENERATOR");
        // Проверяем, не совпадает ли с текущим режимом
        const currentGenMode = (window.lastData?.genMode === true);
        if (currentGenMode === newGenMode) {
            console.log("Режим уже установлен");
            return;
        }

        modeChangePending = true;
        chargeBtn.disabled = true;
        dischargeBtn.disabled = true;

        // Отправляем команду переключения режима
        const commandParam = targetMode; // "MOTOR" или "GENERATOR"
        const ok = await sendPI30Command("FWGMODE", commandParam);

        if (!ok) {
            modeChangePending = false;
            chargeBtn.disabled = false;
            dischargeBtn.disabled = false;
            console.error("❌ Не удалось переключить режим");
        } else {
            console.log("✅ Команда на смену режима отправлена, ожидаем обновления genMode");
        }
    };

    chargeBtn.addEventListener("click", () => switchMode("MOTOR"));
    dischargeBtn.addEventListener("click", () => switchMode("GENERATOR"));

    modeButtonsInitialized = true;
    return true;
}



function syncPWMControl(data) {
    if (pwmChangePending) {
        // Если пришло значение, равное отправленному, снимаем флаг
        if (pendingPwmValue !== null && data.pwmPercent === pendingPwmValue) {
            console.log("✅ Получено подтверждение ШИМ =", data.pwmPercent);
            pwmChangePending = false;
            pendingPwmValue = null;
        } else {
            console.log("⏸️ Пропускаем обновление ШИМ, ожидается ответ");
            return;
        }
    }

    if (data && data.pwmPercent !== undefined) {
        const pwmSpan = document.getElementById("pwmControlValue");
        if (pwmSpan && String(pwmSpan.textContent) !== String(data.pwmPercent)) {
            pwmSpan.textContent = data.pwmPercent;
        }
    }
}




function updateStartStopButtonState(data) {
    const startStopBtn = document.getElementById("startStopBtn");
    if (!startStopBtn) return;

    // Если есть ожидание – не меняем UI, пока не придёт подтверждение
    if (startStopPending) {
        // Если пришёл актуальный статус, снимаем ожидание
        if (data.startActive !== undefined) {
            startStopPending = false;
        } else {
            return;
        }
    }

    // Обновляем по полю startActive
    if (data.startActive !== undefined) {
        const isRunning = data.startActive === true;
        if (isRunning) {
            startStopBtn.dataset.state = "running";
            startStopBtn.textContent = "STOP";
            startStopBtn.classList.add("stop");
        } else {
            startStopBtn.dataset.state = "stopped";
            startStopBtn.textContent = "START";
            startStopBtn.classList.remove("stop");
        }
        startStopBtn.disabled = false;  // снимаем блокировку
    }
}

function initResetButton() {
    const resetBtn = document.getElementById("ResetBtn");
    if (!resetBtn) {
        console.warn("⚠️ Кнопка ResetBtn не найдена");
        return false;
    }

    if (resetInitialized) return true;

    resetBtn.addEventListener("click", async () => {
        if (pwmChangePending) {
            console.log("⏳ Уже выполняется команда ШИМ, повторите позже");
            return;
        }

        resetBtn.disabled = true;
        const originalText = resetBtn.textContent;
        resetBtn.textContent = "Сброс...";

        console.log("🔄 Отправка RESET: установка ШИМ = 0%");

        // Блокируем обновления
        pwmChangePending = true;
        pendingPwmValue = 0;

        // Оптимистичное обновление интерфейса
        const pwmControlSpan = document.getElementById("pwmControlValue");
        if (pwmControlSpan) pwmControlSpan.textContent = "0";
        const dataValueSpan = document.querySelector('.data-value[data-source="pwmPercent"]');
        if (dataValueSpan) dataValueSpan.textContent = "0";

        const success = await sendPI30Command("FWPWMSET", "0");

        if (!success) {
            console.error("❌ Ошибка при сбросе ШИМ");
            pwmChangePending = false;
            pendingPwmValue = null;
            // Откат
            if (window.lastData?.pwmPercent !== undefined) {
                const lastPwm = window.lastData.pwmPercent;
                if (pwmControlSpan) pwmControlSpan.textContent = lastPwm;
                if (dataValueSpan) dataValueSpan.textContent = lastPwm;
            } else {
                if (pwmControlSpan) pwmControlSpan.textContent = "0";
                if (dataValueSpan) dataValueSpan.textContent = "--";
            }
        } else {
            setTimeout(() => {
                if (pwmChangePending) {
                    console.warn("⚠️ Таймаут ожидания подтверждения RESET, снимаем блокировку");
                    pwmChangePending = false;
                    pendingPwmValue = null;
                }
            }, 2000);
        }

        resetBtn.disabled = false;
        resetBtn.textContent = originalText;
    });

    resetInitialized = true;
    console.log("✅ Reset button initialized");
    return true;
}

function updateModeButtonsState(data) {
    if (data.genMode === undefined) return;

    const modeBtns = document.querySelectorAll(".mode-btn");
    if (modeBtns.length !== 2) return;

    // genMode: false = MOTOR (Заряд), true = GENERATOR (Разряд)
    const isMotor = (data.genMode === false);
    
    // Находим кнопки по тексту
    const chargeBtn = Array.from(modeBtns).find(btn => btn.textContent.trim() === "Заряд");
    const dischargeBtn = Array.from(modeBtns).find(btn => btn.textContent.trim() === "Разряд");

    if (chargeBtn && dischargeBtn) {
        if (isMotor) {
            chargeBtn.classList.add("active");
            dischargeBtn.classList.remove("active");
        } else {
            dischargeBtn.classList.add("active");
            chargeBtn.classList.remove("active");
        }
    }

    // Снимаем флаг ожидания, если он был
    if (modeChangePending) {
        modeChangePending = false;
        // Включаем кнопки обратно
        chargeBtn.disabled = false;
        dischargeBtn.disabled = false;
    }
}

/*

function waitForControls() {
    const observer = new MutationObserver((mutations, obs) => {
        const btn = document.getElementById("startStopBtn");
        if (btn) initStartStopButton();

        const pwmPlus = document.getElementById("pwmPlusBtn");
        if (pwmPlus && !pwmInitialized) initPWMControls();
        const modeBtns = document.querySelectorAll(".mode-btn");
        if (modeBtns.length === 2 && !modeButtonsInitialized) initModeButtons();

        if (startStopInitialized && pwmInitialized && modeButtonsInitialized) obs.disconnect();
    });
    observer.observe(document.body, { childList: true, subtree: true });
}


*/
function waitForControls() {
    const observer = new MutationObserver((mutations, obs) => {
        const btn = document.getElementById("startStopBtn");
        if (btn) initStartStopButton();

        const pwmPlus = document.getElementById("pwmPlusBtn");
        if (pwmPlus && !pwmInitialized) initPWMControls();
        
        const modeBtns = document.querySelectorAll(".mode-btn");
        if (modeBtns.length === 2 && !modeButtonsInitialized) initModeButtons();
        
        const resetBtn = document.getElementById("ResetBtn");
        if (resetBtn && !resetInitialized) initResetButton();

        if (startStopInitialized && pwmInitialized && modeButtonsInitialized && resetInitialized) obs.disconnect();
    });
    observer.observe(document.body, { childList: true, subtree: true });
}

waitForControls();



/**
 * Вычисляет кинетическую энергию маховика в Вт·ч
 * @param {number} rpm - скорость вращения, об/мин
 * @returns {number} энергия, Вт·ч (округлённая до 0.01)
 */
function computeEnergyWh(rpm) {
    if (typeof rpm !== 'number' || isNaN(rpm)) return 0;
    const energy = RPM2_WH * rpm * rpm;
    return Math.round(energy * 100) / 100;
}


/**
 * Обновляет отображение энергии маховика на основе полученных данных
 * @param {object} data - объект с полем speed (об/мин)
 */
function updateEnergyDisplay(data) {
    if (!data || data.speed === undefined) return;
    
    const speed = data.speed;
    const energyWh = computeEnergyWh(speed);
    
    const energyElement = document.querySelector('[data-source="energy"]');
    if (energyElement) {
        energyElement.textContent = energyWh;
        console.log(`⚡ Энергия маховика: ${energyWh} Вт·ч при ${speed} об/мин`);
    } else {
        console.warn('⚠️ Элемент с data-source="energy" не найден в DOM');
    }
}
