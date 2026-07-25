"""RBAC plugin — role-based access control for workspace tools.

Gates tool execution based on the active workspace role. Plugged into
the tool guardrail system so every tool call is checked against the
current user's role in the active workspace.

Registration (called by the plugin loader):
    from plugins.rbac import register
    register(ctx)
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.workspace.models import Role

logger = logging.getLogger(__name__)

# Maps each permission to the minimum role required.
# Higher-role users inherit lower permissions (Owner has everything).
PERMISSION_ROLE_MAP: dict[str, Role] = {
    # Workspace management
    "workspace.manage": Role.ADMIN,
    "workspace.delete": Role.OWNER,
    "workspace.members.manage": Role.ADMIN,
    "workspace.settings.write": Role.ADMIN,
    # Data mutation
    "workspace.data.write": Role.MEMBER,
    "workspace.data.read": Role.GUEST,
    # Tool categories gated by role
    "tools.mutating": Role.MEMBER,        # write_file, terminal, etc.
    "tools.readonly": Role.GUEST,         # read_file, web_search, etc.
    "tools.destructive": Role.ADMIN,      # delete_file, force_push, etc.
    "tools.financial": Role.MANAGER,      # send_message, billing
    "tools.admin": Role.ADMIN,            # config changes, plugin mgmt
    # Sub-agents
    "delegate.create": Role.MEMBER,
    "delegate.manage": Role.MANAGER,
    # Skills
    "skills.create": Role.MEMBER,
    "skills.manage": Role.MANAGER,
    # Cron / automations
    "cron.create": Role.MANAGER,
    "cron.manage": Role.ADMIN,
}

# Tools that require specific permissions beyond the category defaults.
# Any tool NOT listed here falls back to "tools.mutating" if it mutates,
# or "tools.readonly" if it's read-only.
TOOL_PERMISSION_OVERRIDES: dict[str, str] = {
    # Destructive
    "delete_file": "tools.destructive",
    "git_reset": "tools.destructive",
    "git_force_push": "tools.destructive",
    # Financial / external
    "send_message": "tools.financial",
    "spotify": "tools.financial",
    "discord": "tools.financial",
    # Admin
    "cronjob": "cron.create",
    "skill_manage": "skills.manage",
    "skill_create": "skills.create",
    "config_set": "tools.admin",
    # Workspace tools
    "workspace_create": "workspace.manage",
    "workspace_member_add": "workspace.members.manage",
    "workspace_member_remove": "workspace.members.manage",
    "workspace_set_active": "workspace.data.read",
    "workspace_list": "workspace.data.read",
    "workspace_member_list": "workspace.data.read",
}

# Read-only tools (mutating=false by default for these)
READ_ONLY_TOOLS: frozenset[str] = frozenset(
    {
        "read_file",
        "glob",
        "grep",
        "list_directory",
        "web_search",
        "web_fetch",
        "session_search",
        "semantic_search",
        "lsp",
        "diagnostics",
        "git_log",
        "git_status",
        "git_diff",
        "task_list",
        "task_output",
        "notebook_read",
        "kanban_list",
        "sessions_list",
        "cron_list",
        "session_status",
        "team_list",
        "spotify_current",
        "workspace_list",
        "workspace_member_list",
    }
)


def resolve_permission(tool_name: str) -> str:
    """Map a tool name to its required permission key."""
    if tool_name in TOOL_PERMISSION_OVERRIDES:
        return TOOL_PERMISSION_OVERRIDES[tool_name]
    if tool_name in READ_ONLY_TOOLS:
        return "tools.readonly"
    return "tools.mutating"


def check_permission(tool_name: str, role_value: int, session_id: str = "") -> tuple[bool, str]:
    """Check whether the given role can use a tool.

    Returns (allowed: bool, reason: str).
    """
    try:
        role = Role(role_value)
    except ValueError:
        return False, f"Unknown role value: {role_value}"

    perm = resolve_permission(tool_name)
    required = PERMISSION_ROLE_MAP.get(perm)
    if required is None:
        # Unknown permission — allow by default (fail open)
        return True, ""

    if role >= required:
        return True, ""

    return False, (
        f"RBAC: tool '{tool_name}' requires permission '{perm}' "
        f"(role {required.name.lower()}+). Your role is {role.name.lower()}."
    )


class WorkspaceGuardrail:
    """Tool guardrail that checks workspace role before tool execution.

    Plugs into the existing `_tool_guardrails.before_call` pipeline.
    When no workspace is active, all tools are allowed (passthrough).
    """

    def before_call(self, tool_name: str, tool_args: dict, agent: Any = None) -> dict:
        """Return a guardrail decision dict.

        {"action": "allow"} or {"action": "block", "reason": "..."}
        """
        if agent is None:
            return {"action": "allow"}

        try:
            from plugins.workspace.storage import get_active_workspace

            session_id = str(getattr(agent, "session_id", "default"))
            ctx = get_active_workspace(session_id)
            if ctx is None:
                # No workspace active — allow all tools
                return {"action": "allow"}

            allowed, reason = check_permission(tool_name, ctx["role"].value if hasattr(ctx["role"], "value") else ctx["role"], session_id)
            if allowed:
                return {"action": "allow"}

            logger.warning("RBAC blocked %s for session %s: %s", tool_name, session_id, reason)
            return {"action": "block", "reason": reason}

        except Exception:
            logger.debug("RBAC guardrail check failed", exc_info=True)
            return {"action": "allow"}


def register(ctx: Any) -> None:
    """Register the RBAC guardrail with the plugin context."""
    guardrail = WorkspaceGuardrail()
    ctx.register_guardrail(guardrail)
    logger.info("rbac plugin registered (WorkspaceGuardrail)")
