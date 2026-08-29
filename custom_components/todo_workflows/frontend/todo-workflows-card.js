const TODO_WORKFLOWS_CARD_VERSION = "1.0.32";
const TODO_WORKFLOWS_FULL_RELOAD_MS = 5 * 60 * 1000;
const TODO_WORKFLOWS_FETCH_COOLDOWN_MS = 200;
console.info("TodoWorkflowsCard v3 loaded", TODO_WORKFLOWS_CARD_VERSION);

class TodoWorkflowsCard extends HTMLElement {
  constructor() {
    super();
    this._config = null;
    this._hass = null;
    this._items = [];
    this._loading = false;
    this._error = null;
    this._lastFetch = 0;
    this._fetchPromise = null;
    this._pendingUpdateTimer = null;
    this._unsubscribeItems = null;
    this._subscriptionHass = null;
    this._formOpen = false;
    this._lastRenderSignature = null;
    this._lastFullReloadAt = 0;
    this._formValues = {
      title: "",
      description: "",
      due: "",
      priority: 0,
      icon: "mdi:check",
      color: "",
      second_color: "",
      icon_background_color: "",
      icon_color: "",
      text_color: "",
      persistent: false,
      resolved_text: "",
      cleanup_hours: 0,
    };
  }

  static getConfigElement() {
    return document.createElement("todo-workflows-card-editor");
  }

  static getStubConfig() {
    return {};
  }

  setConfig(config) {
    if (!config) {
      throw new Error("config is required");
    }
    const { entity, items_entity, ...cardConfig } = config;
    this._config = {
      title: "",
      show_add_button: true,
      add_button_label: "Add",
      ...cardConfig,
    };
    this._initialize();
  }

  set hass(hass) {
    this._hass = hass;
    this._subscribeToItemUpdates();
    const forceFullReload = this._shouldForceFullReload();
    if (forceFullReload || !this._lastFetch) {
      this._update(true);
    }
  }

  getCardSize() {
    return Math.max(1, this._items.length || 1);
  }

  disconnectedCallback() {
    if (this._pendingUpdateTimer) {
      clearTimeout(this._pendingUpdateTimer);
      this._pendingUpdateTimer = null;
    }
    this._unsubscribeFromItemUpdates();
  }

