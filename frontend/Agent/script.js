let corSettingsWS = null;
let currentSessionId = null;
let corWSReady = false;

document.addEventListener("DOMContentLoaded", async () => {
    await checkToken();
    const dhcpCheckbox = document.getElementById("dhcp");
    const wifiDhcpCheckbox = document.getElementById("wifi_dhcp");
    const deviceId = getDeviceIdFromQuery();
    if (!deviceId) return;

    startCORSettingsWS(deviceId);
    initAllEyeIcons();


        [...ethIpFields, ...wifiIpFields].forEach(id => {
        const field = document.getElementById(id);
        if (field) applyIPMask(field);
    });

        dhcpCheckbox.addEventListener("change", () => {
        console.log("ETH DHCP:", dhcpCheckbox.checked);
        updateEthIPFieldsState();
    });

    wifiDhcpCheckbox.addEventListener("change", () => {
        console.log("WiFi DHCP:", wifiDhcpCheckbox.checked);
        updateWifiIPFieldsState();
    });  
});

function updateNodeTitles(nodeName) {
    document.querySelectorAll(".node-title").forEach(el => {
        el.textContent = nodeName || "—";
    });
}

function diagnosticsOn() {
    diagnosticsActive = true;
    console.log("🛠️ Диагностика включена");
}

function diagnosticsOff() {
    diagnosticsActive = false;
    console.log("🛠️ Диагностика выключена");
}


function formatTime(date) {
    const h = String(date.getHours()).padStart(2, "0");
    const m = String(date.getMinutes()).padStart(2, "0");
    const s = String(date.getSeconds()).padStart(2, "0");
    return `${h}:${m}:${s}`;
}

function getDeviceIdFromQuery() {
    const params = new URLSearchParams(window.location.search);
    console.log("Device_ID:", Object.fromEntries(params.entries()));
    return params.get("device_id");
}

async function loadTabSettings(tab) {
    const flagMap = {
        "LAN": { network: true },
        "wifi": { wifi: true },
        "uart": { uart: true },
        "user": { user: true },
        "account": { account: true },
        "system": { system: true }
    };

    const flags = flagMap[tab];
    if (!flags) return;

    const data = await fetchDeviceSettings(flags);
    if (!data) return;

    applySettingsToForm(tab, data);
}



function applySettingsToForm(tab, data) {
    switch (tab) {
        case "LAN": {
            const net = data.network || {};

            // Ethernet
            document.getElementById("ip").value = net.ip || "";
            document.getElementById("mask").value = net.mask || "";
            document.getElementById("gateway").value = net.gateway || "";
            document.getElementById("dns").value = net.dns || "";
            document.getElementById("dhcp").checked = !!net.dhcp_enabled;
            // Wi-Fi
            document.getElementById("wifi_ip").value = net.wifi_ip || "";
            document.getElementById("wifi_mask").value = net.wifi_mask || "";
            document.getElementById("wifi_gateway").value = net.wifi_gateway || "";
            document.getElementById("wifi_dns").value = net.wifi_dns || "";
            document.getElementById("wifi_dhcp").checked = !!net.wifi_dhcp_enabled;

            document.getElementById("port").value = net.port || 80;
            break;
        }

        case "wifi": {
            const wifi = data.wifi || {};

            // Mode
            const modeField = document.getElementById("wifiMode");
            if (modeField) modeField.value = String(wifi.mode);

            // STA
            document.querySelector("[name='sta-ssid']").value = wifi.sta_ssid || "";
            document.querySelector("[name='sta-password']").value = wifi.sta_password || "";

            // AP
            document.querySelector("[name='ap-ssid']").value = wifi.ap_ssid || "";
            document.querySelector("[name='ap-password']").value = wifi.ap_password || "";
            document.querySelector("[name='ap-channel']").value = wifi.ap_channel || 1;

            updateWiFiVisibility();
            break;
        }


        case "uart": {
            const uart = data.uart || {};
            const mode = uart.rs485_mode ? "1" : "0";

            // обычные поля
            document.getElementById("uart-baud").value = uart.baud ;
            document.getElementById("uart-data-bits").value = uart.data_bits ?? 8;
            document.getElementById("uart-stop-bits").value = uart.stop_bits ?? 1;
            document.getElementById("uart-parity").value = uart.parity ?? 0;

            //сначала сбрасываем
            document.querySelectorAll("input[name='uart-mode']").forEach(r => {
                r.checked = false;
            });

            // ✅ применяем ПОСЛЕ рендера
            requestAnimationFrame(() => {
                const rb = document.querySelector(
                    `input[name='uart-mode'][value='${mode}']`
                );
                if (rb) rb.checked = true;
            });

            break;
        }

        case "user": {
            const user = data.user || {};
            document.querySelector("[name='user-login']").value = user.login || "";
            break;
        }

        case "account": {
            const account = data.user || {};
            const wsDiv = document.getElementById('ws-status');
            const color = account.connected ? 'green' : 'red';
            const icon = account.connected ? '✅' : '❌';
            document.querySelector("[name='account-login']").value = account.account_login || "";
            document.querySelector("[name='node-name']").value = account.node_name || "";
            updateNodeTitles(account.node_name || "");
            break;
        }

        case "system": {
            const system = data.system || {};
            buildNumber = system.build_number;
            buildDate = system.build_date;
    
            console.log(`Версия сборки: #${buildNumber} (${buildDate})`);

            const buildInfoText = document.getElementById("buildInfoText");
            if (buildInfoText) {
                buildInfoText.textContent = `Build #${buildNumber} • ${buildDate}`;
            }
            break;
        }

        default:
            console.warn(`Неизвестная вкладка: ${tab}`);
    }
}


