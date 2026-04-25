"""
choreo CLI.

::

    python -m choreo check                # human-readable
    python -m choreo check --json         # machine-readable
    python -m choreo check --strict       # warnings → exit code 1
    python -m choreo check --root <path>  # alternate project root

Exit codes:
    0 — no errors (warnings allowed unless ``--strict``)
    1 — drift detected (errors, or warnings under ``--strict``)
    2 — usage / configuration problem
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from . import __version__
from .diff import compute_findings
from .spec import load_specs
from .types import Finding
from .walker import walk


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m choreo",
        description="Static FSM verification for HOC.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"choreo {__version__}",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="walk the codebase and report drift")
    check.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="project root (default: current working directory)",
    )
    check.add_argument(
        "--specs-dir",
        type=str,
        default="state_machines",
        help="subdirectory under root that holds *_fsm.py modules",
    )
    check.add_argument(
        "--json",
        action="store_true",
        help="emit JSON instead of human-readable text",
    )
    check.add_argument(
        "--strict",
        action="store_true",
        help="treat warnings as errors (exit 1 if any warning)",
    )

    return parser


def _format_human(findings: list[Finding]) -> str:
    if not findings:
        return "choreo: no drift detected.\n"

    by_severity: dict[str, list[Finding]] = {"error": [], "warning": [], "info": []}
    for f in findings:
        by_severity.setdefault(f.severity, []).append(f)

    lines: list[str] = []
    for sev in ("error", "warning", "info"):
        items = by_severity.get(sev, [])
        if not items:
            continue
        header = {"error": "ERRORS", "warning": "WARNINGS", "info": "INFO"}[sev]
        lines.append(f"== {header} ({len(items)}) ==")
        for f in items:
            loc = f"{f.file}:{f.line}" if f.line else f.file or "<no location>"
            lines.append(f"  [{f.fsm}] {f.kind}")
            lines.append(f"    {f.message}")
            if loc:
                lines.append(f"    @ {loc}")
            lines.append("")

    counts = ", ".join(
        f"{len(by_severity.get(s, []))} {s}{'s' if len(by_severity.get(s, [])) != 1 else ''}"
        for s in ("error", "warning", "info")
    )
    lines.append(f"Summary: {counts}")
    return "\n".join(lines) + "\n"


def _format_json(findings: list[Finding]) -> str:
    payload = {
        "version": __version__,
        "findings": [asdict(f) for f in findings],
        "counts": {
            "error": sum(1 for f in findings if f.severity == "error"),
            "warning": sum(1 for f in findings if f.severity == "warning"),
            "info": sum(1 for f in findings if f.severity == "info"),
        },
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command != "check":
        parser.print_help()
        return 2

    root: Path = args.root.resolve()
    if not root.is_dir():
        print(f"choreo: --root {root} is not a directory", file=sys.stderr)
        return 2

    specs = load_specs(root, subdir=args.specs_dir)
    mutations, enums = walk(root)
    findings = compute_findings(specs, mutations, enums)

    output_fn = _format_json if args.json else _format_human
    sys.stdout.write(output_fn(findings))

    has_errors = any(f.severity == "error" for f in findings)
    has_warnings = any(f.severity == "warning" for f in findings)

    if has_errors:
        return 1
    if args.strict and has_warnings:
        return 1
    return 0
