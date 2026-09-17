#!/usr/bin/env python3
"""Generate a code-derived architecture inventory.

The generator deliberately uses only the Python standard library and parses source
with :mod:`ast`; it never imports the application.  This keeps documentation
generation safe in a clean checkout and makes it useful before runtime
dependencies have been installed.
"""

from __future__ import annotations

import argparse
import ast
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

OUTPUT_RELATIVE_PATH = Path("docs/generated/architecture-inventory.md")
GRAPH_OUTPUT_RELATIVE_PATH = Path("docs/generated/module-dependencies.mmd")
SOURCE_ROOT = Path("src")


@dataclass(frozen=True)
class ParsedModule:
    """A parsed Python source module and its source-relative identity."""

    name: str
    path: Path
    tree: ast.Module


@dataclass(frozen=True)
class Route:
    method: str
    path: str
    function: str
    source: str
    line: int


@dataclass(frozen=True)
class ProviderModule:
    module: str
    classes: tuple[str, ...]
    provider_name: str
    factory: str


@dataclass(frozen=True)
class ScheduledJob:
    job_id: str
    callback: str
    trigger: str
    source: str
    line: int


@dataclass(frozen=True)
class ConfigField:
    name: str
    annotation: str
    default: str
    line: int


def _module_name(path: Path, source_root: Path) -> str:
    relative = path.relative_to(source_root).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join([source_root.name, *parts])


def _load_modules(repo_root: Path) -> list[ParsedModule]:
    source_root = repo_root / SOURCE_ROOT
    modules: list[ParsedModule] = []
    for path in sorted(source_root.rglob("*.py")):
        if path.name == "__init__.py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            raise SystemExit(f"cannot parse {path}: {exc}") from exc
        modules.append(ParsedModule(_module_name(path, source_root), path, tree))
    return modules


