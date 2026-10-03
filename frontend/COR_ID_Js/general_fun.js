


const modalConfigs = {
    columnSelectModal: { width: '250px', height:  'auto', top: '300px', left: '200px' },
    corIdModal: { width: '300px', height:  'auto', top: '300px', left: '250px' },
    editModal: { width: '250px', height: 'auto', top: '50px', left: '50px' },
    myModal: { width: '250px', height: '450px', top: '50px', left: '250px' },
    settingsModal: { width: '460px', height: '710px', top: '50px', left: '450px' },
    sessionsModal: { width: '450px', height: '350px', top: 'auto', left: 'auto' },
    step1Modal: { width: '460px', height: '700px', top: '20px', left: 'auto' },
    step2Modal: { width: '460px', height: '700px', top: '20px', left: 'auto' },
    step3Modal: { width: '460px', height: '700px', top: '20px', left: 'auto' },
    step4Modal: { width: '460px', height: '700px', top: '20px', left: 'auto' },
    step5Modal: { width: '460px', height: '700px', top: '20px', left: 'auto' },
    addPatientModal: { width: '460px', height: '700px', top: '15px', left: '300px' },
    addDeviceModal: { width: '250px', height: 'auto', top: '50px', left: '250px' },
    devicesModal: { width: 'auto', height: 'auto', top: 'auto' , left: 'auto' },
    testModal: { width:'250px', height: 'auto', top: 'auto' , left: 'auto' },
    Dicom_upload_modal: { width: '460px', height: 'auto', top: 'auto', left: 'auto' },
    recovery_modal: { width: '250px', height: 'auto', top: 'auto', left: 'auto' },
    rolesModal: { width: '250px', height: 'auto', top: '50px', left: '50px' },
    roleInfoModal: { width: '460px', height: '680px', top: 'auto', left: 'auto' },

    batteryModal: { width: '350px', height: 'auto', top: 'auto', left: 'auto' },
    loadSettingsModal: { width: '350px', height: 'auto', top: 'auto', left: 'auto' },
    GridSettingsModal: { width: '350px', height: 'auto', top: 'auto', left: 'auto' },
    SolarModal: { width: '350px', height: 'auto', top: 'auto', left: 'auto' },
    RegistersModal: { width: '350px', height: 'auto', top: '50px', left: 'auto' },
    inverterModal: { width: '350px', height: 'auto', top: 'auto', left: 'auto' },
    GeneratorModal: { width: '350px', height: 'auto', top: 'auto', left: 'auto' },
    ExcelModal: { width: '350px', height: '550px', top: 'auto', left: 'auto' },
    addObjectModal:  { width: '250px', height: 'auto', top: '50px', left: 'auto' },
    editObjectModal:  { width: '250px', height: 'auto', top: 'auto', left: 'auto' },
    shareObjectModal:  { width: '450px', height: 'auto', top: 'auto', left: 'auto' },
    shareDeviceModal:  { width: '450px', height: 'auto', top: 'auto', left: 'auto' },

    blockModal: { width: '660px', height: '378px', top: '50%', left: '50%' },
    unblockModal: { width: '660px', height: '378px', top: '50%', left: '50%' },
    deleteModal: { width: '660px', height: '378px', top: '50%', left: '50%' },
    restoreModal: { width: '660px', height: '378px', top: '50%', left: '50%' },
    limitModal: { width: '660px', height: 'auto', top: '50%', left: '50%' },
    infoModal: { width: '660px', height: '671px', top: '50%', left: '50%' },
};

//Функция получения токена
function getToken() {
    return localStorage.getItem('access_token') || getTokenFromURL();
}

function buildAuthenticatedWebSocketUrl(pathOrUrl) {
    const token = getToken();
    const url = new URL(pathOrUrl, window.location.origin);

    if (url.protocol === 'http:') {
        url.protocol = 'ws:';
    } else if (url.protocol === 'https:') {
        url.protocol = 'wss:';
    }

    if (token) {
        url.searchParams.set('access_token', token);
    }

    return url.toString();
}

function maskWebSocketUrlForLog(wsUrl) {
    try {
        const url = new URL(wsUrl);
        if (url.searchParams.has('access_token')) {
            url.searchParams.set('access_token', '***');
        }
        return url.toString();
    } catch (error) {
        return wsUrl;
    }
}


function makeModalDraggable(modalId) {
    const modal = document.getElementById(modalId);
    const header = modal.querySelector('.modal-header');
    let isDragging = false;
    let offsetX = 0, offsetY = 0;

    header.onmousedown = function(e) {
        isDragging = true;
        // Вычисляем начальное смещение курсора относительно модального окна
        offsetX = e.clientX - modal.offsetLeft;
        offsetY = e.clientY - modal.offsetTop;

        // Добавляем события перемещения и отпускания мыши
        document.onmousemove = function(e) {
            if (isDragging) {
                modal.style.left = `${e.clientX - offsetX}px`;
                modal.style.top = `${e.clientY - offsetY}px`;
            }
        };

        document.onmouseup = function() {
            isDragging = false;
            document.onmousemove = null;
            document.onmouseup = null;
        };
    };
}


