

   // === Выход ===
logoutBtn.addEventListener("click", async () => {
   await checkToken();
    window.location.href = "/static/COR_ID/devices.html";    
});




async function FactoryDefaults() {
    const token = sessionStorage.getItem("auth_token");

    if (!token) {
        showLogin();
        return;
    }

    const confirmed = confirm(
        "⚠️ СБРОС К ЗАВОДСКИМ НАСТРОЙКАМ\n\n" +
        "Будут удалены:\n" +
        "• Wi-Fi настройки\n" +
        "• Аккаунт и токены\n" +
        "• Все пользовательские параметры\n\n" +
        "Устройство перезагрузится.\n\n" +
        "Продолжить?"
    );

    if (!confirmed) return;

    console.warn("Factory reset requested…");

    try {
        const response = await fetch("/factory_reset", {
            method: "POST",
            headers: {
                "Authorization": "Bearer " + token
            }
        });

        if (response.status === 401) {
            sessionStorage.removeItem("auth_token");
            showTokenExpiredModal();
            return;
        }

        if (!response.ok) {
            const text = await response.text();
            alert("Ошибка сброса: " + text);
            return;
        }

        const json = await response.json();
        console.log("Factory reset:", json);

        // 🔥 Локальная очистка
        sessionStorage.removeItem("auth_token");

        // 🔥 Останавливаем всё
        if (typeof stopHeartbeat === "function") stopHeartbeat();

        if (window.reconnectTimer) {
            clearTimeout(window.reconnectTimer);
            window.reconnectTimer = null;
        }

        if (window.ws) {
            ws.onclose = null;
            ws.close();
            window.ws = null;
        }

        showFactoryResetInProgress();


    } catch (err) {
        console.error("Factory reset failed:", err);
       // alert("Ошибка соединения: " + err);
    }
}

async function rebootDevice() {
    try {
        const res = await sendDeviceSettings({
            system: "reboot"
        });

        if (!res) {
            showPopup({
                type: "error",
                title: "Ошибка",
                message: "Не удалось отправить команду"
            });
            return;
        }

        showPopup({
            type: "info",
            title: "Перезапуск",
            message: "Команда отправлена, ожидаем ответ устройства",
            timeout: 4000
        });

    } catch (err) {
        console.error("❌ reboot error:", err);

        showPopup({
            type: "error",
            title: "Ошибка",
            message: "Нет соединения"
        });
    }
}
async function saveUARTSettings() {

    const mode = parseInt(document.querySelector('input[name="uart-mode"]:checked')?.value || 0);
    const uartData = {
        mode: mode,
        baud: parseInt(document.getElementById("uart-baud").value, 10),
        parity: parseInt(document.getElementById("uart-parity").value, 10),
        stop_bits: parseInt(document.getElementById("uart-stop-bits").value, 10),
        data_bits: parseInt(document.getElementById("uart-data-bits").value, 10)
    };

    console.log("📤 UART settings:", uartData);

    try {

        const result = await sendDeviceSettings({
            uart: JSON.stringify(uartData)
        });

        if (!result) {

            showPopup({
                type: "error",
                title: "Ошибка",
                message: "Не удалось отправить UART настройки"
            });

            return;
        }

        showPopup({
            type: "success",
            title: "Сохранено",
            message: "UART настройки отправлены на устройство",
            timeout: 3000
        });

        console.log("✅ UART settings sent:", result);

    } catch (err) {

        console.error("UART send failed:", err);

        showPopup({
            type: "error",
            title: "Нет соединения",
            message: "Ошибка отправки UART настроек"
        });
    }
}


