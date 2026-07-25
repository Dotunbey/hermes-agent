"""Tests for the workspace plugin — models, storage, tools, and RBAC.

Run with:
    python -m pytest tests/plugins/test_workspace_plugin.py -v
"""

from __future__ import annotations

import os
import sys
import tempfile
import json
from pathlib import Path

import pytest

# Ensure the repo root is on sys.path so we can import plugins.workspace
_repo_root = Path(__file__).resolve().parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class TestRole:
    def test_role_hierarchy(self):
        from plugins.workspace.models import Role

        assert Role.OWNER > Role.ADMIN > Role.MANAGER > Role.MEMBER > Role.GUEST

    def test_from_string_case_insensitive(self):
        from plugins.workspace.models import Role

        assert Role.from_string("owner") == Role.OWNER
        assert Role.from_string("ADMIN") == Role.ADMIN
        assert Role.from_string("Manager") == Role.MANAGER
        assert Role.from_string("member") == Role.MEMBER
        assert Role.from_string("GUEST") == Role.GUEST

    def test_from_string_invalid_raises(self):
        from plugins.workspace.models import Role

        with pytest.raises(ValueError, match="Unknown role"):
            Role.from_string("superuser")

    def test_can_manage_members(self):
        from plugins.workspace.models import Role

        assert Role.OWNER.can_manage_members()
        assert Role.ADMIN.can_manage_members()
        assert not Role.MANAGER.can_manage_members()
        assert not Role.MEMBER.can_manage_members()
        assert not Role.GUEST.can_manage_members()

    def test_can_write(self):
        from plugins.workspace.models import Role

        assert Role.MEMBER.can_write()
        assert not Role.GUEST.can_write()

    def test_can_approve(self):
        from plugins.workspace.models import Role

        assert Role.MANAGER.can_approve()
        assert not Role.MEMBER.can_approve()

    def test_can_admin(self):
        from plugins.workspace.models import Role

        assert Role.ADMIN.can_admin()
        assert not Role.MANAGER.can_admin()

    def test_is_owner(self):
        from plugins.workspace.models import Role

        assert Role.OWNER.is_owner()
        assert not Role.ADMIN.is_owner()


class TestOrganization:
    def test_creation_defaults(self):
        from plugins.workspace.models import Organization

        org = Organization(name="Acme Corp", slug="acme-corp")
        assert org.name == "Acme Corp"
        assert org.slug == "acme-corp"
        assert org.id  # auto-generated
        assert len(org.id) == 12

    def test_to_dict_and_from_row_roundtrip(self):
        from plugins.workspace.models import Organization

        org = Organization(
            name="Test Org",
            slug="test-org",
            description="A test",
            settings={"timezone": "UTC"},
        )
        d = org.to_dict()
        org2 = Organization.from_row(d)
        assert org2.name == org.name
        assert org2.slug == org.slug
        assert org2.description == org.description
        assert org2.settings == org.settings

    def test_from_row_with_json_settings(self):
        from plugins.workspace.models import Organization

        row = {
            "id": "abc123",
            "name": "Test",
            "slug": "test",
            "description": "",
            "settings": '{"theme": "dark"}',
            "created_at": "2025-01-01T00:00:00",
            "updated_at": "2025-01-01T00:00:00",
        }
        org = Organization.from_row(row)
        assert org.settings == {"theme": "dark"}


class TestMember:
    def test_creation_defaults(self):
        from plugins.workspace.models import Member
        from plugins.workspace.models import Role

        m = Member(organization_id="o1", user_id="u1")
        assert m.role == Role.MEMBER
        assert m.is_active is True

    def test_to_dict_and_from_row_roundtrip(self):
        from plugins.workspace.models import Member
        from plugins.workspace.models import Role

        m = Member(
            organization_id="o1",
            user_id="u1",
            display_name="Alice",
            role=Role.ADMIN,
        )
        d = m.to_dict()
        m2 = Member.from_row(d)
        assert m2.user_id == "u1"
        assert m2.role == Role.ADMIN
        assert m2.display_name == "Alice"