const btn = document.getElementById("testAccountBtn"); 

btn.addEventListener("click", () => {
    const loginInput = document.querySelector("input[name='account-login']");
    const passwordInput = document.querySelector("input[name='account-password']");
    const nodeInput = document.querySelector("input[name='node-name']");
    const statusDiv = document.getElementById("ws-status");

    if (!testAccountActive) {
        // Включаем тестовый режим
        const login = loginInput.value.trim();
        const password = passwordInput.value.trim();
        const nodeName = nodeInput.value.trim();

        if (!login || !password) {
            statusDiv.textContent = "⚠️ Заполните логин и пароль";
            statusDiv.style.color = "orange";
            return;
        }

        statusDiv.textContent = "⏳ Включение тестового режима...";
        statusDiv.style.color = "blue";

        ws.send(JSON.stringify({
            action: "test_account",
            account_login: login,
            account_password: password ,
            node_name: nodeName
        }));

        testAccountActive = true;
        statusDiv.textContent = "✅ Тестовый режим включен";
        statusDiv.style.color = "green";
        btn.textContent = "Выйти из тестового режима"; // ← теперь работает

    } else {
        // Выключаем тестовый режим
        statusDiv.textContent = "⏳ Выключение тестового режима...";
        statusDiv.style.color = "blue";
        loadTabSettings("account");
        ws.send(JSON.stringify({
            action: "cancel_test_account"
        }));

        testAccountActive = false;
        statusDiv.textContent = "✅ Тестовый режим отключен";
        statusDiv.style.color = "green";
        btn.textContent = "Включить тестовый режим"; // ← теперь тоже работает
    }
});


document.getElementById("firmwareFile").addEventListener("change", function () {
    const nameBox = document.getElementById("fileName");
    if (this.files.length > 0) {
        nameBox.textContent = this.files[0].name;
    } else {
        nameBox.textContent = "Файл не выбран";
    }
});



// === Гамбургер меню ===
const menuButton = document.getElementById("menuButton");
const sideMenu = document.getElementById("sideMenu");
menuButton.addEventListener("click", () => {
    sideMenu.classList.toggle("open");
});

// === Переключение вкладок ===
const menuItems = document.querySelectorAll(".menu-item[data-tab]");
const tabContents = document.querySelectorAll(".tab-content");

    menuItems.forEach(item => {
        item.addEventListener("click", async () => {
            // Переключаем вкладки UI
            menuItems.forEach(i => i.classList.remove("active"));
            tabContents.forEach(t => t.classList.remove("active"));

            item.classList.add("active");
            const tabId = item.dataset.tab;

            // === Диагностика ===
            if (tabId === "diagnostics") {
                diagnosticsOn();
            } else {
                diagnosticsOff();
            }

            document.getElementById(tabId).classList.add("active");

            // Закрываем меню на мобилке
            sideMenu.classList.remove("open");

            // Загружаем настройки для конкретной вкладки
            await loadTabSettings(tabId);
        });
    });


 // Функция: форматирует IP (добавляет нули и точки)
 function formatIP(value) {
    // Убираем всё, кроме цифр
    let digits = value.replace(/\D/g, '');
    // Разбиваем по 3 цифры
    let parts = [];
    for (let i = 0; i < digits.length; i += 3) {
        parts.push(digits.substr(i, 3));
    }
    // Если неполный блок — дополним нулями
    parts = parts.map(p => p.padStart(3, '0'));
    return parts.join('.').substring(0, 15);
}



