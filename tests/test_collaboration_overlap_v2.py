"""Tests for `collaboration.overlap_v2` -- the deeper-than-filename
Overlap Engine V2 (shared imports/routes/tables/components/config,
confidence score, pairwise conflict scan). Uses `tmp_path` as an ad-hoc
`repo_root` with real files written to disk, since `analyze_files` only
needs a root to resolve relative paths against -- no actual `.git` or
this repo's own content required, keeping these tests independent of
this project's real source tree.
"""

from __future__ import annotations

from futures_bot.collaboration import overlap_v2
from futures_bot.collaboration.overlap_v2 import (
    analyze_files, compute_all_conflicts, compute_overlap_v2,
)


def _write(root, rel_path: str, content: str) -> None:
    full = root / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")


class TestAnalyzeFiles:
    def test_extracts_python_imports(self, tmp_path):
        _write(tmp_path, "a.py", "import os\nfrom collections import OrderedDict\n")
        sig = analyze_files(["a.py"], tmp_path)
        assert "os" in sig.imports
        assert "collections" in sig.imports

    def test_ignores_syntax_errors(self, tmp_path):
        _write(tmp_path, "broken.py", "def f(:\n")
        sig = analyze_files(["broken.py"], tmp_path)
        assert sig.imports == frozenset()

    def test_missing_file_contributes_nothing(self, tmp_path):
        sig = analyze_files(["does_not_exist.py"], tmp_path)
        assert sig.imports == frozenset()
        assert sig.files == frozenset({"does_not_exist.py"})

    def test_extracts_api_routes(self, tmp_path):
        _write(tmp_path, "routes.py", '@router.post("/api/things")\ndef create():\n    pass\n')
        sig = analyze_files(["routes.py"], tmp_path)
        assert "/api/things" in sig.routes

    def test_extracts_db_tables(self, tmp_path):
        _write(tmp_path, "schema.py", 'things = Table(\n    "things",\n    metadata,\n)\n')
        sig = analyze_files(["schema.py"], tmp_path)
        assert "things" in sig.tables

    def test_extracts_ts_imports(self, tmp_path):
        _write(tmp_path, "Foo.tsx", "import { useApi } from '../../useApi'\n")
        sig = analyze_files(["Foo.tsx"], tmp_path)
        assert "../../useApi" in sig.imports

    def test_flags_config_files(self, tmp_path):
        sig = analyze_files(["config.yaml", "src/a.py"], tmp_path)
        assert "config.yaml" in sig.config_files
        assert "src/a.py" not in sig.config_files

    def test_flags_frontend_components(self, tmp_path):
        sig = analyze_files(["frontend/src/components/Foo.tsx"], tmp_path)
        assert "Foo" in sig.frontend_components

    def test_path_escaping_repo_root_is_rejected(self, tmp_path):
        sig = analyze_files(["../../etc/passwd"], tmp_path)
        assert sig.imports == frozenset()


