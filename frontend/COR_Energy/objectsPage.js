import { FormController } from "/static/COR_Energy/formController.js";

let addForm;
let editForm;
let devicesCache = [];
let currentShareObject = null;

document.addEventListener("DOMContentLoaded", initPage);

async function initPage() {
    try {
        console.log("🚀 Init page");

        // 1️⃣ создаём формы сразу
        addForm = new FormController("addObjectModal");
        editForm = new FormController("editObjectModal");

        // 2️⃣ загружаем устройства
        devicesCache = await fetchDevices();

        // 3️⃣ передаём devices после загрузки
     
        addForm.setDevices(devicesCache);
        editForm.setDevices(devicesCache);

        bindUI();

        await loadObjects();

    } catch (e) {
        console.error("Init error:", e);
    }
}



function bindUI() {
    document.getElementById("addObjectBtn")
        .addEventListener("click", () => addForm.open("add"));

    document.getElementById("closeAddObject")
        .addEventListener("click", () => closeModal("addObjectModal"));

    document.getElementById("closeEditObject")
        .addEventListener("click", () => closeModal("editObjectModal"));

    document.getElementById("closeShareObject")
        .addEventListener("click", () => closeModal("shareObjectModal"));    

    document.getElementById("addObjectForm")
        .addEventListener("submit", submitAddObjectForm);

    document.getElementById("editObjectForm")
        .addEventListener("submit", submitEditObjectForm);

    initModalControls("addObjectModal");
    makeModalDraggable("addObjectModal");

    initModalControls("editObjectModal");
    makeModalDraggable("editObjectModal");

    initModalControls("shareObjectModal");
    makeModalDraggable("shareObjectModal");
}


async function openEditModal(obj) {
    const freshDevices = await fetchDevices();
    devicesCache = freshDevices;
    editForm.setDevices(devicesCache, obj);
    editForm.open("edit", obj);
}

async function openShareModal(obj) {

    currentShareObject = obj;

    const modal = document.getElementById("shareObjectModal");

    const title = modal.querySelector(".modal-title");
    if (title) {
        title.textContent = `Доступ к объекту: ${obj.name}`;
    }

    modal.style.display = "block";
    renderShareLoading();

    try {

        const token = getToken();
        checkToken();
        const res = await fetch(
            `${API_BASE_URL}/api/energetic_objects/${obj.id}/access`,
            {
                headers: {
                    Authorization: `Bearer ${token}`
                }
            }
        );

        if (!res.ok) throw new Error(res.status);
        const accesses = await res.json();
        renderAccessList(accesses, obj.id);

    } catch (e) {
        renderShareError(e.message);
    }
}

function renderAccessList(accesses, objectId) {

    const modalContent =
        document.querySelector("#shareObjectModal .modal-content");

    const rows = accesses.map(a => `
        <tr>
            <td>${a.accessing_user_cor_id}</td>
            <td>${a.access_level}</td>
            <td>${new Date(a.created_at).toLocaleString()}</td>

            <td>
                <button 
                    class="icon-btn delete-btn"
                    data-action="delete-access"
                    data-object-id="${objectId}"
                    data-cor-id="${a.accessing_user_cor_id}">
                    <img src="/static/icons/delete.svg" width="24">
                </button>
            </td>
        </tr>
    `).join("");

    modalContent.innerHTML = `
        <table class="phase-table">

            <thead>
                <tr>
                    <th>COR-ID</th>
                    <th>Доступ</th>
                    <th>Дата</th>
                    <th></th>
                </tr>
            </thead>

            <tbody>

                ${rows}

                <tr>

                    <td>
                        <input 
                            style="width:120px;"
                            id="share_user_cor_id"
                            placeholder="COR-ID">
                    </td>

                    <td>
                        <select id="share_access_level">
                            <option value="read">read</option>
                            <option value="read_write">read_write</option>
                            <option value="share">share</option>
                        </select>
                    </td>

                    <td></td>

                    <td>
                        <button 
                            class="icon-btn add-btn"
                            data-action="add-access"
                            data-object-id="${objectId}">
                            <img src="/static/icons/add.svg" width="24">
                        </button>
                    </td>

                </tr>

            </tbody>

        </table>
    `;
}

