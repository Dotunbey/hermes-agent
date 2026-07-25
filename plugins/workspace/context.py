"""Workspace session context — resolves workspace + role at agent init.

Called from the plugin system during agent initialization to populate
the active workspace from session metadata (Discord user id, Telegram
chat id, CLI user, etc.).

Also provides a `SessionContext` dataclass that downstream code
(rbac, tools, hooks) queries for the current workspace state
without importing storage directly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from plugins.workspace.models import Role

logger = logging.getLogger(__name__)


@dataclass(frozen=False)
class SessionContext:
    """Per-session workspace state, resolved once at init.

    Downstream code reads this at the session level; storage.py
    holds the per-session_id mapping.
    """

    org_id: str = ""
    user_id: str = ""
    role: Role = Role.GUEST
    is_active: bool = False

    @property
    def can_write(self) -> bool:
        return self.role.can_write() if self.is_active else True

    @property
    def can_approve(self) -> bool:
        return self.role.can_approve() if self.is_active else True

    @property
    def can_admin(self) -> bool:
        return self.role.can_admin() if self.is_active else True


def resolve_workspace_context(
    session_id: str,
    source: str = "cli",
    platform: str = "",
    user_id: str = "",
) -> Optional[SessionContext]:
    """Resolve the active workspace for a new session.

    Called during agent init. Returns None when workspace is disabled
    or no active workspace exists for the session.
    """
    from hermes_cli.config import cfg_get, load_config

    config = load_config()
    workspace_enabled = cfg_get(config, "workspace", "enabled", default=False)
    if not workspace_enabled:
        return None

    from plugins.workspace.storage import get_active_workspace, get_member, set_active_workspace

    ctx = get_active_workspace(session_id)
    if ctx is not None:
        # Already set — return existing
        role = ctx["role"]
        if isinstance(role, int):
            role = Role(role)
        elif isinstance(role, str):
            role = Role.from_string(role)
        return SessionContext(
            org_id=ctx["org_id"],
            user_id=ctx["user_id"],
            role=role,
            is_active=True,
        )

    # Try to resolve from user_id if given
    if user_id:
        from plugins.workspace.storage import get_user_orgs

        orgs = get_user_orgs(user_id)
        if orgs:
            # Pick the first org the user belongs to
            org = orgs[0]
            member = get_member(org.id, user_id)
            if member:
                set_active_workspace(session_id, org.id, user_id, member.role)
                return SessionContext(
                    org_id=org.id,
                    user_id=user_id,
                    role=member.role,
                    is_active=True,
                )

    return None


def init_workspace_for_session(
    agent: Any,
    source: str = "",
    platform: str = "",
) -> Optional[SessionContext]:
    """Initialize workspace context for an agent session.

    Called by the plugin loader during agent init.
    Attaches the resolved SessionContext to agent._workspace_context.
    """
    try:
        session_id = str(getattr(agent, "session_id", "default"))
        user_id = str(getattr(agent, "_session_source", ""))
        source = source or getattr(agent, "_session_source_origin", "cli")
        platform = platform or getattr(agent, "_platform", "")

        ctx = resolve_workspace_context(
            session_id=session_id,
            source=source,
            platform=platform,
            user_id=user_id,
        )

        agent._workspace_context = ctx
        if ctx and ctx.is_active:
            logger.info(
                "workspace context resolved: org=%s user=%s role=%s",
                ctx.org_id, ctx.user_id, ctx.role.name.lower(),
            )

        return ctx

    except Exception:
        logger.debug("workspace context init failed", exc_info=True)
        return None
