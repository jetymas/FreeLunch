function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function formatNumber(value, digits = 1) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return "n/a";
  }
  return numeric.toFixed(digits);
}

function capabilityChip(label, enabled) {
  return `<span class="chip ${enabled ? "chip-on" : "chip-off"}">${escapeHtml(label)}</span>`;
}

function scoreBar(score) {
  const numeric = Number(score);
  const width = Number.isFinite(numeric) ? Math.max(0, Math.min(100, numeric)) : 0;
  return `
    <div class="score-shell">
      <span class="score-value">${escapeHtml(formatNumber(score, 2))}</span>
      <div class="progress-track">
        <span class="progress-fill" style="width:${width}%"></span>
      </div>
    </div>
  `;
}

function renderSummary(models) {
  const active = models.filter((model) => model.is_active).length;
  const healthy = models.filter((model) => model.is_healthy).length;
  const toolCapable = models.filter((model) => model.supports_tools).length;
  return `
    <div class="metric-grid">
      <article class="metric-card tone-neutral">
        <p class="metric-label">Models</p>
        <p class="metric-value">${escapeHtml(String(models.length))}</p>
        <p class="metric-meta">${active} active / ${healthy} healthy</p>
      </article>
      <article class="metric-card tone-success">
        <p class="metric-label">Tool capable</p>
        <p class="metric-value">${escapeHtml(String(toolCapable))}</p>
        <p class="metric-meta">Can serve tool calls</p>
      </article>
    </div>
  `;
}

export function renderModelsPage(modelsPayload, filters) {
  const models = modelsPayload?.models || [];
  const search = filters?.search || "";
  const provider = filters?.provider || "all";
  const showInactive = Boolean(filters?.showInactive);
  const providerOptions = Array.from(new Set(models.map((model) => model.provider_id))).sort();
  const filtered = models.filter((model) => {
    if (!showInactive && !model.is_active) {
      return false;
    }
    if (provider !== "all" && model.provider_id !== provider) {
      return false;
    }
    if (!search) {
      return true;
    }
    const haystack = `${model.id} ${model.name} ${model.provider_model_id}`.toLowerCase();
    return haystack.includes(search.toLowerCase());
  });

  return `
    <section class="page-panel">
      <div class="section-heading">
        <div>
          <p class="section-kicker">Models</p>
          <h2>Rankings, capabilities, and activation state</h2>
        </div>
      </div>

      ${renderSummary(models)}

      <section class="content-card">
        <form id="modelsFilterForm" class="toolbar-form">
          <input id="modelsSearchInput" type="search" value="${escapeHtml(search)}" placeholder="Search model id or provider model">
          <select id="modelsProviderFilter">
            <option value="all">All providers</option>
            ${providerOptions
              .map(
                (option) =>
                  `<option value="${escapeHtml(option)}" ${provider === option ? "selected" : ""}>${escapeHtml(option)}</option>`,
              )
              .join("")}
          </select>
          <label class="toggle-chip">
            <input id="modelsShowInactive" type="checkbox" ${showInactive ? "checked" : ""}>
            <span>Show inactive</span>
          </label>
          <button type="submit">Apply filters</button>
        </form>
      </section>

      <section class="content-card">
        ${
          filtered.length
            ? `
              <div class="table-shell">
                <table>
                  <thead>
                    <tr>
                      <th>Model</th>
                      <th>Rank</th>
                      <th>Status</th>
                      <th>Capabilities</th>
                      <th>Context</th>
                      <th>Latency</th>
                      <th>Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    ${filtered
                      .map(
                        (model) => `
                          <tr>
                            <td>
                              <strong>${escapeHtml(model.id)}</strong>
                              <div class="table-subtext">${escapeHtml(model.provider_model_id)}</div>
                            </td>
                            <td>${scoreBar(model.composite_score)}</td>
                            <td>
                              <div class="stack-list compact-gap">
                                <span class="badge ${model.is_active ? "badge-success" : "badge-muted"}">${model.is_active ? "Active" : "Inactive"}</span>
                                <span class="badge ${model.is_healthy ? "badge-success" : "badge-warning"}">${model.is_healthy ? "Healthy" : "Unhealthy"}</span>
                              </div>
                            </td>
                            <td>
                              <div class="chip-row">
                                ${capabilityChip("tools", model.supports_tools)}
                                ${capabilityChip("stream", model.supports_streaming)}
                                ${capabilityChip("vision", model.supports_vision)}
                                ${capabilityChip("json", model.supports_structured_output)}
                              </div>
                            </td>
                            <td>
                              <div>${escapeHtml(String(model.context_window ?? "n/a"))}</div>
                              <div class="table-subtext">${escapeHtml(model.tokenizer_family || "unknown tokenizer")}</div>
                            </td>
                            <td>
                              <div>${escapeHtml(formatNumber(model.avg_latency_ms))} ms</div>
                              <div class="table-subtext">TTFB ${escapeHtml(formatNumber(model.avg_ttfb_ms))} ms</div>
                            </td>
                            <td>
                              <button type="button" data-model-action="${model.is_active ? "disable" : "enable"}" data-model-id="${escapeHtml(model.id)}">
                                ${model.is_active ? "Disable" : "Enable"}
                              </button>
                            </td>
                          </tr>
                        `,
                      )
                      .join("")}
                  </tbody>
                </table>
              </div>
            `
            : '<p class="empty-state">No models match the current filters.</p>'
        }
      </section>
    </section>
  `;
}
