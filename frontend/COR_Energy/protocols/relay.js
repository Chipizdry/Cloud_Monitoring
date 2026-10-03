


const RELAYS = {
    GENERATOR_START: 0,   // реле №1 
    GENERATOR_BLOCK: 1    // реле №2
};



// ============================
// Чтение состояния реле (Coils / DO)
// ============================
async function readRelayCoils(
    host,
    port,
    slave_id,
    object_id,
    start = 0,
    count = 8
) {
    try {
        const url =
            `${API_BASE_URL}/api/modbus_tcp/v1_cached/read_coils` +
            `?protocol=modbus_over_tcp` +
            `&host=${host}` +
            `&port=${port}` +
            `&slave_id=${slave_id}` +
            `&object_id=${object_id}` +
            `&start=${start}` +
            `&count=${count}`;

        const resp = await fetch(url, {
            headers: { accept: "application/json" }
        });

        const data = await resp.json();

        if (!data.ok) {
            console.warn("Relay coils read error:", data.error);
            return { ok: false, coils: [] };
        }

        return {
            ok: true,
            coils: data.data // [true, false, ...]
        };

    } catch (err) {
        console.error("Ошибка чтения реле:", err);
        return { ok: false, coils: [] };
    }
}


// ============================
// Чтение дискретных входов (DI)
// ============================
async function readRelayDiscreteInputs(
    host,
    port,
    slave_id,
    object_id,
    start = 0,
    count = 8
) {
    try {
        const url =
            `${API_BASE_URL}/api/modbus_tcp/v1_cached/read_discrete_inputs` +
            `?protocol=modbus_over_tcp` +
            `&host=${host}` +
            `&port=${port}` +
            `&slave_id=${slave_id}` +
            `&object_id=${object_id}` +
            `&start=${start}` +
            `&count=${count}`;

        const resp = await fetch(url, {
            headers: { accept: "application/json" }
        });

        const data = await resp.json();

        if (!data.ok) {
            console.warn("DI read error:", data.error);
            return { ok: false, inputs: [] };
        }

        return {
            ok: true,
            inputs: data.data // [true, false, ...]
        };

    } catch (err) {
        console.error("Ошибка чтения дискретных входов:", err);
        return { ok: false, inputs: [] };
    }
}




// ============================
// Запись состояния реле (DO)
// ============================
async function writeRelayCoil(
    host,
    port,
    slave_id,
    object_id,
    relay,
    state
) {
    try {

        const url =
            `${API_BASE_URL}/api/modbus_tcp/v1/write_coils` +
            `?protocol=modbus_over_tcp` +
            `&host=${encodeURIComponent(host)}` +
            `&port=${port}` +
            `&slave_id=${slave_id}` +
            `&object_id=${object_id}` +
            `&relay=${relay}` +
            `&state=${state}`;

        const resp = await fetch(url, {
            method: "POST"
        });

        if (!resp.ok) {
            console.error("HTTP error:", resp.status);
            return { ok: false };
        }

        return await resp.json();

    } catch (err) {
        console.error("writeRelayCoil error:", err);
        return { ok: false };
    }
}



function initRelayControls(host, port, slave_id, object_id) {

    const startSwitch = document.getElementById("relayStart");
    const blockSwitch = document.getElementById("relayBlock");

    if (!startSwitch || !blockSwitch) {
        console.warn("Relay switches not found");
        return;
    }

    // Вешаем listener один раз
    startSwitch.addEventListener("change", () => {
        updateRelayStatusFromUI(
            host, port, slave_id, object_id,
            RELAYS.GENERATOR_START,
            startSwitch
        );
    });

    blockSwitch.addEventListener("change", () => {
        updateRelayStatusFromUI(
            host, port, slave_id, object_id,
            RELAYS.GENERATOR_BLOCK,
            blockSwitch
        );
    });
}



async function updateRelayStatusFromUI(
    host,
    port,
    slave_id,
    object_id,
    relayIndex,
    switchElement
) {
    switchElement.disabled = true;

    const result = await writeRelayCoil(
        host,
        port,
        slave_id,
        object_id,
        relayIndex,
        switchElement.checked
    );

    if (!result.ok) {
        // откат если ошибка
        switchElement.checked = !switchElement.checked;
        console.warn("Relay update failed");
    }

    switchElement.disabled = false;
}



async function syncRelayUIFromDevice(host, port, slave_id, object_id) {

    const startSwitch = document.getElementById("relayStart");
    const blockSwitch = document.getElementById("relayBlock");

    if (!startSwitch || !blockSwitch) return;

    const resp = await readRelayCoils(
        host,
        port,
        slave_id,
        object_id,
        0,
        8
    );

    if (!resp.ok) return;

    startSwitch.checked = !!resp.coils[RELAYS.GENERATOR_START];
    blockSwitch.checked = !!resp.coils[RELAYS.GENERATOR_BLOCK];
}



