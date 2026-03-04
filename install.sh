#!/usr/bin/env sh
set -eu

REPO="${FREELUNCH_REPO:-jetymas/FreeLunch}"
IMAGE="${FREELUNCH_IMAGE:-ghcr.io/${REPO}:latest}"
INSTALL_DIR="${FREELUNCH_INSTALL_DIR:-${HOME}/.freelunch}"
DEFAULT_PORT="${FREELUNCH_PORT:-8000}"

info() {
    printf '[INFO] %s\n' "$1"
}

warn() {
    printf '[WARN] %s\n' "$1"
}

fail() {
    printf '[ERROR] %s\n' "$1" >&2
    exit 1
}

prompt() {
    question="$1"
    default_value="${2:-}"
    env_name="${3:-}"
    env_value=""
    if [ -n "$env_name" ]; then
        eval "env_is_set=\${${env_name}+x}"
        eval "env_value=\${$env_name-}"
        if [ "${env_is_set:-}" = "x" ]; then
            printf '%s' "$env_value"
            return
        fi
    fi
    if [ -n "$default_value" ]; then
        printf '%s [%s]: ' "$question" "$default_value"
    else
        printf '%s: ' "$question"
    fi
    read -r value
    if [ -z "$value" ]; then
        value="$default_value"
    fi
    printf '%s' "$value"
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "Required command not found: $1"
}

require_docker() {
    require_command docker
    docker info >/dev/null 2>&1 || fail "Docker is installed but not running."
    docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is required."
}

setup_install_dir() {
    UPGRADING=0
    if [ -f "${INSTALL_DIR}/.env" ]; then
        answer="$(prompt "Existing installation found at ${INSTALL_DIR}. Upgrade and preserve config?" "yes" "FREELUNCH_AUTO_CONFIRM")"
        case "$answer" in
            y|Y|yes|YES)
                UPGRADING=1
                ;;
            *)
                fail "Installation cancelled."
                ;;
        esac
    else
        mkdir -p "${INSTALL_DIR}/data"
    fi
}

write_env_file() {
    if [ "${UPGRADING}" -eq 1 ]; then
        if ! grep -q '^FREELUNCH_IMAGE=' "${INSTALL_DIR}/.env"; then
            printf '\nFREELUNCH_IMAGE=%s\n' "$IMAGE" >> "${INSTALL_DIR}/.env"
        fi
        return
    fi

    info "Collecting configuration."
    openrouter_key="$(prompt "OpenRouter API key" "" "OPENROUTER_API_KEY")"
    [ -n "$openrouter_key" ] || fail "OPENROUTER_API_KEY is required."
    gateway_key="$(prompt "Gateway API key (leave blank to disable auth)" "" "GATEWAY_API_KEY")"
    gateway_port="$(prompt "Gateway port" "$DEFAULT_PORT" "FREELUNCH_PORT")"

    cat > "${INSTALL_DIR}/.env" <<EOF
OPENROUTER_API_KEY=${openrouter_key}
GATEWAY_API_KEY=${gateway_key}
DATABASE_URL=data/freelunch.db
APP_ENV=dev
FREELUNCH_PORT=${gateway_port}
FREELUNCH_IMAGE=${IMAGE}
EOF
}

write_config_file() {
    if [ -f "${INSTALL_DIR}/config.yaml" ]; then
        return
    fi

    cat > "${INSTALL_DIR}/config.yaml" <<'EOF'
app:
  env: dev

providers:
  openrouter:
    api_base: "https://openrouter.ai/api/v1"
    active_probe_enabled: true

discovery:
  interval_minutes: 30
  request_timeout_seconds: 15
  leaderboard:
    chatbot_arena:
      enabled: true
      cache_hours: 24
    open_llm:
      enabled: true
      cache_hours: 24

routing:
  default_model: auto
  max_attempts: 3

ranking:
  interval_minutes: 15
  fallback_model: "openrouter/openrouter/free"
  weights:
    benchmark_score: 0.30
    real_world_usage: 0.15
    latency: 0.20
    availability: 0.20
    context_window: 0.10
    feature_support: 0.05

health:
  probe_interval_minutes: 180
  probe_timeout_seconds: 15
  probe_concurrency: 1
  max_probes_per_run: 1
  stale_after_minutes: 360
  top_n_stale_probe: 3
  startup_probe_limit: 2
  consecutive_failures_threshold: 3
  cooldown_minutes: 30
  max_backoff_exponent: 4
  probe_max_tokens: 1
  daily_request_budget_by_provider:
    openrouter: 5

logging:
  request_log_retention_days: 30
EOF
}

write_compose_file() {
    cat > "${INSTALL_DIR}/docker-compose.yml" <<'EOF'
services:
  freelunch:
    image: ${FREELUNCH_IMAGE:-ghcr.io/jetymas/FreeLunch:latest}
    restart: unless-stopped
    ports:
      - "${FREELUNCH_PORT:-8000}:8000"
    env_file:
      - .env
    volumes:
      - ./data:/app/data
      - ./config.yaml:/app/config.yaml:ro
EOF
}

run_install() {
    info "Pulling ${IMAGE}"
    docker pull "${IMAGE}"
    info "Starting FreeLunch"
    docker compose --project-directory "${INSTALL_DIR}" -f "${INSTALL_DIR}/docker-compose.yml" up -d
}

print_summary() {
    port="$(awk -F= '/^FREELUNCH_PORT=/{print $2}' "${INSTALL_DIR}/.env" | tail -n 1)"
    if [ -z "$port" ]; then
        port="$DEFAULT_PORT"
    fi

    printf '\nFreeLunch installed.\n'
    printf 'Gateway URL: http://localhost:%s/v1\n' "$port"
    printf 'Health check: http://localhost:%s/healthz\n' "$port"
    printf 'Install dir: %s\n' "$INSTALL_DIR"
    printf 'Logs: docker compose --project-directory "%s" -f "%s/docker-compose.yml" logs -f\n' "$INSTALL_DIR" "$INSTALL_DIR"
    printf 'Stop: docker compose --project-directory "%s" -f "%s/docker-compose.yml" down\n' "$INSTALL_DIR" "$INSTALL_DIR"
    printf 'Uninstall: sh uninstall.sh\n'
}

info "Installing FreeLunch from ${IMAGE}"
require_docker
setup_install_dir
write_env_file
write_config_file
write_compose_file
run_install
print_summary
