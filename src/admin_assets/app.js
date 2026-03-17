const state = {
  token: sessionStorage.getItem("freelunch_admin_token") || "",
  secretKeys: [],
  secretManagement: {},
};

const elements = {
  bearerToken: document.getElementById("bearerToken"),
  statusMessage: document.getElementById("statusMessage"),
  healthOutput: document.getElementById("healthOutput"),
  modelsTable: document.getElementById("modelsTable"),
  configTable: document.getElementById("configTable"),
  logsTable: document.getElementById("logsTable"),
  secretSummary: document.getElementById("secretSummary"),
  secretHelp: document.getElementById("secretHelp"),
  secretKey: document.getElementById("secretKey"),
  secretValue: document.getElementById("secretValue"),
  configKey: document.getElementById("configKey"),
  configValue: document.getElementById("configValue"),
  logsLimit: document.getElementById("logsLimit"),
  logsSuccessOnly: document.getElementById("logsSuccessOnly"),
  vaultSummary: document.getElementById("vaultSummary"),
  vaultSetupForm: document.getElementById("vaultSetupForm"),
  vaultSetupPassword: document.getElementById("vaultSetupPassword"),
  vaultUnlockForm: document.getElementById("vaultUnlockForm"),
  vaultUnlockPassword: document.getElementById("vaultUnlockPassword"),
  vaultUnlockedCard: document.getElementById("vaultUnlockedCard"),
  lockVaultButton: document.getElementById("lockVaultButton"),
  uninstallPanel: document.getElementById("uninstallPanel"),
};

elements.bearerToken.value = state.token;

