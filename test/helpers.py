from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from iwp_lint.config import load_config, resolve_schema_source
from iwp_lint.parsers.md_parser import parse_markdown_nodes
from iwp_lint.vcs.snapshot_store import SnapshotStore, collect_workspace_files


def _detect_repo_root() -> Path:
    current = Path(__file__).resolve().parent
    for parent in (current, *current.parents):
        if (parent / "pyproject.toml").exists() and (parent / "iwp_lint").exists():
            return parent
    # Fallback for unexpected layouts.
    return Path(__file__).resolve().parents[1]


REPO_ROOT = _detect_repo_root()
SCENARIO_ROOT = REPO_ROOT / "test"
SCHEMA_PROFILES: tuple[str, str] = ("minimal", "official")


@dataclass
class CmdResult:
    returncode: int
    stdout: str
    stderr: str


def workspace_tmpdir() -> tempfile.TemporaryDirectory[str]:
    tmp_root = REPO_ROOT / ".tmp_iwp_e2e_tests"
    tmp_root.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=tmp_root, prefix="e2e_")


def copy_scenario_to_workspace(scenario: str) -> tuple[tempfile.TemporaryDirectory[str], Path]:
    tempdir = workspace_tmpdir()
    workspace = Path(tempdir.name)
    source = SCENARIO_ROOT / scenario
    if not source.exists():
        tempdir.cleanup()
        raise RuntimeError(f"scenario not found: {scenario}")
    shutil.copytree(source, workspace, dirs_exist_ok=True)
    shared_schema_src = SCENARIO_ROOT / "schema"
    shared_schema_dst = workspace.parent / "schema"
    if shared_schema_src.exists():
        shutil.copytree(shared_schema_src, shared_schema_dst, dirs_exist_ok=True)
    return tempdir, workspace


