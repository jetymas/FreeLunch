import { renderHealthPage } from "./page-health.js";
import { renderVaultPage } from "./page-vault.js";
import { renderModelsPage } from "./page-models.js";
import { renderSettingsPage } from "./page-settings.js";
import { renderLogsPage } from "./page-logs.js";

const PAGES = {
  health: { eyebrow: "Health", title: "Gateway health and readiness" },
  vault: { eyebrow: "Vault", title: "Runtime password vault and provider secrets" },
  models: { eyebrow: "Models", title: "Rankings, capabilities, and model activation" },
  settings: { eyebrow: "Settings", title: "Effective config, overrides, and console preferences" },
  logs: { eyebrow: "Logs", title: "Recent request telemetry and failure detail" },
};

const STORAGE_KEYS = {
  token: "freelunch_admin_token",
  defaultPage: "freelunch_admin_default_page",
};

function normalizePage(page) {
  return Object.hasOwn(PAGES, page) ? page : "health";
}

function initialPage() {
  const hashPage = window.location.hash.replace(/^#/, "").trim();
  if (Object.hasOwn(PAGES, hashPage)) {
    return hashPage;
  }
  return normalizePage(localStorage.getItem(STORAGE_KEYS.defaultPage) || "health");
}

const state = {
  token: sessionStorage.getItem(STORAGE_KEYS.token) || "",
  defaultPage: normalizePage(localStorage.getItem(STORAGE_KEYS.defaultPage) || "health"),
  currentPage: initialPage(),
  data: {
    health: null,
    secrets: null,
    models: null,
    config: null,
    logs: null,
    uninstall: null,
  },
  filters: {
    models: {
      search: "",
      provider: "all",
      showInactive: true,
    },
    logs: {
      limit: 50,
      successOnly: "",
      providerId: "",
      requestSource: "",
      modelId: "",
    },
  },
};

const elements = {
  bearerToken: document.getElementById("bearerToken"),
  statusMessage: document.getElementById("statusMessage"),
  pageEyebrow: document.getElementById("pageEyebrow"),
  pageTitle: document.getElementById("pageTitle"),
  globalSummary: document.getElementById("globalSummary"),
  pageView: document.getElementById("pageView"),
  navLinks: Array.from(document.querySelectorAll("[data-page-link]")),
};

elements.bearerToken.value = state.token;

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function setStatus(message, kind = "idle") {
  elements.statusMessage.textContent = message;
  elements.statusMessage.className = `status ${kind}`;
}

function authHeaders() {
  if (!state.token) {
    return {};
  }
  return { Authorization: `Bearer ${state.token}` };
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(),
      ...(options.headers || {}),
    },
  });
  if (!response.ok) {
    const payload = await response.text();
    throw new Error(`${response.status} ${payload || response.statusText}`);
  }
  return response.json();
}

function overrideValueFromInput(rawValue) {
  const trimmed = rawValue.trim();
  if (!trimmed) {
    return "";
  }
  try {
    return JSON.parse(trimmed);
  } catch {
    return trimmed;
  }
}

function updateHash(page) {
  const normalized = normalizePage(page);
  if (window.location.hash !== `#${normalized}`) {
    window.location.hash = normalized;
  }
}

function renderSummaryStrip() {
  const health = state.data.health;
  const models = health?.models || {};
  const secretManagement = state.data.secrets?.secret_management || health?.secret_management || {};
  if (!health) {
    elements.globalSummary.innerHTML =
      '<article class="summary-card"><p class="metric-label">Status</p><p class="metric-value">Loading</p></article>';
    return;
  }
  const providerCount = Array.isArray(models.providers) ? models.providers.length : 0;
  const cards = [
    {
      label: "Gateway",
      value: health.bootstrap?.ready ? "Ready" : "Degraded",
      tone: health.bootstrap?.ready ? "success" : "warn",
      meta: `${models.routable ?? 0} routable models`,
    },
    {
      label: "Providers",
      value: String(providerCount),
      tone: "neutral",
      meta: `${models.total ?? 0} discovered models`,
    },
    {
      label: "Vault",
      value: secretManagement.unlocked
        ? "Unlocked"
        : secretManagement.configured
          ? "Locked"
          : "Not configured",
      tone: secretManagement.unlocked ? "success" : "warn",
      meta: `${secretManagement.stored_secret_count ?? 0} stored secrets`,
    },
    {
      label: "Runtime log",
      value: health.runtime_logging?.enabled ? "Enabled" : "Disabled",
      tone: health.runtime_logging?.enabled ? "success" : "warn",
      meta: health.runtime_logging?.verbosity || "n/a",
    },
  ];

  elements.globalSummary.innerHTML = cards
    .map(
      (card) => `
        <article class="summary-card tone-${card.tone}">
          <p class="metric-label">${escapeHtml(card.label)}</p>
          <p class="metric-value">${escapeHtml(card.value)}</p>
          <p class="metric-meta">${escapeHtml(card.meta)}</p>
        </article>
      `,
    )
    .join("");
}

