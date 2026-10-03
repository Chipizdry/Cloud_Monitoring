function getMonitoringObjectId() {
    if (window.currentObject?.id) {
        return window.currentObject.id;
    }

    const params = new URLSearchParams(window.location.search);
    return params.get("object_Id") || params.get("energetic_object_id");
}

function appendHistoryObjectParams(params, objectName = null) {
    const objectId = getMonitoringObjectId();
    if (objectId) {
        params.set('energetic_object_id', objectId);
    } else if (objectName) {
        params.set('object_name', objectName);
    }
}

async function getHistoryFetchHeaders() {
    if (typeof checkToken === 'function') {
        await checkToken();
    }

    const headers = { 'Accept': 'application/json' };
    const token = typeof getToken === 'function' ? getToken() : null;
    if (token) {
        headers.Authorization = `Bearer ${token}`;
    }
    return headers;
}

function getHistoryApiUrl(path, params) {
    const base = typeof API_BASE_URL !== 'undefined' ? API_BASE_URL : '';
    return `${base}${path}?${params.toString()}`;
}

function initializeMonitoringCharts({ startLive = true } = {}) {
    if (!document.getElementById('powerChart')) {
        return;
    }

    if (window.energyChartsInitialized) {
        if (startLive && currentChartType === 'line') {
            startLiveUpdates();
        }
        return;
    }

    allMeasurements = new Array(100);
    initPageSlider();
    initChartTypeControl();
    initPowerChart();
    window.energyChartsInitialized = true;
    initTimeRangeControl();

    if (!startLive) {
        stopChartUpdates();
    }
}

// Функция для загрузки энергетических данных по временному диапазону
async function loadEnergyDataForTimeRange(range, objectName = null) {
    const now = new Date();
    let startDate;
    let intervalMinutes = 30; // по умолчанию 30 минут

    switch(range) {
        case 'today':
            startDate = new Date(now);
            startDate.setHours(0, 0, 0, 0);
            intervalMinutes = 30;
            break;

        case 'this_week':
            startDate = new Date(now);
            const day = startDate.getDay();
            const diff = (day === 0 ? -6 : 1) - day;
            startDate.setDate(startDate.getDate() + diff);
            startDate.setHours(0, 0, 0, 0);
            intervalMinutes = 30 * 24; // 1 день в минутах
            break;

        case '1d':
            startDate = new Date(now.getTime() - 24 * 3600000);
            intervalMinutes = 30;
            break;

        case '7d':
            startDate = new Date(now.getTime() - 7 * 24 * 3600000);
            intervalMinutes = 60 * 12; // 12 Часов в минутах
            break;

        case '30d':
            startDate = new Date(now.getTime() - 30 * 24 * 3600000);
            intervalMinutes = 60 * 24; // 1 день в минутах
            break;

        default:
            console.error('Неверный диапазон:', range);
            return;
    }

    try {
        isLoading = true;
        showChartLoading();

        const formatDateForAPI = (date) => {
            return date.toISOString().replace(/\.\d{3}Z$/, '');
        };


        const params = new URLSearchParams({
            start_date: formatDateForAPI(startDate),
            end_date: formatDateForAPI(now),
            interval_minutes: intervalMinutes
        });

        appendHistoryObjectParams(params, objectName);

        const url = getHistoryApiUrl('/api/modbus/measurements/energy/', params);
        console.log('Fetching energy data with URL:', url);

        const response = await fetch(url, {
            headers: await getHistoryFetchHeaders()
        });

        if (!response.ok) {
            let errorData;
            try {
                errorData = await response.json();
            } catch (e) {
                errorData = { detail: response.statusText };
            }
            throw new Error(errorData.detail || `HTTP error! status: ${response.status}`);
        }

        const data = await response.json();

        console.log('==== Energy data received ====');
        console.log('range:', range, 'objectName:', objectName);
        console.log('raw data:', data);

        if (data && data.intervals && data.intervals.length > 0) {

            console.log('Intervals count:', data.intervals.length);
            // Формируем данные для графика
            const chartData = processEnergyDataWithPlaceholders(data);

            data.intervals.forEach((i, idx) => {
                console.log(idx, i.interval_start, i.solar_energy_kwh, i.load_energy_kwh, i.grid_energy_kwh, i.battery_energy_kwh);
            });


            // Рисуем график
            updateBarChart(chartData);

            // Выводим итоговые значения
            if (data.totals) {
                updateTotalsDisplay(data.totals);
            }
        }
    } catch (error) {
        console.error('Ошибка загрузки энергетических данных:', error);
        alert(`Ошибка загрузки данных: ${error.message}`);
    } finally {
        isLoading = false;
        hideChartLoading();
    }
}