class TestAnalyzeFilesCache:
    """KNOWN_ISSUES.md ISSUE-039: `file_cache` lets a caller (the
    Integration Queue) parse each distinct file at most once across many
    `analyze_files` calls in one request, instead of once per work item
    that happens to reference it. `file_cache=None` (every caller before
    this fix) must be completely unaffected -- pinned below."""

    def test_shared_cache_reads_a_repeated_file_only_once(self, tmp_path, monkeypatch):
        _write(tmp_path, "a.py", "import os\n")
        read_calls = []
        original_read_text = overlap_v2._read_text

        def counting_read_text(root, rel_path):
            read_calls.append(rel_path)
            return original_read_text(root, rel_path)

        monkeypatch.setattr(overlap_v2, "_read_text", counting_read_text)

        cache: dict = {}
        analyze_files(["a.py"], tmp_path, file_cache=cache)
        analyze_files(["a.py"], tmp_path, file_cache=cache)
        analyze_files(["a.py"], tmp_path, file_cache=cache)

        assert read_calls == ["a.py"]  # only the first call actually reads the file

    def test_without_a_cache_a_repeated_file_is_read_every_time(self, tmp_path, monkeypatch):
        """Pins the pre-fix behavior for every caller that doesn't opt in
        -- `file_cache` must be purely additive, never a silent default
        behavior change."""
        _write(tmp_path, "a.py", "import os\n")
        read_calls = []
        original_read_text = overlap_v2._read_text
        monkeypatch.setattr(overlap_v2, "_read_text", lambda root, rel_path: (read_calls.append(rel_path), original_read_text(root, rel_path))[1])

        analyze_files(["a.py"], tmp_path)
        analyze_files(["a.py"], tmp_path)

        assert read_calls == ["a.py", "a.py"]

    def test_cached_and_uncached_results_are_identical(self, tmp_path):
        _write(tmp_path, "a.py", 'import os\n@router.post("/api/x")\ndef f(): pass\n')
        _write(tmp_path, "b.py", "import sys\n")

        uncached = analyze_files(["a.py", "b.py"], tmp_path)
        cached = analyze_files(["a.py", "b.py"], tmp_path, file_cache={})

        assert uncached == cached

    def test_cache_is_populated_per_file_not_per_call(self, tmp_path):
        """A later call with a *different* file list that overlaps the
        first should still hit the cache for the shared file."""
        _write(tmp_path, "a.py", "import os\n")
        _write(tmp_path, "b.py", "import sys\n")
        cache: dict = {}

        analyze_files(["a.py"], tmp_path, file_cache=cache)
        assert set(cache.keys()) == {"a.py"}

        analyze_files(["a.py", "b.py"], tmp_path, file_cache=cache)
        assert set(cache.keys()) == {"a.py", "b.py"}