function renderCurrentPage() {
  switch (state.currentPage) {
    case "vault":
      return renderVaultPage(
        state.data.secrets?.secret_management || {},
        state.data.secrets?.secrets || [],
      );
    case "models":
      return renderModelsPage(state.data.models || { models: [] }, state.filters.models);
    case "settings":
      return renderSettingsPage(state.data.config || {}, state.data.uninstall || {}, state.defaultPage);
    case "logs":
      return renderLogsPage(state.data.logs || { logs: [], limit: 0 }, state.filters.logs);
    case "health":
    default:
      return renderHealthPage(state.data.health || {});
  }
}

function applyPageChrome() {
  const pageInfo = PAGES[state.currentPage];
  elements.pageEyebrow.textContent = pageInfo.eyebrow;
  elements.pageTitle.textContent = pageInfo.title;
  elements.navLinks.forEach((button) => {
    button.classList.toggle("active", button.dataset.pageLink === state.currentPage);
  });
  renderSummaryStrip();
  elements.pageView.innerHTML = renderCurrentPage();
  bindPageEvents();
}

async function loadHealth() {
  state.data.health = await requestJson("/admin/health");
}

async function loadSecrets() {
  state.data.secrets = await requestJson("/admin/secrets");
}

async function loadModels() {
  state.data.models = await requestJson("/admin/models");
}

async function loadConfig() {
  state.data.config = await requestJson("/admin/config");
}

async function loadUninstall() {
  state.data.uninstall = await requestJson("/admin/uninstall");
}

async function loadLogs() {
  const params = new URLSearchParams();
  params.set("limit", String(state.filters.logs.limit || 50));
  if (state.filters.logs.successOnly) {
    params.set("success_only", state.filters.logs.successOnly);
  }
  if (state.filters.logs.providerId) {
    params.set("provider_id", state.filters.logs.providerId);
  }
  if (state.filters.logs.requestSource) {
    params.set("request_source", state.filters.logs.requestSource);
  }
  if (state.filters.logs.modelId) {
    params.set("model_id", state.filters.logs.modelId);
  }
  state.data.logs = await requestJson(`/admin/logs?${params.toString()}`);
}

async function refreshAll() {
  setStatus("Refreshing all pages...");
  try {
    await Promise.all([
      loadHealth(),
      loadSecrets(),
      loadModels(),
      loadConfig(),
      loadLogs(),
      loadUninstall(),
    ]);
    applyPageChrome();
    setStatus("Admin data refreshed.", "ok");
  } catch (error) {
    setStatus(String(error.message || error), "error");
  }
}

async function refreshCurrentPage() {
  setStatus(`Refreshing ${state.currentPage}...`);
  try {
    const loads = [loadHealth(), loadSecrets()];
    if (state.currentPage === "vault") {
      loads.push(loadModels());
    }
    if (state.currentPage === "models") {
      loads.push(loadModels());
    }
    if (state.currentPage === "settings") {
      loads.push(loadConfig(), loadUninstall());
    }
    if (state.currentPage === "logs") {
      loads.push(loadLogs());
    }
    await Promise.all(loads);
    applyPageChrome();
    setStatus(`${PAGES[state.currentPage].eyebrow} refreshed.`, "ok");
  } catch (error) {
    setStatus(String(error.message || error), "error");
  }
}

function setPage(page, { updateLocation = true } = {}) {
  state.currentPage = normalizePage(page);
  if (updateLocation) {
    updateHash(state.currentPage);
  }
  applyPageChrome();
}

