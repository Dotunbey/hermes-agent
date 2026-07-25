"""Workspace hooks — inject workspace context at turn start.

The `on_turn_start_workspace_context` hook runs before each model turn
and injects the active workspace id, user role, and member list into
the conversation context so the agent always knows its org scope.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def on_turn_start_workspace_context(
    turn: int,
    message: str,
    **kwargs: Any,
) -> None:
    """Inject active workspace details into the conversation context.

    Called by the turn processor before each model API call.
    The injected text appears as a system note so it never
    invalidates the prompt cache.
    """
    agent = kwargs.get("agent")
    if agent is None:
        return

    try:
        from plugins.workspace.storage import get_active_workspace, get_member, list_members

        session_id = str(getattr(agent, "session_id", "default"))
        ctx = get_active_workspace(session_id)
        if ctx is None:
            return

        org_id = ctx["org_id"]
        user_id = ctx["user_id"]
        member = get_member(org_id, user_id)
        if member is None:
            return

        members = list_members(org_id)
        member_names = ", ".join(
            f"{m.display_name or m.user_id} ({m.role.name.lower()})"
            for m in members[:20]
        )

        context_line = (
            f"[workspace: {org_id} | role: {member.role.name.lower()} | "
            f"team: {member_names}]"
        )

        # Inject as system context without mutating the prompt
        try:
            messages = getattr(agent, "messages", [])
            if messages:
                context_note = {"role": "system", "content": context_line}
                # Insert before the last assistant message so it doesn't
                # break the alternating user/assistant pattern
                messages.append(context_note)
        except Exception:
            logger.debug("workspace context injection skipped (no messages list)")

    except Exception:
        logger.debug("workspace context hook failed", exc_info=True)