function setStatus(message, kind = "idle") {
  elements.statusMessage.textContent = message;
  elements.statusMessage.className = `status ${kind}`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
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

function renderTable(columns, rows) {
  if (!rows.length) {
    return `<p class="muted">No data.</p>`;
  }
  const headers = columns.map((column) => `<th>${escapeHtml(column.label)}</th>`).join("");
  const body = rows
    .map((row) => {
      const cells = columns
        .map((column) => `<td>${column.render ? column.render(row) : escapeHtml(row[column.key])}</td>`)
        .join("");
      return `<tr>${cells}</tr>`;
    })
    .join("");
  return `<table><thead><tr>${headers}</tr></thead><tbody>${body}</tbody></table>`;
}

function updateSecretFormAvailability() {
  const unlocked = Boolean(state.secretManagement.unlocked);
  const configured = Boolean(state.secretManagement.configured);
  const disabled = !unlocked;
  elements.secretKey.disabled = disabled;
  elements.secretValue.disabled = disabled;
  document.querySelector('#secretForm button[type="submit"]').disabled = disabled;
  document.getElementById("deleteSecretButton").disabled = disabled;
  if (!configured) {
    elements.secretHelp.textContent = "Create the vault first, then save provider secrets.";
  } else if (!unlocked) {
    elements.secretHelp.textContent = "Unlock the vault to edit provider secrets.";
  } else {
    elements.secretHelp.textContent = "Managed secrets are written encrypted to SQLite and applied immediately.";
  }
}

function renderVault() {
  const configured = Boolean(state.secretManagement.configured);
  const unlocked = Boolean(state.secretManagement.unlocked);
  const storedSecretCount = Number(state.secretManagement.stored_secret_count || 0);
  const loadedSecretCount = Number(state.secretManagement.loaded_secret_count || 0);

  if (!configured) {
    elements.vaultSummary.textContent = "No vault password is configured yet. Creating one unlocks the vault for this process.";
  } else if (!unlocked) {
    elements.vaultSummary.textContent = `Vault configured. ${storedSecretCount} encrypted secret${storedSecretCount === 1 ? "" : "s"} stored.`;
  } else {
    elements.vaultSummary.textContent = `Vault unlocked. ${loadedSecretCount} secret${loadedSecretCount === 1 ? "" : "s"} loaded for this process.`;
  }

  elements.vaultSetupForm.classList.toggle("hidden", configured);
  elements.vaultUnlockForm.classList.toggle("hidden", !configured || unlocked);
  elements.vaultUnlockedCard.classList.toggle("hidden", !configured || !unlocked);
  updateSecretFormAvailability();
}

async function loadHealth() {
  const payload = await requestJson("/admin/health");
  elements.healthOutput.textContent = JSON.stringify(payload, null, 2);
}

async function loadModels() {
  const payload = await requestJson("/admin/models");
  elements.modelsTable.innerHTML = renderTable(
    [
      { key: "id", label: "Model" },
      { key: "provider_id", label: "Provider" },
      { key: "is_active", label: "Active" },
      { key: "is_healthy", label: "Healthy" },
      {
        key: "actions",
        label: "Actions",
        render: (row) =>
          `<button type="button" data-model-action="${row.is_active ? "disable" : "enable"}" data-model-id="${escapeHtml(row.id)}">${row.is_active ? "Disable" : "Enable"}</button>`,
      },
    ],
    payload.models || [],
  );
}

async function loadConfig() {
  const payload = await requestJson("/admin/config");
  elements.configTable.innerHTML = renderTable(
    [
      { key: "key", label: "Key" },
      { key: "value", label: "Value", render: (row) => escapeHtml(JSON.stringify(row.value)) },
      { key: "updated_at", label: "Updated" },
    ],
    payload.overrides || [],
  );
}

async function loadLogs() {
  const params = new URLSearchParams();
  params.set("limit", elements.logsLimit.value || "20");
  if (elements.logsSuccessOnly.value) {
    params.set("success_only", elements.logsSuccessOnly.value);
  }
  const payload = await requestJson(`/admin/logs?${params.toString()}`);
  elements.logsTable.innerHTML = renderTable(
    [
      { key: "timestamp", label: "Timestamp" },
      { key: "selected_model_id", label: "Model" },
      { key: "provider_id", label: "Provider" },
      { key: "success", label: "Success" },
      { key: "gateway_error_category", label: "Error" },
    ],
    payload.logs || [],
  );
}

async function loadSecrets() {
  const payload = await requestJson("/admin/secrets");
  const secrets = payload.secrets || [];
  state.secretManagement = payload.secret_management || {};
  state.secretKeys = secrets.map((item) => item.key);
  elements.secretKey.innerHTML = state.secretKeys
    .map((key) => `<option value="${escapeHtml(key)}">${escapeHtml(key)}</option>`)
    .join("");
  elements.secretSummary.innerHTML = renderTable(
    [
      { key: "label", label: "Target" },
      { key: "source", label: "Source" },
      { key: "configured", label: "Configured" },
      { key: "env_var", label: "Env var" },
      { key: "updated_at", label: "Updated" },
    ],
    secrets,
  );
  renderVault();
}

async function loadUninstallInfo() {
  const payload = await requestJson("/admin/uninstall");
  const commands = (payload.commands || [])
    .map(
      (item) =>
        `<div class="command-card"><strong>${escapeHtml(item.label)}</strong><code>${escapeHtml(item.command)}</code></div>`,
    )
    .join("");
  elements.uninstallPanel.innerHTML = `
    <p class="muted">${escapeHtml(payload.reason || "Uninstall details unavailable.")}</p>
    <p class="muted">Admin UI: <code>${escapeHtml(payload.admin_ui_url || "")}</code></p>
    ${commands || '<p class="muted">No uninstall commands available.</p>'}
  `;
}

async function refreshAll() {
  setStatus("Refreshing…");
  try {
    await Promise.all([
      loadHealth(),
      loadModels(),
      loadConfig(),
      loadLogs(),
      loadSecrets(),
      loadUninstallInfo(),
    ]);
    setStatus("Admin data refreshed.", "ok");
  } catch (error) {
    setStatus(String(error.message || error), "error");
  }
}

document.getElementById("saveTokenButton").addEventListener("click", () => {
  state.token = elements.bearerToken.value.trim();
  sessionStorage.setItem("freelunch_admin_token", state.token);
  refreshAll();
});

document.getElementById("clearTokenButton").addEventListener("click", () => {
  state.token = "";
  elements.bearerToken.value = "";
  sessionStorage.removeItem("freelunch_admin_token");
  refreshAll();
});

document.getElementById("refreshAllButton").addEventListener("click", refreshAll);

document.getElementById("refreshDiscoveryButton").addEventListener("click", async () => {
  setStatus("Running discovery refresh…");
  try {
    await requestJson("/admin/refresh", { method: "POST" });
    await refreshAll();
  } catch (error) {
    setStatus(String(error.message || error), "error");
  }
});

elements.vaultSetupForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  setStatus("Creating vault…");
  try {
    await requestJson("/admin/secrets/vault/setup", {
      method: "POST",
      body: JSON.stringify({ password: elements.vaultSetupPassword.value }),
    });
    elements.vaultSetupPassword.value = "";
    await refreshAll();
    setStatus("Vault created and unlocked.", "ok");
  } catch (error) {
    setStatus(String(error.message || error), "error");
  }
});