// Функция для обновления итогов
function updateTotalsDisplay(totals) {
    document.getElementById('totalSolar').innerText = totals.solar_energy_total + ' кВт·ч';
    document.getElementById('totalLoad').innerText = totals.load_energy_total + ' кВт·ч';
    document.getElementById('totalGridImport').innerText = totals.grid_import_total + ' кВт·ч';
    document.getElementById('totalGridExport').innerText = totals.grid_export_total + ' кВт·ч';
  //  document.getElementById('totalBattery').innerText = totals.battery_energy_total + ' кВт·ч';
}



async function loadEnergyDataForCustomRange(startDate, endDate, objectName = null) {
    try {
        isLoading = true;
        document.getElementById('chartLoadingOverlay').style.display = 'flex';

        const durationHours = (endDate - startDate) / (1000 * 60 * 60);
        let intervalMinutes = 30; // по умолчанию 30 минут

        // Автоматически увеличиваем интервал для больших периодов
        if (durationHours > 24 * 7) { // больше недели
            intervalMinutes = 60 * 24; // 1 день
        } else if (durationHours > 24 * 3) { // больше 3 дней
            intervalMinutes = 60 * 12; // 12 часов
        } else if (durationHours > 24) { // больше суток
            intervalMinutes = 60 * 6; // 6 часов
        } else if (durationHours > 12) { // больше 12 часов
            intervalMinutes = 60; // 1 час
        }

        const formatDateForAPI = (date) => date.toISOString().replace(/\.\d{3}Z$/, '');
       
         // Формируем параметры для query string
         const params = new URLSearchParams({
            start_date: formatDateForAPI(startDate),
            end_date: formatDateForAPI(endDate),
            interval_minutes: intervalMinutes
        });

        appendHistoryObjectParams(params, objectName);

        const url = getHistoryApiUrl('/api/modbus/measurements/energy/', params);
        console.log('Fetching energy data from:', url);
        
        const response = await fetch(url, { 
            headers: await getHistoryFetchHeaders()
        });

        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.detail || `HTTP error! status: ${response.status}`);
        }

        const data = await response.json();

        console.log('==== Energy data received ====');
     //   console.log('range:', range, 'objectName:', objectName);
        console.log('raw data:', data);

        if (data && data.intervals && data.intervals.length > 0) {
            // Формируем данные для графика
            const chartData = processEnergyDataWithPlaceholders(data);
            data.intervals.forEach((i, idx) => {
                console.log(idx, i.interval_start, i.solar_energy_kwh, i.load_energy_kwh, i.grid_energy_kwh, i.battery_energy_kwh);
            });


            // Рисуем график
            updateBarChart(chartData);

            // Выводим итоговые значения
            if (data.totals) {
                updateTotalsDisplay(data.totals);
            }
        } else {
            console.warn('No energy data found for the selected range');
            alert('Нет данных по энергии для выбранного периода');
        }
    } catch (error) {
        console.error('Error loading custom energy data:', error);
        alert(`Ошибка загрузки данных: ${error.message}`);
    } finally {
        isLoading = false;
        document.getElementById('chartLoadingOverlay').style.display = 'none';
    }
}