// Объект для хранения состояния каждого модального окна
const modalStates = {};

// Универсальная функция для закрытия модального окна
function closeModal(modalId) {
    console.log(`Закрытие модального окна: ${modalId}`);
    const modal = document.getElementById(modalId);
    if (!modal) {
        console.error(`Модальное окно с id "${modalId}" не найдено.`);
        return;
    }
    modal.style.display = 'none';
    const container = modal.closest('.modalCustom');
    if (container) container.classList.remove('open');
}

function setupModalCloseByButton() {
    document.addEventListener('click', function (e) {
        const btn = e.target.closest('[data-action="close"]');
        if (!btn) return;

        const modalRoot = btn.closest('.modal, .sessionsModal');

        if (modalRoot && modalRoot.id) {
            closeModal(modalRoot.id);
        } else {
            const container = btn.closest('.modalCustom');
            if (container) container.classList.remove('open');
        }
    });
}


// Универсальная функция для минимизации модального окна
function minimizeModal(modalId) {
    const modal = document.getElementById(modalId);
    const maximizeButton = modal.querySelector('[data-action="maximize"]');

    if (modal) {
        // Сохраняем текущее состояние перед минимизацией
        if (!modalStates[modalId]?.minimized) {
            modalStates[modalId] = {
                ...modalStates[modalId],
                width: modal.style.width || getComputedStyle(modal).width,
                height: modal.style.height || getComputedStyle(modal).height,
                top: modal.style.top || getComputedStyle(modal).top,
                left: modal.style.left || getComputedStyle(modal).left,
                minimized: true,
                maximized: false,
            };
        }

        // Применяем минимизацию
        modal.classList.add('minimized');
        modal.style.width = '200px'; // Размеры минимизированного окна
        modal.style.height = '40px';
        modal.style.top = 'auto';
        modal.style.left = '10px';
        modal.style.transform = 'none';

        // Изменяем иконку кнопки максимизации/восстановления
        if (maximizeButton) maximizeButton.textContent = '🗖';
    } else {
        console.error(`Модальное окно с id "${modalId}" не найдено.`);
    }
}

// Универсальная функция для максимизации/восстановления модального окна

function maximizeModal(modalId) {
    const modal = document.getElementById(modalId);
    const maximizeButton = modal.querySelector('[data-action="maximize"]');

    if (modal) {
        const isMaximized = modalStates[modalId]?.maximized || false;
        const isMinimized = modalStates[modalId]?.minimized || false;

        if (isMinimized) {
            // Восстановление из минимизированного состояния
            modal.classList.remove('minimized');
            modal.style.width = modalStates[modalId].width;
            modal.style.height = modalStates[modalId].height;
            modal.style.top = modalStates[modalId].top;
            modal.style.left = modalStates[modalId].left;
            modal.style.transform = 'none';
            modalStates[modalId].minimized = false;

            // Изменяем иконку кнопки на двойной квадрат
            if (maximizeButton) maximizeButton.textContent = '🗖';
        } else if (isMaximized) {
            // Восстановление окна до исходного состояния из modalConfigs
            const defaultConfig = modalConfigs[modalId];
            if (defaultConfig) {
                modal.style.width = defaultConfig.width;
                modal.style.height = defaultConfig.height;
                modal.style.top = defaultConfig.top;
                modal.style.left = defaultConfig.left;
                modal.style.transform = 'none';
            }
            modalStates[modalId].maximized = false;

            // Изменяем иконку кнопки на двойной квадрат
            if (maximizeButton) maximizeButton.textContent = '🗖';
        } else {
            // Максимизация окна
            modalStates[modalId] = {
                ...modalStates[modalId],
                width: modal.style.width || getComputedStyle(modal).width,
                height: modal.style.height || getComputedStyle(modal).height,
                top: modal.style.top || getComputedStyle(modal).top,
                left: modal.style.left || getComputedStyle(modal).left,
            };

            modal.style.width = '100%';
            modal.style.height = '100%';
            modal.style.top = '0';
            modal.style.left = '0';
            modal.style.transform = 'none';
            modalStates[modalId].maximized = true;

            // Изменяем иконку кнопки на одинарный квадрат
            if (maximizeButton) maximizeButton.textContent = '🗗';
        }
    } else {
        console.error(`Модальное окно с id "${modalId}" не найдено.`);
    }
}