def _string_value(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _source_location(path: Path, repo_root: Path) -> str:
    return path.relative_to(repo_root).as_posix()


def _source_link(source: str, line: int) -> str:
    return f"[{source}:{line}](../../{source}#L{line})"


def _attribute_or_name(node: ast.AST | None) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _attribute_or_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ast.unparse(node) if node is not None else "?"


def _compact_expression(node: ast.AST | None) -> str:
    if node is None:
        return "(required)"
    expression = ast.unparse(node).replace("\n", " ")
    return " ".join(expression.split())


def _internal_dependencies(module: ParsedModule) -> tuple[str, ...]:
    dependencies: set[str] = set()
    for node in ast.walk(module.tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "src" or alias.name.startswith("src."):
                    dependencies.add(alias.name)
        elif (
            isinstance(node, ast.ImportFrom)
            and node.level == 0
            and node.module
            and (node.module == "src" or node.module.startswith("src."))
        ):
            dependencies.add(node.module)
    dependencies.discard(module.name)
    return tuple(sorted(dependencies))


def _external_dependencies(module: ParsedModule) -> tuple[str, ...]:
    dependencies: set[str] = set()
    for node in ast.walk(module.tree):
        if isinstance(node, ast.Import):
            dependencies.update(alias.name.split(".", maxsplit=1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            dependencies.add(node.module.split(".", maxsplit=1)[0])
    dependencies.discard("__future__")
    dependencies = {
        name
        for name in dependencies
        if name != "src" and name not in sys.stdlib_module_names
    }
    return tuple(sorted(dependencies))


def collect_module_dependencies(
    modules: Iterable[ParsedModule],
) -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[str, ...]]]:
    internal: dict[str, tuple[str, ...]] = {}
    external: dict[str, tuple[str, ...]] = {}
    for module in modules:
        internal[module.name] = _internal_dependencies(module)
        external[module.name] = _external_dependencies(module)
    return internal, external


def collect_routes(modules: Iterable[ParsedModule], repo_root: Path) -> list[Route]:
    routes: list[Route] = []
    for module in modules:
        source = _source_location(module.path, repo_root)
        for node in ast.walk(module.tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                    continue
                if not isinstance(decorator.func.value, ast.Name) or decorator.func.value.id != "router":
                    continue
                method = decorator.func.attr.lower()
                path = _string_value(decorator.args[0]) if decorator.args else None
                if path is None or method not in {"get", "post", "put", "patch", "delete", "head", "options"}:
                    continue
                routes.append(Route(method.upper(), path, node.name, source, node.lineno))
    return sorted(routes, key=lambda route: (route.path, route.method, route.source, route.line))


def _class_string_attributes(node: ast.ClassDef) -> dict[str, str]:
    values: dict[str, str] = {}
    for statement in node.body:
        if isinstance(statement, ast.Assign):
            targets = statement.targets
        elif isinstance(statement, ast.AnnAssign):
            targets = [statement.target]
        else:
            continue
        value = _string_value(statement.value)
        if value is None:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                values[target.id] = value
    return values


def collect_provider_modules(modules: Iterable[ParsedModule]) -> list[ProviderModule]:
    providers: list[ProviderModule] = []
    for module in modules:
        if not module.name.startswith("src.providers.") or module.name in {
            "src.providers.base",
            "src.providers.registry",
        }:
            continue
        adapter_classes: list[str] = []
        provider_name = ""
        for node in module.tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            class_attributes = _class_string_attributes(node)
            if node.name.endswith("Adapter"):
                adapter_classes.append(node.name)
                provider_name = class_attributes.get("name", provider_name)
        factory = ""
        for node in module.tree.body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name in {
                "build_provider_adapter",
                "PROVIDER_BOOTSTRAP_DESCRIPTOR",
            }:
                factory = node.name
        if adapter_classes or factory or module.name.endswith("openrouter"):
            providers.append(
                ProviderModule(
                    module.name,
                    tuple(sorted(adapter_classes)),
                    provider_name,
                    factory,
                )
            )
    return sorted(providers, key=lambda provider: provider.module)


def _trigger_expression(node: ast.AST | None) -> str:
    if not isinstance(node, ast.Call):
        return _compact_expression(node)
    function = _attribute_or_name(node.func)
    arguments = [f"{index}={_compact_expression(argument)}" for index, argument in enumerate(node.args)]
    arguments.extend(
        f"{keyword.arg}={_compact_expression(keyword.value)}"
        for keyword in node.keywords
        if keyword.arg is not None
    )
    return f"{function}({', '.join(arguments)})"


def collect_scheduled_jobs(modules: Iterable[ParsedModule], repo_root: Path) -> list[ScheduledJob]:
    jobs: list[ScheduledJob] = []
    for module in modules:
        source = _source_location(module.path, repo_root)
        for node in ast.walk(module.tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "add_job" or not node.args:
                continue
            callback = _attribute_or_name(node.args[0])
            job_id = next(
                (_string_value(keyword.value) for keyword in node.keywords if keyword.arg == "id"),
                None,
            )
            if job_id is None:
                continue
            trigger = node.args[1] if len(node.args) > 1 else next(
                (keyword.value for keyword in node.keywords if keyword.arg == "trigger"),
                None,
            )
            jobs.append(ScheduledJob(job_id, callback, _trigger_expression(trigger), source, node.lineno))
    return sorted(jobs, key=lambda job: (job.job_id, job.source, job.line))


def _is_class_var(annotation: ast.AST | None) -> bool:
    if isinstance(annotation, ast.Name):
        return annotation.id == "ClassVar"
    if isinstance(annotation, ast.Subscript):
        return _attribute_or_name(annotation.value) == "ClassVar"
    return False


def collect_config_fields(modules: Iterable[ParsedModule]) -> list[ConfigField]:
    for module in modules:
        if module.name != "src.config":
            continue
        for node in module.tree.body:
            if not isinstance(node, ast.ClassDef) or node.name != "Settings":
                continue
            fields: list[ConfigField] = []
            for statement in node.body:
                if not isinstance(statement, ast.AnnAssign) or not isinstance(statement.target, ast.Name):
                    continue
                if _is_class_var(statement.annotation):
                    continue
                fields.append(
                    ConfigField(
                        statement.target.id,
                        _compact_expression(statement.annotation),
                        _compact_expression(statement.value),
                        statement.lineno,
                    )
                )
            return fields
    return []


def _markdown_table(rows: Iterable[tuple[str, ...]]) -> str:
    materialized = list(rows)
    if not materialized:
        return "_None detected._\n"
    width = len(materialized[0])
    header = materialized[0]
    body = materialized[1:]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in range(width)) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in body)
    return "\n".join(lines) + "\n"


def render_inventory(repo_root: Path) -> str:
    modules = _load_modules(repo_root)
    internal, external = collect_module_dependencies(modules)
    routes = collect_routes(modules, repo_root)
    providers = collect_provider_modules(modules)
    jobs = collect_scheduled_jobs(modules, repo_root)
    config_fields = collect_config_fields(modules)
    module_graph = render_module_graph(repo_root).strip()

    lines = [
        "<!-- Generated by scripts/generate_architecture.py; do not edit manually. -->",
        "<!-- Run `python scripts/generate_architecture.py` after source changes. -->",
        "",
        "# Architecture inventory",
        "",
        "This file is generated from the Python source tree using static AST inspection. "
        "It records implementation facts; architectural intent belongs in `docs/architecture.md`.",
        "",
        "The high-level module relationship graph is rendered below and is also "
        "available as standalone [Mermaid source](module-dependencies.mmd).",
        "",
        "## Module dependency graph",
        "",
        "```mermaid",
        module_graph,
        "```",
        "",
        "## Source modules and dependencies",
        "",
        _markdown_table(
            [("Module", "Internal dependencies", "External imports")]
            + [
                (
                    f"`{name}`",
                    ", ".join(f"`{dependency}`" for dependency in internal[name]) or "—",
                    ", ".join(f"`{dependency}`" for dependency in external[name]) or "—",
                )
                for name in sorted(internal)
            ]
        ).rstrip(),
        "",
        "## HTTP routes",
        "",
        _markdown_table(
            [("Method", "Path", "Handler", "Source")]
            + [
                (
                    route.method,
                    f"`{route.path}`",
                    f"`{route.function}`",
                    _source_link(route.source, route.line),
                )
                for route in routes
            ]
        ).rstrip(),
        "",
        "## Provider modules",
        "",
        _markdown_table(
            [("Module", "Provider name", "Adapter classes", "Factory")]
            + [
                (
                    f"`{provider.module}`",
                    f"`{provider.provider_name}`" if provider.provider_name else "—",
                    ", ".join(f"`{name}`" for name in provider.classes) or "—",
                    f"`{provider.factory}`" if provider.factory else "—",
                )
                for provider in providers
            ]
        ).rstrip(),
        "",
        "## Scheduled jobs",
        "",
        _markdown_table(
            [("Job id", "Callback", "Trigger", "Source")]
            + [
                (
                    f"`{job.job_id}`",
                    f"`{job.callback}`",
                    f"`{job.trigger}`",
                    _source_link(job.source, job.line),
                )
                for job in jobs
            ]
        ).rstrip(),
        "",
        "## `Settings` fields",
        "",
        _markdown_table(
            [("Field", "Type", "Default", "Source")]
            + [
                (
                    f"`{field.name}`",
                    f"`{field.annotation}`",
                    f"`{field.default}`",
                    _source_link("src/config.py", field.line),
                )
                for field in config_fields
            ]
        ).rstrip(),
        "",
    ]
    return "\n".join(lines)


def _graph_node_name(module: str) -> str:
    if module.startswith("src.providers.") or module == "src.providers":
        return "src.providers"
    return module


def _graph_identifier(module: str) -> str:
    return "node_" + "_".join(part for part in module.split(".") if part)


def render_module_graph(repo_root: Path) -> str:
    """Render a readable high-level graph, collapsing provider adapters."""

    modules = _load_modules(repo_root)
    internal, _ = collect_module_dependencies(modules)
    graph_modules = {_graph_node_name(name) for name in internal}
    graph_modules.update(
        _graph_node_name(dependency) for dependencies in internal.values() for dependency in dependencies
    )
    graph_modules.discard("src.__init__")
    graph_modules.discard("src.providers.__init__")
    graph_modules.discard("src.providers")

    edges: set[tuple[str, str]] = set()
    for source, dependencies in internal.items():
        source_node = _graph_node_name(source)
        if source_node not in graph_modules and source_node != "src.providers":
            continue
        for dependency in dependencies:
            target_node = _graph_node_name(dependency)
            if target_node != source_node:
                edges.add((source_node, target_node))

    lines = [
        "%% Generated by scripts/generate_architecture.py; do not edit manually.",
        "%% Run `python scripts/generate_architecture.py` after source changes.",
        "flowchart LR",
        '    subgraph provider_boundary["Provider boundary"]',
        '        node_src_providers["src.providers.*"]',
        "    end",
    ]
    for module in sorted(graph_modules):
        lines.append(f'    {_graph_identifier(module)}["{module}"]')
    for source, target in sorted(edges):
        source_identifier = "node_src_providers" if source == "src.providers" else _graph_identifier(source)
        target_identifier = "node_src_providers" if target == "src.providers" else _graph_identifier(target)
        lines.append(f"    {source_identifier} --> {target_identifier}")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if the generated architecture inventory is missing or stale",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository root (defaults to the parent of scripts/)",
    )
    args = parser.parse_args(argv)
    repo_root = args.repo_root.resolve()
    output_path = repo_root / OUTPUT_RELATIVE_PATH
    graph_output_path = repo_root / GRAPH_OUTPUT_RELATIVE_PATH
    generated = render_inventory(repo_root)
    generated_graph = render_module_graph(repo_root)

    if args.check:
        try:
            current = output_path.read_text(encoding="utf-8")
            current_graph = graph_output_path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            print(f"stale architecture inventory: {exc.filename} is missing", file=sys.stderr)
            return 1
        if current != generated or current_graph != generated_graph:
            print(
                "stale architecture inventory: regenerate docs/generated artifacts",
                file=sys.stderr,
            )
            return 1
        return 0

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not output_path.exists() or output_path.read_text(encoding="utf-8") != generated:
        output_path.write_text(generated, encoding="utf-8")
    if not graph_output_path.exists() or graph_output_path.read_text(encoding="utf-8") != generated_graph:
        graph_output_path.write_text(generated_graph, encoding="utf-8")
    print(output_path.relative_to(repo_root))
    print(graph_output_path.relative_to(repo_root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