function initChartTypeControl() {
    const chartTypeSelect = document.getElementById('chartTypeSelect');
    if (!chartTypeSelect) return; // если вдруг элемент не найден

    // Загружаем сохраненные настройки при инициализации
   // loadChartSettings();

    chartTypeSelect.addEventListener('change', function() {
        currentChartType = this.value;
        // Сохраняем настройки сразу после изменения
        saveChartSettings();
        // сначала всё останавливаем
        stopChartUpdates();

        // Уничтожаем оба графика, если они есть
        if (powerChart) { powerChart.destroy(); powerChart = null; }
        if (energyChart) { energyChart.destroy(); energyChart = null;}

        // обновляем селектор периодов под выбранный тип
        updateTimeRangeOptions(currentChartType);

        if (currentChartType === 'line') {
            document.getElementById('energyTotals').classList.add('hidden');  // 🔹 всегда скрываем в line
            initPowerChart();   // заново создаём line chart
            startLiveUpdates(); // включаем live режим
        } else if (currentChartType === 'bar') {
            document.getElementById('energyTotals').classList.remove('hidden'); // 🔹 показываем в bar
            loadEnergyDataForTimeRange('today'); // рисуем bar chart
        }
    });
}




function updateBarChart(chartData) {
    const ctx = document.getElementById('powerChart').getContext('2d');

    if (energyChart) {
        energyChart.destroy();
    }

    if (!chartData || chartData.length === 0) return;

    const startDate = new Date(chartData[0].interval);
    const endDate = new Date(chartData[chartData.length - 1].interval);
    const labels = chartData.map(i => formatDateLabel(i.interval, startDate, endDate, 'bar'));

    // вычисляем опорную высоту для заглушки — небольшой процент от реального максимума
    let maxVal = 0;
    chartData.forEach(i => {
        if (i.has_sufficient_data) {
            maxVal = Math.max(maxVal,
                Math.abs(i.solar || 0),
                Math.abs(i.load || 0),
                Math.abs(i.grid || 0),
                Math.abs(i.battery || 0)
            );
        }
    });
    const placeholderHeight = maxVal > 0 ? Math.max(maxVal * 0.04, 0.1) : 0.1; // минимум 0.1

    // Для реальных данных — оставляем значения, для пропусков — 0
    const solarData = chartData.map(i => i.has_sufficient_data ? i.solar : 0);
    const loadData  = chartData.map(i => i.has_sufficient_data ? i.load  : 0);
    const gridData  = chartData.map(i => i.has_sufficient_data ? i.grid  : 0);
    const battData  = chartData.map(i => i.has_sufficient_data ? i.battery : 0);

    // Отдельный датасет-заглушка: рисуется только там, где нет данных
    const placeholderData = chartData.map(i => i.has_sufficient_data ? 0 : placeholderHeight);

    energyChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels,
            datasets: [
                // Заглушки — первый слой (иначе, если поставить в конец, порядок рисования не важен,
                // т.к. реальные датасеты имеют нули в этих позициях)
                {
                    label: 'Нет данных',
                    data: placeholderData,
                    backgroundColor: 'rgba(200,200,200,0.6)',
                    borderColor: 'rgba(150,150,150,0.9)',
                    borderWidth: 1
                },
                {
                    label: 'Солнечная энергия (кВт·ч)',
                    data: solarData,
                    backgroundColor: 'rgba(255, 206, 86, 0.7)',
                    borderColor: 'rgba(255, 206, 86, 1)',
                    borderWidth: 1
                },
                {
                    label: 'Нагрузка (кВт·ч)',
                    data: loadData,
                    backgroundColor: 'rgba(75, 192, 192, 0.7)',
                    borderColor: 'rgba(75, 192, 192, 1)',
                    borderWidth: 1
                },
                {
                    label: 'Сеть (кВт·ч)',
                    data: gridData,
                    backgroundColor: 'rgba(255, 99, 132, 0.7)',
                    borderColor: 'rgba(255, 99, 132, 1)',
                    borderWidth: 1
                },
                {
                    label: 'Батарея (кВт·ч)',
                    data: battData,
                    backgroundColor: 'rgba(153, 102, 255, 0.7)',
                    borderColor: 'rgba(153, 102, 255, 1)',
                    borderWidth: 1
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { position: 'top' },
                tooltip: {
                    callbacks: {
                        label: function(context) {
                            const idx = context.dataIndex;
                            const interval = chartData[idx];
                            // специальное сообщение для заглушки
                            if (!interval.has_sufficient_data) {
                                return `${context.dataset.label}: Нет данных`;
                            }
                            const v = context.raw;
                            return `${context.dataset.label}: ${Number(v).toFixed(2)} кВт·ч`;
                        }
                    }
                }
            },
            scales: {
                x: {
                    stacked: true,
                    ticks: {
                        maxRotation: 45,
                        minRotation: 45,
                        callback: function(value, index) {
                            const interval = chartData[index];
                            return interval && !interval.has_sufficient_data ? '---' : this.getLabelForValue(value);
                        }
                    }
                },
                y: {
                    stacked: false,
                    title: { display: true, text: 'Энергия (кВт·ч)' },
                    beginAtZero: true
                }
            }
        }
    });
}



