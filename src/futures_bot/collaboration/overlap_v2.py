"""Overlap Engine V2 -- deeper-than-filename analysis of how two units of
work relate. `overlap.py`'s V1 check (still used unchanged by
`create_work_item`/`check_overlap`/`merge_summary` -- see that module's
docstring) only catches work that touches the *same file*. V2 additionally
looks at:

- Shared Python imports (parsed via `ast`, not a regex, so it's immune to
  imports that happen to appear inside a string or comment)
- Shared frontend (TypeScript/TSX) imports (regex-based: no TS parser is
  a project dependency)
- Shared FastAPI route paths (`@router.get/post/...` decorators)
- Shared SQLAlchemy table names (`Table("name", ...)` / `__tablename__`)
- Shared config files (by extension/well-known name)
- Keyword overlap between titles/descriptions ("related work items")

...and produces a single explainable 0-100 confidence score plus a
factor-by-factor breakdown, instead of V1's coarse file-count-only risk
bucket. Still warn-only (see `overlap.py`'s docstring for that rule) and
still an explicit, documented heuristic -- not a real dependency graph or
semantic/AST-level impact analysis. That's intentionally out of scope
here too; see ROADMAP.md's "Future" section. V2's actual job is narrower
and more modest: catch the specific, common case V1 misses -- two work
items touching *different* files that both import the same module, hit
the same API route, or touch the same DB table.

Every analysis step degrades gracefully: a proposed file that doesn't
exist on disk yet (a task that hasn't started), or that fails to parse,
just contributes nothing to that factor rather than raising -- V2 is
strictly additive on top of V1's file-path overlap, never a precondition
for it.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import git_info

#: Below this, a factor's contribution rounds to zero anyway -- keeps
#: `factors` free of noise like `{"shared_imports": 0}`.
_MIN_CONTRIBUTION = 1

#: Per-unit point weight for each overlap dimension, applied to however
#: many distinct items are shared in that dimension. Deliberately simple,
#: additive, and documented (not a learned/black-box model) -- same
#: design philosophy as `overlap.py::_risk_level`. Routes and DB tables
#: weigh most heavily: two tasks hitting the same API route or table are
#: far more likely to produce a real merge conflict than two tasks that
#: both happen to `import os`.
_WEIGHTS = {
    "shared_files": 15,
    "shared_imports": 6,
    "shared_routes": 30,
    "shared_tables": 30,
    "shared_config_files": 8,
    "shared_frontend_components": 12,
    "shared_keywords": 3,
}
_MAX_SCORE = 100

_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "of", "to", "in", "for", "on", "with", "is", "are",
    "fix", "add", "update", "improve", "implement", "build", "create", "make", "this",
    "that", "it", "its", "into", "from", "at", "as", "be", "not", "new",
})

_CONFIG_NAMES = {"config.yaml", "config.example.yaml", "pyproject.toml", "alembic.ini", "docker-compose.yml"}
_CONFIG_SUFFIXES = {".yaml", ".yml", ".toml", ".ini", ".env"}

_ROUTE_DECORATOR_RE = re.compile(r'@router\.(?:get|post|put|patch|delete)\(\s*["\']([^"\']+)["\']')
_TABLE_CALL_RE = re.compile(r'\bTable\(\s*["\']([^"\']+)["\']')
_TABLENAME_RE = re.compile(r'__tablename__\s*=\s*["\']([^"\']+)["\']')
#: `import x from 'y'` / `import 'y'` / `require('y')` -- covers the
#: overwhelming majority of this frontend's actual import styles without
#: pulling in a real TS/JS parser.
_TS_IMPORT_RE = re.compile(r'''(?:from|import|require\()\s*['"]([^'"]+)['"]''')


def _read_text(repo_root: Path, rel_path: str) -> Optional[str]:
    try:
        full = (repo_root / rel_path).resolve()
        full.relative_to(repo_root.resolve())  # reject a path that escapes the repo
    except (OSError, ValueError):
        return None
    if not full.is_file():
        return None
    try:
        return full.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None


def _python_imports(text: str) -> set[str]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set()
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def _ts_imports(text: str) -> set[str]:
    return set(_TS_IMPORT_RE.findall(text))


def _api_routes(text: str) -> set[str]:
    return set(_ROUTE_DECORATOR_RE.findall(text))


def _db_tables(text: str) -> set[str]:
    return set(_TABLE_CALL_RE.findall(text)) | set(_TABLENAME_RE.findall(text))


def _is_config_file(rel_path: str) -> bool:
    p = Path(rel_path)
    return p.name in _CONFIG_NAMES or p.suffix in _CONFIG_SUFFIXES


def _frontend_component_name(rel_path: str) -> Optional[str]:
    p = Path(rel_path)
    if p.suffix in (".tsx", ".jsx") and "components" in p.parts:
        return p.stem
    return None


def _keywords(*texts: Optional[str]) -> set[str]:
    words: set[str] = set()
    for text in texts:
        if not text:
            continue
        words.update(w for w in re.findall(r"[a-zA-Z][a-zA-Z0-9_]{2,}", text.lower()) if w not in _STOPWORDS)
    return words


@dataclass(frozen=True)
class FileSignature:
    """What a proposed set of files actually touches, one level deeper
    than the paths themselves."""
    files: frozenset[str] = frozenset()
    imports: frozenset[str] = frozenset()
    routes: frozenset[str] = frozenset()
    tables: frozenset[str] = frozenset()
    config_files: frozenset[str] = frozenset()
    frontend_components: frozenset[str] = frozenset()


def analyze_files(paths: list[str], repo_root: Optional[Path] = None) -> FileSignature:
    """Reads each path (best-effort -- a missing/unreadable file just
    contributes nothing) and extracts its imports/routes/tables/etc."""
    root = repo_root if repo_root is not None else git_info.repo_root()
    imports: set[str] = set()
    routes: set[str] = set()
    tables: set[str] = set()
    config_files: set[str] = set()
    components: set[str] = set()

    for rel_path in paths:
        if _is_config_file(rel_path):
            config_files.add(rel_path)
        component = _frontend_component_name(rel_path)
        if component:
            components.add(component)
        if root is None:
            continue
        text = _read_text(root, rel_path)
        if text is None:
            continue
        if rel_path.endswith(".py"):
            imports |= _python_imports(text)
            routes |= _api_routes(text)
            tables |= _db_tables(text)
        elif rel_path.endswith((".ts", ".tsx", ".js", ".jsx")):
            imports |= _ts_imports(text)

    return FileSignature(
        files=frozenset(paths), imports=frozenset(imports), routes=frozenset(routes),
        tables=frozenset(tables), config_files=frozenset(config_files), frontend_components=frozenset(components),
    )


@dataclass(frozen=True)
class OverlapWarningV2:
    work_item_id: str
    title: str
    owner_user_id: str | None
    risk: str  # one of collaboration.RISK_LEVELS
    confidence: int  # 0-100
    factors: dict[str, int] = field(default_factory=dict)
    reason: str = ""


def _risk_from_confidence(confidence: int) -> str:
    if confidence <= 0:
        return "no_risk"
    if confidence >= 70:
        return "critical"
    if confidence >= 45:
        return "high"
    if confidence >= 25:
        return "medium"
    return "low"


_FACTOR_LABELS = {
    "shared_files": "file(s)",
    "shared_imports": "import(s)",
    "shared_routes": "API route(s)",
    "shared_tables": "DB table(s)",
    "shared_config_files": "config file(s)",
    "shared_frontend_components": "frontend component(s)",
    "shared_keywords": "keyword(s) in the title/description",
}


def _reason(factors: dict[str, int], confidence: int, risk: str, shared_names: dict[str, tuple[str, ...]]) -> str:
    parts = []
    for key, count in factors.items():
        names = shared_names.get(key, ())
        label = _FACTOR_LABELS.get(key, key)
        if names:
            shown = ", ".join(names[:4])
            more = f" (+{len(names) - 4} more)" if len(names) > 4 else ""
            parts.append(f"{count} {label} [{shown}{more}]")
        else:
            parts.append(f"{count} {label}")
    detail = "; ".join(parts) if parts else "no shared factors"
    return f"Confidence {confidence}/100 ({risk}): {detail}."


def _score_pair(proposed: FileSignature, other: FileSignature, keyword_overlap: frozenset[str]) -> tuple[int, dict[str, int], dict[str, tuple[str, ...]]]:
    dims: dict[str, frozenset[str]] = {
        "shared_files": proposed.files & other.files,
        "shared_imports": proposed.imports & other.imports,
        "shared_routes": proposed.routes & other.routes,
        "shared_tables": proposed.tables & other.tables,
        "shared_config_files": proposed.config_files & other.config_files,
        "shared_frontend_components": proposed.frontend_components & other.frontend_components,
        "shared_keywords": keyword_overlap,
    }
    factors: dict[str, int] = {}
    shared_names: dict[str, tuple[str, ...]] = {}
    score = 0
    for key, shared in dims.items():
        if not shared:
            continue
        contribution = min(len(shared) * _WEIGHTS[key], _MAX_SCORE)
        if contribution < _MIN_CONTRIBUTION:
            continue
        factors[key] = len(shared)
        shared_names[key] = tuple(sorted(shared))
        score += contribution
    return min(score, _MAX_SCORE), factors, shared_names


def compute_overlap_v2(
    proposed_files: list[str], active_items: list[dict], *,
    proposed_title: Optional[str] = None, proposed_description: Optional[str] = None,
    repo_root: Optional[Path] = None,
) -> list[OverlapWarningV2]:
    """`active_items` is every other currently-active work item (same
    shape `overlap.detect_overlap` expects). Returns one `OverlapWarningV2`
    per active item with a nonzero confidence score, sorted highest-first
    -- an item that shares nothing across every dimension is omitted
    entirely, matching V1's "nothing to warn about" convention."""
    root = repo_root if repo_root is not None else git_info.repo_root()
    proposed_sig = analyze_files(proposed_files, root)
    proposed_keywords = _keywords(proposed_title, proposed_description)

    warnings: list[OverlapWarningV2] = []
    for item in active_items:
        other_sig = analyze_files(item.get("estimated_files") or [], root)
        other_keywords = _keywords(item.get("title"), item.get("description"))
        keyword_overlap = frozenset(proposed_keywords & other_keywords)

        confidence, factors, shared_names = _score_pair(proposed_sig, other_sig, keyword_overlap)
        if confidence <= 0:
            continue
        risk = _risk_from_confidence(confidence)
        warnings.append(OverlapWarningV2(
            work_item_id=item["id"], title=item["title"], owner_user_id=item.get("owner_user_id"),
            risk=risk, confidence=confidence, factors=factors,
            reason=_reason(factors, confidence, risk, shared_names),
        ))

    _risk_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "no_risk": 4}
    warnings.sort(key=lambda w: (_risk_order[w.risk], -w.confidence))
    return warnings