const shareModal = document.getElementById("shareObjectModal");

shareModal.addEventListener("click", async (e) => {

    const btn = e.target.closest("[data-action]");
    if (!btn) return;

    const action = btn.dataset.action;

    if (action === "add-access") {
        const objectId = btn.dataset.objectId;
        await addShare(objectId);
    }

    if (action === "delete-access") {
    const objectId = btn.dataset.objectId;
    const corId = btn.dataset.corId;
    await deleteShare(objectId, corId);
   }

});

async function addShare(objectId) {

    const userInput = document.getElementById("share_user_cor_id");
    const accessSelect = document.getElementById("share_access_level");

    const userCorId = userInput.value.trim();
    const accessLevel = accessSelect.value;

    if (!userCorId) {

        alert("Введите COR-ID");
        return;

    }

    try {
        await checkToken();
        const token = getToken();
       
        const res = await fetch(
            `${API_BASE_URL}/api/energetic_objects/share`,
            {
                method: "POST",
                headers: {
                    "Authorization": `Bearer ${token}`,
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({
                    energetic_object_id: objectId,
                    accessing_user_cor_id: userCorId,
                    access_level: accessLevel
                })
            }
        );

        if (!res.ok) throw new Error(await res.text());

        await openShareModal(currentShareObject);

    } catch (e) {

        alert(e.message);

    }
}

async function deleteShare(objectId, corId) {

    if (!confirm(`Удалить доступ для ${corId}?`)) return;

    try {
        await checkToken();
        const token = getToken();
        const res = await fetch(
            `${API_BASE_URL}/api/energetic_objects/${objectId}/access/${corId}`,
            {
                method: "DELETE",
                headers: {
                    "Authorization": `Bearer ${token}`
                }
            }
        );

        if (!res.ok) throw new Error(await res.text());

        // перезагрузка списка
        await openShareModal(currentShareObject);

    } catch (e) {

        alert("Ошибка удаления: " + e.message);
        console.error(e);

    }

}

function renderShareLoading() {

    const modalContent =
        document.querySelector("#shareObjectModal .modal-content");

    modalContent.innerHTML =
        `<div style="padding:20px">Загрузка...</div>`;
}

function renderShareError(message) {

    const modalContent =
        document.querySelector("#shareObjectModal .modal-content");

    modalContent.innerHTML =
        `<div style="color:red;padding:20px">${message}</div>`;
}

async function submitAddObjectForm(e) {
    e.preventDefault();
    await checkToken();
    const token = getToken();
   
    const formData = new FormData(e.target);
    const selectedAgent = document.getElementById("agent_id").value;
    const slaveId = formData.get("slave_id");
    const objectData = {
        name: formData.get("name"),
        description: formData.get("description"),
        vendor: formData.get("vendor"),
        model_name: formData.get("model_name"),
        protocol: formData.get("protocol"),
        cor_bridges: selectedAgent ? [selectedAgent] : [],
        ip_address: formData.get("ip_address") || null,
        port: formData.get("port") ? Number(formData.get("port")) : null,
        slave_ids: slaveId ? [Number(slaveId)] : [],
        inverter_login: formData.get("inverter_login") || null,
        inverter_password: formData.get("inverter_password") || null,
        is_active: true
    };

    try {
        const response = await fetch(`${API_BASE_URL}/api/energetic_objects/`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "Authorization": `Bearer ${token}`
            },
            body: JSON.stringify(objectData)
        });

        if (!response.ok) {
            throw new Error(await response.text());
        }

        closeModal("addObjectModal");
        loadObjects();

    } catch (error) {
        alert(error.message);
    }
}


async function submitEditObjectForm(e) {
    e.preventDefault();
    await checkToken();
    const token = getToken();
    const formData = new FormData(e.target);
    const objectId = formData.get("id");

    const slaveId = formData.get("slave_id");
    const agentId = formData.get("agent_id");
    const data = {
        ...Object.fromEntries(formData.entries()),

        // преобразование
        slave_ids: slaveId ? [Number(slaveId)] : [],
        cor_bridges: agentId ? [agentId] : []
    };

    // удалить одиночное поле
    delete data.slave_id;
    delete data.agent_id;

    try {
        const response = await fetch(
            `${API_BASE_URL}/api/energetic_objects/${objectId}`,
            {
                method: "PUT",
                headers: { "Content-Type": "application/json", 'Authorization': `Bearer ${token}` },
                body: JSON.stringify(data)
            }
        );

        if (!response.ok) throw new Error(await response.text());

        closeModal("editObjectModal");
        loadObjects();

    } catch (e) {
        alert(e.message);
    }
}


