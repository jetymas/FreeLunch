from pathlib import Path

from scripts.generate_architecture import main, render_inventory, render_module_graph

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_generated_architecture_artifacts_are_current() -> None:
    assert main(["--check", "--repo-root", str(REPO_ROOT)]) == 0
    assert (REPO_ROOT / "docs/generated/architecture-inventory.md").read_text(
        encoding="utf-8"
    ) == render_inventory(REPO_ROOT)
    assert (REPO_ROOT / "docs/generated/module-dependencies.mmd").read_text(
        encoding="utf-8"
    ) == render_module_graph(REPO_ROOT)