async function saveWiFiSettings() {
    const token = sessionStorage.getItem("auth_token");
    if (!token) {
        showLogin();
        return;
    }

    const wifiForm = document.getElementById("wifiForm");

    const payload = {
        mode: parseInt(document.getElementById("wifiMode").value),

        sta_ssid: wifiForm.querySelector("[name='sta-ssid']").value,
        sta_password: wifiForm.querySelector("[name='sta-password']").value,

        ap_ssid: wifiForm.querySelector("[name='ap-ssid']").value,
        ap_password: wifiForm.querySelector("[name='ap-password']").value,
        ap_channel: parseInt(
            wifiForm.querySelector("[name='ap-channel']").value
        ),
    };

    try {
        const res = await fetch('/save_settings/wifi', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': 'Bearer ' + token
            },
            body: JSON.stringify(payload)
        });

        // 🔐 Сессия истекла
        if (res.status === 401) {
            sessionStorage.removeItem("auth_token");
            showTokenExpiredModal();
            return;
        }

        let json = {};
        try {
            json = await res.json();
        } catch (_) {
            // backend может вернуть не JSON
        }

        if (!res.ok) {
            showPopup({
                type: "error",
                title: "Ошибка",
                message: json.message || "Ошибка сохранения Wi-Fi настроек"
            });
            return;
        }

        // ✅ Успех
        showPopup({
            type: "success",
            title: "Сохранено",
            message: json.message || "Wi-Fi настройки сохранены",
            timeout: 3000
        });

    } catch (err) {
        console.error("WiFi save failed:", err);
        showPopup({
            type: "error",
            title: "Нет соединения",
            message: "Не удалось сохранить Wi-Fi настройки"
        });
    }
}


    // === Сохранение настроек аккаунта ===
saveAccountBtn.addEventListener('click', async () => {
    const nodeName = document.getElementById("node-name").value.trim();
    const login = document.getElementById("account-login").value.trim();
    const password = document.getElementById("account-password").value;

    if (!login) {
        alert("Введите login");
        return;
    }

    const accountData = {
        node_name: nodeName,
        login: login,
        password: password
    };

    const result = await sendDeviceSettings({
        account: JSON.stringify(accountData)
    });

    if (result) {
        console.log("Аккаунт успешно сохранён:", result);
        alert("Настройки аккаунта сохранены");
    } else {
        alert("Ошибка сохранения аккаунта");
    }
});



// === Сохранение настроек пользователя ===
userForm.addEventListener("submit", async (e) => {
    e.preventDefault();

    const login = userForm.querySelector('[name="user-login"]').value.trim();
    const pass = userForm.querySelector('[name="user-password"]').value.trim();
    const confirm = userForm.querySelector('[name="user-confirm-password"]').value.trim();

    if (!login) {
        alert("Введите login");
        return;
    }

    if (pass !== confirm) {
        alert("Пароли не совпадают!");
        return;
    }

    if (pass.length < 5) {
        alert("Пароль должен содержать минимум 5 символов");
        return;
    }

    const userData = {
        login: login,
        password: pass
    };

    const result = await sendDeviceSettings({
        user: JSON.stringify(userData)
    });

    if (result) {
        console.log("Пользователь успешно сохранён:", result);

        showPopup({
            type: "success",
            title: "Сохранено",
            message: "Настройки пользователя сохранены 💾",
            timeout: 3000
        });

    } else {
        showPopup({
            type: "error",
            title: "Ошибка",
            message: "Ошибка сохранения пользователя"
        });
    }
});



        async function uploadLittleFS() {
           
            const fsFile = document.getElementById("firmwareFile").files[0];
            if (!fsFile) {
                alert("Select filesystem image!");
                return;
            }

            const otaSession = sessionStorage.getItem("ota_session");
            if (!otaSession) {
                alert("OTA session expired");
                return;
            }
            console.log("OTA session:", otaSession);
            const res = await fetch("/update_fs", {
                method: "POST",
                headers: {
                    "X-OTA-Session": otaSession
                },
                body: fsFile
            });

            if (!res.ok) {
                alert("Filesystem upload failed");
                return;
            }

            sessionStorage.removeItem("ota_session");
            alert("🎉 Update complete!");
        }



        async function waitDeviceOnline(
            timeoutMs = 90000,
            intervalMs = 1000
        ) {
            const start = Date.now();

            while (Date.now() - start < timeoutMs) {
                try {
                    const res = await fetch("/ping", {
                        method: "GET",
                        cache: "no-store"
                    });

                    if (res.ok) {
                        console.log("✅ Device is online");
                        return;
                    }
                } catch (e) {
                    console.log(" Device is offline");
                }

                await new Promise(r => setTimeout(r, intervalMs));
            }

            throw new Error("Device did not come online in time");
        }