function updateTimeRangeOptions(chartType) {
    const timeRangeSelect = document.getElementById('timeRangeSelect');
    if (!timeRangeSelect) return;

    // очищаем старые опции
    timeRangeSelect.innerHTML = '';

    const ranges = chartType === 'line' ? lineTimeRanges : barTimeRanges;

    ranges.forEach(r => {
        const opt = document.createElement('option');
        opt.value = r.value;
        opt.textContent = r.label;
        timeRangeSelect.appendChild(opt);
    });

    // выбираем первую опцию по умолчанию
    timeRangeSelect.value = ranges[0].value;
}



function initTimeRangeControl() {
    // Установим текущую дату в кастомных полях
    const now = new Date();
    const oneDayAgo = new Date(now.getTime() - (24 * 3600000));
    document.getElementById('startDate').value = formatDateTimeLocal(oneDayAgo);
    document.getElementById('endDate').value = formatDateTimeLocal(now);
    
    // Обработчик изменения периода
    document.getElementById('timeRangeSelect').addEventListener('change', function() {
        const isRealtime = this.value === 'realtime';
        const isCustom = this.value === 'custom';
        
        // Управление видимостью элементов
        document.querySelector('.pages-control').style.display = isRealtime ? 'flex' : 'none';
        document.querySelector('.time-range-slider-container').style.display = isRealtime ? 'block' : 'none';
        document.querySelector('.time-display').style.display = isRealtime ? 'block' : 'none';
        document.getElementById('customDateRange').style.display = isCustom ? 'flex' : 'none';
        
        if (currentChartType === 'line') {
            document.getElementById('energyTotals').classList.add('hidden');  // 🔹 всегда скрываем в line
            if (isRealtime) {
                startLiveUpdates();
            } else if (isCustom) {
                stopChartUpdates();
            } else {
                stopChartUpdates();
                loadDataForTimeRange(this.value);
            }
        } else if (currentChartType === 'bar') {
            document.getElementById('energyTotals').classList.remove('hidden'); // 🔹 показываем в bar
            document.querySelector('.pages-control').style.display =  'none';
            if (isCustom) {
                stopChartUpdates();
            } else {
                currentBarTimeRange = this.value;
                loadEnergyDataForTimeRange(currentBarTimeRange);
            }
        }


    });


    
// Обработчик для кастомного диапазона
document.getElementById('applyCustomRange').addEventListener('click', function() {
    const startDate = new Date(document.getElementById('startDate').value);
    const endDate = new Date(document.getElementById('endDate').value);
    
    if (!startDate || !endDate) {
        alert('Пожалуйста, выберите обе даты');
        return;
    }
    
    if (startDate >= endDate) {
        alert('Конечная дата должна быть позже начальной');
        return;
    }
    
    stopChartUpdates();

    if (currentChartType === 'line') {
        fetchAveragedMeasurements(startDate, endDate);
    } else if (currentChartType === 'bar') {
        // Для столбчатого графика сохраняем custom как текущий диапазон
        currentBarTimeRange = 'custom';
        saveChartSettings();
        loadEnergyDataForCustomRange(startDate, endDate);
    }
});
        // Запускаем режим реального времени по умолчанию
        startLiveUpdates();


}
  