  _initialize() {
    if (this.shadowRoot) {
      this._render();
      return;
    }

    const root = this.attachShadow({ mode: "open" });
    const style = document.createElement("style");
    style.textContent = `
      :host {
        --todo-row-height: 58px;
        --todo-radius: 14px;
        --todo-icon-size: 40px;
        --todo-gap: 5px;
        display: block;
      }

      ha-card {
        padding: 0px;
        background: none;
        border: none;
        box-shadow: none;
      }

      .header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        margin-bottom: 10px;
      }

      .title {
        font-weight: 600;
        font-size: 14px;
      }

      .actions {
        display: flex;
        gap: 8px;
        align-items: center;
      }

      .add-btn {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        padding: 6px 10px;
        border-radius: 20px;
        border: 1px solid var(--divider-color);
        background: var(--ha-card-background, var(--card-background-color));
        color: var(--primary-text-color);
        cursor: pointer;
        font-size: 12px;
      }

      .add-btn ha-icon {
        --mdc-icon-size: 16px;
      }

      .list {
        display: flex;
        flex-direction: column;
        gap: 5px;
      }

      .row {
        display: flex;
        align-items: center;
        height: var(--todo-row-height);
        border-radius: var(--todo-radius);
        background: var(--todo-row-background, var(--todo-row-color, rgba(255,255,255,0.04)));
        border: 1px solid var(--todo-row-border-color, rgba(255,255,255,0.18));
        padding: 6px 10px;
        gap: var(--todo-gap);
        box-shadow: 0 4px 12px rgba(0,0,0,0.12);
      }

      .row.is-complete {
        opacity: 0.7;
      }

      .row:hover {
        border-color: var(--todo-row-border-color, rgba(255,255,255,0.22));
        box-shadow: 0 4px 12px rgba(0,0,0,0.18);
      }

      .row:hover .icon {
        background: var(--todo-icon-bg-hover-color, rgba(255,255,255,0.12));
      }

      .icon {
        display: flex;
        align-items: center;
        justify-content: center;
        width: var(--todo-icon-size);
        height: var(--todo-icon-size);
        border-radius: 50%;
        background: var(--todo-icon-bg-color, rgba(255,255,255,0.08));
        color: var(--todo-icon-color, var(--todo-row-text-color, #000000));
        flex-shrink: 0;
      }

      .icon ha-icon {
        --mdc-icon-size: 22px;
      }

      .content {
        display: flex;
        flex-direction: column;
        justify-content: center;
        flex: 1 1 auto;
        overflow: hidden;
        color: var(--todo-row-text-color, #000000);
      }

      .task-title {
        font-size: 13px;
        font-weight: 600;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }

      .task-desc {
        font-size: 12px;
        opacity: 0.75;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }

      .task-desc-row {
        display: flex;
        align-items: center;
        min-width: 0;
      }

      .task-desc-row .task-desc {
        flex: 1 1 auto;
      }

      .due-inline {
        flex-shrink: 0;
        align-self: center;
      }

      .task-meta {
        display: flex;
        gap: 8px;
        align-items: center;
        font-size: 11px;
        opacity: 0.75;
      }

      .badge {
        padding: 2px 6px;
        border-radius: 12px;
        background: rgba(255,255,255,0.08);
        color: var(--todo-row-text-color, #000000);
      }

      .check {
        width: 30px;
        height: 30px;
        border-radius: 50%;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        cursor: pointer;
        background: rgba(255,255,255,0.08);
        color: var(--todo-row-text-color, #000000);
      }

      .check ha-icon {
        --mdc-icon-size: 18px;
        color: inherit;
      }

      .form {
        margin: 8px 0 12px;
        padding: 10px;
        border-radius: 16px;
        border: 1px solid var(--divider-color);
        display: grid;
        gap: 8px;
      }

      .form-row {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
        gap: 8px;
      }

      .form-actions {
        display: flex;
        justify-content: flex-end;
        gap: 8px;
      }

      .btn {
        padding: 6px 12px;
        border-radius: 12px;
        border: 1px solid var(--divider-color);
        background: var(--ha-card-background, var(--card-background-color));
        cursor: pointer;
      }

      .error {
        color: var(--error-color);
        font-size: 12px;
      }

      .empty {
        font-size: 12px;
        opacity: 0.7;
      }
    `;

    root.appendChild(style);
    this._card = document.createElement("ha-card");
    root.appendChild(this._card);
    this._render();
  }

  _render(force = false) {
    if (!this._card) {
      return;
    }

    const renderSignature = this._getRenderSignature();
    if (!force && renderSignature === this._lastRenderSignature) {
      return;
    }
    this._lastRenderSignature = renderSignature;
    this._lastFullReloadAt = Date.now();

    this._card.innerHTML = "";

    const header = document.createElement("div");
    header.className = "header";

    const headerText = (this._config?.title || "").trim();
    if (headerText) {
      const title = document.createElement("div");
      title.className = "title";
      title.textContent = headerText;
      header.appendChild(title);
    }

    const actions = document.createElement("div");
    actions.className = "actions";

    if (this._config?.show_add_button) {
      const addButton = document.createElement("button");
      addButton.className = "add-btn";
      addButton.addEventListener("click", () => this._toggleForm());

      const addIcon = document.createElement("ha-icon");
      addIcon.setAttribute("icon", "mdi:plus");
      addButton.appendChild(addIcon);

      const addLabel = document.createElement("span");
      addLabel.textContent = this._config?.add_button_label || "Add";
      addButton.appendChild(addLabel);

      actions.appendChild(addButton);
    }

    if (actions.childElementCount) {
      header.appendChild(actions);
    }
    if (header.childElementCount) {
      this._card.appendChild(header);
    }

    if (this._formOpen && this._config?.show_add_button) {
      this._card.appendChild(this._renderForm());
    }

    if (this._error) {
      const error = document.createElement("div");
      error.className = "error";
      error.textContent = this._error;
      this._card.appendChild(error);
    }

    const list = document.createElement("div");
    list.className = "list";

    if (!this._items.length) {
      const empty = document.createElement("div");
      empty.className = "empty";
      if (this._loading) {
        empty.textContent = "Loading...";
      } else {
        empty.textContent = "Keine Aufgaben";
      }
      list.appendChild(empty);
    } else {
      this._items.forEach((item) => {
        list.appendChild(this._renderRow(item));
      });
    }

    this._card.appendChild(list);
  }

