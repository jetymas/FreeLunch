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
    docker compose --project-directory "${INSTALL_DIR}" -f "${INSTALL_DIR}/docker-compose.yml" down --volumes 2>/dev/null || true
fi

rm -rf "${INSTALL_DIR}"

printf 'FreeLunch has been uninstalled.\n'
printf 'The Docker image is still cached locally. To remove it:\n'
printf '  docker rmi ghcr.io/jetymas/FreeLunch:latest\n'