# ---------------------------------------------------------------------------
# Storage (in-memory SQLite)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_storage_connection():
    """Force a fresh in-memory database for every test."""
    import plugins.workspace.storage as s

    s._conn = None
    # Replace db_path to use a temp file so tests are isolated
    import hermes_constants

    original = getattr(hermes_constants, "get_hermes_home", None)
    tmp = tempfile.mkdtemp(prefix="hermes_test_ws_")

    def _fake_home():
        return tmp

    hermes_constants.get_hermes_home = _fake_home
    yield
    hermes_constants.get_hermes_home = original
    s._conn = None
    # Cleanup
    import shutil

    shutil.rmtree(tmp, ignore_errors=True)


class TestStorage:
    def test_create_organization(self):
        from plugins.workspace.storage import create_organization, get_organization

        org = create_organization("Test", "test", "desc")
        assert org.name == "Test"

        fetched = get_organization(org.id)
        assert fetched is not None
        assert fetched.name == "Test"
        assert fetched.slug == "test"

    def test_get_by_slug(self):
        from plugins.workspace.storage import create_organization, get_organization_by_slug

        create_organization("Foo", "foo")
        org = get_organization_by_slug("foo")
        assert org is not None
        assert org.name == "Foo"

    def test_list_organizations(self):
        from plugins.workspace.storage import create_organization, list_organizations

        create_organization("A", "a")
        create_organization("B", "b")
        orgs = list_organizations()
        assert len(orgs) == 2

    def test_update_organization(self):
        from plugins.workspace.storage import create_organization, update_organization

        org = create_organization("Old", "old")
        updated = update_organization(org.id, name="New", description="Updated")
        assert updated is not None
        assert updated.name == "New"
        assert updated.description == "Updated"

    def test_delete_organization(self):
        from plugins.workspace.storage import create_organization, delete_organization, get_organization

        org = create_organization("Del", "del")
        assert delete_organization(org.id)
        assert get_organization(org.id) is None

    def test_add_and_get_member(self):
        from plugins.workspace.storage import create_organization, add_member, get_member
        from plugins.workspace.models import Role

        org = create_organization("Org", "org")
        m = add_member(org.id, "user-1", "Alice", Role.ADMIN)
        assert m is not None

        fetched = get_member(org.id, "user-1")
        assert fetched is not None
        assert fetched.role == Role.ADMIN
        assert fetched.display_name == "Alice"

    def test_add_member_duplicate_returns_none(self):
        from plugins.workspace.storage import create_organization, add_member

        org = create_organization("Org", "org")
        add_member(org.id, "user-1")
        m2 = add_member(org.id, "user-1")
        assert m2 is None

    def test_add_member_nonexistent_org_returns_none(self):
        from plugins.workspace.storage import add_member

        m = add_member("nonexistent", "user-1")
        assert m is None

    def test_remove_member(self):
        from plugins.workspace.storage import create_organization, add_member, remove_member, get_member

        org = create_organization("Org", "org")
        add_member(org.id, "user-1")
        assert remove_member(org.id, "user-1")
        assert get_member(org.id, "user-1") is None

    def test_list_members(self):
        from plugins.workspace.storage import create_organization, add_member, list_members
        from plugins.workspace.models import Role

        org = create_organization("Org", "org")
        add_member(org.id, "u1", role=Role.OWNER)
        add_member(org.id, "u2", role=Role.MEMBER)
        add_member(org.id, "u3", role=Role.GUEST)

        members = list_members(org.id)
        assert len(members) == 3
        # Sorted by role DESC: Owner > Member > Guest
        assert members[0].role == Role.OWNER
        assert members[-1].role == Role.GUEST

    def test_get_user_orgs(self):
        from plugins.workspace.storage import create_organization, add_member, get_user_orgs

        org1 = create_organization("Org1", "org1")
        org2 = create_organization("Org2", "org2")
        add_member(org1.id, "user-1")
        add_member(org2.id, "user-1")

        orgs = get_user_orgs("user-1")
        assert len(orgs) == 2

    def test_active_workspace_session(self):
        from plugins.workspace.storage import set_active_workspace, get_active_workspace, clear_active_workspace
        from plugins.workspace.models import Role

        set_active_workspace("sess-1", "org-1", "user-1", Role.ADMIN)
        ctx = get_active_workspace("sess-1")
        assert ctx is not None
        assert ctx["org_id"] == "org-1"
        assert ctx["role"] == Role.ADMIN

        clear_active_workspace("sess-1")
        assert get_active_workspace("sess-1") is None

    def test_audit_log(self):
        from plugins.workspace.storage import create_organization, get_audit_log

        org = create_organization("Org", "org", actor="test-runner")
        log = get_audit_log(org.id)
        assert len(log) >= 1
        assert log[0]["action"] == "org.create"
        assert log[0]["actor"] == "test-runner"


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------