elements.vaultUnlockForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  setStatus("Unlocking vault…");
  try {
    await requestJson("/admin/secrets/vault/unlock", {
      method: "POST",
      body: JSON.stringify({ password: elements.vaultUnlockPassword.value }),
    });
    elements.vaultUnlockPassword.value = "";
    await refreshAll();
    setStatus("Vault unlocked.", "ok");
  } catch (error) {
    setStatus(String(error.message || error), "error");
  }
});

elements.lockVaultButton.addEventListener("click", async () => {
  setStatus("Locking vault…");
  try {
    await requestJson("/admin/secrets/vault/lock", { method: "POST" });
    await refreshAll();
    setStatus("Vault locked.", "ok");
  } catch (error) {
    setStatus(String(error.message || error), "error");
  }
});

document.getElementById("secretForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  setStatus("Saving secret…");
  try {
    await requestJson(`/admin/secrets/${elements.secretKey.value}`, {
      method: "PUT",
      body: JSON.stringify({ value: elements.secretValue.value }),
    });
    elements.secretValue.value = "";
    await loadSecrets();
    await loadHealth();
    setStatus("Secret saved.", "ok");
  } catch (error) {
    setStatus(String(error.message || error), "error");
  }
});

document.getElementById("deleteSecretButton").addEventListener("click", async () => {
  setStatus("Deleting secret…");
  try {
    await requestJson(`/admin/secrets/${elements.secretKey.value}`, { method: "DELETE" });
    elements.secretValue.value = "";
    await loadSecrets();
    await loadHealth();
    setStatus("Secret deleted.", "ok");
  } catch (error) {
    setStatus(String(error.message || error), "error");
  }
});

document.getElementById("configForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  setStatus("Saving override…");
  try {
    await requestJson(`/admin/config/${elements.configKey.value.trim()}`, {
      method: "PUT",
      body: JSON.stringify({ value: JSON.parse(elements.configValue.value) }),
    });
    await loadConfig();
    await loadHealth();
    setStatus("Override saved.", "ok");
  } catch (error) {
    setStatus(String(error.message || error), "error");
  }
});

document.getElementById("deleteConfigButton").addEventListener("click", async () => {
  setStatus("Deleting override…");
  try {
    await requestJson(`/admin/config/${elements.configKey.value.trim()}`, { method: "DELETE" });
    await loadConfig();
    await loadHealth();
    setStatus("Override deleted.", "ok");
  } catch (error) {
    setStatus(String(error.message || error), "error");
  }
});

document.getElementById("logsForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  setStatus("Loading logs…");
  try {
    await loadLogs();
    setStatus("Logs loaded.", "ok");
  } catch (error) {
    setStatus(String(error.message || error), "error");
  }
});

elements.modelsTable.addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-model-action]");
  if (!button) {
    return;
  }
  setStatus("Updating model…");
  try {
    await requestJson(
      `/admin/models/${encodeURIComponent(button.dataset.modelId)}/${button.dataset.modelAction}`,
      { method: "POST" },
    );
    await loadModels();
    await loadHealth();
    setStatus("Model updated.", "ok");
  } catch (error) {
    setStatus(String(error.message || error), "error");
  }
});

refreshAll();
