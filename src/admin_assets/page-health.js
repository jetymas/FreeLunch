function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function formatNumber(value, digits = 0) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return "n/a";
  }
  return numeric.toFixed(digits);
}

function percentage(value, total) {
  const safeTotal = Number(total);
  if (!Number.isFinite(safeTotal) || safeTotal <= 0) {
    return 0;
  }
  const safeValue = Number(value);
  if (!Number.isFinite(safeValue) || safeValue <= 0) {
    return 0;
  }
  return Math.max(0, Math.min(100, Math.round((safeValue / safeTotal) * 100)));
}

function renderMetricCard(label, value, tone = "neutral", meta = "") {
  return `
    <article class="metric-card tone-${tone}">
      <p class="metric-label">${escapeHtml(label)}</p>
      <p class="metric-value">${escapeHtml(value)}</p>
      ${meta ? `<p class="metric-meta">${escapeHtml(meta)}</p>` : ""}
    </article>
  `;
}

function renderProviderCards(providers) {
  if (!providers.length) {
    return '<p class="empty-state">No providers discovered yet.</p>';
  }
  return providers
    .map((provider) => {
      const total = Number(provider.total || 0);
      const routable = Number(provider.routable || 0);
      const fill = percentage(routable, total);
      return `
        <article class="stack-card">
          <div class="stack-row">
            <strong>${escapeHtml(provider.provider_id)}</strong>
            <span class="badge ${routable > 0 ? "badge-success" : "badge-muted"}">${routable}/${total} routable</span>
          </div>
          <div class="progress-track">
            <span class="progress-fill" style="width:${fill}%"></span>
          </div>
        </article>
      `;
    })
    .join("");
}

function renderBudgetCards(budgets) {
  if (!budgets.length) {
    return '<p class="empty-state">No probe budget data is available.</p>';
  }
  return budgets
    .map((budget) => {
      const limit = Number(budget.limit || 0);
      const used = Number(budget.used || 0);
      const fill = percentage(used, limit || 1);
      return `
        <article class="stack-card">
          <div class="stack-row">
            <strong>${escapeHtml(budget.provider_id)}</strong>
            <span class="badge ${used < limit ? "badge-success" : "badge-warning"}">${used}/${limit} used</span>
          </div>
          <div class="progress-track">
            <span class="progress-fill tone-warn" style="width:${fill}%"></span>
          </div>
          <p class="card-copy">${escapeHtml(String(budget.remaining ?? 0))} requests remaining today.</p>
        </article>
      `;
    })
    .join("");
}

