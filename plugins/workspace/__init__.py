"""Workspace plugin — multi-tenant organization model for Hermes Agent.

Provides organization CRUD, member management, and a role hierarchy
(Owner > Admin > Manager > Member > Guest) that gates every downstream
business capability.

Storage is a dedicated SQLite database at ``$HERMES_HOME/workspace/workspace.db``,
scoped so workspaces never leak across orgs.

Registration (called by the plugin loader):
    from plugins.workspace import register
    register(ctx)   # ctx.register_tool(...), ctx.register_hook(...)
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def register(ctx: Any) -> None:
    """Plugin entry point — called by the hermes-agent plugin loader.

    Registers workspace tools (org CRUD, member CRUD) and hooks
    (session-start workspace context injection).
    """
    from plugins.workspace.tools import (
        workspace_create_tool,
        workspace_list_tool,
        workspace_member_add_tool,
        workspace_member_remove_tool,
        workspace_member_list_tool,
        workspace_set_active_tool,
    )
    from plugins.workspace.hooks import on_turn_start_workspace_context

    ctx.register_tool(workspace_create_tool)
    ctx.register_tool(workspace_list_tool)
    ctx.register_tool(workspace_member_add_tool)
    ctx.register_tool(workspace_member_remove_tool)
    ctx.register_tool(workspace_member_list_tool)
    ctx.register_tool(workspace_set_active_tool)
    ctx.register_hook("on_turn_start", on_turn_start_workspace_context)

    logger.info("workspace plugin registered (%d tools, 1 hook)", 6)