class TestComputeOverlapV2:
    def test_no_active_items_returns_empty(self, tmp_path):
        assert compute_overlap_v2(["a.py"], [], repo_root=tmp_path) == []

    def test_shared_file_produces_a_warning(self, tmp_path):
        _write(tmp_path, "shared.py", "import os\n")
        active = [{"id": "w1", "title": "Other", "owner_user_id": "alice", "estimated_files": ["shared.py"]}]

        warnings = compute_overlap_v2(["shared.py"], active, repo_root=tmp_path)

        assert len(warnings) == 1
        assert warnings[0].work_item_id == "w1"
        assert warnings[0].confidence > 0
        assert "shared_files" in warnings[0].factors

    def test_shared_imports_across_different_files_is_detected(self, tmp_path):
        _write(tmp_path, "a.py", "import shared_module\n")
        _write(tmp_path, "b.py", "import shared_module\n")
        active = [{"id": "w1", "title": "Other", "owner_user_id": None, "estimated_files": ["b.py"]}]

        warnings = compute_overlap_v2(["a.py"], active, repo_root=tmp_path)

        assert len(warnings) == 1
        assert warnings[0].factors.get("shared_imports") == 1
        # No file overlap at all -- V1 would have missed this entirely.
        assert "shared_files" not in warnings[0].factors

    def test_shared_route_outweighs_a_shared_import(self, tmp_path):
        route_src = '@router.post("/api/orders")\ndef create():\n    pass\n'
        _write(tmp_path, "a.py", route_src)
        _write(tmp_path, "b.py", route_src)
        _write(tmp_path, "c_import.py", "import shared_module\n")
        _write(tmp_path, "d_import.py", "import shared_module\n")
        route_only = [{"id": "route", "title": "Route", "owner_user_id": None, "estimated_files": ["b.py"]}]
        import_only = [{"id": "import", "title": "Import", "owner_user_id": None, "estimated_files": ["d_import.py"]}]

        route_warnings = compute_overlap_v2(["a.py"], route_only, repo_root=tmp_path)
        import_warnings = compute_overlap_v2(["c_import.py"], import_only, repo_root=tmp_path)

        assert route_warnings[0].factors["shared_routes"] == 1
        assert route_warnings[0].confidence > import_warnings[0].confidence
        assert route_warnings[0].risk != "low"

    def test_no_overlap_at_all_returns_empty(self, tmp_path):
        _write(tmp_path, "a.py", "import os\n")
        _write(tmp_path, "b.py", "import sys\n")
        active = [{"id": "w1", "title": "Completely unrelated", "owner_user_id": None, "estimated_files": ["b.py"]}]

        assert compute_overlap_v2(["a.py"], active, repo_root=tmp_path) == []

    def test_keyword_overlap_in_titles_contributes(self, tmp_path):
        active = [{"id": "w1", "title": "Refactor authentication middleware", "owner_user_id": None, "estimated_files": []}]

        warnings = compute_overlap_v2(
            [], active, proposed_title="Fix authentication bug", repo_root=tmp_path,
        )

        assert len(warnings) == 1
        assert "shared_keywords" in warnings[0].factors

    def test_sorted_most_severe_first(self, tmp_path):
        _write(tmp_path, "shared.py", "import os\n")
        active = [
            {"id": "low", "title": "Unrelated but keyword overlap widget", "owner_user_id": None, "estimated_files": []},
            {"id": "high", "title": "Heavy overlap", "owner_user_id": None, "estimated_files": ["shared.py"]},
        ]

        warnings = compute_overlap_v2(
            ["shared.py"], active, proposed_title="widget task", repo_root=tmp_path,
        )

        assert warnings[0].work_item_id == "high"

    def test_confidence_is_bounded_at_100(self, tmp_path):
        # Contrive a huge number of shared imports to try to exceed the cap.
        imports = "\n".join(f"import mod_{i}" for i in range(50))
        _write(tmp_path, "a.py", imports)
        _write(tmp_path, "b.py", imports)
        active = [{"id": "w1", "title": "Other", "owner_user_id": None, "estimated_files": ["b.py"]}]

        warnings = compute_overlap_v2(["a.py"], active, repo_root=tmp_path)

        assert warnings[0].confidence == 100


class TestComputeAllConflicts:
    def test_no_conflicts_among_unrelated_items(self, tmp_path):
        _write(tmp_path, "a.py", "import os\n")
        _write(tmp_path, "b.py", "import sys\n")
        items = [
            {"id": "w1", "title": "A", "owner_user_id": None, "estimated_files": ["a.py"]},
            {"id": "w2", "title": "B", "owner_user_id": None, "estimated_files": ["b.py"]},
        ]

        assert compute_all_conflicts(items, repo_root=tmp_path) == []

    def test_finds_a_pairwise_conflict(self, tmp_path):
        _write(tmp_path, "shared.py", "import os\n")
        items = [
            {"id": "w1", "title": "A", "owner_user_id": "alice", "estimated_files": ["shared.py"]},
            {"id": "w2", "title": "B", "owner_user_id": "bob", "estimated_files": ["shared.py"]},
        ]

        pairs = compute_all_conflicts(items, repo_root=tmp_path)

        assert len(pairs) == 1
        assert {pairs[0].item_a["id"], pairs[0].item_b["id"]} == {"w1", "w2"}

    def test_never_reports_the_same_pair_twice(self, tmp_path):
        _write(tmp_path, "shared.py", "import os\n")
        items = [
            {"id": f"w{i}", "title": f"Task {i}", "owner_user_id": None, "estimated_files": ["shared.py"]}
            for i in range(4)
        ]

        pairs = compute_all_conflicts(items, repo_root=tmp_path)

        # 4 items all sharing one file -> C(4,2) = 6 unique pairs, never 12.
        assert len(pairs) == 6
        seen = {frozenset((p.item_a["id"], p.item_b["id"])) for p in pairs}
        assert len(seen) == 6
