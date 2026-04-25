"""
AST walker that discovers state mutations and Enum declarations.

For each ``.py`` file under ``root`` (excluding ``tests/``, ``benchmarks/``,
``snapshot/``, the ``choreo`` package itself, and a few standard
directories), parses the AST and looks for two patterns:

1. ``obj.state = EnumName.MEMBER`` — a direct mutation.
2. ``obj._set_state(EnumName.MEMBER)`` — HOC's internal setter wrapper
   used inside ``core/cells_base.py``. Without this, transitions like
   ``ACTIVE`` (only ever set internally) would falsely look dead.

3. ``class X(Enum):`` (or ``class X(enum.Enum)``) — an enum decl,
   captured with its member names. Member names are the LHS of any
   ``Name = <expr>`` statement in the class body.

The walker performs no type resolution; it matches purely on syntactic
shape. False negatives are possible (e.g. ``obj.state = func_returning_a_state()``,
``setattr(obj, "state", X)``, computed attribute names) but for HOC's
codebase the two literal patterns cover every observed call-site.
"""

from __future__ import annotations

import ast
from pathlib import Path

from .types import EnumDecl, Mutation

DEFAULT_EXCLUDE: frozenset[str] = frozenset(
    {
        "tests",
        "benchmarks",
        "snapshot",
        "choreo",
        "scripts",
        "docs",
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        "build",
        "dist",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        ".hypothesis",
        ".benchmarks",
    }
)


class _Visitor(ast.NodeVisitor):
    """Collects mutations and enum decls from a single module's AST."""

    def __init__(self, file: str) -> None:
        self.file = file
        self.mutations: list[Mutation] = []
        self.enums: list[EnumDecl] = []

    # ── Pattern 1: obj.state = EnumName.MEMBER ──────────────────────────
    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            if not isinstance(target, ast.Attribute):
                continue
            if target.attr != "state":
                continue
            mutation = _extract_enum_member(node.value)
            if mutation is None:
                continue
            enum_name, member_name = mutation
            self.mutations.append(
                Mutation(
                    file=self.file,
                    line=node.lineno,
                    enum_name=enum_name,
                    member_name=member_name,
                    pattern="assign",
                )
            )
        self.generic_visit(node)

    # ── Pattern 2: obj._set_state(EnumName.MEMBER) ──────────────────────
    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "_set_state" and len(node.args) >= 1:
            extracted = _extract_enum_member(node.args[0])
            if extracted is not None:
                enum_name, member_name = extracted
                self.mutations.append(
                    Mutation(
                        file=self.file,
                        line=node.lineno,
                        enum_name=enum_name,
                        member_name=member_name,
                        pattern="_set_state",
                    )
                )
        self.generic_visit(node)

    # ── Pattern 3: class X(Enum) ─────────────────────────────────────────
    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        is_enum = False
        for base in node.bases:
            # Direct: class X(Enum)
            if isinstance(base, ast.Name) and base.id == "Enum":
                is_enum = True
                break
            # Attribute: class X(enum.Enum)
            if isinstance(base, ast.Attribute) and base.attr == "Enum":
                is_enum = True
                break

        if is_enum:
            members = _extract_enum_members(node)
            if members:
                self.enums.append(
                    EnumDecl(
                        file=self.file,
                        line=node.lineno,
                        name=node.name,
                        members=tuple(members),
                    )
                )

        self.generic_visit(node)


def _extract_enum_member(value: ast.expr) -> tuple[str, str] | None:
    """Match ``Name(id="EnumName").MEMBER`` and return ``(EnumName, MEMBER)``.
    Returns None if the expression is not a simple attribute on a name."""
    if not isinstance(value, ast.Attribute):
        return None
    if not isinstance(value.value, ast.Name):
        return None
    return value.value.id, value.attr


def _extract_enum_members(class_node: ast.ClassDef) -> list[str]:
    """Collect ``NAME = <expr>`` statements (the convention for enum members)."""
    members: list[str] = []
    for stmt in class_node.body:
        # Match: NAME = <expr>  (auto() or literal). Skip dunder/private
        # members (typically not real enum values).
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            target = stmt.targets[0]
            if isinstance(target, ast.Name) and not target.id.startswith("_"):
                members.append(target.id)
    return members


def _is_excluded(path: Path, exclude: frozenset[str]) -> bool:
    """True if any directory in ``path``'s ancestry is in ``exclude``."""
    return bool(set(path.parts) & exclude)


def walk(
    root: Path,
    *,
    exclude: frozenset[str] | None = None,
) -> tuple[list[Mutation], list[EnumDecl]]:
    """
    Walk all ``*.py`` files under ``root`` and return mutations + enums.

    Files in excluded directories are skipped. Files that fail to parse
    (e.g. SyntaxError) are silently skipped — choreo is an additive
    static check, not a compiler.
    """
    if exclude is None:
        exclude = DEFAULT_EXCLUDE

    all_mutations: list[Mutation] = []
    all_enums: list[EnumDecl] = []

    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        if _is_excluded(rel.parent, exclude):
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            continue

        visitor = _Visitor(file=str(rel).replace("\\", "/"))
        visitor.visit(tree)
        all_mutations.extend(visitor.mutations)
        all_enums.extend(visitor.enums)

    return all_mutations, all_enums
