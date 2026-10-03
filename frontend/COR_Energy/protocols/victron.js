

let solarFlat = {};
let victronMonitorRunning = false;
let VictreoneMaxPower = 150000; // Максимальная мощность для инвертора (можно настроить)

async function startMonitoringVictronModbusTcp(objectData) {

    if (victronMonitorRunning) {
        console.warn("Victron monitoring already running");
        return;
    }

    victronMonitorRunning = true;

    const INTERVAL = 1000;
    const INVERTER_MAX_POWER = 150000;
    setDeviceVisibility("Generator", "hidden");
    const { ip_address: host, port, slave_id, id: object_id, protocol } = objectData;

    while (victronMonitorRunning) {

        try {

            /* ===== DATA ===== */

            await InverterPowerStatus();

            const gridRes = await EssAcStatus();
            if (gridRes?.success) gridData = gridRes.data;

            await VebusStatus();
            await fetchEss();
            await EssAdvancedSettings();

            battData = await BatteryStatus();

            const solarRes = await SolarChargerPower();
            if (solarRes?.success) solarData = solarRes.data;

            await DynamicEssSettings();


            /* ===== UI ===== */

            if (gridData?.inputPowerTotal != null) {

                const gridPower = gridData.inputPowerTotal;

                updatePowerByName("Grid", PowerToIndicator(gridPower, INVERTER_MAX_POWER));
                networkFlowLabel.textContent = formatPowerLabel(gridPower, "grid");
                setIconStatus("Grid", "normal");
            }

            if (gridData?.LoadTotalPower != null) {

                const loadPower = gridData.LoadTotalPower;

                updatePowerByName("Load", PowerToIndicator(loadPower, INVERTER_MAX_POWER));
                loadIndicatorLabel.textContent = formatPowerLabel(loadPower, "load");
                setIconStatus("Load", "normal");
            }

            if ((battData?.batteryPower != null) && (battData?.batterySOC != null)) {

                updatePowerByName("Battery", PowerToIndicator(battData.batteryPower, INVERTER_MAX_POWER));

                updateBatteryFill(battData.batterySOC);

                batteryFlowLabel.textContent = formatPowerLabel(battData.batteryPower, "battery");

                setIconStatus("Battery", "normal");
            }

            if (solarData?.TotalPVPower != null) {

                updatePowerByName("Solar", PowerToIndicator(solarData.TotalPVPower, INVERTER_MAX_POWER));

                solarPowerLabel.textContent = formatPowerLabel(solarData.TotalPVPower, "solar");

                setIconStatus("Solar", "normal");

                solarFlat = renderSolarDynamicTables(solarData);
            }


            /* ===== GLOBAL DATA ===== */

            window.lastData = {
                ...window.lastData,
                ...window.gridData,
                ...window.genData,
                ...window.battData,
                ...window.loadData,
                ...window.InvGridOut,
                ...window.gridDataPower,
                ...solarFlat
            };

            window.updateUIByData(window.lastData);

            hideLoading();

        } catch (err) {

            console.error("Ошибка мониторинга Victron:", err);

        }

        await new Promise(r => setTimeout(r, INTERVAL));
    }

    victronMonitorRunning = false;
}
// Остановка мониторинга
function stopMonitoringVictron() {
    victronMonitorRunning = false;
}