function bindVaultEvents() {
  const vaultSetupForm = document.getElementById("vaultSetupForm");
  const vaultUnlockForm = document.getElementById("vaultUnlockForm");
  const lockVaultButton = document.getElementById("lockVaultButton");
  const secretForm = document.getElementById("secretForm");
  const deleteSecretButton = document.getElementById("deleteSecretButton");

  vaultSetupForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const password = document.getElementById("vaultSetupPassword").value;
    setStatus("Creating vault...");
    try {
      await requestJson("/admin/secrets/vault/setup", {
        method: "POST",
        body: JSON.stringify({ password }),
      });
      await Promise.all([loadHealth(), loadSecrets(), loadModels()]);
      applyPageChrome();
      setStatus("Vault created and unlocked.", "ok");
    } catch (error) {
      setStatus(String(error.message || error), "error");
    }
  });

  vaultUnlockForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const password = document.getElementById("vaultUnlockPassword").value;
    setStatus("Unlocking vault...");
    try {
      await requestJson("/admin/secrets/vault/unlock", {
        method: "POST",
        body: JSON.stringify({ password }),
      });
      await Promise.all([loadHealth(), loadSecrets(), loadModels()]);
      applyPageChrome();
      setStatus("Vault unlocked.", "ok");
    } catch (error) {
      setStatus(String(error.message || error), "error");
    }
  });

  lockVaultButton?.addEventListener("click", async () => {
    setStatus("Locking vault...");
    try {
      await requestJson("/admin/secrets/vault/lock", { method: "POST" });
      await Promise.all([loadHealth(), loadSecrets(), loadModels()]);
      applyPageChrome();
      setStatus("Vault locked.", "ok");
    } catch (error) {
      setStatus(String(error.message || error), "error");
    }
  });

  secretForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const secretKey = document.getElementById("secretKey").value;
    const secretValue = document.getElementById("secretValue").value;
    setStatus("Saving secret...");
    try {
      await requestJson(`/admin/secrets/${encodeURIComponent(secretKey)}`, {
        method: "PUT",
        body: JSON.stringify({ value: secretValue }),
      });
      await Promise.all([loadHealth(), loadSecrets(), loadModels()]);
      applyPageChrome();
      setStatus("Secret saved.", "ok");
    } catch (error) {
      setStatus(String(error.message || error), "error");
    }
  });

  deleteSecretButton?.addEventListener("click", async () => {
    const secretKey = document.getElementById("secretKey").value;
    setStatus("Deleting secret...");
    try {
      await requestJson(`/admin/secrets/${encodeURIComponent(secretKey)}`, {
        method: "DELETE",
      });
      await Promise.all([loadHealth(), loadSecrets(), loadModels()]);
      applyPageChrome();
      setStatus("Secret deleted.", "ok");
    } catch (error) {
      setStatus(String(error.message || error), "error");
    }
  });
}

function bindModelsEvents() {
  const modelsFilterForm = document.getElementById("modelsFilterForm");
  modelsFilterForm?.addEventListener("submit", (event) => {
    event.preventDefault();
    state.filters.models.search = document.getElementById("modelsSearchInput").value.trim();
    state.filters.models.provider = document.getElementById("modelsProviderFilter").value;
    state.filters.models.showInactive = document.getElementById("modelsShowInactive").checked;
    applyPageChrome();
  });

  elements.pageView.querySelectorAll("[data-model-action]").forEach((button) => {
    button.addEventListener("click", async () => {
      const modelId = button.dataset.modelId;
      const action = button.dataset.modelAction;
      setStatus(`Updating ${modelId}...`);
      try {
        await requestJson(`/admin/models/${encodeURIComponent(modelId)}/${action}`, {
          method: "POST",
        });
        await Promise.all([loadHealth(), loadModels()]);
        applyPageChrome();
        setStatus("Model state updated.", "ok");
      } catch (error) {
        setStatus(String(error.message || error), "error");
      }
    });
  });
}