  _renderForm() {
    const form = document.createElement("div");
    form.className = "form";

    const title = this._createField("Titel", "title");
    const description = this._createField("Beschreibung", "description");
    const due = this._createField("Datum (YYYY-MM-DD)", "due");
    const priority = this._createField("Priorität", "priority", "number");
    const icon = this._createField("Icon (mdi:...)", "icon");
    const color = this._createField("Farbe (#RRGGBB)", "color");
    const secondColor = this._createField("2. Farbe (#RRGGBB)", "second_color");
    const iconBackgroundColor = this._createField("Icon Hintergrund (#RRGGBB)", "icon_background_color");
    const iconColor = this._createField("Icon Farbe (#RRGGBB)", "icon_color");
    const textColor = this._createField("Textfarbe (#RRGGBB)", "text_color");
    const resolved = this._createField("Gelöst Text", "resolved_text");
    const cleanupHours = this._createField("Löschzeitraum (Stunden)", "cleanup_hours", "number");

    const row1 = document.createElement("div");
    row1.className = "form-row";
    row1.appendChild(title);

    const row2 = document.createElement("div");
    row2.className = "form-row";
    row2.appendChild(description);
    row2.appendChild(due);

    const row3 = document.createElement("div");
    row3.className = "form-row";
    row3.appendChild(priority);

    const row4 = document.createElement("div");
    row4.className = "form-row";
    row4.appendChild(icon);
    row4.appendChild(color);

    const row5 = document.createElement("div");
    row5.className = "form-row";
    row5.appendChild(secondColor);
    row5.appendChild(textColor);

    const row6 = document.createElement("div");
    row6.className = "form-row";
    row6.appendChild(iconBackgroundColor);
    row6.appendChild(iconColor);

    const row7 = document.createElement("div");
    row7.className = "form-row";
    row7.appendChild(resolved);
    row7.appendChild(cleanupHours);

    const persistentSwitch = document.createElement("ha-switch");
    persistentSwitch.checked = this._formValues.persistent;
    persistentSwitch.addEventListener("change", (event) => {
      this._formValues.persistent = event.target.checked;
    });

    const persistentRow = document.createElement("ha-formfield");
    persistentRow.setAttribute("label", "Dauerhaft (nicht löschen)");
    persistentRow.appendChild(persistentSwitch);

    const actions = document.createElement("div");
    actions.className = "form-actions";

    const cancel = document.createElement("button");
    cancel.className = "btn";
    cancel.textContent = "Abbrechen";
    cancel.addEventListener("click", () => this._toggleForm(false));

    const submit = document.createElement("button");
    submit.className = "btn";
    submit.textContent = "Speichern";
    submit.addEventListener("click", () => this._submitForm());

    actions.appendChild(cancel);
    actions.appendChild(submit);

    form.appendChild(row1);
    form.appendChild(row2);
    form.appendChild(row3);
    form.appendChild(row4);
    form.appendChild(row5);
    form.appendChild(row6);
    form.appendChild(row7);
    form.appendChild(persistentRow);
    form.appendChild(actions);

    return form;
  }

  _createField(label, key, type = "text") {
    const field = document.createElement("ha-textfield");
    field.label = label;
    field.type = type;
    field.value = this._formValues[key] ?? "";
    field.addEventListener("input", (event) => {
      const value = type === "number" ? Number(event.target.value) : event.target.value;
      this._formValues[key] = value;
    });
    return field;
  }

