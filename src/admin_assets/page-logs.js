function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function metric(label, value, tone = "neutral", meta = "") {
  return `
    <article class="metric-card tone-${tone}">
      <p class="metric-label">${escapeHtml(label)}</p>
      <p class="metric-value">${escapeHtml(value)}</p>
      ${meta ? `<p class="metric-meta">${escapeHtml(meta)}</p>` : ""}
    </article>
  `;
}

export function renderLogsPage(logPayload, filters) {
  const logs = logPayload?.logs || [];
  const successCount = logs.filter((entry) => entry.success).length;
  const failureCount = logs.filter((entry) => !entry.success).length;

  return `
    <section class="page-panel">
      <div class="section-heading">
        <div>
          <p class="section-kicker">Logs</p>
          <h2>Recent request telemetry and failure detail</h2>
        </div>
      </div>

      <div class="metric-grid">
        ${metric("Entries loaded", String(logs.length), "neutral", `limit ${logPayload?.limit ?? 0}`)}
        ${metric("Successes", String(successCount), successCount ? "success" : "neutral")}
        ${metric("Failures", String(failureCount), failureCount ? "warn" : "success")}
      </div>

      <section class="content-card">
        <form id="logsForm" class="toolbar-form">
          <input id="logsLimit" type="number" min="1" max="500" value="${escapeHtml(String(filters.limit || 50))}">
          <select id="logsSuccessOnly">
            <option value="" ${filters.successOnly === "" ? "selected" : ""}>All outcomes</option>
            <option value="true" ${filters.successOnly === "true" ? "selected" : ""}>Success only</option>
            <option value="false" ${filters.successOnly === "false" ? "selected" : ""}>Failures only</option>
          </select>
          <input id="logsProviderId" type="text" value="${escapeHtml(filters.providerId || "")}" placeholder="Provider filter">
          <input id="logsRequestSource" type="text" value="${escapeHtml(filters.requestSource || "")}" placeholder="Source filter">
          <input id="logsModelId" type="text" value="${escapeHtml(filters.modelId || "")}" placeholder="Model id filter">
          <button type="submit">Load logs</button>
        </form>
      </section>

      <section class="content-card">
        ${
          logs.length
            ? `
              <div class="log-list">
                ${logs
                  .map(
                    (entry) => `
                      <article class="log-entry ${entry.success ? "log-success" : "log-failure"}">
                        <div class="log-head">
                          <div>
                            <strong>${escapeHtml(entry.selected_model_id)}</strong>
                            <p class="log-meta">${escapeHtml(entry.timestamp)} | ${escapeHtml(entry.provider_id)} | ${escapeHtml(entry.request_source || "client")}</p>
                          </div>
                          <span class="badge ${entry.success ? "badge-success" : "badge-warning"}">${entry.success ? "Success" : "Failure"}</span>
                        </div>
                        <div class="chip-row">
                          <span class="chip chip-on">attempt ${escapeHtml(String(entry.attempt_index ?? 0))}</span>
                          ${entry.was_streaming ? '<span class="chip chip-on">stream</span>' : '<span class="chip chip-off">non-stream</span>'}
                          ${entry.had_tools ? '<span class="chip chip-on">tools</span>' : ""}
                          ${entry.had_vision ? '<span class="chip chip-on">vision</span>' : ""}
                          ${entry.was_fallback ? '<span class="chip chip-off">fallback</span>' : ""}
                        </div>
                        <div class="detail-grid">
                          <div><span class="detail-label">Provider model</span><span>${escapeHtml(entry.selected_provider_model_id || "n/a")}</span></div>
                          <div><span class="detail-label">Tokenizer</span><span>${escapeHtml(entry.selected_tokenizer_family || "n/a")}</span></div>
                          <div><span class="detail-label">Latency</span><span>${escapeHtml(String(entry.latency_ms ?? "n/a"))} ms</span></div>
                          <div><span class="detail-label">TTFB</span><span>${escapeHtml(String(entry.ttfb_ms ?? "n/a"))} ms</span></div>
                          <div><span class="detail-label">Prompt est/actual</span><span>${escapeHtml(String(entry.estimated_prompt_tokens ?? "n/a"))} / ${escapeHtml(String(entry.prompt_tokens ?? "n/a"))}</span></div>
                          <div><span class="detail-label">Completion / total</span><span>${escapeHtml(String(entry.completion_tokens ?? "n/a"))} / ${escapeHtml(String(entry.total_tokens ?? "n/a"))}</span></div>
                        </div>
                        ${
                          entry.gateway_error_category || entry.error_message
                            ? `<p class="log-error">${escapeHtml(entry.gateway_error_category || "error")} ${entry.error_code ? `(${escapeHtml(entry.error_code)})` : ""}: ${escapeHtml(entry.error_message || "n/a")}</p>`
                            : ""
                        }
                      </article>
                    `,
                  )
                  .join("")}
              </div>
            `
            : '<p class="empty-state">No logs match the current filters.</p>'
        }
      </section>
    </section>
  `;
}