function renderSolarDynamicTables(solarData) {
    const container = document.querySelector("#SolarModal .solar-dynamic");
    if (!container) return {};

    container.innerHTML = "";

    const solarFlat = {};

    if (!solarData?.chargers) return solarFlat;

    solarData.chargers.forEach(charger => {

        const block = document.createElement("div");
        block.className = "modal-block";

        const title = document.createElement("h4");
        title.textContent = `MPPT ${charger.slave}`;
        block.appendChild(title);

        if (charger.error) {

            const errDiv = document.createElement("div");
            errDiv.style.color = "red";
            errDiv.textContent = `Ошибка: ${charger.error}`;
            block.appendChild(errDiv);

        } else {

            const tableHTML = `
                <table class="phase-table">
                  <thead>
                    <tr>
                        <th>String</th>
                        <th>Напряжение (В)</th>
                        <th>Ток (А)</th>
                        <th>Мощность (Вт)</th>
                    </tr>
                  </thead>
                  <tbody>
                    ${charger.strings.map((s, i) => {

                        // 🔹 формируем ключи для updateUIByData
                        solarFlat[`solar_${charger.slave}_${i+1}_voltage`] = s.voltage;
                        solarFlat[`solar_${charger.slave}_${i+1}_current`] = s.current;
                        solarFlat[`solar_${charger.slave}_${i+1}_power`] = s.power;

                        return `
                            <tr>
                                <td>String ${i+1}</td>
                                <td data-source="solar_${charger.slave}_${i+1}_voltage">—</td>
                                <td data-source="solar_${charger.slave}_${i+1}_current">—</td>
                                <td data-source="solar_${charger.slave}_${i+1}_power">—</td>
                            </tr>
                        `;

                    }).join("")}
                  </tbody>
                </table>
            `;

            block.innerHTML += tableHTML;

            // мощность MPPT
            solarFlat[`solar_${charger.slave}_power`] = charger.power;
        }

        container.appendChild(block);
    });

    solarFlat["TotalPVPower"] = solarData.TotalPVPower;

    return solarFlat;
}
/**
 * Универсальная функция для получения ESS настроек через Modbus API
 * @param {string} host - IP или hostname устройства
 * @param {number} port - порт Modbus
 * @param {number} slave_id - адрес слейва
 */
async function fetchEss({ host, port, slave_id }) {
    try {
        const queryParams = new URLSearchParams({
            host: host,
            port: port,
            slave_id: slave_id
        });

        const response = await fetch(`${API_BASE_URL}/api/modbus/ess_settings_cached`, {
            method: 'GET',
            headers: {
                'Content-Type': 'application/json',
            }
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Ошибка чтения ESS настроек');
        }

        const data = await response.json();
        const socValue = data.minimum_soc_limit || 40;

        updateBatteryLimitLine(socValue);

        // Обновляем исходное значение только если ползунок не был изменен пользователем
        if (!isSliderChanged) {
            initialSocValue = socValue;
            document.getElementById('State_Of_Сharge').value = socValue;
            document.getElementById('socSliderValue').textContent = socValue;
        }

        document.getElementById('vebusSOC').textContent = socValue;

        return { success: true, data };
    } catch (err) {
        console.error("❗ Ошибка получения ESS настроек:", err);
        return { success: false, error: err.message || 'Modbus ошибка' };
    }
}


async function BatteryStatus() {
    try {
      const res = await fetch(`${API_BASE_URL}/api/modbus/victron_battery_status_cached`);
      const data = await res.json();
                batteryData = {
                    batterySOC: data.soc,
                    batteryVoltage: data.voltage,
                    batteryCurrent: data.current,
                    batterySOH: data.soh,
                    batteryTemperature: data.temperature,
                    batteryPower: data.power
                };
                
    } catch (err) {
      console.error("Ошибка при получении данных батареи:", err);
    }
     console.log("Battery parsed results:", batteryData);
    return batteryData;
}


async function EssAcStatus() {
    try {
        const response = await fetch(`${API_BASE_URL}/api/modbus/victron_ac_status_cached`, {
            method: 'GET',
            headers: { 'Content-Type': 'application/json', }
        });

        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.detail || 'Ошибка при запросе AC параметров ESS');
        }

        const data = await response.json();
        console.log("Получены AC параметры ESS:", data);
        return {
            success: true,
            data: data
        };
     
    } catch (err) {
        console.error('❗ Ошибка получения AC параметров ESS:', err);
        return {
            success: false,
            error: err.message || 'Modbus ошибка'
        };
    }

      
}

async function InverterPowerStatus() {
    try {
        const response = await fetch(`${API_BASE_URL}/api/modbus/inverter_power_status_cached`);

        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.detail || 'Ошибка при запросе данных мощности');
        }

        const data = await response.json();

        return {
            success: true,
            data
        };

    } catch (error) {
        console.error('❗ Ошибка получения данных мощности инвертора:', error);

        return {
            success: false,
            error: error.message || 'Modbus ошибка'
        };
    }
}