class TestRBAC:
    def test_resolve_permission_mutating_tool(self):
        from plugins.rbac import resolve_permission

        assert resolve_permission("write_file") == "tools.mutating"
        assert resolve_permission("terminal") == "tools.mutating"

    def test_resolve_permission_readonly_tool(self):
        from plugins.rbac import resolve_permission

        assert resolve_permission("read_file") == "tools.readonly"
        assert resolve_permission("web_search") == "tools.readonly"

    def test_resolve_permission_override(self):
        from plugins.rbac import resolve_permission

        assert resolve_permission("send_message") == "tools.financial"
        assert resolve_permission("workspace_create") == "workspace.manage"

    def test_check_permission_owner_can_do_anything(self):
        from plugins.rbac import check_permission
        from plugins.workspace.models import Role

        for tool in ["terminal", "write_file", "send_message", "workspace_create", "cronjob"]:
            allowed, _ = check_permission(tool, Role.OWNER.value)
            assert allowed, f"Owner should be allowed to use {tool}"

    def test_check_permission_guest_readonly(self):
        from plugins.rbac import check_permission
        from plugins.workspace.models import Role

        allowed, _ = check_permission("read_file", Role.GUEST.value)
        assert allowed

        allowed, reason = check_permission("write_file", Role.GUEST.value)
        assert not allowed
        assert "tools.mutating" in reason

    def test_check_permission_member_vs_financial(self):
        from plugins.rbac import check_permission
        from plugins.workspace.models import Role

        allowed, _ = check_permission("write_file", Role.MEMBER.value)
        assert allowed

        allowed, reason = check_permission("send_message", Role.MEMBER.value)
        assert not allowed
        assert "tools.financial" in reason

    def test_check_permission_manager_vs_admin(self):
        from plugins.rbac import check_permission
        from plugins.workspace.models import Role

        # Manager CAN use financial tools
        allowed, _ = check_permission("send_message", Role.MANAGER.value)
        assert allowed

        # Manager CAN manage skills (skills.manage = MANAGER+)
        allowed, _ = check_permission("skill_manage", Role.MANAGER.value)
        assert allowed

        # But Manager CANNOT use admin tools
        allowed, reason = check_permission("config_set", Role.MANAGER.value)
        assert not allowed
        assert "tools.admin" in reason

    def test_workspace_guardrail_no_context_allows(self):
        from plugins.rbac import WorkspaceGuardrail

        g = WorkspaceGuardrail()
        decision = g.before_call("write_file", {})
        assert decision["action"] == "allow"

    def test_workspace_guardrail_with_context_blocks_guest(self):
        from plugins.rbac import WorkspaceGuardrail
        from plugins.workspace.models import Role
        from plugins.workspace.storage import set_active_workspace, clear_active_workspace

        set_active_workspace("test-session", "org-1", "user-guest", Role.GUEST)

        class FakeAgent:
            session_id = "test-session"

        g = WorkspaceGuardrail()
        decision = g.before_call("write_file", {}, agent=FakeAgent())
        assert decision["action"] == "block"
        assert "tools.mutating" in decision["reason"]

        clear_active_workspace("test-session")


