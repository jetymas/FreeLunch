function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function renderVaultSummary(secretManagement) {
  const configured = Boolean(secretManagement?.configured);
  const unlocked = Boolean(secretManagement?.unlocked);
  return `
    <div class="metric-grid">
      <article class="metric-card tone-${configured ? "success" : "warn"}">
        <p class="metric-label">Vault</p>
        <p class="metric-value">${configured ? "Configured" : "Not configured"}</p>
        <p class="metric-meta">Runtime password vault</p>
      </article>
      <article class="metric-card tone-${unlocked ? "success" : "warn"}">
        <p class="metric-label">State</p>
        <p class="metric-value">${unlocked ? "Unlocked" : "Locked"}</p>
        <p class="metric-meta">${escapeHtml(String(secretManagement?.loaded_secret_count ?? 0))} secrets loaded now</p>
      </article>
      <article class="metric-card tone-neutral">
        <p class="metric-label">Stored secrets</p>
        <p class="metric-value">${escapeHtml(String(secretManagement?.stored_secret_count ?? 0))}</p>
        <p class="metric-meta">Encrypted in SQLite</p>
      </article>
    </div>
  `;
}

function renderSecretRows(secrets) {
  if (!secrets.length) {
    return '<p class="empty-state">No secret slots are available.</p>';
  }
  return `
    <div class="table-shell">
      <table>
        <thead>
          <tr>
            <th>Target</th>
            <th>Source</th>
            <th>Env var</th>
            <th>Updated</th>
          </tr>
        </thead>
        <tbody>
          ${secrets
            .map(
              (secret) => `
                <tr>
                  <td>
                    <strong>${escapeHtml(secret.label)}</strong>
                    <div class="table-subtext">${escapeHtml(secret.key)}</div>
                  </td>
                  <td><span class="badge ${secret.source === "managed" ? "badge-success" : "badge-muted"}">${escapeHtml(secret.source)}</span></td>
                  <td>${escapeHtml(secret.env_var || "n/a")}</td>
                  <td>${escapeHtml(secret.updated_at || "n/a")}</td>
                </tr>
              `,
            )
            .join("")}
        </tbody>
      </table>
    </div>
  `;
}

export function renderVaultPage(secretManagement, secrets) {
  const configured = Boolean(secretManagement?.configured);
  const unlocked = Boolean(secretManagement?.unlocked);
  const options = (secrets || [])
    .map(
      (secret) =>
        `<option value="${escapeHtml(secret.key)}">${escapeHtml(secret.label)} (${escapeHtml(secret.key)})</option>`,
    )
    .join("");

  return `
    <section class="page-panel">
      <div class="section-heading">
        <div>
          <p class="section-kicker">Vault</p>
          <h2>Runtime password vault and provider secrets</h2>
        </div>
      </div>

      ${renderVaultSummary(secretManagement)}

      <div class="two-column-grid">
        <section class="content-card">
          <div class="section-heading compact">
            <div>
              <p class="section-kicker">Vault Actions</p>
              <h3>Setup and lock state</h3>
            </div>
          </div>
          ${
            !configured
              ? `
                <form id="vaultSetupForm" class="stacked-form">
                  <label for="vaultSetupPassword">Create vault password</label>
                  <input id="vaultSetupPassword" type="password" placeholder="Choose a vault password">
                  <button type="submit">Create vault</button>
                </form>
                <p class="help-copy">The vault password is only kept in memory for the running process. Restarting FreeLunch locks it again.</p>
              `
              : unlocked
                ? `
                  <div class="stack-list">
                    <p class="help-copy">The vault is currently unlocked for this running process.</p>
                    <button id="lockVaultButton" type="button" class="ghost">Lock vault</button>
                  </div>
                `
                : `
                  <form id="vaultUnlockForm" class="stacked-form">
                    <label for="vaultUnlockPassword">Unlock vault</label>
                    <input id="vaultUnlockPassword" type="password" placeholder="Enter the vault password">
                    <button type="submit">Unlock vault</button>
                  </form>
                  <p class="help-copy">Unlock before saving or deleting provider secrets.</p>
                `
          }
        </section>

        <section class="content-card">
          <div class="section-heading compact">
            <div>
              <p class="section-kicker">Secret Editor</p>
              <h3>Provider credential slots</h3>
            </div>
          </div>
          <form id="secretForm" class="stacked-form">
            <label for="secretKey">Secret target</label>
            <select id="secretKey" ${unlocked ? "" : "disabled"}>${options}</select>
            <label for="secretValue">Secret value</label>
            <input id="secretValue" type="password" placeholder="Enter the secret value" ${unlocked ? "" : "disabled"}>
            <div class="form-actions">
              <button type="submit" ${unlocked ? "" : "disabled"}>Save secret</button>
              <button id="deleteSecretButton" type="button" class="ghost" ${unlocked ? "" : "disabled"}>Delete secret</button>
            </div>
          </form>
          <p class="help-copy">
            ${
              !configured
                ? "Create the vault before saving provider secrets."
                : unlocked
                  ? "Secrets are encrypted in SQLite and applied immediately after save."
                  : "Unlock the vault to edit provider secrets."
            }
          </p>
        </section>
      </div>

      <section class="content-card">
        <div class="section-heading compact">
          <div>
            <p class="section-kicker">Secret Slots</p>
            <h3>Current provider credential targets</h3>
          </div>
        </div>
        ${renderSecretRows(secrets || [])}
      </section>
    </section>
  `;
}