function updateWiFiVisibility() {
    const mode = parseInt(document.getElementById("wifiMode").value);

    const staFields = document.querySelectorAll(".wifi-sta");
    const apFields  = document.querySelectorAll(".wifi-ap");
    const lanFields = document.querySelectorAll(".wifi-lan");

    // Скрыть всё
    staFields.forEach(el => el.classList.add("hidden"));
    apFields.forEach(el => el.classList.add("hidden"));
    lanFields.forEach(el => el.classList.add("hidden"));

    switch (mode) {
        case 1: // STA
            staFields.forEach(el => el.classList.remove("hidden"));
            break;

        case 2: // AP
            apFields.forEach(el => el.classList.remove("hidden"));
            break;

        case 3: // APSTA
            staFields.forEach(el => el.classList.remove("hidden"));
            apFields.forEach(el => el.classList.remove("hidden"));
            break;

        case 0: // OFF
        default:
            // всё скрыто
            break;
    }
}
 document.getElementById("wifiMode").addEventListener("change", updateWiFiVisibility);

async function fetchDeviceSettings(flags = {}) {
    await checkToken();
    const token = localStorage.getItem("access_token");
    if (!token) return null;

    const deviceId = getDeviceIdFromQuery();
    if (!deviceId) return null;

    try {
        const res = await fetch(`${API_BASE_URL}/api/energetic/get_settings`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "Authorization": `Bearer ${token}`
            },
            body: JSON.stringify({
                session_token: deviceId,
                user: false,
                account: false,
                network: false,
                wifi: false,
                uart: false,
                system: false,
                all: false,
                ...flags
            })
        });

        const text = await res.text();

        if (!res.ok) {
            console.error("Ошибка HTTP:", text);
            if (text.includes("не подключено")) {
                showDeviceOfflineModal();
            }

            return null;
        }

        const result = JSON.parse(text);
        console.log("HTTP ответ:", result);

        return result;

    } catch (err) {
        console.error("❌ Fetch error:", err);
        showDeviceOfflineModal();

        return null;
    }
}



async function sendDeviceSettings(flags = {}) {
   await checkToken();
    const token = localStorage.getItem("access_token");
    if (!token) return null;

    const deviceId = getDeviceIdFromQuery();
    if (!deviceId) return null;

    try {

        const res = await fetch(`${API_BASE_URL}/api/energetic/send_settings`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "Authorization": `Bearer ${token}`
            },
            body: JSON.stringify({
                session_token: deviceId,

                user: null,
                account: null,
                network: null,
                wifi: null,
                uart: null,
                system: null,
                traceroute: null,
                all: null,

                ...flags
            })
        });

        if (!res.ok) {
            const err = await res.text();
            console.error("Ошибка HTTP:", err);
            return null;
        }

        const result = await res.json();

        console.log("Ответ send_settings:", result);

        return result;

    } catch (err) {

        console.error("Ошибка отправки настроек:", err);
        return null;

    }
}



    
// === Функция получения настроек с сервера ===
    async function fetchSettings() {
        await checkToken();
        const token = sessionStorage.getItem("auth_token");
        if (!token) return;

        try {
            const res = await fetch("/get_settings", {
                method: "GET",
                headers: {
                    "Authorization": `Bearer ${token}`
                }
            });

            if (res.status === 401) {
               // alert("Сессия истекла, пожалуйста, авторизуйтесь снова 💩");
                sessionStorage.removeItem("auth_token");
                showTokenExpiredModal();
               
                return;
            }

            if (!res.ok) {
                const text = await res.text();
                alert(`Ошибка получения настроек: ${text}`);
                return;
            }

            const data = await res.json();
            window.allSettings = data; // Сохраняем глобально
            console.log("Полученные настройки:", data);

            // === Заполняем форму LAN ===
            
            if (data.network) {
                const net = data.network;
               
                document.getElementById("dhcp").checked = !!net.dhcp_enabled;
                document.getElementById("wifi_dhcp").checked = !!net.wifi_dhcp_enabled;

                // Обновляем состояние полей IP в зависимости от DHCP
                updateAllIPFieldsState();
            }
            
               // === Автоматически применяем настройки на активную вкладку ===
          const activeTab = document.querySelector(".menu-item.active")?.dataset.tab || "account";
          loadTabSettings(activeTab);
        return data; 


        } catch (err) {
            alert("Ошибка соединения при получении настроек: " + err);
        }
    }



  
  // === Проверка корректности IP ===