function initModalControls(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) {
        const closeButton = modal.querySelector('[data-action="close"]');
        const minimizeButton = modal.querySelector('[data-action="minimize"]');
        const maximizeButton = modal.querySelector('[data-action="maximize"]');

        if (closeButton) closeButton.addEventListener('click', () => closeModal(modalId));
        if (minimizeButton) minimizeButton.addEventListener('click', () => minimizeModal(modalId));
        if (maximizeButton) maximizeButton.addEventListener('click', () => maximizeModal(modalId));

        // Устанавливаем начальные размеры и положение
        const config = modalConfigs[modalId];
        if (config) {
            modal.style.width = config.width;
            if (config.height) {
                modal.style.height = config.height;
            }
            modal.style.top = config.top;
            modal.style.left = config.left;
            modal.style.transform = 'none'; // Убираем стандартное центрирование
        }

        // Сохраняем начальное состояние окна
        modalStates[modalId] = {
            maximized: false,
            minimized: false,
            ...config // Сохраняем параметры для восстановления
        };
    } else {
        console.error(`Модальное окно с id "${modalId}" не найдено.`);
    }
}



// Инициализация всех модальных окон на странице
function initAllModals() {
 //   const modals = document.querySelectorAll('.modal'); // Предполагаем, что все модальные окна имеют класс "modal"
    const modals = document.querySelectorAll('.modal, .sessionsModal');
    modals.forEach((modal) => {
        const modalId = modal.id;
        if (modalId) {
            initModalControls(modalId);
        }
    });
}


function togglePassword(inputId, iconId) {
    const passwordInput = document.getElementById(inputId);
    const eyeIcon = document.getElementById(iconId);

    // Переключение типа поля
    const isPasswordHidden = passwordInput.type === 'password';
    passwordInput.type = isPasswordHidden ? 'text' : 'password';

    // Обновление иконки в зависимости от состояния
    updateEyeIcon(eyeIcon, isPasswordHidden);
}