  _renderRow(item) {
    const row = document.createElement("div");
    row.className = "row";
    if (item.status === "completed") {
      row.classList.add("is-complete");
    }

    const iconContainer = document.createElement("div");
    iconContainer.className = "icon";

    const icon = document.createElement("ha-icon");
    icon.setAttribute("icon", item.icon || "mdi:clipboard-check");
    iconContainer.appendChild(icon);

    const content = document.createElement("div");
    content.className = "content";

    const title = document.createElement("div");
    title.className = "task-title";
    title.textContent = item.title || item.ident || "";

    const descRow = document.createElement("div");
    descRow.className = "task-desc-row";
    const desc = document.createElement("div");
    desc.className = "task-desc";
    desc.textContent = item.description || "";
    descRow.appendChild(desc);

    const meta = document.createElement("div");
    meta.className = "task-meta";

    if (item.persistent && item.status === "completed") {
      const resolved = document.createElement("span");
      resolved.className = "badge";
      resolved.textContent = item.resolved_text || "Gelöst";
      meta.appendChild(resolved);
    }

    content.appendChild(title);
    content.appendChild(descRow);
    if (meta.childElementCount) {
      content.appendChild(meta);
    }

    row.style.setProperty("--todo-row-text-color", this._rowTextColor(item.text_color));
    row.style.setProperty("--todo-row-background", this._rowBackground(item.color, item.second_color));
    row.style.setProperty(
      "--todo-icon-bg-color",
      this._iconBackgroundColor(item.icon_background_color, item.color, item.second_color)
    );
    row.style.setProperty(
      "--todo-icon-bg-hover-color",
      this._iconBackgroundColor(item.icon_background_color, item.color, item.second_color, true)
    );
    row.style.setProperty("--todo-icon-color", this._iconForegroundColor(item.icon_color, item.text_color));

    const check = document.createElement("div");
    check.className = "check";
    const checkIcon = document.createElement("ha-icon");
    checkIcon.setAttribute(
      "icon",
      item.status === "completed" ? "mdi:checkbox-marked" : "mdi:checkbox-blank-outline"
    );
    check.appendChild(checkIcon);
    check.addEventListener("click", (event) => {
      event.stopPropagation();
      this._completeItem(item);
    });

    const dueInline = item.due ? document.createElement("span") : null;
    if (dueInline) {
      dueInline.className = "badge due-inline";
      dueInline.textContent = this._formatDue(item.due);
    }

    if (item.color || item.second_color) {
      row.style.setProperty("--todo-row-color", this._rowColor(item.color));
      row.style.setProperty(
        "--todo-row-border-color",
        this._rowBorderColor(item.color, item.second_color)
      );
    }
    row.appendChild(iconContainer);
    row.appendChild(content);
    if (dueInline) {
      row.appendChild(dueInline);
    }
    row.appendChild(check);

    return row;
  }

  _toggleForm(force) {
    if (typeof force === "boolean") {
      this._formOpen = force;
    } else {
      this._formOpen = !this._formOpen;
    }
    this._render();
  }

  async _submitForm() {
    if (!this._hass) {
      return;
    }

    const title = (this._formValues.title || "").trim();
    if (!title) {
      this._error = "Titel ist erforderlich";
      this._render();
      return;
    }

    const ident = title;

    await this._hass.callService("todo_workflows", "upsert_item", {
      ident,
      title,
      description: this._formValues.description || "",
      due: this._formValues.due || "",
      priority: Number(this._formValues.priority || 0),
      icon: this._formValues.icon || "",
      color: this._formValues.color || "",
      second_color: this._formValues.second_color || "",
      icon_background_color: this._formValues.icon_background_color || "",
      icon_color: this._formValues.icon_color || "",
      text_color: this._formValues.text_color || "",
      persistent: Boolean(this._formValues.persistent),
      resolved_text: this._formValues.resolved_text || "",
    });

    this._formOpen = false;
    this._error = null;
    this._formValues.title = "";
    this._formValues.description = "";
    this._formValues.due = "";
    this._formValues.priority = 0;
    this._formValues.color = "";
    this._formValues.second_color = "";
    this._formValues.icon_background_color = "";
    this._formValues.icon_color = "";
    this._formValues.text_color = "";
    this._formValues.resolved_text = "";
    this._formValues.persistent = false;

    this._render(true);
  }

  async _completeItem(item) {
    const ident = item.ident || item.title;
    if (!this._hass || !ident) {
      return;
    }
    const payload = {
      ident,
      item_id: item.id,
      title: item.title,
      persistent: Boolean(item.persistent),
    };
    delete payload.uid;
    const payloadKeys = Object.keys(payload);
    console.debug("todo_workflows.complete_item payload", payload, payloadKeys);

    this._applyOptimisticCompletion(item);
    this._render(true);

    try {
      await this._hass.callService("todo_workflows", "complete_item_v2", payload);
    } catch (err) {
      this._error = err?.message || String(err);
      await this._fetchItems(true);
      this._render(true);
      return;
    }

    this._render(true);
  }