function validateIP(ip) {
    const parts = ip.split(".");
    if (parts.length !== 4) return false;
    return parts.every(p => {
        const num = parseInt(p, 10);
        return !isNaN(num) && num >= 0 && num <= 255;
    });
}


    // === Обработчик сохранения ===
    document.getElementById("lanForm").addEventListener("submit", (e) => {
        e.preventDefault();
        const formData = new FormData(e.target);
        const params = new URLSearchParams(formData);

        // Проверяем все IP
        const invalid = ipFields.find(id => !validateIP(document.getElementById(id).value));
        if (invalid) {
            alert(`Поле "${invalid}" заполнено неверно 💩`);
            document.getElementById(invalid).focus();
            return;
        }

        alert("Настройки сети сохранены ✅");
        console.log("LAN settings:", Object.fromEntries(formData));
        // TODO: fetch('/save_network', {...})
    });


function setFieldsDisabled(fieldIds, disabled) {
    fieldIds.forEach(id => {
        const field = document.getElementById(id);
        if (!field) return;

        field.disabled = disabled;
        field.classList.toggle("ip-disabled", disabled);

        // 💡 чтобы required не ломал форму
        if (disabled) {
            field.removeAttribute("required");
        } else {
            field.setAttribute("required", "required");
        }
    });
}


function updateEthIPFieldsState() {
    const disabled = dhcpCheckbox.checked;
    setFieldsDisabled(ethIpFields, disabled);
}

function updateWifiIPFieldsState() {
    const disabled = wifiDhcpCheckbox.checked;
    setFieldsDisabled(wifiIpFields, disabled);
}

function updateAllIPFieldsState() {
    updateEthIPFieldsState();
    updateWifiIPFieldsState();
}



 // === Форматирование и маска ввода ===
 function applyIPMask(input) {
     input.addEventListener("input", (e) => {
         let value = e.target.value.replace(/[^\d]/g, "");
         let parts = [];
 
         // Разбиваем по 3 цифры (но не добавляем ведущие нули)
         for (let i = 0; i < value.length && parts.length < 4; i += 3) {
             parts.push(value.substring(i, i + 3));
         }
 
         // Соединяем с точками
         e.target.value = parts.join(".");
 
         // Автопереход курсора, когда введено 3 цифры и нет точки
         if (e.inputType === "insertText" && value.length < 12 && value.length % 3 === 0 && !e.target.value.endsWith(".")) {
             e.target.value += ".";
         }
     });
 
     // При фокусе показываем шаблон, если пусто
     input.addEventListener("focus", (e) => {
         if (e.target.value.trim() === "") e.target.placeholder = "___.___.___.___";
     });
 
     // При потере фокуса — завершаем IP и проверяем
     input.addEventListener("blur", (e) => {
         let parts = e.target.value.split(".").filter(Boolean);
 
         // Дополняем до 4 октетов
         while (parts.length < 4) parts.push("0");
 
         // Убираем лишние ведущие нули
         parts = parts.map(p => String(parseInt(p || "0", 10)));
 
         e.target.value = parts.join(".");
 
         // Проверка корректности
         if (!validateIP(e.target.value)) {
             e.target.style.border = "2px solid red";
             e.target.title = "Некорректный IP адрес";
         } else {
             e.target.style.border = "";
             e.target.title = "";
         }
     });
 }
 
function startOTAFromSelect() {
    const select = document.getElementById("firmwareSelect");
    const filename = select.value;

    if (!filename) {
        showPopup({
            type: "error",
            title: "Ошибка",
            message: "Выберите прошивку"
        });
        return;
    }

    uploadFirmware(filename);
}




