"""Tests for `accounts.permissions` -- a pure, stateless capability table.
Not enforced anywhere server-side yet (see the module's own docstring);
these tests just lock in the table itself.
"""

from __future__ import annotations

from futures_bot.accounts.permissions import CAPABILITIES, can, capabilities_for


class TestCan:
    def test_owner_has_every_capability(self):
        assert all(can("owner", cap) for cap in CAPABILITIES)

    def test_admin_has_every_capability(self):
        assert all(can("admin", cap) for cap in CAPABILITIES)

    def test_member_cannot_manage_organization_or_members(self):
        assert not can("member", "manage_organization")
        assert not can("member", "manage_members")
        assert can("member", "manage_work")
        assert can("member", "view")

    def test_viewer_can_only_view(self):
        assert can("viewer", "view")
        assert not can("viewer", "manage_work")
        assert not can("viewer", "manage_members")
        assert not can("viewer", "manage_organization")

    def test_unknown_role_is_false_not_an_exception(self):
        assert can("superuser", "view") is False

    def test_unknown_capability_is_false_not_an_exception(self):
        assert can("owner", "delete_the_database") is False


class TestCapabilitiesFor:
    def test_returns_the_full_set_for_owner(self):
        assert capabilities_for("owner") == frozenset(CAPABILITIES)

    def test_returns_empty_for_unknown_role(self):
        assert capabilities_for("nope") == frozenset()