  _applyOptimisticCompletion(item) {
    if (item.persistent) {
      this._setItems(
        this._items.map((entry) => {
          if (!this._isSameItem(entry, item)) {
            return entry;
          }
          return {
            ...entry,
            status: "completed",
            completed_at: entry.completed_at || new Date().toISOString(),
          };
        })
      );
      return;
    }

    this._setItems(this._items.filter((entry) => !this._isSameItem(entry, item)));
  }

  _isSameItem(left, right) {
    if (!left || !right) {
      return false;
    }
    if (left.id && right.id) {
      return String(left.id) === String(right.id);
    }
    if (left.ident && right.ident) {
      return String(left.ident) === String(right.ident);
    }
    return String(left.title || "") === String(right.title || "");
  }

  async _update(force = false) {
    if (!this._hass || !this._config) {
      return;
    }

    const now = Date.now();
    if (force && this._pendingUpdateTimer) {
      clearTimeout(this._pendingUpdateTimer);
      this._pendingUpdateTimer = null;
    }

    if (!force && now - this._lastFetch < TODO_WORKFLOWS_FETCH_COOLDOWN_MS) {
      const remainingDelay = TODO_WORKFLOWS_FETCH_COOLDOWN_MS - (now - this._lastFetch);
      if (!this._pendingUpdateTimer) {
        this._pendingUpdateTimer = setTimeout(() => {
          this._pendingUpdateTimer = null;
          this._update();
        }, remainingDelay);
      }
      this._render(false);
      return;
    }

    await this._fetchItems(force);
    this._render(force);
  }

  async _fetchItems(force = false) {
    if (!this._hass || !this._config) {
      return;
    }

    if (this._fetchPromise && !force) {
      await this._fetchPromise;
      return;
    }

    this._loading = true;
    this._error = null;
    this._lastFetch = Date.now();

    this._fetchPromise = this._hass
      .callWS({
        type: "todo_workflows/list_items",
      })
      .then((result) => {
        const items =
          result?.items ?? result?.response?.items ?? result?.result?.items;
        if (Array.isArray(items)) {
          this._setItems(items);
          return;
        }
        this._setItems([]);
      })
      .catch((err) => {
        this._error = err?.message || String(err);
        this._setItems([]);
      })
      .finally(() => {
        this._loading = false;
        this._fetchPromise = null;
      });

    await this._fetchPromise;
  }

  _subscribeToItemUpdates() {
    if (!this._hass?.connection || this._subscriptionHass === this._hass) {
      return;
    }
    this._unsubscribeFromItemUpdates();
    this._subscriptionHass = this._hass;
    this._hass.connection
      .subscribeEvents(
        async () => {
          await this._fetchItems(true);
          this._render(true);
        },
        "todo_workflows_items_updated"
      )
      .then((unsubscribe) => {
        if (this._subscriptionHass === this._hass) {
          this._unsubscribeItems = unsubscribe;
        } else {
          unsubscribe();
        }
      })
      .catch((error) => {
        if (this._subscriptionHass === this._hass) {
          console.debug(
            "Todo Workflows live updates are unavailable; using list refreshes.",
            error
          );
        }
      });
  }

  _unsubscribeFromItemUpdates() {
    if (this._unsubscribeItems) {
      this._unsubscribeItems();
      this._unsubscribeItems = null;
    }
    this._subscriptionHass = null;
  }

  _sortItems(items) {
    return [...items].sort((a, b) => {
      const priorityA = Number.isFinite(a.priority) ? a.priority : 999;
      const priorityB = Number.isFinite(b.priority) ? b.priority : 999;
      if (priorityA !== priorityB) {
        return priorityA - priorityB;
      }
      const dueA = a.due ? Date.parse(a.due) : Number.MAX_SAFE_INTEGER;
      const dueB = b.due ? Date.parse(b.due) : Number.MAX_SAFE_INTEGER;
      if (dueA !== dueB) {
        return dueA - dueB;
      }
      return String(a.title || "").localeCompare(String(b.title || ""));
    });
  }

  _setItems(items) {
    const nextItems = this._sortItems(items);
    const nextSignature = JSON.stringify(nextItems);
    const currentSignature = JSON.stringify(this._items);
    if (nextSignature === currentSignature) {
      return;
    }
    this._items = nextItems;
  }