// Функция установки иконки (по умолчанию закрытый глаз)
function updateEyeIcon(eyeIcon, isPasswordHidden) {

    eyeIcon.innerHTML = ''; // Очистка
    const svgIcon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svgIcon.setAttribute('width', '25');
    svgIcon.setAttribute('height', '24');
    svgIcon.setAttribute('viewBox', '0 0 25 24');
    svgIcon.setAttribute('fill', 'none');
    svgIcon.setAttribute('xmlns', 'http://www.w3.org/2000/svg');

    if (isPasswordHidden) {
        // Открытый глаз
        svgIcon.innerHTML = `
           <svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
            <path d="M22.1918 11.4766C22.1623 11.41 21.4476 9.82457 19.8588 8.23578C17.7418 6.11881 15.068 
              5 12.125 5C9.182 5 6.50815 6.11881 4.39118 8.23578C2.8024 9.82457 2.08437 11.4125 2.05821 
              11.4766C2.01983 11.563 2 11.6564 2 11.7508C2 11.8453 2.01983 11.9387 2.05821 12.0251C2.08774 
              12.0917 2.8024 13.6763 4.39118 15.2651C6.50815 17.3812 9.182 18.5 12.125 18.5C15.068 18.5 
              17.7418 17.3812 19.8588 15.2651C21.4476 13.6763 22.1623 12.0917 22.1918 12.0251C22.2302 
              11.9387 22.25 11.8453 22.25 11.7508C22.25 11.6564 22.2302 11.563 22.1918 11.4766ZM12.125 
              17.15C9.52794 17.15 7.25909 16.2059 5.3809 14.3445C4.61028 13.5781 3.95464 12.7042 3.43437 
              11.75C3.95444 10.7957 4.6101 9.92172 5.3809 9.15547C7.25909 7.29416 9.52794 6.35 12.125 
              6.35C14.7221 6.35 16.9909 7.29416 18.8691 9.15547C19.6412 9.92159 20.2983 10.7955 20.8198 
              11.75C20.2115 12.8857 17.5613 17.15 12.125 17.15ZM12.125 7.7C11.324 7.7 10.541 7.93753 
              9.87494 8.38255C9.20892 8.82757 8.68982 9.46009 8.38328 10.2001C8.07675 10.9402 7.99655 
              11.7545 8.15282 12.5401C8.30909 13.3257 8.69481 14.0474 9.26121 14.6138C9.82762 15.1802 
              10.5493 15.5659 11.3349 15.7222C12.1205 15.8785 12.9348 15.7983 13.6749 15.4917C14.4149 
              15.1852 15.0474 14.6661 15.4925 14.0001C15.9375 13.334 16.175 12.551 16.175 11.75C16.1739 
              10.6762 15.7468 9.64674 14.9876 8.88745C14.2283 8.12817 13.1988 7.70112 12.125 7.7ZM12.125 
              14.45C11.591 14.45 11.069 14.2917 10.625 13.995C10.1809 13.6983 9.83488 13.2766 9.63052 
              12.7833C9.42617 12.2899 9.3727 11.747 9.47688 11.2233C9.58106 10.6995 9.83821 10.2184 
              10.2158 9.84082C10.5934 9.46321 11.0745 9.20606 11.5983 9.10188C12.122 8.9977 12.6649 
              9.05117 13.1582 9.25553C13.6516 9.45989 14.0733 9.80595 14.37 10.25C14.6666 10.694 14.825 
              11.216 14.825 11.75C14.825 12.4661 14.5405 13.1528 14.0342 13.6592C13.5278 14.1655 12.8411 
              14.45 12.125 14.45Z" fill="#5B4296"/>
            </svg>
        `;
    } else {
        // Закрытый глаз
        svgIcon.innerHTML = `
          <svg width="25" height="24" viewBox="0 0 25 24" fill="none" xmlns="http://www.w3.org/2000/svg">
        <g id="icon / 24x24 / eye closed">
        <path id="Vector" d="M22.5469 14.6062C22.4594 14.6561 22.3629 14.6882 22.263 14.7008C22.163
           14.7133 22.0616 14.7061 21.9645 14.6794C21.8674 14.6527 21.7765 14.6071 21.697 14.5453C21.6175
           14.4834 21.551 14.4065 21.5013 14.3189L19.6819 11.1399C18.6242 11.8551 17.4575 12.3938 16.2272
           12.7351L16.7892 16.1076C16.8058 16.2069 16.8027 16.3084 16.78 16.4065C16.7573 16.5046 16.7155 
           16.5972 16.657 16.6791C16.5985 16.761 16.5244 16.8306 16.439 16.8838C16.3535 16.9371 16.2585 
           16.973 16.1592 16.9895C16.1183 16.9962 16.077 16.9997 16.0357 17C15.8544 16.9997 15.6792 16.9352
           15.541 16.8179C15.4029 16.7006 15.3108 16.5382 15.2811 16.3594L14.7286 13.0483C13.5635 13.2104 
           12.3815 13.2104 11.2164 13.0483L10.6639 16.3594C10.6342 16.5385 10.5418 16.7012 10.4033 
           16.8186C10.2647 16.9359 10.089 17.0002 9.90745 17C9.8651 16.9998 9.82284 16.9963 9.78105 
           16.9895C9.68175 16.973 9.58668 16.9371 9.50127 16.8838C9.41585 16.8306 9.34177 16.761 9.28326
           16.6791C9.22474 16.5972 9.18294 16.5046 9.16025 16.4065C9.13756 16.3084 9.13441 16.2069 9.15099
           16.1076L9.71594 12.7351C8.48611 12.3927 7.32 11.853 6.26308 11.137L4.44951 14.3189C4.39921 
           14.4066 4.33214 14.4834 4.25213 14.5452C4.17212 14.6069 4.08074 14.6522 3.9832 14.6786C3.88566
           14.7051 3.78387 14.712 3.68365 14.6991C3.58343 14.6861 3.48674 14.6536 3.3991 14.6033C3.31145
           14.553 3.23457 14.4859 3.17285 14.4059C3.11112 14.3259 3.06576 14.2345 3.03935 14.137C3.01295
           14.0394 3.00601 13.9377 3.01894 13.8374C3.03187 13.7372 3.06441 13.6405 3.11471 13.5529L5.02977
           10.2015C4.35711 9.62037 3.73856 8.97939 3.18174 8.28645C3.11229 8.20892 3.05938 8.11805 3.02623
           8.01939C2.99307 7.92072 2.98037 7.81634 2.9889 7.71261C2.99744 7.60887 3.02702 7.50796 3.07584 
           7.41604C3.12467 7.32412 3.19171 7.24311 3.27289 7.17797C3.35406 7.11282 3.44766 7.0649 3.54797 
           7.03713C3.64828 7.00936 3.7532 7.00232 3.85632 7.01645C3.95944 7.03057 4.0586 7.06557 4.14775 
           7.11929C4.2369 7.17301 4.31416 7.24434 4.37482 7.32892C5.96433 9.29569 8.745 11.6378 12.9715 
           11.6378C17.1981 11.6378 19.9788 9.29282 21.5683 7.32892C21.6283 7.24261 21.7053 7.16957 21.7948 
           7.11433C21.8842 7.05909 21.984 7.02285 22.088 7.00784C22.192 6.99284 22.298 6.99941 22.3994 
           7.02713C22.5008 7.05485 22.5954 7.10314 22.6773 7.16899C22.7592 7.23483 22.8267 7.31683 22.8756
           7.40988C22.9244 7.50293 22.9536 7.60504 22.9613 7.70986C22.969 7.81467 22.9551 7.91995 22.9204
           8.01915C22.8856 8.11834 22.8309 8.20933 22.7595 8.28645C22.2026 8.97939 21.5841 9.62037 20.9114 
           10.2015L22.8265 13.5529C22.8779 13.6402 22.9114 13.7369 22.9252 13.8374C22.939 13.9378 22.9327 
           14.04 22.9067 14.1379C22.8807 14.2359 22.8355 14.3277 22.7737 14.4081C22.7119 14.4885 22.6348 
           14.5558 22.5469 14.6062Z" fill="#5B4296"/>
        </g>
        </svg>
        `;
    }

    eyeIcon.appendChild(svgIcon);
}
// Автоматическая инициализация при загрузке страницы
document.addEventListener('DOMContentLoaded', function () {
    initAllModals();
    setupModalCloseByButton();
});