async function fetchMeasurements(page = 1) {
    try {
        isLoading = true;
        document.getElementById('loadingIndicator').style.display = 'inline';

        const params = new URLSearchParams({
            page,
            page_size: 100
        });
        appendHistoryObjectParams(params);
        
        const response = await fetch(getHistoryApiUrl('/api/modbus/measurements/', params), {
            headers: await getHistoryFetchHeaders()
        });
        if (!response.ok) {
            throw new Error('Network response was not ok');
        }
        const data = await response.json();
        
        isLoading = false;
        document.getElementById('loadingIndicator').style.display = 'none';
        
        return data.items || [];
    } catch (error) {
        console.error('Error fetching measurements:', error);
        isLoading = false;
        document.getElementById('loadingIndicator').style.display = 'none';
        return [];
    }
}


   // Функция для загрузки и отображения конкретной страницы
   async function loadAndDisplayPage(page) {
    if (isLoading || currentPage === page) return;
    
    currentPage = page;
    document.getElementById('currentPageDisplay').textContent = `Текущая страница: ${currentPage}`;
    
    const newMeasurements = await fetchMeasurements(page);
    if (newMeasurements.length > 0) {
        allMeasurements[currentPage - 1] = newMeasurements;
        updateChartData();
    }
}


// Модифицированная функция загрузки страниц
async function loadPages(startPage) {
    if (isLoading) return;
    
    isLoading = true;
    document.getElementById('loadingIndicator').style.display = 'inline';
    
    try {
        // Загружаем все страницы для текущего диапазона
        const pagePromises = [];
        for (let i = 0; i < pagesPerScreen; i++) {
            const page = startPage + i;
            if (page <= 100) {
                allMeasurements[page - 1] = undefined;
            }
            if (page > 100) break; // Не превышаем максимальное количество страниц
            
            if (!allMeasurements[page - 1]) {
                pagePromises.push(fetchMeasurements(page));
            }
        }
        
        const results = await Promise.all(pagePromises);
        
        // Сохраняем загруженные данные
        results.forEach((measurements, index) => {
            const page = startPage + index;
            if (measurements && measurements.length > 0) {
                allMeasurements[page - 1] = measurements;
            }
        });
        
        currentPage = startPage;
        document.getElementById('currentPageDisplay').textContent = 
           `Страницы: ${currentPage}–${Math.min(currentPage + pagesPerScreen - 1, 100)}`
            
        updateChartData();
    } catch (error) {
        console.error('Error loading pages:', error);
    } finally {
        isLoading = false;
        document.getElementById('loadingIndicator').style.display = 'none';
    }
}


// Инициализация ползунка
function initPageSlider() {
    const slider = document.getElementById('pageSlider');
    if (!slider) return;
    
    slider.addEventListener('input', function() {
        isSliderMoving = true;
    });
    
    slider.addEventListener('change', async function() {
        const page = parseInt(this.value);
        // Корректируем номер страницы с учетом количества страниц на экран
        const startPage = Math.min(page, 100 - pagesPerScreen + 1);
        await loadPages(startPage);
        isSliderMoving = false;
    });
}


// Функция для обработки данных измерений
function processMeasurementsData(measurements) {
    if (!measurements) return { labels: [], loadPower: [], solarPower: [], batteryPower: [], essTotalInputPower: [], soc: [] };
    
    const sortedMeasurements = [...measurements].sort((a, b) => 
        new Date(a.measured_at) - new Date(b.measured_at)
    );

    const labels = [];
    const soc = [];
    const loadPower = [];
    const solarPower = [];
    const batteryPower = [];
    const essTotalInputPower = [];

    const totalDurationMs = new Date(sortedMeasurements.at(-1)?.measured_at) - new Date(sortedMeasurements[0]?.measured_at);
    const toKilowatts = (value) => {
        const numeric = Number(value);
        return Number.isFinite(numeric) ? Math.round(numeric / 10) / 100 : null;
    };

    sortedMeasurements.forEach(measurement => {
        labels.push(
            formatDateLabel(measurement.measured_at, totalDurationMs, sortedMeasurements.length, 'line')
        );
        loadPower.push(toKilowatts(measurement.inverter_total_ac_output));
        solarPower.push(toKilowatts(measurement.solar_total_pv_power));
        batteryPower.push(toKilowatts(measurement.general_battery_power));
        essTotalInputPower.push(toKilowatts(measurement.ess_total_input_power));
        soc.push(Number.isFinite(Number(measurement.soc)) ? Number(measurement.soc) : null);
    });

    return { labels, loadPower, solarPower, batteryPower, essTotalInputPower, soc };
}



