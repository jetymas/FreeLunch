#!/usr/bin/env sh
set -eu

INSTALL_DIR="${FREELUNCH_INSTALL_DIR:-${HOME}/.freelunch}"

printf 'This will stop and remove the FreeLunch container, config, and local data in %s.\n' "$INSTALL_DIR"
if [ "${FREELUNCH_AUTO_CONFIRM:-}" = "yes" ]; then
    confirm="yes"
else
    printf 'Type "yes" to continue: '
    read -r confirm
fi

if [ "$confirm" != "yes" ]; then
    printf 'Uninstall cancelled.\n'
    exit 0
fi

if [ -f "${INSTALL_DIR}/docker-compose.yml" ]; then
    docker compose --project-directory "${INSTALL_DIR}" -f "${INSTALL_DIR}/docker-compose.yml" stop freelunch 2>/dev/null || true
    if [ -f "${INSTALL_DIR}/.env" ] \
        && grep -q '^FREELUNCH_ALLOW_DATA_CHOWN=1$' "${INSTALL_DIR}/.env" \
        && grep -q '^  freelunch-data-migration:' "${INSTALL_DIR}/docker-compose.yml"; then
        if ! docker compose --project-directory "${INSTALL_DIR}" -f "${INSTALL_DIR}/docker-compose.yml" \
            run --rm --no-deps \
            -e "FREELUNCH_RESTORE_HOST_UID=$(id -u)" \
            -e "FREELUNCH_RESTORE_HOST_GID=$(id -g)" \
            freelunch-data-migration /usr/local/bin/migrate-data-ownership --restore-host; then
            printf 'Could not restore data ownership; install directory and data were preserved.\n' >&2
            exit 1
        fi
    fi
    docker compose --project-directory "${INSTALL_DIR}" -f "${INSTALL_DIR}/docker-compose.yml" down --volumes 2>/dev/null || true
fi

rm -rf "${INSTALL_DIR}"

printf 'FreeLunch has been uninstalled.\n'
printf 'The Docker image is still cached locally. To remove it:\n'
printf '  docker rmi ghcr.io/jetymas/freelunch:latest\n'