// Функция для проверки истечения срока действия токена
function isTokenExpired(token) {
    const decodedToken = decodeToken(token);
    if (!decodedToken || !decodedToken.exp) {
        return true;
    }
    const currentTime = Math.floor(Date.now() / 1000);
    return decodedToken.exp < currentTime;
}

// Функция для декодирования токена
function decodeToken(token) {
    if(!token){
        return null
    }

    try {
        const base64Url = token.split('.')[1];
        const base64 = base64Url.replace(/-/g, '+').replace(/_/g, '/');
        const jsonPayload = decodeURIComponent(atob(base64).split('').map(function(c) {
            return '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2);
        }).join(''));
        return JSON.parse(jsonPayload);
    } catch (error) {
        console.error("Failed to decode token:", error);
        return null;
    }
}

// Функция вычисления оставшегося времени жизни токена
function calculateTokenLifetime(decodedToken) {
    if (!decodedToken.exp) {
        console.warn("Token does not have an 'exp' field.");
        return null;
    }

    const currentTime = Math.floor(Date.now() / 1000); // Текущее время в секундах
    const remainingTime = decodedToken.exp - currentTime;

    if (remainingTime <= 0) {
        console.warn("Token has already expired.");
        return 0;
    }
    console.log(`Оставшееся время жизни токена: ${formatTime(remainingTime)}`);
    return remainingTime; // В секундах
}

// Функция форматирования времени
function formatTime(seconds) {
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const secs = seconds % 60;

    return `${hours}ч ${minutes}м ${secs}с`;
}

function showTokenExpiredModal() {

    // Очищаем всё содержимое body
    document.body.innerHTML = "";
    // Добавляем модальное окно в body
    const modalHTML = `
    <div id="tokenExpiredModal" class="modal" 
       style="height: auto; 
       padding: 20px; 
       display: flex; 
       flex-direction: column;
       position: fixed;
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

        <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 10px;">
            <h1 style="font-size: 18px; margin: 0; color: #291161;">Срок действия сессии истёк</h1>
        </div>
        
        <p style="margin: 0; color: #5B4296; text-align: center;">Пожалуйста, войдите снова, чтобы продолжить.</p>
        
        <button id="loginRedirectButton" style="
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
            transition: background-color 0.3s;">Войти
        </button>
    </div>
</div>
`;
    document.body.style.display = "none";
    document.body.innerHTML = modalHTML;
    document.body.style.display = "block";


      // Удаление токенов из localStorage
      localStorage.removeItem('access_token');
      localStorage.removeItem('refresh_token');
      localStorage.removeItem('isAdmin');
      localStorage.removeItem('redirectUrl');


      // Очистка кэша страницы
      if ('caches' in window) {
          caches.keys().then(names => {
              for (let name of names) caches.delete(name);
          });
      }


    // Добавляем обработчик кнопки для перехода на страницу логина
    document.getElementById('loginRedirectButton').addEventListener('click', () => {
        window.location.href = "/";
    });

}

function getTokenFromURL() {
    const urlParams = new URLSearchParams(window.location.search);
    return urlParams.get('access_token'); // Извлекаем токен из URL
}



// Функция для проверки истечения токена + проверка ответа сервера
async function checkToken() {
    let token = getToken();

    if (!token) {
        showTokenExpiredModal();
        return false;
    }

    const decodedToken = decodeToken(token);
    calculateTokenLifetime(decodedToken);
    if (!decodedToken) {
        showTokenExpiredModal();
        return false;
    }

    if (isTokenExpired(token)) {
        try {
            const newToken = await refreshAccessToken(); 
            if (!newToken) {
                showTokenExpiredModal();
                return false;
            }
            localStorage.setItem('access_token', newToken);
            token = newToken;
        } catch (error) {
            console.error("Failed to refresh token:", error);
            showTokenExpiredModal();
            return false;
        }
    }

    return true;
}

