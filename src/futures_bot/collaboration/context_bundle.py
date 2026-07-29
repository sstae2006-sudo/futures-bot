"""SIL Phase 4 "Intelligent Automation Layer" -- the "Automatic AI
Context" piece: one call that gathers what a human or an AI-assisted
session would otherwise have to collect by hand before starting work
(active work items, recent commits, relevant KNOWN_ISSUES/ROADMAP
excerpts, similar past work, current merge risk) -- "reduce repeated
context gathering," in the brief's own words.

This is explicitly an aggregation of systems that already exist and are
already tested (`collaboration/store.py`, `git_info.py`, `overlap_v2.py`,
`merge_readiness.py`) -- not a new "architecture graph" or persisted
index. There is no architecture graph in this codebase (see this
package's own `__init__.py` docstring and ROADMAP.md's "Future" section)
-- "relevant architecture" here means Overlap V2's live, on-demand
import/route/table signals for the files actually named in the request,
computed fresh every call, same as everywhere else V2 is used.

"Relevant documentation" is a plain case-insensitive substring search
over KNOWN_ISSUES.md/ROADMAP.md's own text -- not semantic search, not an
embedding index. Cheap, explainable, and honest about being a heuristic
rather than real retrieval, matching every other heuristic in this
package's own stated design philosophy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import git_info
from .git_info import BranchInfo, Commit
from .overlap_v2 import OverlapWarningV2, _keywords, analyze_files, compute_overlap_v2
from .store import get_collaboration_store

#: Repo-root-relative -- read fresh on every call (these files change
#: often enough during active development that caching would just be
#: another thing to invalidate correctly).
_DOC_FILES = ("KNOWN_ISSUES.md", "ROADMAP.md")
#: How many matching lines (with surrounding context) to return per doc
#: file -- enough to be useful, small enough that this stays a quick
#: aggregation call, not a document dump.
_MAX_DOC_EXCERPTS_PER_FILE = 5
_DOC_EXCERPT_CONTEXT_LINES = 2


@dataclass(frozen=True)
class DocExcerpt:
    file: str
    line_number: int
    text: str


@dataclass(frozen=True)
class ContextBundle:
    active_work_items: list[dict]
    similar_past_work: list[dict]
    recent_commits: list[Commit]
    branch_info: BranchInfo
    overlap_warnings: list[OverlapWarningV2]
    relevant_docs: list[DocExcerpt]


def _search_docs(keywords: set[str], repo_root: Optional[Path]) -> list[DocExcerpt]:
    if not keywords or repo_root is None:
        return []
    excerpts: list[DocExcerpt] = []
    for filename in _DOC_FILES:
        path = repo_root / filename
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        found_in_file = 0
        for i, line in enumerate(lines):
            if found_in_file >= _MAX_DOC_EXCERPTS_PER_FILE:
                break
            lowered = line.lower()
            if not any(kw in lowered for kw in keywords):
                continue
            start = max(0, i - _DOC_EXCERPT_CONTEXT_LINES)
            end = min(len(lines), i + _DOC_EXCERPT_CONTEXT_LINES + 1)
            excerpt_text = "\n".join(lines[start:end]).strip()
            if not excerpt_text:
                continue
            excerpts.append(DocExcerpt(file=filename, line_number=i + 1, text=excerpt_text))
            found_in_file += 1
    return excerpts


def _similar_past_work(keywords: set[str], exclude_ids: set[str], limit: int = 5) -> list[dict]:
    """Every work item ever created (not just active ones) -- "similar
    past work" is exactly the kind of thing that's most useful once a
    task is already completed and its outcome is known, which
    `fetch_active_work_items` deliberately excludes."""
    if not keywords:
        return []
    store = get_collaboration_store()
    scored: list[tuple[int, dict]] = []
    for item in store.fetch_work_items():
        if item["id"] in exclude_ids:
            continue
        item_keywords = _keywords(item.get("title"), item.get("description"))
        overlap = len(keywords & item_keywords)
        if overlap > 0:
            scored.append((overlap, item))
    scored.sort(key=lambda pair: -pair[0])
    return [item for _, item in scored[:limit]]


def build_context_bundle(
    *, proposed_files: Optional[list[str]] = None, title: Optional[str] = None,
    description: Optional[str] = None, org_id: Optional[str] = None,
    commit_limit: int = 10,
) -> ContextBundle:
    proposed_files = proposed_files or []
    repo_root = git_info.repo_root()
    store = get_collaboration_store()

    active = store.fetch_active_work_items(org_id=org_id)
    overlap_warnings = compute_overlap_v2(
        proposed_files, active, proposed_title=title, proposed_description=description, repo_root=repo_root,
    )

    signature = analyze_files(proposed_files, repo_root)
    keywords = _keywords(title, description) | {Path(f).stem.lower() for f in proposed_files}
    similar = _similar_past_work(keywords, exclude_ids={i["id"] for i in active})

    doc_keywords = {kw for kw in keywords if len(kw) >= 4} | {t.lower() for t in signature.tables}
    relevant_docs = _search_docs(doc_keywords, repo_root)

    return ContextBundle(
        active_work_items=active,
        similar_past_work=similar,
        recent_commits=git_info.recent_commits(limit=commit_limit),
        branch_info=git_info.get_branch_info(),
        overlap_warnings=overlap_warnings,
        relevant_docs=relevant_docs,
    )