// Функция для обновления данных графика
function updateChartData() {
    let combinedLabels = [];
    let combinedSoc = [];
    let combinedLoadPower = [];
    let combinedSolarPower = [];
    let combinedBatteryPower = [];
    let combinedEssTotalInputPower = [];

  //  console.log(`==== Обновление графика ====`);
  //  console.log(`Текущий диапазон: страницы ${currentPage}–${currentPage + pagesPerScreen - 1}`);

    for (let i = pagesPerScreen - 1; i >= 0; i--) {
        const page = currentPage + i;
        if (page > 100) continue;

        const measurements = allMeasurements[page - 1];
        if (measurements) {
            const {
                labels,
                soc,
                loadPower,
                solarPower,
                batteryPower,
                essTotalInputPower
            } = processMeasurementsData(measurements);
    
       //     console.log(`Страница ${page}: от ${labels[0]} до ${labels[labels.length - 1]}`);

            // Вставляем в конец — чтобы сохранить хронологию
            combinedLabels.push(...labels);
            combinedLoadPower.push(...loadPower);
            combinedSolarPower.push(...solarPower);
            combinedBatteryPower.push(...batteryPower);
            combinedEssTotalInputPower.push(...essTotalInputPower);
            combinedSoc.push(...soc);
        } else {
            console.log(`Страница ${page} — пусто или не загружена`);
        }
    }

    if (!powerChart || combinedLabels.length === 0) return;

    powerChart.data.labels = combinedLabels;
    powerChart.data.datasets[0].data = combinedLoadPower;
    powerChart.data.datasets[1].data = combinedSolarPower;
    powerChart.data.datasets[2].data = combinedBatteryPower;
    powerChart.data.datasets[3].data = combinedEssTotalInputPower;
    powerChart.data.datasets[4].data = combinedSoc;

    const allData = [
         ...combinedLoadPower,
         ...combinedSolarPower,
         ...combinedBatteryPower,
         ...combinedEssTotalInputPower
         ].filter(value => Number.isFinite(Number(value)));
          if (allData.length === 0)
             { powerChart.update(); 
             return; }
            const maxPower = Math.max(...allData);
            const minPower = Math.min(...allData);
             // Автоматически рассчитываем диапазон оси Y 
             updatePowerYAxis(minPower, maxPower);
             //  // Одно обновление графика
               powerChart.update();
}


function startLiveUpdates() {
    // Останавливаем предыдущие обновления, если они есть
    stopChartUpdates();
    
    // Загружаем первую страницу
    loadPages(1);
    
    // Запускаем интервал обновлений
    chartUpdateInterval = setInterval(async () => {
        if (!isSliderMoving && currentPage === 1) {
            const newMeasurements = await fetchMeasurements(1);
            if (newMeasurements.length > 0) {
                allMeasurements[0] = newMeasurements;
                updateChartData();
            }
        }
    }, 3000);
}




function stopChartUpdates() {
    if (chartUpdateInterval) {
        clearInterval(chartUpdateInterval);
        chartUpdateInterval = null;
    }
   
}

// Останавливаем обновления при закрытии вкладки
window.addEventListener('beforeunload', () => {
    stopChartUpdates();
});


function formatDateLabel(dateString, startDate, endDate, chartType) {
    const date = new Date(dateString);
    
    if (chartType === 'bar') {
        const duration = endDate - startDate;
        const days = duration / (1000 * 60 * 60 * 24);
        
        if (days <= 1) {
            // Для периода до 1 дня: показываем часы и минуты
            return date.toLocaleTimeString('ru-RU', { 
                hour: '2-digit', 
                minute: '2-digit' 
            });
        } else if (days <= 7) {
            // Для периода до недели: показываем день и время
            return date.toLocaleString('ru-RU', { 
                day: '2-digit',
                month: '2-digit',
                hour: '2-digit'
            });
        } else {
            // Для больших периодов: показываем только дату
            return date.toLocaleDateString('ru-RU', {
                day: '2-digit',
                month: '2-digit'
            });
        }
    }
    
    return date.toLocaleTimeString('ru-RU', { 
        hour: '2-digit', 
        minute: '2-digit' 
    });
}