function renderSchedulerRows(jobs) {
  const entries = Object.entries(jobs || {});
  if (!entries.length) {
    return '<p class="empty-state">Scheduler jobs are not reporting yet.</p>';
  }
  return `
    <div class="table-shell">
      <table>
        <thead>
          <tr>
            <th>Job</th>
            <th>Runs</th>
            <th>Last success</th>
            <th>Last failure</th>
            <th>Next run</th>
          </tr>
        </thead>
        <tbody>
          ${entries
            .map(
              ([jobName, jobState]) => `
                <tr>
                  <td>${escapeHtml(jobName)}</td>
                  <td>${escapeHtml(String(jobState.run_count ?? 0))}</td>
                  <td>${escapeHtml(jobState.last_success_at || "n/a")}</td>
                  <td>${escapeHtml(jobState.last_failure_at || "n/a")}</td>
                  <td>${escapeHtml(jobState.next_run_at || "n/a")}</td>
                </tr>
              `,
            )
            .join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderProbeCandidates(candidates) {
  if (!candidates.length) {
    return '<p class="empty-state">No probe candidates are queued right now.</p>';
  }
  return candidates
    .map(
      (candidate) => `
        <article class="stack-card">
          <div class="stack-row">
            <strong>${escapeHtml(candidate.model_id)}</strong>
            <span class="badge badge-muted">${escapeHtml(candidate.reason || "unknown")}</span>
          </div>
          <p class="card-copy">Provider: ${escapeHtml(candidate.provider_id || "n/a")}</p>
          <p class="card-copy">Last probe: ${escapeHtml(candidate.last_probe_at || "never")}</p>
        </article>
      `,
    )
    .join("");
}

function renderReviewFlags(reviewFlags) {
  const tokenizerFlags = reviewFlags?.tokenizer_families || [];
  if (!tokenizerFlags.length) {
    return '<p class="empty-state">No token-estimation review flags are raised.</p>';
  }
  return tokenizerFlags
    .map(
      (flag) => `
        <article class="stack-card">
          <div class="stack-row">
            <strong>${escapeHtml(flag.tokenizer_family || "unknown")}</strong>
            <span class="badge badge-warning">${escapeHtml(String(flag.context_exceeded_failures || 0))} context failures</span>
          </div>
          <p class="card-copy">Mismatch rate: ${escapeHtml(formatNumber(flag.mismatch_rate_pct, 1))}%</p>
          <p class="card-copy">Recoveries: ${escapeHtml(String(flag.context_failover_recoveries || 0))}</p>
        </article>
      `,
    )
    .join("");
}

function renderRecentErrors(errors) {
  if (!errors.length) {
    return '<p class="empty-state">No recent model errors.</p>';
  }
  return `
    <div class="table-shell">
      <table>
        <thead>
          <tr>
            <th>Model</th>
            <th>Consecutive failures</th>
            <th>Last error</th>
          </tr>
        </thead>
        <tbody>
          ${errors
            .map(
              (error) => `
                <tr class="row-alert">
                  <td>${escapeHtml(error.model_id)}</td>
                  <td>${escapeHtml(String(error.consecutive_failures || 0))}</td>
                  <td>${escapeHtml(error.last_error || "n/a")}</td>
                </tr>
              `,
            )
            .join("")}
        </tbody>
      </table>
    </div>
  `;
}

export function renderHealthPage(health) {
  if (!health) {
    return '<section class="page-panel"><p class="empty-state">Health data is unavailable.</p></section>';
  }

  const models = health.models || {};
  const runtimeLogging = health.runtime_logging || {};
  const probeState = health.probe_state || {};
  const probeBuckets = probeState.buckets || {};
  const probePolicy = probeState.policy || {};
  const recentProbeActivity = health.recent_probe_activity || {};
  const bootstrap = health.bootstrap || {};

  return `
    <section class="page-panel">
      <div class="section-heading">
        <div>
          <p class="section-kicker">Health Overview</p>
          <h2>Gateway status and operational signals</h2>
        </div>
      </div>

      <div class="metric-grid">
        ${renderMetricCard("Gateway", bootstrap.ready ? "Ready" : "Degraded", bootstrap.ready ? "success" : "warn", `Started ${bootstrap.started_at || "n/a"}`)}
        ${renderMetricCard("Routable models", String(models.routable ?? 0), "neutral", `${models.healthy ?? 0} healthy / ${models.total ?? 0} total`)}
        ${renderMetricCard("Writer queue", String(health.db?.writer_queue_depth ?? 0), "neutral", "SQLite write backlog")}
        ${renderMetricCard("Vault", health.secret_management?.unlocked ? "Unlocked" : health.secret_management?.configured ? "Locked" : "Not configured", health.secret_management?.unlocked ? "success" : "warn", `${health.secret_management?.stored_secret_count ?? 0} stored secrets`)}
        ${renderMetricCard("Runtime logging", runtimeLogging.enabled ? "Enabled" : "Disabled", runtimeLogging.enabled ? "success" : "warn", `${runtimeLogging.verbosity || "n/a"} verbosity`)}
        ${renderMetricCard("Probe pool", String(probePolicy.max_probes_per_run ?? 0), "neutral", `${probeBuckets.never_probed ?? 0} never-probed / ${probeBuckets.stale ?? 0} stale`)}
      </div>

      <div class="two-column-grid">
        <section class="content-card">
          <div class="section-heading compact">
            <div>
              <p class="section-kicker">Providers</p>
              <h3>Routability by provider</h3>
            </div>
          </div>
          <div class="stack-list">${renderProviderCards(models.providers || [])}</div>
        </section>

        <section class="content-card">
          <div class="section-heading compact">
            <div>
              <p class="section-kicker">Budgets</p>
              <h3>Probe budget usage</h3>
            </div>
          </div>
          <div class="stack-list">${renderBudgetCards(health.probe_budgets || [])}</div>
        </section>
      </div>

      <div class="two-column-grid">
        <section class="content-card">
          <div class="section-heading compact">
            <div>
              <p class="section-kicker">Scheduler</p>
              <h3>Recurring jobs</h3>
            </div>
          </div>
          ${renderSchedulerRows(health.scheduler?.jobs || {})}
        </section>

        <section class="content-card">
          <div class="section-heading compact">
            <div>
              <p class="section-kicker">Probe State</p>
              <h3>Next likely probe targets</h3>
            </div>
          </div>
          <div class="stack-list">${renderProbeCandidates(probeState.next_candidates || [])}</div>
        </section>
      </div>

      <div class="two-column-grid">
        <section class="content-card">
          <div class="section-heading compact">
            <div>
              <p class="section-kicker">Activity</p>
              <h3>Recent probe activity</h3>
            </div>
          </div>
          <div class="metric-grid metric-grid-tight">
            ${renderMetricCard("Total requests", String(recentProbeActivity.total_requests ?? 0))}
            ${renderMetricCard("Failures", String(recentProbeActivity.failures ?? 0), recentProbeActivity.failures ? "warn" : "success")}
            ${renderMetricCard("Last request", recentProbeActivity.last_request_at || "n/a")}
            ${renderMetricCard("Review window", `${health.token_estimation_review?.review_window_days ?? 0} days`)}
          </div>
        </section>

        <section class="content-card">
          <div class="section-heading compact">
            <div>
              <p class="section-kicker">Token Estimation</p>
              <h3>Review flags</h3>
            </div>
          </div>
          <div class="stack-list">${renderReviewFlags(health.token_estimation_review?.review_flags || {})}</div>
        </section>
      </div>

      <section class="content-card">
        <div class="section-heading compact">
          <div>
            <p class="section-kicker">Failures</p>
            <h3>Recent model errors</h3>
          </div>
        </div>
        ${renderRecentErrors(health.recent_model_errors || [])}
      </section>
    </section>
  `;
}