@dataclass(frozen=True)
class ConflictPair:
    item_a: dict
    item_b: dict
    warning: OverlapWarningV2


def compute_all_conflicts(active_items: list[dict], *, repo_root: Optional[Path] = None) -> list[ConflictPair]:
    """Every pairwise conflict across the *whole* currently-active set --
    Mission Control's "Conflict Warnings" view calls this once instead of
    one `/overlap-v2` request per work item (an O(n) request fan-out that
    would just be this same O(n^2) comparison spread across the network
    instead of done once, here). Each file gets analyzed once per item
    (cached in `signatures` below), not once per pair, since re-parsing
    the same file for every comparison it's involved in would be wasted
    work at any real active-item count."""
    root = repo_root if repo_root is not None else git_info.repo_root()
    signatures = [analyze_files(item.get("estimated_files") or [], root) for item in active_items]
    keyword_sets = [_keywords(item.get("title"), item.get("description")) for item in active_items]

    pairs: list[ConflictPair] = []
    for i in range(len(active_items)):
        for j in range(i + 1, len(active_items)):
            keyword_overlap = frozenset(keyword_sets[i] & keyword_sets[j])
            confidence, factors, shared_names = _score_pair(signatures[i], signatures[j], keyword_overlap)
            if confidence <= 0:
                continue
            risk = _risk_from_confidence(confidence)
            item_a, item_b = active_items[i], active_items[j]
            warning = OverlapWarningV2(
                work_item_id=item_b["id"], title=item_b["title"], owner_user_id=item_b.get("owner_user_id"),
                risk=risk, confidence=confidence, factors=factors,
                reason=_reason(factors, confidence, risk, shared_names),
            )
            pairs.append(ConflictPair(item_a=item_a, item_b=item_b, warning=warning))

    pairs.sort(key=lambda p: -p.warning.confidence)
    return pairs