function bindSettingsEvents() {
  document.getElementById("saveDefaultPageButton")?.addEventListener("click", () => {
    state.defaultPage = normalizePage(document.getElementById("defaultPageSelect").value);
    localStorage.setItem(STORAGE_KEYS.defaultPage, state.defaultPage);
    setStatus(`Default page saved as ${state.defaultPage}.`, "ok");
  });

  const settingsForm = document.getElementById("settingsForm");
  settingsForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const key = document.getElementById("settingsKey").value.trim();
    const rawValue = document.getElementById("settingsValue").value;
    setStatus(`Saving override ${key}...`);
    try {
      await requestJson(`/admin/config/${encodeURIComponent(key)}`, {
        method: "PUT",
        body: JSON.stringify({ value: overrideValueFromInput(rawValue) }),
      });
      await Promise.all([loadHealth(), loadConfig()]);
      applyPageChrome();
      setStatus("Override saved.", "ok");
    } catch (error) {
      setStatus(String(error.message || error), "error");
    }
  });

  document.getElementById("deleteSettingButton")?.addEventListener("click", async () => {
    const key = document.getElementById("settingsKey").value.trim();
    setStatus(`Deleting override ${key}...`);
    try {
      await requestJson(`/admin/config/${encodeURIComponent(key)}`, {
        method: "DELETE",
      });
      await Promise.all([loadHealth(), loadConfig()]);
      applyPageChrome();
      setStatus("Override deleted.", "ok");
    } catch (error) {
      setStatus(String(error.message || error), "error");
    }
  });

  elements.pageView.querySelectorAll("[data-load-setting-key]").forEach((button) => {
    button.addEventListener("click", () => {
      document.getElementById("settingsKey").value = button.dataset.loadSettingKey || "";
      document.getElementById("settingsValue").value = button.dataset.loadSettingValue || "";
      setStatus(`Loaded ${button.dataset.loadSettingKey} into the editor.`, "ok");
    });
  });

  document.getElementById("gatewayAuthForm")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const key = document.getElementById("gatewayAuthKey").value;
    setStatus("Updating gateway auth...");
    try {
      await requestJson("/admin/gateway-auth", {
        method: "PUT",
        body: JSON.stringify({ key }),
      });
      state.token = key.trim();
      elements.bearerToken.value = state.token;
      sessionStorage.setItem(STORAGE_KEYS.token, state.token);
      await Promise.all([loadHealth(), loadConfig()]);
      applyPageChrome();
      setStatus("Gateway auth updated. The new token is now active in this browser session.", "ok");
    } catch (error) {
      setStatus(String(error.message || error), "error");
    }
  });

  document.getElementById("disableGatewayAuthButton")?.addEventListener("click", async () => {
    setStatus("Disabling gateway auth...");
    try {
      await requestJson("/admin/gateway-auth", { method: "DELETE" });
      state.token = "";
      elements.bearerToken.value = "";
      sessionStorage.removeItem(STORAGE_KEYS.token);
      await Promise.all([loadHealth(), loadConfig()]);
      applyPageChrome();
      setStatus("Gateway auth disabled.", "ok");
    } catch (error) {
      setStatus(String(error.message || error), "error");
    }
  });

  document.getElementById("inheritGatewayAuthButton")?.addEventListener("click", async () => {
    setStatus("Reverting gateway auth to environment inheritance...");
    try {
      await requestJson("/admin/gateway-auth/inherit", { method: "POST" });
      await Promise.all([loadHealth(), loadConfig()]);
      applyPageChrome();
      setStatus("Gateway auth now follows the environment/default behavior.", "ok");
    } catch (error) {
      setStatus(String(error.message || error), "error");
    }
  });
}

function bindLogsEvents() {
  const logsForm = document.getElementById("logsForm");
  logsForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    state.filters.logs.limit = Number(document.getElementById("logsLimit").value || 50);
    state.filters.logs.successOnly = document.getElementById("logsSuccessOnly").value;
    state.filters.logs.providerId = document.getElementById("logsProviderId").value.trim();
    state.filters.logs.requestSource = document.getElementById("logsRequestSource").value.trim();
    state.filters.logs.modelId = document.getElementById("logsModelId").value.trim();
    setStatus("Loading logs...");
    try {
      await loadLogs();
      applyPageChrome();
      setStatus("Logs refreshed.", "ok");
    } catch (error) {
      setStatus(String(error.message || error), "error");
    }
  });
}

function bindPageEvents() {
  if (state.currentPage === "vault") {
    bindVaultEvents();
  }
  if (state.currentPage === "models") {
    bindModelsEvents();
  }
  if (state.currentPage === "settings") {
    bindSettingsEvents();
  }
  if (state.currentPage === "logs") {
    bindLogsEvents();
  }
}

document.getElementById("saveTokenButton").addEventListener("click", async () => {
  state.token = elements.bearerToken.value.trim();
  sessionStorage.setItem(STORAGE_KEYS.token, state.token);
  await refreshAll();
});

document.getElementById("clearTokenButton").addEventListener("click", async () => {
  state.token = "";
  elements.bearerToken.value = "";
  sessionStorage.removeItem(STORAGE_KEYS.token);
  await refreshAll();
});

document.getElementById("refreshAllButton").addEventListener("click", refreshAll);
document.getElementById("refreshCurrentButton").addEventListener("click", refreshCurrentPage);

document.getElementById("refreshDiscoveryButton").addEventListener("click", async () => {
  setStatus("Running discovery refresh...");
  try {
    await requestJson("/admin/refresh", { method: "POST" });
    await refreshAll();
    setStatus("Discovery refresh completed.", "ok");
  } catch (error) {
    setStatus(String(error.message || error), "error");
  }
});

elements.navLinks.forEach((button) => {
  button.addEventListener("click", () => setPage(button.dataset.pageLink));
});

window.addEventListener("hashchange", () => {
  const nextPage = normalizePage(window.location.hash.replace(/^#/, "").trim() || state.defaultPage);
  if (nextPage !== state.currentPage) {
    setPage(nextPage, { updateLocation: false });
  }
});

updateHash(state.currentPage);
refreshAll();