// === Обработка данных от сервера ===
function processEnergyDataWithPlaceholders(serverData) {
    if (!serverData || !Array.isArray(serverData.intervals) || serverData.intervals.length === 0) {
        return [];
    }

    return serverData.intervals.map(interval => ({
    
        interval: interval.interval_start,
        interval_start: interval.interval_start,
        solar: interval.solar_energy_kwh != null ? Number(interval.solar_energy_kwh) : 0,
        load: interval.load_energy_kwh != null ? Number(interval.load_energy_kwh) : 0,
        grid: interval.grid_energy_kwh != null ? Number(interval.grid_energy_kwh) : 0,
        battery: interval.battery_energy_kwh != null ? Number(interval.battery_energy_kwh) : 0,
        measurement_count: interval.measurement_count || 0,
        has_sufficient_data: Boolean(interval.has_sufficient_data)
    }));
}


// ============================================================
// Динамический диапазон оси Y для графика мощности
// ============================================================
function updatePowerYAxis(minPower, maxPower) {

    if (!powerChart) return;

    // Если данных нет
    if (!Number.isFinite(minPower) || !Number.isFinite(maxPower)) {
        powerChart.options.scales.y.min = -5;
        powerChart.options.scales.y.max = 5;
        return;
    }

    // Если все значения одинаковые
    if (minPower === maxPower) {
        const value = Math.abs(minPower);

        if (value < 1) {
            minPower = -1;
            maxPower = 1;
        } else {
            const margin = value * 0.2;

            minPower -= margin;
            maxPower += margin;
        }
    }

    // Реальный диапазон данных
    const range = maxPower - minPower;

    // Запас сверху/снизу — 10%
    let margin = range * 0.10;

    // Для маленьких значений минимум 0.5 кВт запаса
    margin = Math.max(margin, 0.5);

    let yMin = minPower - margin;
    let yMax = maxPower + margin;

    // --------------------------------------------------------
    // Если вся мощность положительная — начинаем с нуля
    // --------------------------------------------------------
    if (minPower >= 0) {
        yMin = 0;
    }

    // --------------------------------------------------------
    // Если вся мощность отрицательная — заканчиваем нулём
    // --------------------------------------------------------
    if (maxPower <= 0) {
        yMax = 0;
    }

    // --------------------------------------------------------
    // Выбираем красивый шаг шкалы
    // 1 / 2 / 5 / 10 / 20 / 50 / 100 ...
    // --------------------------------------------------------
    const desiredTicks = 6;

    const rawStep = Math.abs(yMax - yMin) / desiredTicks;

    const magnitude = Math.pow(10, Math.floor(Math.log10(rawStep)));
    const normalized = rawStep / magnitude;

    let niceStep;

    if (normalized <= 1) {
        niceStep = 1;
    } else if (normalized <= 2) {
        niceStep = 2;
    } else if (normalized <= 5) {
        niceStep = 5;
    } else {
        niceStep = 10;
    }

    niceStep *= magnitude;

    // --------------------------------------------------------
    // Округляем границы под выбранный шаг
    // --------------------------------------------------------
    yMin = Math.floor(yMin / niceStep) * niceStep;
    yMax = Math.ceil(yMax / niceStep) * niceStep;

    // --------------------------------------------------------
    // Если диапазон маленький и есть только положительная
    // мощность — не делаем отрицательную часть
    // --------------------------------------------------------
    if (minPower >= 0) {
        yMin = 0;
    }

    // --------------------------------------------------------
    // Если есть отрицательная и положительная мощность,
    // обязательно оставляем 0 внутри шкалы
    // --------------------------------------------------------
    if (minPower < 0 && maxPower > 0) {
        yMin = Math.floor(yMin / niceStep) * niceStep;
        yMax = Math.ceil(yMax / niceStep) * niceStep;
    }

    // Устанавливаем диапазон
    powerChart.options.scales.y.min = yMin;
    powerChart.options.scales.y.max = yMax;

    // Задаём красивый шаг
    powerChart.options.scales.y.ticks = {
        stepSize: niceStep
    };

    console.log(
        `📊 Power Y-axis: ${yMin} ... ${yMax} kW, step=${niceStep} kW`
    );
}