async function uploadFirmware(filename = null) {
  try {
   await checkToken();
    const deviceId = getDeviceIdFromQuery();
    const accessToken = localStorage.getItem("access_token");

    if (!deviceId) throw new Error("device_id отсутствует");
    if (!accessToken) throw new Error("Нет access_token");

    console.log("🚀 Запуск OTA для:", deviceId);

    const response = await fetch(`${API_BASE_URL}/api/energetic/firmware/update`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${accessToken}`   // ← ВАЖНО
      },
      body: JSON.stringify({
        session_token: deviceId,
        ...(filename && { filename })
      })
    });

    const data = await response.json();

    if (!response.ok) {
      throw new Error(data.detail || "Firmware update failed");
    }

    showPopup({
      type: "success",
      title: "OTA запущена",
      message: data.message
    });

    return data;

  } catch (error) {
    console.error("Firmware update error:", error);

    showPopup({
      type: "error",
      title: "Ошибка OTA",
      message: error.message
    });

    throw error;
  }
}


const uploadBtn = document.getElementById("uploadBtn");
const firmwareSelect = document.getElementById("firmwareSelect");
const firmwareFile = document.getElementById("firmwareFile");

// проверка состояния
function updateUploadButtonState() {
    const hasSelect = firmwareSelect.value && firmwareSelect.value !== "";
    const hasFile = firmwareFile.files && firmwareFile.files.length > 0;

    uploadBtn.disabled = !(hasSelect || hasFile);
}

// при выборе из списка
firmwareSelect.addEventListener("change", updateUploadButtonState);

// при выборе файла
firmwareFile.addEventListener("change", updateUploadButtonState);




function md5(arrayBuffer) {
    const bytes = new Uint8Array(arrayBuffer);
    const words = [];
    for (let i = 0; i < bytes.length; i++) {
        words[i >>> 2] |= bytes[i] << (24 - (i % 4) * 8);
    }
    const wordArray = CryptoJS.lib.WordArray.create(words, bytes.length);
    return CryptoJS.MD5(wordArray).toString();
}


function addDiagRow(type, payload) {
    if (!diagList) return;

    const now = new Date();
    const timeStr = formatTime(now);

    let interval = "-";
    if (lastDiagTimestamp) {
        const diffMs = now - lastDiagTimestamp;
        interval = `+${diffMs}ms`;
    }
    lastDiagTimestamp = now;

    const row = document.createElement("div");
    row.className = "diag-row";

    // =========================
    // 🔥 НОРМАЛИЗАЦИЯ
    // =========================
    let data = {};
    if (typeof payload === "object" && payload !== null) {
        data = payload.data || payload;
    }

    const commandType = data.command_type || data.cmd || null;
    const category = data.category || null;
    const hexResponse = data.hex_response || null;

    // =========================
    // 🎯 ОПРЕДЕЛЕНИЕ ТИПА
    // =========================
    let msgType = "unknown";

    if (hexResponse) {
        msgType = "modbus";
    } else if (commandType === "settings_response") {
        msgType = "settings";
    } else if (commandType === "set_settings_ack") {
        msgType = "settings_ack";
    } else if (commandType) {
        msgType = "command";
    } else if (data.type === "ping") { 
        msgType = "ping";
    } else if (data.type === "connection_established") {
        msgType = "connection";
    } else if (data.ota_phase || data.progress !== undefined) {
        msgType = "ota";
    } else if (data.type === "network") {
        msgType = "network";
    }
    // =========================
    // 🧠 HEADER (самое важное)
    // =========================
    let headerText = "";

    switch (msgType) {
        case "modbus":
            if (hexResponse === "No response from RS485") {
                headerText = "❌ Modbus timeout";
            } else {
                headerText = `📥 Modbus (${commandType || "resp"})`;

                if (hexResponse) {
                    headerText += ` | ${hexResponse.slice(0, 24)}...`;
                }
            }
            break;

        case "settings":
            headerText = `⚙️ Settings: ${category || "unknown"}`;
            break;

        case "settings_ack":
            headerText = `✅ Settings applied`;
            break;

        case "command":
            headerText = `📤 Command: ${commandType}`;
            break;

        case "ping":  
            headerText = `📡 PING`;
            break;
        case "connection":
            headerText = `🔗 Connection established`;  
            break;
        case "ota":
            const phase = data.ota_phase || "Загрузка";
            const percent = data.progress !== undefined ? ` (${data.progress}%)` : "";
            headerText = `🔄 OTA: ${phase}${percent}`;
            break;
        case "network":
            headerText = "🌐 Network status update";
            break;   
        default:
            headerText = "❓ Unknown message";
    }

    // =========================
    // 📦 ПОЛНЫЙ JSON
    // =========================
    const fullJson =
        typeof payload === "object"
            ? JSON.stringify(payload, null, 2)
            : String(payload);

    // =========================
    // 🎨 TYPE CLASS
    // =========================
    let typeClass = type.toLowerCase(); // tx / rx

    if (msgType === "modbus" && hexResponse === "No response from RS485") {
        typeClass += " error";
    }

    // =========================
    // 🧱 РЕНДЕР
    // =========================
    row.innerHTML = `
        <span class="diag-type ${typeClass}">${type}</span>
        <span class="diag-interval">${interval}</span>
        <div class="diag-content">
            <div class="diag-header">${headerText}</div>
            <pre class="diag-value hidden">${fullJson}</pre>
        </div>
    `;

    // =========================
    // 🔽 TOGGLE JSON
    // =========================
    row.querySelector(".diag-header").addEventListener("click", () => {
        row.querySelector(".diag-value").classList.toggle("hidden");
    });

    // =========================
    // 📜 APPEND
    // =========================
    diagList.appendChild(row);
    diagList.scrollTop = diagList.scrollHeight;

    while (diagList.children.length > DIAG_MAX_ROWS) {
        diagList.removeChild(diagList.firstChild);
    }
}


document.querySelectorAll('input[name="netType"]').forEach(radio => {
    radio.addEventListener("change", () => {
        document.getElementById("ethernetBlock")
            .classList.toggle("hidden", radio.value !== "ethernet");

        document.getElementById("wifiBlock")
            .classList.toggle("hidden", radio.value !== "wifi");
    });
});



function showPopup({
    type = "info",
    title = "",
    message = "",
    timeout = 2000
}) {
    const container = document.getElementById("popup-container");
    if (!container) return;

    const popup = document.createElement("div");
    popup.className = `popup ${type}`;

    popup.innerHTML = `
        ${title ? `<h4>${title}</h4>` : ""}
        <div>${message}</div>
    `;

    container.appendChild(popup);

    if (timeout > 0) {
        setTimeout(() => {
            popup.style.opacity = "0";
            setTimeout(() => popup.remove(), 300);
        }, timeout);
    }

    return popup;
}

function handleCORSettingsMessage(msg) {

       // 📡 Диагностика — логируем всё
    console.log("Получено сообщение:", msg);
        addDiagRow("WS", msg);
      const payload = msg.data || {};
         // === OTA прогресс ===
    if (msg.data && (msg.data.ota_phase || msg.data.progress !== undefined)) {
        updateOTAProgress(msg.data); 
        return;
    }

         // === NETWORK STATUS ===
    if (msg.data && msg.data.type === "network" && msg.data.network) {

        function updateNetStatus(elementId, label, state) {
            const el = document.getElementById(elementId);
            if (!el) return;

            const allowedStates = ["up", "down", "connecting"];
            const status = allowedStates.includes(state) ? state : "unknown";

            el.classList.remove("up", "down", "connecting", "unknown");
            el.classList.add("net-status", status);
          //  el.textContent = `${label}: ${status.toUpperCase()}`;

            if (label === "Wi-Fi") {
                const rssi = msg.data.network?.wifi_sta?.rssi;
                let rssiText = "";
                let rssiColor = "";
                if (typeof rssi === "number") {
                    rssiText = ` RSSI ${rssi} dBm`;
                    if (rssi > -60) rssiColor = "green";
                    else if (rssi > -70) rssiColor = "orange";
                    else rssiColor = "red";
                }
                el.innerHTML = `${label}: ${status.toUpperCase()} <span style="color:${rssiColor}">${rssiText}</span>`;
            } else {
                el.textContent = `${label}: ${status.toUpperCase()}`;
            }
        }

        updateNetStatus(
            "ethernet-status",
            "Ethernet",
            msg.data.network?.ethernet?.state
        );

        updateNetStatus(
            "wifi-status",
            "Wi-Fi",
            msg.data.network?.wifi_sta?.state
        );

        return;
    }

    // 3️⃣ Cloud status
    if (msg.cloud_status) {
        console.log("☁️ Cloud status:", msg.cloud_status);
        window.corCloudState = { status: msg.cloud_status, session: msg.session_token };
        updateCloudStatusUI(window.corCloudState);
        return;
    }

    // 4️⃣ ACK от устройства (set_settings_ack)
    if (payload.command_type === "set_settings_ack") {

        console.log("✅ ACK от устройства:", payload);

        // 🔥 reboot
        if (payload.action === "reboot") {

            showPopup({
                type: "info",
                title: "Перезагрузка",
                message: "Устройство уходит в перезагрузку..."
            });

            // можно сразу показать offline
            setTimeout(() => {
                showPopup({
                    type: "info",
                    title: "Ожидание",
                    message: "Ожидаем повторное подключение устройства"
                });
            }, 1500);

            return;
        }

      

        return;
    }


    // 2️⃣ Новая структура settings_response
    if (payload.command_type === "settings_response") {
        const category = payload.category;
        const data = payload.data;

        if (!category || !data) {
            console.warn("⚠️ Некорректный settings_response:", msg);
            return;
        }
        parseCORSettings(category, data);
        return;
    }

    // 3️⃣ Сообщение о подключении (connection_established)
    if (msg.type === "connection_established") {
        console.log("🔗 WS подключение установлено для устройства", msg.device_id);
         fetchDeviceSettings({ all: true });
           populateFirmwareDropdown();
        return;
    }
    
    // 4️⃣ ping
    if (msg.type === "ping") {
        addDiagRow("PING", msg.timestamp);
        return;
    }

    // === 🔍 TRACEROUTE RESULT / ERROR / HOP ===
        if (payload.command_type === "traceroute_result" ||
            payload.command_type === "traceroute_error"  ||
            payload.command_type === "traceroute_hop")
        {
            handleTracerouteMessage(payload);
            return;
        }

        if (payload.command_type === "traceroute_started") {
            // информационное — просто игнорируем
            return;
        }
    console.warn("⚠️ Неизвестное сообщение:", msg);
}




function startCORSettingsWS(sessionId) {

    if (corSettingsWS) return;

    const wsUrl = buildAuthenticatedWebSocketUrl(
        `wss://dev.monitoring.cor-int.com/dev-modbus/responses?device_id=${encodeURIComponent(sessionId)}`
    );
    console.log("🌐 COR Settings WS URL:", maskWebSocketUrlForLog(wsUrl));
    corSettingsWS = new WebSocket(wsUrl);

    corSettingsWS.onopen = () => {
        console.log("✅ COR Settings WS подключён");
        corWSReady = true;
    };

    corSettingsWS.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        handleCORSettingsMessage(msg);
    };

    corSettingsWS.onclose = () => {
        corSettingsWS = null;
        corWSReady = false;
        console.log("🔌 WS закрыт");
    };
}