def run_python_module(module: str, args: list[str], cwd: Path | None = None) -> CmdResult:
    proc = subprocess.run(
        [sys.executable, "-m", module, *args],
        cwd=str(cwd or REPO_ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    return CmdResult(returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)


def run_build(args: list[str]) -> CmdResult:
    return run_python_module("iwp_build", args)


def run_lint(args: list[str]) -> CmdResult:
    return run_python_module("iwp_lint", args)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def set_schema_file(config_path: Path, schema_file: Path) -> None:
    lines = config_path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    in_schema_block = False
    replaced = False
    for line in lines:
        if line.strip() == "schema:":
            in_schema_block = True
            out.append(line)
            continue
        if in_schema_block and line.startswith("  file:"):
            out.append(f"  file: {schema_file.as_posix()}")
            replaced = True
            continue
        if in_schema_block and line and not line.startswith("  "):
            in_schema_block = False
        out.append(line)
    if not replaced:
        raise AssertionError(f"schema.file not found in config: {config_path}")
    config_path.write_text("\n".join(out) + "\n", encoding="utf-8")


def apply_schema_profile(config_path: Path, profile: str) -> None:
    if profile == "minimal":
        return
    if profile == "official":
        set_schema_file(config_path, REPO_ROOT / "schema/iwp-schema.v1.json")
        return
    raise AssertionError(f"unsupported schema profile: {profile}")


def write_architecture_markdown(workspace: Path, profile: str, anchors: list[str]) -> None:
    path = workspace / "InstructWare.iw/architecture.md"
    if profile == "minimal":
        items = "\n".join(f"- {item}" for item in anchors)
        path.write_text(f"# Architecture\n\n## Layout Tree\n{items}\n", encoding="utf-8")
        return
    if profile == "official":
        scope_items = "\n".join(f"- {item}" for item in anchors)
        path.write_text(
            "# Architecture\n\n"
            "## Architecture Scope\n"
            f"{scope_items}\n\n"
            "## State Management\n"
            "- State Owner\n\n"
            "## Rendering Strategy\n"
            "- Render Rule\n\n"
            "## Event Orchestration\n"
            "- Event Rule\n",
            encoding="utf-8",
        )
        return
    raise AssertionError(f"unsupported schema profile: {profile}")


def write_i18n_home_markdown(workspace: Path, profile: str, anchor: str) -> None:
    source_file = workspace / "InstructWare.iw/views/pages/home.md"
    if profile == "minimal":
        source_file.write_text(f"# 首页\n\n## 布局树\n- {anchor}\n", encoding="utf-8")
        return
    if profile == "official":
        source_file.write_text(
            "# 首页\n\n"
            "## 布局树\n"
            f"- {anchor}\n\n"
            "## 交互钩子\n"
            "- 点击阅读宣言 navigates to docs/manifesto\n",
            encoding="utf-8",
        )
        return
    raise AssertionError(f"unsupported schema profile: {profile}")


def assert_build_diff_contract(
    testcase: Any,
    payload: dict[str, Any],
    *,
    expected_md_file: str,
    expect_gap_errors: bool,
    expected_mode: str = "diff",
    min_impacted_nodes: int = 1,
) -> None:
    summary = payload["summary"]
    compile_payload = payload["compile"]
    intent = payload["intent_diff"]
    gap_report = payload["gap_report"]
    metrics = gap_report["metrics"]
    changed_md_files = intent["changed_md_files"]
    impacted_nodes = intent["impacted_nodes"]

    testcase.assertEqual(summary["build_mode"], expected_mode)
    testcase.assertEqual(summary["changed_md_count"], len(changed_md_files))
    testcase.assertEqual(summary["impacted_nodes_count"], len(impacted_nodes))
    if expected_mode != "bootstrap_full":
        testcase.assertIn(expected_md_file, changed_md_files)
        testcase.assertGreaterEqual(summary["impacted_nodes_count"], min_impacted_nodes)
        testcase.assertTrue(
            any(node.get("source_path") == expected_md_file for node in impacted_nodes)
        )
    else:
        testcase.assertEqual(changed_md_files, [])
        testcase.assertEqual(impacted_nodes, [])
    testcase.assertNotIn("compiled_files", compile_payload)
    testcase.assertLessEqual(float(metrics.get("node_linked_percent", 0.0)), 100.0)
    testcase.assertLessEqual(float(metrics.get("node_tested_percent", 0.0)), 100.0)
    if expect_gap_errors:
        testcase.assertGreater(summary["gap_error_count"], 0)
    else:
        testcase.assertEqual(summary["gap_error_count"], 0)


def write_links_for_source(workspace: Path, source_path: str) -> list[str]:
    config_path = workspace / ".iwp-lint.yaml"
    config = load_config(str(config_path))
    schema_path = resolve_schema_source(config)
    nodes = parse_markdown_nodes(
        iwp_root=config.iwp_root_path,
        critical_patterns=config.critical_node_patterns,
        schema_path=schema_path,
        exclude_markdown_globs=config.schema_exclude_markdown_globs,
        node_registry_file=config.node_registry_file,
        page_only_enabled=config.page_only.enabled,
        authoring_tokens_enabled=config.authoring.tokens.enabled,
    )
    filtered = [node for node in nodes if node.source_path == source_path]
    lines = [f"// @iwp.link {node.source_path}::{node.node_id}" for node in filtered]
    link_file = workspace / "_ir/src/iwp_links.ts"
    link_file.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(lines)
    link_file.write_text((body + "\n") if body else "", encoding="utf-8")
    return lines


def append_link_line(workspace: Path, source_path: str, node_id: str) -> None:
    link_file = workspace / "_ir/src/iwp_links.ts"
    line = f"// @iwp.link {source_path}::{node_id}\n"
    if link_file.exists():
        existing = link_file.read_text(encoding="utf-8")
    else:
        link_file.parent.mkdir(parents=True, exist_ok=True)
        existing = ""
    link_file.write_text(existing + line, encoding="utf-8")


def latest_snapshot_id(config_path: Path) -> int | None:
    config = load_config(str(config_path))
    db_path = (config.project_root / config.snapshot_db_file).resolve()
    store = SnapshotStore(db_path)
    return store.latest_snapshot_id()


def create_snapshot_baseline(config_path: Path) -> int:
    config = load_config(str(config_path))
    db_path = (config.project_root / config.snapshot_db_file).resolve()
    store = SnapshotStore(db_path)
    files = collect_workspace_files(
        project_root=config.project_root,
        iwp_root=config.iwp_root,
        iwp_root_path=config.iwp_root_path,
        code_roots=config.code_roots,
        include_ext=config.include_ext,
        code_exclude_globs=config.code_exclude_globs,
        exclude_markdown_globs=config.schema_exclude_markdown_globs,
    )
    return store.create_snapshot(files)
