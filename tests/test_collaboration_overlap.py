"""Unit tests for `collaboration.overlap.detect_overlap` -- pure function,
no store/DB involved. See that module's docstring: warn-only, never
blocking, exact-path intersection.
"""

from __future__ import annotations

from futures_bot.collaboration.overlap import detect_overlap


def _item(item_id, title, files, owner=None):
    return {"id": item_id, "title": title, "owner_user_id": owner, "estimated_files": files}


class TestDetectOverlap:
    def test_no_overlap_returns_empty(self):
        active = [_item("w1", "Other task", ["src/b.py"])]
        assert detect_overlap(["src/a.py"], active) == []

    def test_empty_proposed_files_returns_empty(self):
        active = [_item("w1", "Other task", ["src/a.py"])]
        assert detect_overlap([], active) == []

    def test_full_overlap_is_critical(self):
        active = [_item("w1", "Other task", ["src/a.py", "src/b.py"])]
        warnings = detect_overlap(["src/a.py", "src/b.py"], active)
        assert len(warnings) == 1
        assert warnings[0].risk == "critical"
        assert warnings[0].overlapping_files == ("src/a.py", "src/b.py")

    def test_small_partial_overlap_is_low(self):
        active = [_item("w1", "Big task", [f"src/f{i}.py" for i in range(20)] + ["src/shared.py"])]
        warnings = detect_overlap(["src/mine.py", "src/shared.py"] + [f"src/only_mine_{i}.py" for i in range(10)], active)
        assert len(warnings) == 1
        assert warnings[0].risk == "low"
        assert warnings[0].overlapping_files == ("src/shared.py",)

    def test_items_with_no_overlap_are_omitted_not_returned_as_no_risk(self):
        active = [
            _item("w1", "Overlaps", ["src/a.py"]),
            _item("w2", "No overlap", ["src/z.py"]),
        ]
        warnings = detect_overlap(["src/a.py"], active)
        assert [w.work_item_id for w in warnings] == ["w1"]

    def test_sorted_most_severe_first(self):
        active = [
            _item("w-low", "Low overlap", ["src/a.py"] + [f"src/x{i}.py" for i in range(20)]),
            _item("w-critical", "Full overlap", ["src/a.py", "src/b.py"]),
        ]
        warnings = detect_overlap(["src/a.py", "src/b.py"], active)
        assert [w.work_item_id for w in warnings] == ["w-critical", "w-low"]

    def test_reason_is_human_readable_and_names_files(self):
        active = [_item("w1", "Other", ["src/a.py"], owner="u1")]
        warnings = detect_overlap(["src/a.py"], active)
        assert "src/a.py" in warnings[0].reason
        assert warnings[0].owner_user_id == "u1"