function requestAllSettings() {
    if (!corSettingsWS || corSettingsWS.readyState !== WebSocket.OPEN) {
        console.warn("WS не готов");
        return;
    }

    corSettingsWS.send(JSON.stringify({
        action: "get_settings",
        all: true
    }));

    console.log("📤 Запрос ALL settings отправлен");
}



function parseCORSettings(category, data) {
    if (!window.corSettings) window.corSettings = {};
    window.corSettings[category] = data;

    console.log(`📂 Получены настройки [${category}]:`, data);

    // Трансформируем данные под существующий applySettingsToForm
    const transformed = {};

    switch (category) {
        case "account":
            applySettingsToForm("account", {
        user: {
                account_login: data.account_login || "",
                node_name: data.node_name || "",
                password: data.password || "",
                connected: data.connected,
                status: data.status
            }
        });
        break;

        case "network":
            applySettingsToForm("LAN", {
                network: {
                    ip: data.ip || "",
                    mask: data.mask || "",
                    gateway: data.gateway || "",
                    dns: data.dns || "",
                    dhcp_enabled: !!data.dhcp_enabled,

                    wifi_ip: data.wifi_ip || "",
                    wifi_mask: data.wifi_mask || "",
                    wifi_gateway: data.wifi_gateway || "",
                    wifi_dns: data.wifi_dns || "",
                    wifi_dhcp_enabled: !!data.wifi_dhcp_enabled,

                    port: data.port
                }
            });
             updateAllIPFieldsState();
            break;

        case "wifi":
            applySettingsToForm("wifi", {
                wifi: {
                    mode: data.mode ?? 0,
                    sta_ssid: data.sta_ssid || "",
                    sta_password: data.sta_password || "",
                    ap_ssid: data.ap_ssid || "",
                    ap_password: data.ap_password || "",
                    ap_channel: data.ap_channel || 1
                }
            });
            break;

        case "uart":
            applySettingsToForm("uart", {
                uart: {
                    baud: data.baud ?? 9600,
                    data_bits: data.data_bits ?? 8,
                    stop_bits: data.stop_bits ?? 1,
                    parity: data.parity ?? 0,
                    rs485_mode: !!data.rs485_mode
                }
            });
            break;

        case "user":
            applySettingsToForm("user", {
                user: {
                    login: data.login || "",
                    password: data.password || "",
                    confirm_password: data.password || "",
                    connected: !!data.connected,
                    status: data.status || ""
                }
            });
            break;

        case "system":
            applySettingsToForm("system", {
                system: {
                    build_number: data.build_number || "",
                    build_date: data.build_date || ""
                }
            });       
            break;

        default:
            console.warn("⚠️ Неизвестная категория:", category);
    }
}