// Основная функция для загрузки и отображения данных
    async function loadObjects() {
        await checkToken();
        const token = getToken();
       
        const loadingElement = document.getElementById('loading');
        const errorElement = document.getElementById('error');
        const tableContainer = document.getElementById('table-container');
        const tableBody = document.getElementById('table-body');

        try {
            // UI — начало загрузки
            loadingElement.style.display = 'block';
            errorElement.style.display = 'none';
            tableContainer.style.display = 'none';

            const response = await fetch( `${API_BASE_URL}/api/energetic_objects/`, {    
                method: 'GET',
                headers: {
                    'Accept': 'application/json',
                    'Content-Type': 'application/json',
                      Authorization: `Bearer ${token}`
                }
            });

            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }

            let objects = await response.json();
            console.log("📦 Данные объектов:", objects);
            // ✔ защита от случаев, если бек вернёт { data: [...] }
            if (!Array.isArray(objects)) {
                if (Array.isArray(objects?.data)) {
                    objects = objects.data;
                } else {
                    throw new Error("Неверный формат ответа от сервера");
                }
            }

            // UI — конец загрузки
            loadingElement.style.display = 'none';

            if (objects.length > 0) {
                renderTable(objects);
            } else {
                tableBody.innerHTML = `
                <tr>
                    <td colspan="6" style="text-align:center">Нет данных для отображения</td>
                </tr>`;
            }

            tableContainer.style.display = 'block';

        } catch (error) {
            loadingElement.style.display = 'none';
            errorElement.style.display = 'block';
            errorElement.textContent = `Ошибка при загрузке данных: ${error.message}`;
            console.error('Error loading objects:', error);
        }
    }



async function fetchDevices() {
    try {
        await checkToken();
        const token = getToken();

        const response = await fetch(
            `${API_BASE_URL}/api/energetic/devices`,
            {
                headers: {
                    Authorization: `Bearer ${token}`
                }
            }
        );

        if (!response.ok) throw new Error(response.status);

        const data = await response.json();
        console.log("📦 Данные устройств:", data);
        return Array.isArray(data) ? data : [];

    } catch (e) {
        console.error("Device fetch error:", e);
        return [];
    }
}

        
        // Функция для отрисовки таблицы