// Функция для прослушки всех событий с делегированием
function setupTokenCheckOnActions() {
    document.addEventListener('click', async function(event) {
        const target = event.target;

        if (target.tagName === 'BUTTON' || target.tagName === 'A') {

            const valid = await checkToken();

            if (!valid) {
                event.preventDefault();
                event.stopPropagation();
            }
        }
    });
}


async function goBack(url) {
    // Проверяем токен
    if (await checkToken()) {
        const token = getToken();
        window.location.href = `${url}`;
    }
}

//Отображение прогресс-бара паролей
function updatePasswordStrength(password, targetId) {
    const strengthBar = document.getElementById(targetId);
    if (!strengthBar) {
        console.error(`Элемент с ID ${targetId} не найден`);
        return;
    }

    const strengthFill = strengthBar.querySelector('.password-strength-fill');
    if (!strengthFill) {
        console.error('Элемент .password-strength-fill не найден');
        return;
    }

    if (!password) {
        strengthFill.style.width = '0%';
        return;
    }

    let score = 0;
    if (password.length >= 8) score += 1;
    if (password.length >= 12) score += 1;
    if (/[A-Z]/.test(password)) score += 1;
    if (/[a-z]/.test(password)) score += 1;
    if (/\d/.test(password)) score += 1;
    if (/[\W_]/.test(password)) score += 1;

    let percent, colorClass;
    if (password.length < 6) {
        percent = 25;
        colorClass = "password-strength-weak";
    } else if (score <= 3) {
        percent = 40;
        colorClass = "password-strength-weak";
    } else if (score <= 5) {
        percent = 65;
        colorClass = "password-strength-medium";
    } else if (score <= 7) {
        percent = 85;
        colorClass = "password-strength-good";
    } else {
        percent = 100;
        colorClass = "password-strength-strong";
    }

    strengthFill.style.width = `${percent}%`;
    strengthFill.className = `password-strength-fill ${colorClass}`;
}

// Обработчик генерации пароля
async function generatePassword(event) {
    if( await checkToken()){
        console.log('Кнопка "Сгенерировать пароль" нажата');
        event.preventDefault();

        let passwordField, targetId;
        if (event.target.id === 'generatePasswordBtnCreate') {
            passwordField = document.getElementById('generatedPassword');
            targetId = 'passwordStrengthCreate';
        } else if (event.target.id === 'generatePasswordBtnEdit') {
            passwordField = document.getElementById('edit_password');
            targetId = 'passwordStrengthEdit';
        }

        const length = parseInt(document.getElementById('passwordLength')?.value) || 12;
        const includeUppercase = document.getElementById('includeUppercase')?.checked || false;
        const includeLowercase = document.getElementById('includeLowercase')?.checked || true;
        const includeDigits = document.getElementById('includeNumbers')?.checked || false;
        const includeSpecial = document.getElementById('includeSymbols')?.checked || false;

        const settings = {
            length: length,
            include_uppercase: includeUppercase,
            include_lowercase: includeLowercase,
            include_digits: includeDigits,
            include_special: includeSpecial
        };

        fetch('/api/password_generator/generate_password/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(settings)
        })
        .then(response => {
            if (!response.ok) {
                throw new Error('Network response was not ok');
            }
            return response.json();
        })
        .then(data => {
            if (passwordField) {
                passwordField.value = data.password;
                // Проверяем, что функция определена перед вызовом
                if (typeof updatePasswordStrength === 'function') {
                    updatePasswordStrength(data.password, targetId);
                } else {
                    console.error('updatePasswordStrength function is not defined');
                }
            }
        })
        .catch(error => {
            console.error('Ошибка сети при попытке генерации пароля:', error);
        });
    }
}


/**
 * Функция для шифрования всех данных учётных записей с использованием супер-пароля восстановления
 * @param {Array} records - Массив объектов с учётными данными (включая все поля)
 * @param {string} recoveryPassword - Супер-пароль восстановления
 * @returns {Promise<Blob>} - Возвращает Blob с зашифрованными данными в формате txt
 */