// ===================================
// Обновление прогресса OTA
// ===================================
function updateOTAProgress(otaData) {
    const statusText = document.getElementById("statusText");
    const progressBar = document.getElementById("progressBar");
    if (!statusText || !progressBar) return;
    const phase = otaData.ota_phase || "Загрузка";
    const percent = otaData.progress !== undefined ? otaData.progress : 0;
    // Текст статуса
    statusText.textContent = `🔄 ${phase} (${percent}%)`;
    // Визуальный прогресс
    progressBar.style.width = percent + "%";
}



function showDeviceOfflineModal() {
   
    if (document.getElementById("device-offline-modal")) return;
    
     document.getElementById("appContainer").classList.add("hidden");

    const modalDiv = document.createElement("div");
    modalDiv.id = "device-offline-modal";

    modalDiv.innerHTML = `
        <div class="modal" style="height: 220px; 
            width:290px;
            padding: 20px; 
            display: flex; 
            boarder:1px white;
            border-radius: 30px;
            flex-direction: column;
            position: fixed;
            background: rgba(255, 255, 255, 0.7);
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            z-index: 1000;">
        <div style="display: flex; flex-direction: column; align-items: center; gap: 15px; flex: 1; justify-content: center;">

        <svg width="80" height="80" viewBox="0 0 80 80" fill="none" xmlns="http://www.w3.org/2000/svg">
            <rect width="80" height="80" rx="40" fill="white"/>
            <path fill-rule="evenodd" clip-rule="evenodd" d="M40.0605 24.2002C41.6475 24.2002 43.0791 
            25.0459 43.8896 26.4629L56.9111 49.1445C57.3018 49.8145 57.5088 50.5742 57.5088 51.3379C57.5088 
            53.9648 55.6729 55.8008 53.0459 55.8008H27.0586C24.4316 55.8008 22.5977 53.9648 22.5977 
            51.3379C22.5996 50.5723 22.8057 49.8184 23.1963 49.1582L36.2139 26.4639C37.041 25.0459 
            38.4795 24.2002 40.0605 24.2002Z" fill="#DF1125"/>
            <path fill-rule="evenodd" clip-rule="evenodd" d="M39.0374 33.7061C39.2815 33.458 39.636 
            33.3213 40.0364 33.3213C40.4329 33.3213 40.7903 33.4619 41.0413 33.7188C41.2727 33.9551 
            41.3968 34.2744 41.3899 34.6191L41.1526 43.5195C41.139 44.2734 40.7581 44.6738 40.053 
            44.6738C39.3255 44.6738 38.9339 44.2734 38.9192 43.5176L38.6995 34.6025C38.6927 34.249 
            38.8099 33.9385 39.0374 33.7061ZM41.6768 48.6055C41.6768 49.4629 40.9492 50.1621 40.0537 
            50.1621C39.1729 50.1621 38.4297 49.4492 38.4297 48.6055C38.4297 47.748 39.1582 47.0488 
            40.0537 47.0488C40.9639 47.0488 41.6768 47.7324 41.6768 48.6055Z" fill="white"/>
        </svg>

        <!-- Заголовок -->
        <h1 style="font-size: 14px; margin: 0; color: #291161;">
            Устройство недоступно
        </h1>

        <!-- Текст -->
        <p style="
            margin: 0;
            color: #5B4296;
            font-size: 10px;
            text-align: center;
        ">
            Нет соединения с устройством
        </p>

        <!-- Кнопка -->
         <button id="device-ok-btn" style="
            padding: 10px;
            border: none;
            margin: 10px 0;
            background-color: #7527B2;
            color: white;
            cursor: pointer;
            border-radius: 12px;
            width: 100%;
            max-width: 200px;
            height: 40px;
            transition: background-color 0.3s;">Ок
        </button>

        </div>
    </div>
    `;

    document.body.appendChild(modalDiv);

    // обработчик кнопки
    document.getElementById("device-ok-btn").addEventListener("click", () => {
        goToDevices();
    });
}

function goToDevices() {
    checkToken();
    window.location.href = "/static/COR_ID/devices.html";
}


async function populateFirmwareDropdown() {
    const select = document.getElementById("firmwareSelect");
    if (!select) return;

    select.innerHTML = `<option value="">Загрузка...</option>`;

    const files = await fetchFirmwareFiles();

    if (!files.length) {
        select.innerHTML = `<option value="">Нет доступных прошивок</option>`;
        return;
    }

    select.innerHTML = `<option value="">Выберите прошивку</option>`;

    files.forEach(file => {
        const option = document.createElement("option");
        option.value = file.filename;

        // красиво: имя + размер + дата
        option.textContent = `${file.filename} (${(file.size_bytes / 1024).toFixed(0)} KB)`;

        select.appendChild(option);
    });
}
