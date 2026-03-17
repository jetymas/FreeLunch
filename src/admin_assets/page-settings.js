function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function formatJson(value) {
  if (typeof value === "string") {
    return value;
  }
  return JSON.stringify(value);
}

function groupSettings(effectiveValues) {
  const grouped = new Map();
  for (const entry of effectiveValues || []) {
    const section = entry.section || "other";
    const rows = grouped.get(section) || [];
    rows.push(entry);
    grouped.set(section, rows);
  }
  return Array.from(grouped.entries()).sort(([left], [right]) => left.localeCompare(right));
}

function renderOverrideRows(overrides) {
  if (!overrides.length) {
    return '<p class="empty-state">No runtime overrides are active.</p>';
  }
  return `
    <div class="table-shell">
      <table>
        <thead>
          <tr>
            <th>Key</th>
            <th>Value</th>
            <th>Updated</th>
            <th>Action</th>
          </tr>
        </thead>
        <tbody>
          ${overrides
            .map(
              (override) => `
                <tr>
                  <td>${escapeHtml(override.key)}</td>
                  <td><code>${escapeHtml(String(override.value))}</code></td>
                  <td>${escapeHtml(override.updated_at || "n/a")}</td>
                  <td>
                    <button type="button" class="ghost" data-load-setting-key="${escapeHtml(override.key)}" data-load-setting-value="${escapeHtml(String(override.value))}">Load</button>
                  </td>
                </tr>
              `,
            )
            .join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderSettingsGroups(effectiveValues) {
  return groupSettings(effectiveValues)
    .map(
      ([section, rows]) => `
        <section class="content-card">
          <div class="section-heading compact">
            <div>
              <p class="section-kicker">Settings</p>
              <h3>${escapeHtml(section)}</h3>
            </div>
          </div>
          <div class="table-shell">
            <table>
              <thead>
                <tr>
                  <th>Key</th>
                  <th>Value</th>
                  <th>Type</th>
                  <th>Editable</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                ${rows
                  .map(
                    (row) => `
                      <tr>
                        <td>${escapeHtml(row.key)}</td>
                        <td><code>${escapeHtml(formatJson(row.value))}</code></td>
                        <td>${escapeHtml(row.type || "unknown")}</td>
                        <td><span class="badge ${row.overridable ? "badge-success" : "badge-muted"}">${row.overridable ? "Yes" : "Read only"}</span></td>
                        <td>
                          ${
                            row.overridable
                              ? `<button type="button" class="ghost" data-load-setting-key="${escapeHtml(row.key)}" data-load-setting-value="${escapeHtml(formatJson(row.value))}">Edit</button>`
                              : ""
                          }
                        </td>
                      </tr>
                    `,
                  )
                  .join("")}
              </tbody>
            </table>
          </div>
        </section>
      `,
    )
    .join("");
}

function renderUninstall(uninstall) {
  const commands = (uninstall?.commands || [])
    .map(
      (command) => `
        <div class="command-card">
          <strong>${escapeHtml(command.label)}</strong>
          <code>${escapeHtml(command.command)}</code>
        </div>
      `,
    )
    .join("");
  return `
    <section class="content-card">
      <div class="section-heading compact">
        <div>
          <p class="section-kicker">Uninstall</p>
          <h3>Host-side removal</h3>
        </div>
      </div>
      <p class="help-copy">${escapeHtml(uninstall?.reason || "Uninstall details unavailable.")}</p>
      ${commands || '<p class="empty-state">No uninstall commands are available.</p>'}
    </section>
  `;
}

function renderGatewayAuth(gatewayAuth) {
  const auth = gatewayAuth || {};
  return `
    <section class="content-card">
      <div class="section-heading compact">
        <div>
          <p class="section-kicker">Security</p>
          <h3>Gateway bearer auth</h3>
        </div>
      </div>
      <div class="metric-grid metric-grid-tight">
        <article class="metric-card tone-${auth.enabled ? "success" : "warn"}">
          <p class="metric-label">Effective state</p>
          <p class="metric-value">${escapeHtml(auth.enabled ? "Enabled" : "Disabled")}</p>
          <p class="metric-meta">${escapeHtml(auth.source || "disabled")}</p>
        </article>
        <article class="metric-card tone-neutral">
          <p class="metric-label">Mode</p>
          <p class="metric-value">${escapeHtml(auth.mode || "inherit")}</p>
          <p class="metric-meta">${auth.env_configured ? "Environment key present" : "No env key detected"}</p>
        </article>
      </div>
      <form id="gatewayAuthForm" class="stacked-form">
        <label for="gatewayAuthKey">Set managed gateway auth key</label>
        <input id="gatewayAuthKey" type="password" placeholder="Enter a new bearer token">
        <div class="form-actions">
          <button type="submit">Set managed key</button>
          <button id="disableGatewayAuthButton" type="button" class="ghost">Disable auth</button>
          <button id="inheritGatewayAuthButton" type="button" class="ghost">Use env/default</button>
        </div>
      </form>
      <p class="help-copy">Changing gateway auth affects the token required for all subsequent admin and API requests. If you set a new key, the UI should keep using it immediately.</p>
    </section>
  `;
}

export function renderSettingsPage(configPayload, uninstall, defaultPage) {
  const effectiveValues = configPayload?.effective_values || [];
  const overrides = configPayload?.overrides || [];
  const gatewayAuth = configPayload?.gateway_auth || {};

  return `
    <section class="page-panel">
      <div class="section-heading">
        <div>
          <p class="section-kicker">Settings</p>
          <h2>Effective config, overrides, and console preferences</h2>
        </div>
      </div>

      <div class="two-column-grid">
        <section class="content-card">
          <div class="section-heading compact">
            <div>
              <p class="section-kicker">Console</p>
              <h3>Default landing page</h3>
            </div>
          </div>
          <div class="stacked-form">
            <label for="defaultPageSelect">Open the admin UI on</label>
            <select id="defaultPageSelect">
              <option value="health" ${defaultPage === "health" ? "selected" : ""}>Health</option>
              <option value="vault" ${defaultPage === "vault" ? "selected" : ""}>Vault</option>
              <option value="models" ${defaultPage === "models" ? "selected" : ""}>Models</option>
              <option value="settings" ${defaultPage === "settings" ? "selected" : ""}>Settings</option>
              <option value="logs" ${defaultPage === "logs" ? "selected" : ""}>Logs</option>
            </select>
            <button id="saveDefaultPageButton" type="button">Save homepage</button>
          </div>
        </section>

        <section class="content-card">
          <div class="section-heading compact">
            <div>
              <p class="section-kicker">Overrides</p>
              <h3>Edit runtime override values</h3>
            </div>
          </div>
          <form id="settingsForm" class="stacked-form">
            <label for="settingsKey">Override key</label>
            <input id="settingsKey" type="text" placeholder="Select a setting row or type a valid override key">
            <label for="settingsValue">Override value</label>
            <textarea id="settingsValue" rows="4" placeholder='JSON or raw string value'></textarea>
            <div class="form-actions">
              <button type="submit">Save override</button>
              <button id="deleteSettingButton" type="button" class="ghost">Delete override</button>
            </div>
          </form>
        </section>
      </div>

      <section class="content-card">
        <div class="section-heading compact">
          <div>
            <p class="section-kicker">Active Overrides</p>
            <h3>Current runtime override map</h3>
          </div>
        </div>
        ${renderOverrideRows(overrides)}
      </section>

      ${renderGatewayAuth(gatewayAuth)}

      ${renderSettingsGroups(effectiveValues)}

      ${renderUninstall(uninstall)}
    </section>
  `;
}