  _shouldForceFullReload() {
    return Date.now() - this._lastFullReloadAt >= TODO_WORKFLOWS_FULL_RELOAD_MS;
  }

  _getRenderSignature() {
    return JSON.stringify({
      title: this._config?.title || "",
      showAddButton: this._config?.show_add_button ?? true,
      addButtonLabel: this._config?.add_button_label || "Add",
      formOpen: this._formOpen,
      loading: this._loading,
      error: this._error,
      items: this._items,
    });
  }

  _rowColor(value) {
    if (!value || typeof value !== "string") {
      return "rgba(255,255,255,0.04)";
    }
    const hex = value.trim();
    if (!hex.startsWith("#") || (hex.length !== 7 && hex.length !== 4)) {
      return value;
    }
    const full = this._expandHex(hex);
    return full;
  }

  _rowBorderColor(primaryValue, secondaryValue) {
    const value = this._baseSurfaceColor(primaryValue, secondaryValue);
    if (!value) {
      return "rgba(255,255,255,0.18)";
    }
    const full = value;
    const r = parseInt(full.slice(1, 3), 16);
    const g = parseInt(full.slice(3, 5), 16);
    const b = parseInt(full.slice(5, 7), 16);
    const lighten = 36;
    const lr = Math.min(255, r + lighten);
    const lg = Math.min(255, g + lighten);
    const lb = Math.min(255, b + lighten);
    return `rgb(${lr}, ${lg}, ${lb})`;
  }

  _rowTextColor(value) {
    if (!value || typeof value !== "string") {
      return "#000000";
    }
    return value.trim() || "#000000";
  }

  _rowBackground(primaryValue, secondaryValue) {
    const primary = this._normalizedHexColor(primaryValue);
    const secondary = this._normalizedHexColor(secondaryValue);
    if (primary && secondary) {
      return `linear-gradient(135deg, ${primary} 0%, ${secondary} 100%)`;
    }
    if (primary) {
      return primary;
    }
    return "rgba(255,255,255,0.04)";
  }

  _iconBackgroundColor(iconBackgroundValue, primaryValue, secondaryValue, hover = false) {
    const iconBackground = this._normalizedHexColor(iconBackgroundValue);
    if (iconBackground) {
      return hover ? this._hoverColor(iconBackground) : iconBackground;
    }
    return this._iconSurfaceColor(primaryValue, secondaryValue, hover);
  }

  _iconForegroundColor(iconColorValue, textColorValue) {
    const iconColor = this._normalizedHexColor(iconColorValue);
    if (iconColor) {
      return iconColor;
    }
    return this._rowTextColor(textColorValue);
  }

  _iconSurfaceColor(primaryValue, secondaryValue, hover = false) {
    const value = this._baseSurfaceColor(primaryValue, secondaryValue);
    if (!value) {
      return hover ? "rgba(255,255,255,0.18)" : "rgba(255,255,255,0.13)";
    }
    const full = value;
    const r = parseInt(full.slice(1, 3), 16);
    const g = parseInt(full.slice(3, 5), 16);
    const b = parseInt(full.slice(5, 7), 16);
    const brightness = this._colorBrightness(r, g, b);
    const adjustment = brightness < 105 ? (hover ? 56 : 40) : hover ? -44 : -28;

    return this._adjustRgbColor(r, g, b, adjustment);
  }

  _hoverColor(hex) {
    const { r, g, b } = this._hexToRgb(hex);
    const brightness = this._colorBrightness(r, g, b);
    const adjustment = brightness < 105 ? 32 : -22;
    return this._adjustRgbColor(r, g, b, adjustment);
  }

  _baseSurfaceColor(primaryValue, secondaryValue) {
    const primary = this._normalizedHexColor(primaryValue);
    const secondary = this._normalizedHexColor(secondaryValue);
    if (primary && secondary) {
      return this._mixHexColors(primary, secondary);
    }
    return primary || null;
  }

  _normalizedHexColor(value) {
    if (!value || typeof value !== "string") {
      return null;
    }
    const hex = value.trim();
    if (!hex.startsWith("#") || (hex.length !== 7 && hex.length !== 4)) {
      return null;
    }
    return this._expandHex(hex);
  }