function renderTable(objects) {
    const tableBody = document.getElementById('table-body');
    tableBody.innerHTML = '';

    objects.forEach(obj => {
        const row = document.createElement('tr');
        row.dataset.id = obj.id;

        let statusClass = 'status-unknown';
        let statusText = 'Неизвестно';

        if (obj.is_active === true) {
            statusClass = 'status-active';
            statusText = 'Активен';
        } else if (obj.is_active === false) {
            statusClass = 'status-inactive';
            statusText = 'Неактивен';
        }

   
        row.innerHTML = `
            <td>${obj.name || 'Не указано'}</td>
            <td>${obj.description || 'Не указано'}</td>
            <td>${obj.protocol || 'Не указано'}</td>
            <td class="${statusClass}">${statusText}</td>

            <td class="actions">
                <button type="button" class="more-btn">⋮</button>

                <div class="actions-menu">
                    <button type="button" class="action-edit">
                        <svg width="20" height="20" viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
                            <path d="M15.7474 11.9794V15.5793C15.7472 16.0886 15.5447 16.577 15.1846 16.9372C14.8244 
                            17.2974 14.336 17.4998 13.8266 17.5H4.42077C4.16761 17.4999 3.91696 17.4498 3.68323 
                            17.3525C3.4495 17.2552 3.2373 17.1127 3.05882 16.9332C2.88033 16.7537 2.73908 16.5406 
                            2.64319 16.3063C2.5473 16.072 2.49865 15.8211 2.50003 15.5679V6.1726C2.49853 5.91984 
                            2.5472 5.66929 2.64323 5.43548C2.73927 5.20166 2.88075 4.98923 3.05948 4.8105C3.23822 
                            4.63177 3.45065 4.49028 3.68446 4.39425C3.91827 4.29822 4.16882 4.24954 4.42158 
                            4.25105H8.02064M15.7473 7.56389L12.4353 4.25104M5.81121 13.0837V11.3283C5.81284 
                            11.0389 5.92797 10.7608 6.13147 10.5557L13.8598 2.82731C13.9624 2.72362 14.0845 
                            2.6413 14.2191 2.58511C14.3536 2.52893 14.498 2.5 14.6439 2.5C14.7897 2.5 14.9341 
                            2.52893 15.0687 2.58511C15.2032 2.6413 15.3253 2.72362 15.4279 2.82731L17.1719 
                            4.5713C17.2756 4.67385 17.3579 4.79594 17.4141 4.93052C17.4702 5.0651 17.4992 
                            5.20949 17.4992 5.35532C17.4992 5.50116 17.4702 5.64555 17.4141 5.78013C17.3579 
                            5.91471 17.2756 6.0368 17.1719 6.13935L9.44351 13.8677C9.23801 14.0718 8.96046 
                            14.1868 8.67084 14.188H6.9155C6.77042 14.1882 6.62673 14.1598 6.49265 14.1043C6.35858 
                            14.0489 6.23676 13.9676 6.13418 13.865C6.03159 13.7624 5.95026 13.6406 5.89484 
                            13.5065C5.83942 13.3724 5.811 13.2288 5.81121 13.0837Z" stroke="#5B4296" 
                            stroke-linecap="round" stroke-linejoin="round"/>
                        </svg> 
                         Редактировать</button>
                    <button  type="button" class="action-pause">
                         <svg width="18" height="18" viewBox="0 0 18 18" fill="none" xmlns="http://www.w3.org/2000/svg">
                             <path d="M16.5508 8.5498C16.5508 9.60038 16.3439 10.6407 15.9418 11.6113C15.5398 12.5819 
                             14.9505 13.4638 14.2076 14.2067C13.4648 14.9495 12.5829 15.5388 11.6122 15.9408C10.6416 
                             16.3429 9.60136 16.5498 8.55078 16.5498C7.50021 16.5498 6.45992 16.3429 5.48931 
                             15.9408C4.51871 15.5388 3.6368 14.9495 2.89393 14.2067C2.15106 13.4638 1.56178 
                             12.5819 1.15974 11.6113C0.757707 10.6407 0.550781 9.60038 0.550781 8.5498C0.550781 
                             6.42807 1.39364 4.39324 2.89393 2.89295C4.39422 1.39266 6.42905 0.549805 8.55078 
                             0.549805C10.6725 0.549805 12.7073 1.39266 14.2076 2.89295C15.7079 4.39324 16.5508 
                             6.42807 16.5508 8.5498Z" stroke="#5B4296" stroke-width="1.1" 
                             stroke-linecap="round" stroke-linejoin="round"/>
                             <path d="M5.88477 6.77219C5.88477 6.53644 5.97842 6.31035 6.14512 6.14365C6.31181 
                             5.97695 6.53791 5.8833 6.77365 5.8833H10.3292C10.565 5.8833 10.7911 5.97695 10.9577 
                             6.14365C11.1244 6.31035 11.2181 6.53644 11.2181 6.77219V10.3277C11.2181 10.5635 
                             11.1244 10.7896 10.9577 10.9563C10.7911 11.123 10.565 11.2166 10.3292 
                             11.2166H6.77365C6.53791 11.2166 6.31181 11.123 6.14512 10.9563C5.97842 10.7896 
                             5.88477 10.5635 5.88477 10.3277V6.77219Z" stroke="#5B4296" stroke-width="1.1" 
                             stroke-linecap="round" stroke-linejoin="round"/>
                         </svg>
                        ${obj.is_active === true ? 'Приостановить' : 'Запустить'}</button>
                    <button type="button" class="action-enable-polling-tasks">
                        <svg width="18" height="18" viewBox="0 0 18 18" fill="none" xmlns="http://www.w3.org/2000/svg">
                            <path d="M6.75 4.5L12.75 9L6.75 13.5V4.5Z" stroke="#5B4296" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/>
                        </svg>
                        Включить фоновые задачи</button>
                    <button type="button" class="action-disable-polling-tasks">
                        <svg width="18" height="18" viewBox="0 0 18 18" fill="none" xmlns="http://www.w3.org/2000/svg">
                            <path d="M5.25 5.25H12.75V12.75H5.25V5.25Z" stroke="#5B4296" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/>
                        </svg>
                        Выключить фоновые задачи</button>
                     <button type="button" class="action-share">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                            <rect width="24" height="24"/>
                            <rect width="2943" height="8354" transform="translate(-445 -7638)" fill="none"/>
                            <path d="M17.5001 2.5C17.0408 2.49982 16.5876 2.60508 16.1755 2.80768C15.7633 
                            3.01028 15.4032 3.3048 15.1228 3.66855C14.8424 4.0323 14.6493 4.45556 14.5583 
                            4.90572C14.4674 5.35588 14.481 5.82092 14.5981 6.265C14.5258 6.28097 14.4562 
                            .30689 14.3911 6.342L11.6341 7.845L8.12807 9.85C8.0934 9.87063 8.06 9.89334 
                            8.02807 9.918C7.54032 9.62955 6.98084 9.48514 6.41442 9.50149C5.84799 9.51784 
                            5.29778 9.69428 4.82747 10.0104C4.35717 10.3265 3.98601 10.7693 3.75697 
                            11.2876C3.52792 11.806 3.45036 12.3785 3.53325 12.9391C3.61614 13.4997 
                            .85611 14.0253 4.22536 14.4551C4.59461 14.885 5.07805 15.2014 5.61972 
                            15.3679C6.16138 15.5343 6.73911 15.544 7.28602 15.3957C7.83294 15.2474 
                            8.32668 14.9473 8.71007 14.53L11.6361 16.157L14.5901 17.768C14.5301 
                            18.0033 14.5001 18.2477 14.5001 18.501C14.5 19.1993 14.7435 19.8758 15.1887 
                            20.4138C15.6339 20.9518 16.2528 21.3177 16.9388 21.4484C17.6248 21.579 
                            18.3349 21.4663 18.9466 21.1295C19.5584 20.7928 20.0336 20.2532 20.2902 
                            19.6038C20.5468 18.9543 20.5688 18.2357 20.3524 17.5717C20.136 16.9078 
                            19.6948 16.3401 19.1048 15.9666C18.5147 15.5931 17.8129 15.4371 17.1202 
                            15.5255C16.4275 15.6139 15.7873 15.9412 15.3101 16.451L12.3621 14.844L9.41607 
                            13.208C9.59604 12.4654 9.48588 11.6821 9.10807 11.018L12.3661 9.156L15.1091 
                            7.659C15.174 7.62308 15.2335 7.57832 15.2861 7.526C15.623 7.89393 16.0463 
                            8.17203 16.5178 8.33513C16.9892 8.49823 17.4939 8.54119 17.9862 8.46012C18.4784 
                            8.37904 18.9427 8.17649 19.3369 7.8708C19.7311 7.56511 20.0429 7.16593 20.244 
                            6.70939C20.4451 6.25285 20.5292 5.75336 20.4887 5.25614C20.4481 4.75892 20.2842 
                            4.27966 20.0118 3.86174C19.7394 3.44382 19.367 3.10043 18.9284 2.86266C18.4899 
                            2.62489 17.9989 2.50024 17.5001 2.5Z" fill="#5B4296"/>
                        </svg>
                     Поделиться</button>     
                    <button type="button" class="action-delete-object" style="color:#DF1125;">
                        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
                            <path d="M6.33333 2.70325H9.5C9.5 2.29357 9.33318 1.90066 9.03625 1.61097C8.73932 1.32128 
                            8.33659 1.15854 7.91667 1.15854C7.49674 1.15854 7.09401 1.32128 6.79708 1.61097C6.50015 
                            1.90066 6.33333 2.29357 6.33333 2.70325ZM5.14583 2.70325C5.14583 2.34826 5.2175 1.99674 
                            5.35675 1.66876C5.496 1.34079 5.7001 1.04278 5.95739 0.791764C6.21469 0.540744 6.52014 
                            0.341624 6.85631 0.205773C7.19249 0.0699217 7.5528 0 7.91667 0C8.28054 0 8.64085 0.0699217 
                            8.97702 0.205773C9.31319 0.341624 9.61865 0.540744 9.87594 0.791764C10.1332 1.04278 10.3373 
                            1.34079 10.4766 1.66876C10.6158 1.99674 10.6875 2.34826 10.6875 2.70325H15.2396C15.3971 
                            2.70325 15.5481 2.76428 15.6594 2.87292C15.7708 2.98155 15.8333 3.12889 15.8333 
                            3.28252C15.8333 3.43615 15.7708 3.58349 15.6594 3.69212C15.5481 3.80076 15.3971 3.86179 
                            15.2396 3.86179H14.1946L13.2683 13.2158C13.1973 13.9325 12.8551 14.5978 12.3086 15.0817C11.762 
                            15.5656 11.0503 15.8336 10.3122 15.8333H5.52108C4.78314 15.8334 4.07162 15.5654 3.52525 
                            15.0814C2.97888 14.5975 2.63683 13.9324 2.56579 13.2158L1.63875 3.86179H0.59375C0.436278 
                            3.86179 0.285255 3.80076 0.173905 3.69212C0.0625555 3.58349 0 3.43615 0 3.28252C0 3.12889 
                            0.0625555 2.98155 0.173905 2.87292C0.285255 2.76428 0.436278 2.70325 0.59375 
                            2.70325H5.14583ZM6.72917 6.37195C6.72917 6.21832 6.66661 6.07098 6.55526 5.96235C6.44391 
                            5.85371 6.29289 5.79268 6.13542 5.79268C5.97794 5.79268 5.82692 5.85371 5.71557 
                            5.96235C5.60422 6.07098 5.54167 6.21832 5.54167 6.37195V12.1646C5.54167 12.3183 5.60422 
                            12.4656 5.71557 12.5742C5.82692 12.6829 5.97794 12.7439 6.13542 12.7439C6.29289 12.7439 
                            6.44391 12.6829 6.55526 12.5742C6.66661 12.4656 6.72917 12.3183 6.72917 
                            12.1646V6.37195ZM9.69792 5.79268C9.85539 5.79268 10.0064 5.85371 10.1178 5.96235C10.2291 
                            6.07098 10.2917 6.21832 10.2917 6.37195V12.1646C10.2917 12.3183 10.2291 12.4656 10.1178 
                            12.5742C10.0064 12.6829 9.85539 12.7439 9.69792 12.7439C9.54044 12.7439 9.38942 12.6829 
                            9.27807 12.5742C9.16672 12.4656 9.10417 12.3183 9.10417 12.1646V6.37195C9.10417 6.21832 
                            9.16672 6.07098 9.27807 5.96235C9.38942 5.85371 9.54044 5.79268 9.69792 5.79268ZM3.74775 
                            13.1046C3.79045 13.5345 3.99574 13.9335 4.32358 14.2238C4.65143 14.5141 5.07834 14.6749 
                            5.52108 14.6748H10.3122C10.755 14.6749 11.1819 14.5141 11.5097 14.2238C11.8376 13.9335 
                            12.0429 13.5345 12.0856 13.1046L13.0023 3.86179H2.831L3.74775 13.1046Z" fill="#DF1125"/>
                        </svg>
                     Удалить</button>
                   
                </div>
            </td>
        `;

    
        const btn = row.querySelector('.more-btn');
        const menu = row.querySelector('.actions-menu');

        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            closeAllMenus();
            menu.classList.toggle('visible');
        });


        const editBtn = menu.querySelector('.action-edit');
            if (editBtn) {
                editBtn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    e.preventDefault();
                    openEditModal(obj);
                });
            }
        
       const shareBtn = menu.querySelector('.action-share');
        if (shareBtn) {
            shareBtn.addEventListener('click', (e) => { 
                e.stopPropagation();
                e.preventDefault();

                closeAllMenus();   
                openShareModal(obj);
            });
        }

        menu.querySelector('.action-pause').onclick = (e) => {
            e.stopPropagation();
            handleStop(obj);
        };

        menu.querySelector('.action-enable-polling-tasks').onclick = (e) => {
            e.stopPropagation();
            setObjectPollingTasksActive(obj, true);
        };

        menu.querySelector('.action-disable-polling-tasks').onclick = (e) => {
            e.stopPropagation();
            setObjectPollingTasksActive(obj, false);
        };

        menu.querySelector('.action-delete-object').onclick = (e) => {
            e.stopPropagation();
            handleDelete(obj);
        };

        // Переход при клике на строку
        row.addEventListener('click', async (e) => {
            if (e.target.closest('button')) return;
            await checkToken();
            const userCorId = localStorage.getItem('COR-ID');  
            window.location.href = `/static/COR_Energy/object.html?userCorId=${userCorId}&object_Id=${obj.id}`;
        });
        tableBody.appendChild(row);
    });
}