saveNetworkBtn.addEventListener("click", async (e) => {
    e.preventDefault();

    const token = sessionStorage.getItem("auth_token");
    if (!token) {
        showLogin();
        return;
    }

    const netType = document.querySelector('input[name="netType"]:checked').value;

    const payload = {
        dhcp_enabled: document.getElementById("dhcp").checked,
        ip: document.getElementById("ip").value.trim(),
        mask: document.getElementById("mask").value.trim(),
        gateway: document.getElementById("gateway").value.trim(),
        dns: document.getElementById("dns").value.trim(),

        wifi_dhcp_enabled: document.getElementById("wifi_dhcp").checked,
        wifi_ip: document.getElementById("wifi_ip").value.trim(),
        wifi_mask: document.getElementById("wifi_mask").value.trim(),
        wifi_gateway: document.getElementById("wifi_gateway").value.trim(),
        wifi_dns: document.getElementById("wifi_dns").value.trim(),

        port: parseInt(document.getElementById("port").value, 10),
        net_type: netType // ethernet | wifi (если понадобится)
    };

    console.log("Saving NETWORK:", payload);

    try {
        const res = await fetch("/save_settings/network", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "Authorization": "Bearer " + token
            },
            body: JSON.stringify(payload)
        });

        if (res.status === 401) {
            sessionStorage.removeItem("auth_token");
            showTokenExpiredModal();
            showLogin();
            return;
        }

        let json = {};
        try { json = await res.json(); } catch {}

        if (!res.ok) {
            showPopup({
                type: "error",
                title: "Ошибка",
                message: json.message || "Ошибка сохранения сети"
            });
            return;
        }

        showPopup({
            type: "success",
            title: "Сохранено",
            message: json.message || "Сетевые настройки сохранены 💾",
            timeout: 3000
        });

    } catch (err) {
        console.error("Network save failed:", err);
        showPopup({
            type: "error",
            title: "Нет соединения",
            message: "Не удалось сохранить сетевые настройки"
        });
    }
});


async function fetchFirmwareFiles() {
   await checkToken();
   const token = localStorage.getItem("access_token");
   if (!token) return [];

    try {
        const res = await fetch(`${API_BASE_URL}/api/energetic/firmware/files`, {
            method: "GET",
            headers: {
                "Authorization": `Bearer ${token}`
            }
        });

        if (!res.ok) {
            const text = await res.text();
            console.error("Ошибка загрузки прошивок:", text);
            return [];
        }

        const data = await res.json();
        console.log("📦 Firmware files:", data.files);
        return data.files || [];

    } catch (err) {
        console.error("❌ Ошибка запроса прошивок:", err);
        return [];
    }
}


