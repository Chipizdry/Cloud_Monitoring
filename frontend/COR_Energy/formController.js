

export class FormController {
    constructor(modalId) {
        this.modalId = modalId;
        this.modal = document.getElementById(modalId);
        this.devices = [];          // устройства, отображаемые в select (зависит от режима)
        this.allDevices = [];       // все устройства (для поиска и показа при редактировании)
        this.state = { mode: "add", data: {}, vendor: null, model: null, protocol: null };
        this.cache = { vendors: null, models: {}, schemas: {} };
        this.init();
    }

    /*
    getFreeCorBridges(devices, currentObject = null) {
        const currentBridgeIds = new Set(currentObject?.cor_bridges || []);
        return devices.filter(d => {
            const isBridge = String(d.device_id).startsWith("COR-");
            if (!isBridge) return false;
            if (currentObject) {
                // При редактировании: свободные ИЛИ текущее устройство (по id или device_id)
                return !d.is_assigned || currentBridgeIds.has(d.id) || currentBridgeIds.has(d.device_id);
            } else {
                // При добавлении: только свободные
                return !d.is_assigned;
            }
        });
    }  */

    getFreeCorBridges(devices, currentObject = null) {
        const currentIds = new Set(currentObject?.cor_bridges || []);
        return devices.filter(d => {
            // Убрать проверку на COR-
            if (currentObject) {
                return !d.is_assigned || currentIds.has(d.id) || currentIds.has(d.device_id);
            } else {
                return !d.is_assigned;
            }
        });
    }

    init() {
        if (!this.modal) return console.error("Modal not found:", this.modalId);

        this.vendorSelect = this.modal.querySelector('[data-role="vendor"]');
        this.modelSelect = this.modal.querySelector('[data-role="model"]');
        this.protocolSelect = this.modal.querySelector('[data-role="protocol"]');

        if (!this.vendorSelect || !this.modelSelect || !this.protocolSelect) {
            return console.error("Required fields missing");
        }

        this.vendorSelect.onchange = () => this.setVendor(this.vendorSelect.value);
        this.modelSelect.onchange = () => this.setModel(this.modelSelect.value);
        this.protocolSelect.onchange = () => this.setProtocol(this.protocolSelect.value);
        this.fetchVendors(); // стартуем с подгрузки вендоров
    }

    /** Подгружаем список вендоров с бекенда */
    async fetchVendors() {
        if (this.cache.vendors) return this.renderVendors(this.cache.vendors);
        try {
            const res = await fetch("/api/Vendor_schemas/vendors");
            const vendors = await res.json();
            this.cache.vendors = vendors;
            this.renderVendors(vendors);
           // console.log("Vendors fetched:", vendors);
        } catch (e) {
            console.error("Failed to fetch vendors", e);
        }
    }

    renderVendors(vendors) {
        this.vendorSelect.innerHTML = '<option value="">Выберите вендора</option>';
        vendors.forEach(v => {
            this.vendorSelect.append(new Option(v, v)); 
        });
      //  console.log(`[${this.modalId}] Vendors rendered:`, vendors);
    }

    /** Подгружаем список моделей для выбранного вендора */
    async setVendor(vendorId) {
        this.state.vendor = vendorId;
        this.modelSelect.innerHTML = '<option value="">Загрузка...</option>';

        if (!vendorId) return;

        if (this.cache.models[vendorId]) {
            return this.renderModels(this.cache.models[vendorId]);
        }

        try {
            const res = await fetch(`/api/Vendor_schemas/models?vendor=${vendorId}`);
            const models = await res.json();
            this.cache.models[vendorId] = models;
            this.renderModels(models);
           // console.log("Models fetched:", models);
        } catch (e) {
            console.error("Failed to fetch models", e);
        }
    }

    renderModels(models) {
        this.modelSelect.innerHTML = '<option value="">Выберите модель</option>';
        models.forEach(m => {
            this.modelSelect.append(new Option(m, m)); 
        });
      //  console.log(`[${this.modalId}] Models rendered:`, models);
    }

    /** Подгружаем схему по модели */
    async setModel(modelId) {
        this.state.model = modelId;
        this.protocolSelect.innerHTML = '<option value="">Загрузка...</option>';

        if (!modelId || !this.state.vendor) return;

        const cacheKey = `${this.state.vendor}_${modelId}`;
        if (this.cache.schemas[cacheKey]) {
            return this.applySchema(this.cache.schemas[cacheKey]);
        }

        try {
            const res = await fetch(`/api/Vendor_schemas/${this.state.vendor}/${modelId}`);
            const schema = await res.json(); // { protocols: {...}, fields: [...] }
            this.cache.schemas[cacheKey] = schema;
            this.applySchema(schema);
          //  console.log("Schema fetched:", schema);
        } catch (e) {
            console.error("Failed to fetch schema", e);
        }
    }