function handleDelete(obj) {
    console.log("Удалить:", obj.id);     
    deleteObject(obj.id);
}

async function handleStop(obj) {
    const nextIsActive = obj.is_active !== true;
    const actionText = nextIsActive ? "запустить" : "приостановить";
    const objectName = obj.name || obj.id;

    if (!confirm(`Вы действительно хотите ${actionText} объект "${objectName}"?`)) {
        return;
    }

    try {
        closeAllMenus();
        await checkToken();
        const token = getToken();

        const response = await fetch(`${API_BASE_URL}/api/energetic_objects/${obj.id}`, {
            method: "PUT",
            headers: {
                "Content-Type": "application/json",
                "Authorization": `Bearer ${token}`
            },
            body: JSON.stringify({ is_active: nextIsActive })
        });

        if (!response.ok) {
            const errorText = await response.text();
            throw new Error(errorText || `HTTP ${response.status}`);
        }

        await loadObjects();

    } catch (e) {
        console.error(e);
        alert("Ошибка изменения статуса объекта: " + e.message);
    }
}

async function setObjectPollingTasksActive(obj, isActive) {
    const actionText = isActive ? "включить" : "выключить";
    const objectName = obj.name || obj.id;

    if (!confirm(`Вы действительно хотите ${actionText} все фоновые задачи объекта "${objectName}"?`)) {
        return;
    }

    try {
        closeAllMenus();
        await checkToken();
        const token = getToken();

        const response = await fetch(`${API_BASE_URL}/api/polling-tasks/object/${obj.id}/set-active`, {
            method: "PATCH",
            headers: {
                "Content-Type": "application/json",
                "Authorization": `Bearer ${token}`
            },
            body: JSON.stringify({ is_active: isActive })
        });

        if (!response.ok) {
            const errorText = await response.text();
            throw new Error(errorText || `HTTP ${response.status}`);
        }

        const result = await response.json();
        alert(
            `Фоновые задачи объекта ${isActive ? "включены" : "выключены"}.\n` +
            `Изменено: ${result.updated_tasks} из ${result.total_tasks}.`
        );

        await loadObjects();

    } catch (e) {
        console.error(e);
        alert("Ошибка изменения фоновых задач: " + e.message);
    }
}

function closeAllMenus() {
document.querySelectorAll('.actions-menu.visible')
    .forEach(menu => menu.classList.remove('visible'));
}

document.addEventListener('click', (e) => {
    if (!e.target.closest('.actions-menu') &&
        !e.target.closest('.more-btn')) {
        closeAllMenus();
    }
});


        

    async function deleteObject(objectId) {
        await checkToken();
        const token = getToken();
        if (!confirm("Удалить объект безвозвратно?")) return;
        try {
            const response = await fetch(`${API_BASE_URL}/api/energetic_objects/${objectId}`, {
               method: 'DELETE',
               headers: {
                    "Content-Type": "application/json",
                    "Authorization": `Bearer ${token}`
                }
            });

            if (!response.ok) {
                const err = await response.json();
                throw new Error(err.detail || "Ошибка удаления");
            }

            // Удаляем строку из таблицы
            const row = document.querySelector(`tr[data-id="${objectId}"]`);
            if (row) row.remove();
            alert("Объект удалён");

        } catch (e) {
            console.error(e);
            alert("Ошибка: " + e.message);
        }
    }