async function VebusStatus() {
    try {
        const res = await fetch(`${API_BASE_URL}/api/modbus/vebus_status_cached`);
        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.detail || "Ошибка запроса VE.Bus");
        }

        const data = await res.json();
        console.log("Получены данные VE.Bus:", data);
        return {
            success: true,
            data: data
        };
    } catch (error) {
        console.error("❗ Ошибка получения VE.Bus данных:", error);
        return {
            success: false,
            error: error.message || "Modbus ошибка"
        };
    }
}




async function fetchEss() {
    try {
        const response = await fetch(`${API_BASE_URL}/api/modbus/ess_settings_cached`, {
            method: 'GET',
            headers: {
                'Content-Type': 'application/json',
            }
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Ошибка чтения ESS настроек');
        }

        const data = await response.json();
        const socValue = data.minimum_soc_limit || 40;
       
        return {
            success: true,
            data: data
        };
    } catch (err) {
        console.error("❗ Ошибка получения ESS настроек:", err);
        return {
            success: false,
            error: err.message || 'Modbus ошибка'
        };
    }
}


async function fetchGridLimitingStatus() {
    try {
        const res = await fetch(`${API_BASE_URL}/api/modbus/ess_advanced_settings_cached`);
        const data = await res.json();

        const switchElement = document.getElementById('gridLimitingSwitch');
        switchElement.checked = (data.grid_limiting_status === 1);
    } catch (e) {
        console.error("Ошибка загрузки состояния ограничения отдачи:", e);
    }
}



async function SolarChargerStatus() {
    try {
        const response = await fetch(`${API_BASE_URL}/api/modbus/solarchargers_status`);
        if (!response.ok) {
            throw new Error('Ошибка запроса данных солнечных контроллеров');
        }

        const data = await response.json();

        console.log("Получены данные солнечных контроллеров:", data);
    return {
        success: true,
        data: data                      

    };  

    } catch (error) {
        console.error('❗ Ошибка при получении данных:', error);
         return {
            success: false,
            error: err.message || 'Modbus ошибка'
        };
          
    }
   
}


// ✅ Загружаем итоги по каждому устройству и общий итог
async function SolarChargersSum() {
    try {
        const response = await fetch(`${API_BASE_URL}/api/modbus/solarchargers_sum`, {
            method: 'GET',
            headers: { 'Content-Type': 'application/json' }
        });

        if (!response.ok) {
            throw new Error(`Ошибка HTTP: ${response.status}`);
        }

        const data = await response.json();

       console.log("Получены итоги по солнечным контроллерам:", data);
       

        return data;
    } catch (error) {
        console.error("⚠️ Ошибка при вызове /solarchargers_sum:", error);
        return null;
    }
}



async function SolarChargerPower() {
    try {
        const response = await fetch(`${API_BASE_URL}/api/modbus/victron_solarchargers_status_cached`);
        if (!response.ok) {
            throw new Error('Ошибка запроса данных солнечных инверторов');
        }

        const data = await response.json();

        console.log("Получены данные солнечных инверторов:", data);
    return {
        success: true,
        data: data                      

    };  

    } catch (error) {
        console.error('❗ Ошибка при получении данных:', error);
         return {
            success: false,
            error: err.message || 'Modbus ошибка'
        };
          
    }
   
}


async function EssAdvancedSettings() {
    try {
        const res = await fetch('/api/modbus/ess_advanced_settings_cached');
        if (!res.ok) {
            const error = await res.json();
            throw new Error(error.detail || 'Ошибка запроса ESS настроек');
        }

        const data = await res.json();
            console.log("Получены ESS расширенные настройки:", data);   
    return {
            success: true,
            data: data
        };

    } catch (err) {
        console.error("❗ Ошибка получения ESS расширенных настроек:", err);
        return {
            success: false,
            error: err.message || 'Modbus ошибка'
        };
    }
}




async function DynamicEssSettings() {
    try {
      const response = await fetch(`${API_BASE_URL}/api/modbus/dynamic_ess_settings_cached`);
      if (!response.ok) throw new Error("Ошибка запроса: " + response.status);
      const data = await response.json();

  
    } catch (err) {
        console.error("❗ Ошибка получения динамических ESS настроек:", err);
    }
  }



  