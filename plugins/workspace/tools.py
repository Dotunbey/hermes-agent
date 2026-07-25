"""Workspace tools — exposed to the model as workspace_* tool functions.

Each tool follows the registry.register() convention: a schema dict
and a handler callable. Schemas are service-gated — they only load when
the workspace plugin is enabled in config.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from tools.registry import tool_error

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

WORKSPACE_CREATE_SCHEMA = {
    "name": "workspace_create",
    "description": (
        "Create a new organization workspace. You become its owner. "
        "Returns the workspace id, name, and slug."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Display name of the organization (e.g. 'Acme Corp').",
            },
            "slug": {
                "type": "string",
                "description": "URL-safe short name, lowercase with hyphens (e.g. 'acme-corp').",
            },
            "description": {
                "type": "string",
                "description": "Optional description of the organization.",
            },
        },
        "required": ["name", "slug"],
    },
}

WORKSPACE_LIST_SCHEMA = {
    "name": "workspace_list",
    "description": "List all workspaces the current user is a member of.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}

WORKSPACE_MEMBER_ADD_SCHEMA = {
    "name": "workspace_member_add",
    "description": (
        "Add a user to a workspace with a given role. "
        "Requires Admin or Owner role in the workspace."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "org_id": {
                "type": "string",
                "description": "The workspace id.",
            },
            "user_id": {
                "type": "string",
                "description": "The user to add (platform-specific id, e.g. Discord user id).",
            },
            "display_name": {
                "type": "string",
                "description": "Display name for the member.",
            },
            "role": {
                "type": "string",
                "description": "Role: owner, admin, manager, member, or guest.",
                "enum": ["owner", "admin", "manager", "member", "guest"],
            },
        },
        "required": ["org_id", "user_id", "role"],
    },
}

WORKSPACE_MEMBER_REMOVE_SCHEMA = {
    "name": "workspace_member_remove",
    "description": "Remove a member from a workspace. Requires Admin or Owner role.",
    "parameters": {
        "type": "object",
        "properties": {
            "org_id": {
                "type": "string",
                "description": "The workspace id.",
            },
            "user_id": {
                "type": "string",
                "description": "The user to remove.",
            },
        },
        "required": ["org_id", "user_id"],
    },
}

WORKSPACE_MEMBER_LIST_SCHEMA = {
    "name": "workspace_member_list",
    "description": "List all active members of a workspace with their roles.",
    "parameters": {
        "type": "object",
        "properties": {
            "org_id": {
                "type": "string",
                "description": "The workspace id.",
            },
        },
        "required": ["org_id"],
    },
}

WORKSPACE_SET_ACTIVE_SCHEMA = {
    "name": "workspace_set_active",
    "description": "Set the active workspace for the current session.",
    "parameters": {
        "type": "object",
        "properties": {
            "org_id": {
                "type": "string",
                "description": "The workspace id to activate.",
            },
        },
        "required": ["org_id"],
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_session_id(agent: Any) -> str:
    """Best-effort session id from the agent object."""
    try:
        return str(agent.session_id)
    except AttributeError:
        return "default"


def _get_actor(agent: Any) -> str:
    """Best-effort user id from the agent object."""
    try:
        return str(agent._session_source or "unknown")
    except AttributeError:
        return "unknown"


def _require_admin(org_id: str, agent: Any) -> Optional[str]:
    """Return an error string if the current user cannot manage the workspace."""
    from plugins.workspace.models import Role
    from plugins.workspace.storage import get_active_workspace, get_member

    sid = _get_session_id(agent)
    ctx = get_active_workspace(sid)
    user_id = _get_actor(agent)

    member = get_member(org_id, user_id) if org_id else None
    role = member.role if member else (Role(ctx["role"]) if ctx and ctx.get("org_id") == org_id else None)

    if role is None:
        return f"You are not a member of workspace {org_id}."
    if not role.can_manage_members():
        return f"You need Admin or Owner role to manage workspace {org_id}. Your role is {role.name.lower()}."
    return None


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------


def workspace_create_tool(agent: Any, args: dict) -> str:
    from plugins.workspace.storage import add_member, create_organization
    from plugins.workspace.models import Role

    name = str(args.get("name", "")).strip()
    slug = str(args.get("slug", "")).strip()
    description = str(args.get("description", "")).strip()

    if not name:
        return tool_error("name is required.")
    if not slug:
        return tool_error("slug is required.")
    if not slug.replace("-", "").replace("_", "").isalnum():
        return tool_error("slug must contain only letters, numbers, hyphens, and underscores.")

    org = create_organization(name=name, slug=slug, description=description, actor=_get_actor(agent))
    # Creator becomes Owner
    add_member(org.id, _get_actor(agent), display_name="Owner", role=Role.OWNER, actor=_get_actor(agent))

    # Auto-activate for this session
    from plugins.workspace.storage import set_active_workspace

    set_active_workspace(_get_session_id(agent), org.id, _get_actor(agent), Role.OWNER)

    return json.dumps({"result": "created", "workspace": org.to_dict()})


def workspace_list_tool(agent: Any, args: dict) -> str:
    from plugins.workspace.storage import get_user_orgs

    orgs = get_user_orgs(_get_actor(agent))
    return json.dumps({"workspaces": [o.to_dict() for o in orgs]})


def workspace_member_add_tool(agent: Any, args: dict) -> str:
    from plugins.workspace.storage import add_member
    from plugins.workspace.models import Role

    org_id = str(args.get("org_id", ""))
    user_id = str(args.get("user_id", ""))
    display_name = str(args.get("display_name", ""))
    role_str = str(args.get("role", "member")).lower()

    err = _require_admin(org_id, agent)
    if err:
        return tool_error(err)

    try:
        role = Role.from_string(role_str)
    except ValueError:
        return tool_error(f"Invalid role: {role_str}. Must be one of: owner, admin, manager, member, guest.")

    member = add_member(org_id, user_id, display_name=display_name, role=role, actor=_get_actor(agent))
    if member is None:
        return tool_error(f"Could not add {user_id} to {org_id} (already a member or org not found).")

    return json.dumps({"result": "added", "member": member.to_dict()})


def workspace_member_remove_tool(agent: Any, args: dict) -> str:
    from plugins.workspace.storage import remove_member

    org_id = str(args.get("org_id", ""))
    user_id = str(args.get("user_id", ""))

    err = _require_admin(org_id, agent)
    if err:
        return tool_error(err)

    removed = remove_member(org_id, user_id, actor=_get_actor(agent))
    return json.dumps({"result": "removed" if removed else "not_found"})


def workspace_member_list_tool(agent: Any, args: dict) -> str:
    from plugins.workspace.storage import list_members

    org_id = str(args.get("org_id", ""))
    members = list_members(org_id)
    return json.dumps({"members": [m.to_dict() for m in members]})


def workspace_set_active_tool(agent: Any, args: dict) -> str:
    from plugins.workspace.storage import get_active_workspace, get_member, set_active_workspace

    org_id = str(args.get("org_id", ""))
    user_id = _get_actor(agent)
    member = get_member(org_id, user_id)
    if member is None:
        return tool_error(f"You are not a member of workspace {org_id}.")

    set_active_workspace(_get_session_id(agent), org_id, user_id, member.role)
    return json.dumps({"result": "activated", "org_id": org_id, "role": member.role.name.lower()})