async function startTraceroute() {
    const host = document.getElementById("tracerouteHost").value.trim();

    if (!host) {
        showPopup({
            type: "error",
            title: "Traceroute",
            message: "Введите адрес узла"
        });
        return;
    }

    // UI: старт
    const output   = document.getElementById("tracerouteOutput");
    const startBtn = document.getElementById("tracerouteBtn");
    const stopBtn  = document.getElementById("tracerouteStopBtn");

    output.textContent =
        `Traceroute → ${host}\n` +
        `Запуск...\n\n`;

    tracerouteRunning = true;
    if (startBtn) startBtn.disabled = true;
    if (stopBtn)  stopBtn.disabled  = false;

    // device_id из query
    const deviceId = getDeviceIdFromQuery();
    if (!deviceId) {
        showPopup({
            type: "error",
            title: "Traceroute",
            message: "Не найден device_id"
        });
        resetTracerouteUI();
        return;
    }

    try {
        await checkToken();
        const token = localStorage.getItem("access_token");
        if (!token) {
            showPopup({
                type: "error",
                title: "Сессия истекла",
                message: "Требуется повторная авторизация"
            });
            resetTracerouteUI();
            return;
        }

        const payload = {
            session_token: deviceId,
            url: host
        };

        console.log("📤 POST /api/energetic/traceroute:", payload);

        const res = await fetch(`${API_BASE_URL}/api/energetic/traceroute`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "Authorization": `Bearer ${token}`
            },
            body: JSON.stringify(payload)
        });

        const text = await res.text();

        if (!res.ok) {
            console.error("Ошибка HTTP:", text);

            let detail = "Не удалось запустить traceroute";
            try {
                const j = JSON.parse(text);
                if (j.detail) detail = j.detail;
            } catch (_) {}

            showPopup({
                type: "error",
                title: "Ошибка traceroute",
                message: detail
            });

            resetTracerouteUI();
            return;
        }

        const result = JSON.parse(text);
        console.log("✅ Traceroute отправлен:", result);

        // Устройство ответит по COR WS → handleCORSettingsMessage
        // → traceroute_result / traceroute_error

    } catch (err) {
        console.error("❌ Traceroute error:", err);

        showPopup({
            type: "error",
            title: "Нет соединения",
            message: "Ошибка отправки traceroute"
        });

        resetTracerouteUI();
    }
}


        /* Небольшой хелпер — сброс кнопок и флага */
        function resetTracerouteUI() {
            tracerouteRunning = false;
            const startBtn = document.getElementById("tracerouteBtn");
            if (startBtn) startBtn.disabled = false;
        }

/* ===================================================
 * Отрисовка ответа traceroute
 * =================================================== */
function handleTracerouteMessage(data) {
    const output = document.getElementById("tracerouteOutput");
    if (!output) return;

    // ---------- Прогресс по хопу ----------
    if (data.command_type === "traceroute_hop") {
        const ip = (data.ip && data.ip !== "*") ? data.ip : "*";

        const rtts = (data.rtt_ms || [])
            .map(v => (v === null || v < 0) ? "*" : `${v.toFixed(2)} ms`)
            .join("  ");

        const mark = data.destination ? "  ✅" : "";

        output.textContent +=
            `${String(data.hop).padStart(2, " ")}  ` +
            `${ip.padEnd(15, " ")}  ${rtts}${mark}\n`;

        output.scrollTop = output.scrollHeight;
        return;
    }

    // ---------- Ошибка ----------
    if (data.command_type === "traceroute_error") {
        const reason =
            data.error_name === "already_running" ? "трассировка уже запущена" :
            data.error_name === "missing_url"     ? "не указан хост" :
            data.error_name || "неизвестная ошибка";

        output.textContent +=
            `\n❌ Ошибка traceroute: ${reason} (code ${data.error})\n`;

        output.scrollTop = output.scrollHeight;
        resetTracerouteUI();
        return;
    }

    // ---------- Финальный результат ----------
    const lines = [];

    lines.push(
        `traceroute to ${data.host} (${data.destination}), ` +
        `${data.max_hops} hops max`
    );
    lines.push("");

    (data.hops || []).forEach(hop => {
        const ip = (hop.ip && hop.ip !== "*") ? hop.ip : "*";

        const rtts = (hop.rtt_ms || [])
            .map(v => (v === null || v < 0) ? "*" : `${v.toFixed(2)} ms`)
            .join("  ");

        const mark = hop.destination ? "  ✅" : "";

        lines.push(
            `${String(hop.hop).padStart(2, " ")}  ` +
            `${ip.padEnd(15, " ")}  ${rtts}${mark}`
        );
    });

    lines.push("");
    lines.push(
        data.reached
            ? "✅ Узел достигнут"
            : "⚠️ Узел не достигнут (нет ответов на верхних хопах)"
    );
    lines.push(`Всего шагов: ${data.hop_count}`);

    output.textContent = lines.join("\n");
    output.scrollTop = output.scrollHeight;

    resetTracerouteUI();
}