async function encryptAndSaveRecords(records, recoveryPassword) {
    try {
        // 1. Подготовка данных - включаем все поля каждой записи
        const dataToEncrypt = records.map(record => ({
            record_id: record.record_id,
            record_name: record.record_name,
            website: record.website,
            username: record.username,
            password: record.password,
            notes: record.notes, // Шифруем комментарии
            is_favorite: record.is_favorite,
            created_at: record.created_at,
            updated_at: record.updated_at
            // Добавьте другие поля, если они есть
        }));

        // 2. Преобразуем данные в строку JSON
        const dataString = JSON.stringify(dataToEncrypt);

        // 3. Генерируем ключ шифрования из супер-пароля
        const encoder = new TextEncoder();
        const passwordBuffer = encoder.encode(recoveryPassword);

        // Генерируем случайную соль
        const salt = window.crypto.getRandomValues(new Uint8Array(16));

        // Создаем ключевой материал из пароля
        const keyMaterial = await window.crypto.subtle.importKey(
            'raw',
            passwordBuffer,
            { name: 'PBKDF2' },
            false,
            ['deriveKey']
        );

        // Производим ключ с помощью PBKDF2
        const key = await window.crypto.subtle.deriveKey(
            {
                name: 'PBKDF2',
                salt: salt,
                iterations: 100000, // Достаточно большое число итераций для безопасности
                hash: 'SHA-256'
            },
            keyMaterial,
            { name: 'AES-GCM', length: 256 }, // Используем AES-GCM с ключом 256 бит
            false,
            ['encrypt']
        );

        // 4. Шифруем данные
        const iv = window.crypto.getRandomValues(new Uint8Array(12)); // Генерируем IV
        const dataBuffer = encoder.encode(dataString);

        const encryptedData = await window.crypto.subtle.encrypt(
            {
                name: 'AES-GCM',
                iv: iv,
                additionalData: encoder.encode('additional-auth-data'), // Дополнительные данные для аутентификации
                tagLength: 128 // Длина тега аутентификации
            },
            key,
            dataBuffer
        );

        // 5. Формируем итоговый файл:
        // Формат: [соль (16 байт)][IV (12 байт)][зашифрованные данные][тег аутентификации (16 байт)]
        const combined = new Uint8Array(salt.length + iv.length + encryptedData.byteLength);
        combined.set(salt, 0); // Добавляем соль
        combined.set(iv, salt.length); // Добавляем IV
        // Добавляем зашифрованные данные (включая тег аутентификации)
        combined.set(new Uint8Array(encryptedData), salt.length + iv.length);

        // 6. Создаем Blob для скачивания
        return new Blob([combined], { type: 'text/plain' });

    } catch (error) {
        console.error('Ошибка при шифровании данных:', error);
        throw new Error('Не удалось зашифровать данные. Пожалуйста, попробуйте снова.');
    }
}

/**
 * Функция для создания и скачивания файла с зашифрованными данными
 * @param {Array} records - Массив объектов с учётными данными
 */
async function downloadEncryptedRecords(records) {
    try {
        // Получаем супер-пароль восстановления
        const response = await fetch('/api/user/get_recovery_code', {
            method: 'GET',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': 'Bearer ' + localStorage.getItem('access_token')
            }
        });

        if (!response.ok) {
            throw new Error('Ошибка получения супер-пароля');
        }

        const data = await response.json();
        const recoveryPassword = data['users recovery code'];

        // Шифруем данные
        const encryptedBlob = await encryptAndSaveRecords(records, recoveryPassword);

        // Создаем ссылку для скачивания
        const url = window.URL.createObjectURL(encryptedBlob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'account_records.cor';
        document.body.appendChild(a);
        a.click();

        // Очищаем
        window.URL.revokeObjectURL(url);
        document.body.removeChild(a);

    } catch (error) {
        console.error('Ошибка при создании файла:', error);
        alert('Не удалось создать файл с зашифрованными данными');
    }
}


/**
 * Функция для дешифровки учётных данных из файла
 * @param {File} file - Файл с зашифрованными данными
 * @param {string} recoveryPassword - Супер-пароль восстановления
 * @returns {Promise<Array>} - Возвращает массив с дешифрованными учётными записями
 */
async function decryptRecordsFromFile(file, recoveryPassword) {
    try {
        console.log('Начало дешифровки файла...');

        // 1. Читаем файл как ArrayBuffer
        const fileBuffer = await file.arrayBuffer();
        const fileBytes = new Uint8Array(fileBuffer);
        console.log('Размер файла:', fileBytes.length, 'байт');

        // 2. Извлекаем компоненты из файла
        const salt = fileBytes.slice(0, 16);
        const iv = fileBytes.slice(16, 28); // 16 + 12 = 28 (IV)
        const encryptedData = fileBytes.slice(28); // Остальное - зашифрованные данные + тег

        console.log('Извлечены компоненты:',
            '\nСоль (16 байт):', salt,
            '\nIV (12 байт):', iv,
            '\nДанные + тег:', encryptedData.length, 'байт');

        // 3. Генерируем ключ из супер-пароля
        const encoder = new TextEncoder();
        const passwordBuffer = encoder.encode(recoveryPassword);

        console.log('Генерация ключа из пароля...');
        const keyMaterial = await window.crypto.subtle.importKey(
            'raw',
            passwordBuffer,
            { name: 'PBKDF2' },
            false,
            ['deriveKey']
        );

        const key = await window.crypto.subtle.deriveKey(
            {
                name: 'PBKDF2',
                salt: salt,
                iterations: 100000,
                hash: 'SHA-256'
            },
            keyMaterial,
            { name: 'AES-GCM', length: 256 },
            false,
            ['decrypt']
        );
        console.log('Ключ успешно сгенерирован');

        // 4. Дешифруем данные
        console.log('Процесс дешифровки...');
        const decryptedData = await window.crypto.subtle.decrypt(
            {
                name: 'AES-GCM',
                iv: iv,
                additionalData: encoder.encode('additional-auth-data'),
                tagLength: 128
            },
            key,
            encryptedData
        );
        console.log('Данные успешно дешифрованы');

        // 5. Преобразуем в строку и парсим JSON
        const decoder = new TextDecoder();
        const decryptedString = decoder.decode(decryptedData);
        console.log('Дешифрованная строка:', decryptedString);

        const records = JSON.parse(decryptedString);
        console.log('Дешифрованные записи:', records);

        return records;

    } catch (error) {
        console.error('Полная ошибка дешифровки:', {
            name: error.name,
            message: error.message,
            stack: error.stack
        });
        throw new Error('Не удалось дешифровать файл. Проверьте: 1) правильность супер-пароля, 2) что файл не поврежден');
    }
}