  _mixHexColors(firstHex, secondHex) {
    const first = this._hexToRgb(firstHex);
    const second = this._hexToRgb(secondHex);
    return this._rgbToHex(
      Math.round((first.r + second.r) / 2),
      Math.round((first.g + second.g) / 2),
      Math.round((first.b + second.b) / 2)
    );
  }

  _hexToRgb(hex) {
    return {
      r: parseInt(hex.slice(1, 3), 16),
      g: parseInt(hex.slice(3, 5), 16),
      b: parseInt(hex.slice(5, 7), 16),
    };
  }

  _rgbToHex(r, g, b) {
    return `#${[r, g, b]
      .map((channel) => channel.toString(16).padStart(2, "0"))
      .join("")}`;
  }

  _colorBrightness(r, g, b) {
    return (r * 299 + g * 587 + b * 114) / 1000;
  }

  _adjustRgbColor(r, g, b, adjustment) {
    const nextR = Math.max(0, Math.min(255, r + adjustment));
    const nextG = Math.max(0, Math.min(255, g + adjustment));
    const nextB = Math.max(0, Math.min(255, b + adjustment));
    return `rgb(${nextR}, ${nextG}, ${nextB})`;
  }

  _expandHex(hex) {
    return hex.length === 4
      ? `#${hex[1]}${hex[1]}${hex[2]}${hex[2]}${hex[3]}${hex[3]}`
      : hex;
  }

  _formatDue(due) {
    const parsed = Date.parse(due);
    if (Number.isNaN(parsed)) {
      return due;
    }
    return new Date(parsed).toLocaleDateString(this._hass?.locale?.language || "de-DE");
  }

}

customElements.define("todo-workflows-card", TodoWorkflowsCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "todo-workflows-card",
  name: "Todo Workflows",
  description: "Interactive todo list card with title-based workflows.",
  preview: false,
});

class TodoWorkflowsCardEditor extends HTMLElement {
  constructor() {
    super();
    this._config = null;
    this._hass = null;
    this._lastRenderSignature = null;
    this._lastFullReloadAt = 0;
  }

  setConfig(config) {
    this._config = config;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    if (!this.shadowRoot) {
      this._render(true);
      return;
    }
    this._updateEditorBindings();
    if (Date.now() - this._lastFullReloadAt >= TODO_WORKFLOWS_FULL_RELOAD_MS) {
      this._render(true);
    }
  }

  _render(force = false) {
    const renderSignature = JSON.stringify({
      title: this._config?.title || "",
      showAddButton: this._config?.show_add_button ?? true,
    });
    if (!force && this.shadowRoot && renderSignature === this._lastRenderSignature) {
      this._updateEditorBindings();
      return;
    }
    this._lastRenderSignature = renderSignature;
    this._lastFullReloadAt = Date.now();

    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
    }
    const root = this.shadowRoot;
    root.innerHTML = `
      <style>
        .form {
          display: grid;
          gap: 8px;
        }
      </style>
      <div class="form">
        <ha-textfield label="Title" name="title"></ha-textfield>
        <ha-switch name="show_add_button"></ha-switch>
      </div>
    `;

    const title = root.querySelector("ha-textfield[name=title]");
    const addToggle = root.querySelector("ha-switch[name=show_add_button]");

    this._updateEditorBindings();

    const handler = (event) => {
      const target = event.target;
      if (!this._config) {
        return;
      }
      const newConfig = { ...this._config };
      if (target.name === "show_add_button") {
        newConfig.show_add_button = target.checked;
      } else {
        newConfig[target.name] = target.value;
      }
      this._config = newConfig;
      this.dispatchEvent(
        new CustomEvent("config-changed", {
          detail: { config: newConfig },
          bubbles: true,
          composed: true,
        })
      );
    };

    title.addEventListener("input", handler);
    addToggle.addEventListener("change", handler);
  }

  _updateEditorBindings() {
    if (!this.shadowRoot) {
      return;
    }
    const title = this.shadowRoot.querySelector("ha-textfield[name=title]");
    const addToggle = this.shadowRoot.querySelector("ha-switch[name=show_add_button]");
    if (!title || !addToggle) {
      return;
    }
    title.value = this._config?.title || "";
    addToggle.checked = this._config?.show_add_button ?? true;
  }
}

customElements.define("todo-workflows-card-editor", TodoWorkflowsCardEditor);