    applySchema(schema) {
        // протоколы
        this.protocolSelect.innerHTML = '<option value="">Выберите протокол</option>';
        Object.entries(schema.protocols || {}).forEach(([key, cfg]) => {
            this.protocolSelect.append(new Option(cfg.label, key));
        });

        // динамические поля
        this.renderDynamicFields(schema.fields || []);
    }

    setProtocol(protocol) {
        this.state.protocol = protocol;
        if (protocol) this.protocolSelect.value = protocol;

        const cacheKey = `${this.state.vendor}_${this.state.model}`;
        const schema = this.cache.schemas[cacheKey];
        const fields = schema?.protocols?.[protocol]?.show || [];
        this.renderDynamicFields(fields);

        if (protocol === "cor_bridge") this.renderAgentSelect();
    }

    renderDynamicFields(fields) {
        const allFields = this.modal.querySelectorAll(".form[data-field]");
        allFields.forEach(el => {
            el.style.display = "none";
            const input = el.querySelector("input, select, textarea");
            if (input) input.disabled = true;
        });

        fields.forEach(name => {
            const field = this.modal.querySelector(`[data-field="${name}"]`);
            if (!field) return;
            field.style.display = "block";
            const input = field.querySelector("input, select, textarea");
            if (input) {
                input.disabled = false;
                if (input.dataset.wasRequired === "true") input.required = true;
            }
        });
    }

    async open(mode = "add", data = {}) {
        this.state.mode = mode;
        this.state.data = data;
        this.reset();
        // Заполняем базовые поля
        this.fillBaseFields(data);

        // Асинхронно обрабатываем селекты
        if (data.vendor) {
            await this.setVendor(data.vendor); // подгружаем модели
            this.vendorSelect.value = data.vendor; // <--- Устанавливаем селект вендора

            if (data.model_name) {
                await this.setModel(data.model_name); // подгружаем схему
                this.modelSelect.value = data.model_name; // <--- Устанавливаем селект модели
                if (data.protocol) {
                    this.setProtocol(data.protocol); // динамические поля
                }
            }
        }

        // COR-Agent и slave_id
        if (data.cor_bridges?.length) this.setValue("agent_id", data.cor_bridges[0]);
        if (Array.isArray(data.slave_ids) && data.slave_ids.length > 0) {
            this.setValue("slave_id", data.slave_ids[0]);
        } else {
            this.setValue("slave_id", "");
        }

        this.modal.style.display = "flex";
        this.renderAgentSelect(this._currentObject);
    }

    fillBaseFields(data) {
        const mappings = {
            id: "id",
            name: "name",
            description: "description",
            ip_address: "ip_address",
            port: "port",
            slave_id: "slave_id",
            agent_id: "agent_id",
            objectLogin: "inverter_login",
            objectPassword: "inverter_password"
        };
        Object.entries(mappings).forEach(([fieldName, dataKey]) => {
            this.setValue(fieldName, data[dataKey]);
        });
    }

    setValue(name, value) {
        const el = this.modal.querySelector(`[name="${name}"]`);
        if (!el) return;
        if (name === "agent_id" && value) {
            // Ищем device.id, а не device_id
            const matchedDevice = this.devices.find(device => device.id === value);
            el.value = matchedDevice ? matchedDevice.id : value;
            return;
        }
        el.value = value || "";
    }

    hideDynamicFields() {
        const fields = this.modal.querySelectorAll(".form[data-field]");
        fields.forEach(el => {
            el.style.display = "none";
            const input = el.querySelector("input, select, textarea");
            if (input) input.disabled = true;
        });
    }

    reset() {
        const form = this.modal.querySelector("form");
        if (form) form.reset();
        this.state.vendor = null;
        this.state.model = null;
        this.state.protocol = null;
        this.hideDynamicFields();
    }


    setDevices(devices, currentObject = null) {
        this.devices = this.getFreeCorBridges(devices, currentObject);
        this._currentObject = currentObject;
    }

    renderAgentSelect(currentObject = null) {
        const agentSelect = this.modal.querySelector('[name="agent_id"]');
        if (!agentSelect) return;
        const currentBridgeIds = currentObject?.cor_bridges || [];
        agentSelect.innerHTML = '';
        const placeholder = document.createElement("option");
        placeholder.value = "";
        placeholder.textContent = "Выберите COR-Agent";
        placeholder.disabled = true;
        placeholder.selected = currentBridgeIds.length === 0;
        agentSelect.appendChild(placeholder);
        this.devices.forEach(device => {
            const option = document.createElement("option");
            option.value = device.id;        // ← UUID
            option.textContent = device.name || device.device_id;
            agentSelect.appendChild(option);
        });
        if (currentBridgeIds.length > 0) {
            const storedId = currentBridgeIds[0]; // может быть device_id (COR-...) или UUID
            const optionToSelect = Array.from(agentSelect.options).find(opt => {
                const device = this.devices.find(d => d.id === opt.value);
                return device && (device.id === storedId || device.device_id === storedId);
            });
            agentSelect.value = optionToSelect ? optionToSelect.value : "";
        } else {
            agentSelect.value = "";
        }
    }
}