/**
 * Функция для загрузки и дешифровки файла с учётными записями
 */
async function uploadAndDecryptRecords() {
    try {
        console.log('Инициализация процесса загрузки файла...');

        // Создаем input для выбора файла
        const fileInput = document.createElement('input');
        fileInput.type = 'file';
        fileInput.accept = '.cor,.txt';
        fileInput.style.display = 'none';

        fileInput.onchange = async (e) => {
            const file = e.target.files[0];
            if (!file) {
                console.log('Файл не выбран');
                return;
            }

            console.log('Выбран файл:', file.name, 'размер:', file.size, 'байт');

            try {
                // Запрашиваем супер-пароль восстановления
                const recoveryPassword = prompt('Введите супер-пароль восстановления:');
                if (!recoveryPassword) {
                    console.log('Пользователь отменил ввод пароля');
                    return;
                }

                console.log('Начало дешифровки файла...');
                const records = await decryptRecordsFromFile(file, recoveryPassword);

                console.log('Успешно дешифровано записей:', records.length);
                alert(`Успешно дешифровано ${records.length} записей!`);

                // Отображаем записи в интерфейсе
                populateTable(records, true);

            } catch (error) {
                console.error('Ошибка при обработке файла:', error);
                alert(error.message);
            }
        };

        document.body.appendChild(fileInput);
        fileInput.click();
        document.body.removeChild(fileInput);

    } catch (error) {
        console.error('Ошибка в uploadAndDecryptRecords:', error);
        alert('Произошла ошибка при загрузке файла. Пожалуйста, попробуйте снова.');
    }
}


async function deleteAccount() {
    if (await checkToken()) {
        const confirmDelete = confirm('Вы уверены, что хотите удалить аккаунт? Это действие необратимо.');

        if (!confirmDelete) return;

        try {
            const token =getToken();

            const response = await fetch('/api/user/delete_my_account', {
                method: 'DELETE',
                headers: {
                    'Authorization': 'Bearer ' + token
                }
            });

            if (response.ok) {
                alert('Аккаунт успешно удалён.');
                // Например, выйти на главную страницу или форму входа
                window.location.href = "/";

            } else {
                const errorData = await response.json();
                alert('Ошибка при удалении аккаунта: ' + (errorData.message || response.status));
            }
        } catch (error) {
            console.error(error);
            alert('Произошла ошибка при удалении аккаунта.');
        }
    }
}



async function refreshAccessToken() {
    const refreshToken = localStorage.getItem('refresh_token');
    const deviceId = localStorage.getItem('device_id');

    if (!refreshToken || !deviceId) {
        console.warn("Нет refresh токена или device_id — требуется повторный вход.");
        showTokenExpiredModal();
        return;
    }

    try {
        const response = await fetch("/api/auth/refresh_token", {
            method: "GET",
            headers: {
                "Authorization": `Bearer ${refreshToken}`,
                "X-Device-Type": "Desktop",
                "X-App-Id": "Desktop",
                "X-Device-Id": deviceId
            }
        });

        if (response.ok) {
            const data = await response.json();
            console.log("Токен обновлён:", data);

            // Обновляем токены в localStorage
            localStorage.setItem('access_token', data.access_token);
            localStorage.setItem('refresh_token', data.refresh_token);

            return data.access_token; // вернём новый токен
        } else {
            console.error("Не удалось обновить токен, статус:", response.status);
            showTokenExpiredModal();
        }
    } catch (error) {
        console.error("Ошибка при обновлении токена:", error);
        showTokenExpiredModal();
    }
}
