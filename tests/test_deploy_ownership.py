import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_image_runs_application_as_dedicated_non_root_user() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()

    assert "useradd --uid 10001 --gid 10001" in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert "ENV DATABASE_URL=/app/data/freelunch.db" in dockerfile


def test_compose_runs_scoped_migration_before_non_root_app() -> None:
    compose = (ROOT / "docker-compose.yml").read_text()

    assert "freelunch-data-migration:" in compose
    assert 'command: ["/usr/local/bin/migrate-data-ownership"]' in compose
    assert "condition: service_completed_successfully" in compose
    assert 'user: "10001:10001"' in compose
    assert "cap_add:\n      - CHOWN" in compose
    assert "FREELUNCH_ALLOW_DATA_CHOWN: ${FREELUNCH_ALLOW_DATA_CHOWN:-0}" in compose
    assert "    env_file:" not in compose.split("  freelunch:\n", 1)[0]


def test_installer_platforms_gate_ownership_repair_safely() -> None:
    shell_installer = (ROOT / "install.sh").read_text()
    shell_uninstaller = (ROOT / "uninstall.sh").read_text()
    powershell_installer = (ROOT / "install.ps1").read_text()

    assert "FREELUNCH_ALLOW_DATA_CHOWN=1" in shell_installer
    assert "FREELUNCH_ALLOW_DATA_CHOWN=0" in powershell_installer
    assert "migrate-data-ownership --restore-host" in shell_uninstaller
    assert shell_uninstaller.index("--restore-host") < shell_uninstaller.index("down --volumes")


def test_migration_is_fixed_to_data_mount_and_skips_symlinks() -> None:
    migration = (ROOT / "deploy/migrate-data-ownership.sh").read_text()

    assert "DATA_DIR=/app/data" in migration
    assert '[ ! -L "$DATA_DIR" ]' in migration
    assert '[ ! -L "$MARKER" ]' in migration
    assert 'find "$DATA_DIR" -xdev ! -type f ! -type d ! -type l' in migration
    assert "FREELUNCH_ALLOW_DATA_CHOWN:-0" in migration
    assert "--restore-host" in migration


def test_migration_refuses_to_run_without_root() -> None:
    if os.geteuid() == 0:
        return

    result = subprocess.run(
        ["sh", str(ROOT / "deploy/migrate-data-ownership.sh")],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "must run as root" in result.stderr