# ---------------------------------------------------------------------------
# Workspace tools (unit tests using fake agent)
# ---------------------------------------------------------------------------


class FakeAgent:
    session_id = "test-tools"
    _session_source = "test-user"


class TestWorkspaceTools:
    def test_workspace_create_and_list(self):
        from plugins.workspace import tools

        agent = FakeAgent()
        result = json.loads(tools.workspace_create_tool(agent, {"name": "Test Corp", "slug": "test-corp"}))
        assert result["result"] == "created"
        assert result["workspace"]["name"] == "Test Corp"

        result2 = json.loads(tools.workspace_list_tool(agent, {}))
        assert len(result2["workspaces"]) >= 1

    def test_workspace_member_add_and_list(self):
        from plugins.workspace import tools

        agent = FakeAgent()
        tools.workspace_create_tool(agent, {"name": "Team", "slug": "team"})

        # Get the org id from listing
        result = json.loads(tools.workspace_list_tool(agent, {}))
        org_id = result["workspaces"][0]["id"]

        # Add a member
        r = json.loads(tools.workspace_member_add_tool(agent, {
            "org_id": org_id,
            "user_id": "new-user",
            "display_name": "New User",
            "role": "member",
        }))
        assert r["result"] == "added"

        # List members
        r2 = json.loads(tools.workspace_member_list_tool(agent, {"org_id": org_id}))
        assert len(r2["members"]) >= 2  # creator + new member

    def test_workspace_set_active(self):
        from plugins.workspace import tools
        from plugins.workspace.storage import get_active_workspace

        agent = FakeAgent()
        tools.workspace_create_tool(agent, {"name": "Switch", "slug": "switch"})
        result = json.loads(tools.workspace_list_tool(agent, {}))
        org_id = result["workspaces"][0]["id"]

        r = json.loads(tools.workspace_set_active_tool(agent, {"org_id": org_id}))
        assert r["result"] == "activated"

        ctx = get_active_workspace("test-tools")
        assert ctx is not None
        assert ctx["org_id"] == org_id


# ---------------------------------------------------------------------------
# Session context
# ---------------------------------------------------------------------------


class TestSessionContext:
    def test_disabled_workspace_returns_none(self, monkeypatch):
        from plugins.workspace import context as ctx

        def fake_load_config():
            return {}

        def fake_cfg_get(cfg, *keys, default=None):
            if keys == ("workspace", "enabled"):
                return False
            return default

        monkeypatch.setattr("hermes_cli.config.load_config", fake_load_config)
        monkeypatch.setattr("hermes_cli.config.cfg_get", fake_cfg_get)

        result = ctx.resolve_workspace_context("s-1")
        assert result is None

    def test_resolve_from_user_id(self, monkeypatch):
        from plugins.workspace import context as ctx
        from plugins.workspace.storage import create_organization, add_member
        from plugins.workspace.models import Role

        org = create_organization("Auto", "auto")
        add_member(org.id, "auto-user", "Auto User", Role.ADMIN)

        def fake_load_config():
            return {}

        def fake_cfg_get(cfg, *keys, default=None):
            if keys == ("workspace", "enabled"):
                return True
            return default

        monkeypatch.setattr("hermes_cli.config.load_config", fake_load_config)
        monkeypatch.setattr("hermes_cli.config.cfg_get", fake_cfg_get)

        result = ctx.resolve_workspace_context("s-auto", user_id="auto-user")
        assert result is not None
        assert result.org_id == org.id
        assert result.role == Role.ADMIN
        assert result.is_active is True
