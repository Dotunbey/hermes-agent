"""Workspace storage — thread-safe SQLite adapter.

One database at ``$HERMES_HOME/workspace/workspace.db`` holds all
organizations and memberships. DDL runs idempotently on first access
so the plugin is zero-config.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any, List, Optional

from plugins.workspace.models import Member, Organization, Role

logger = logging.getLogger(__name__)

_conn_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None


def _db_path() -> Path:
    """Return the workspace database path, creating the directory if needed."""
    from hermes_constants import get_hermes_home

    ws_dir = Path(get_hermes_home()) / "workspace"
    ws_dir.mkdir(parents=True, exist_ok=True)
    return ws_dir / "workspace.db"


def _get_conn() -> sqlite3.Connection:
    """Lazy-init the workspace database connection with WAL mode."""
    global _conn
    if _conn is not None:
        return _conn
    with _conn_lock:
        if _conn is not None:
            return _conn
        db = _db_path()
        _conn = sqlite3.connect(str(db), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
        _create_tables(_conn)
        logger.info("workspace database opened at %s", db)
        return _conn


def _create_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS organizations (
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            slug        TEXT NOT NULL UNIQUE,
            description TEXT NOT NULL DEFAULT '',
            settings    TEXT NOT NULL DEFAULT '{}',
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS members (
            id              TEXT PRIMARY KEY,
            organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            user_id         TEXT NOT NULL,
            display_name    TEXT NOT NULL DEFAULT '',
            role            TEXT NOT NULL DEFAULT 'member',
            joined_at       TEXT NOT NULL,
            is_active       INTEGER NOT NULL DEFAULT 1,
            UNIQUE(organization_id, user_id)
        );

        CREATE INDEX IF NOT EXISTS idx_members_user
            ON members(user_id);

        CREATE TABLE IF NOT EXISTS audit_log (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            org_id    TEXT NOT NULL,
            actor     TEXT NOT NULL,
            action    TEXT NOT NULL,
            target    TEXT NOT NULL DEFAULT '',
            detail    TEXT NOT NULL DEFAULT '{}',
            timestamp TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_audit_org
            ON audit_log(org_id, timestamp);
        """
    )
    conn.commit()


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Organization CRUD
# ---------------------------------------------------------------------------


def create_organization(
    name: str, slug: str, description: str = "", actor: str = ""
) -> Organization:
    conn = _get_conn()
    org = Organization(name=name, slug=slug, description=description)
    with conn:
        conn.execute(
            """INSERT INTO organizations (id, name, slug, description, settings, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (org.id, org.name, org.slug, org.description, json.dumps(org.settings), org.created_at, org.updated_at),
        )
    _audit(org.id, actor, "org.create", org.id, {"name": name, "slug": slug})
    logger.info("created organization %s (%s)", org.name, org.id)
    return org


def get_organization(org_id: str) -> Optional[Organization]:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM organizations WHERE id = ?", (org_id,)).fetchone()
    return Organization.from_row(dict(row)) if row else None


def get_organization_by_slug(slug: str) -> Optional[Organization]:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM organizations WHERE slug = ?", (slug,)).fetchone()
    return Organization.from_row(dict(row)) if row else None


def list_organizations() -> List[Organization]:
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM organizations ORDER BY created_at DESC").fetchall()
    return [Organization.from_row(dict(r)) for r in rows]


def update_organization(org_id: str, **fields) -> Optional[Organization]:
    org = get_organization(org_id)
    if not org:
        return None
    for key in ("name", "slug", "description"):
        if key in fields:
            setattr(org, key, fields[key])
    org.updated_at = _now()
    conn = _get_conn()
    with conn:
        conn.execute(
            """UPDATE organizations SET name=?, slug=?, description=?, updated_at=?
               WHERE id=?""",
            (org.name, org.slug, org.description, org.updated_at, org_id),
        )
    return org


def delete_organization(org_id: str, actor: str = "") -> bool:
    conn = _get_conn()
    with conn:
        cur = conn.execute("DELETE FROM organizations WHERE id = ?", (org_id,))
        deleted = cur.rowcount > 0
    if deleted:
        _audit(org_id, actor, "org.delete", org_id, {})
        logger.info("deleted organization %s", org_id)
    return deleted


# ---------------------------------------------------------------------------
# Membership CRUD
# ---------------------------------------------------------------------------


def add_member(
    org_id: str,
    user_id: str,
    display_name: str = "",
    role: Role = Role.MEMBER,
    actor: str = "",
) -> Optional[Member]:
    org = get_organization(org_id)
    if not org:
        return None
    member = Member(
        organization_id=org_id,
        user_id=user_id,
        display_name=display_name,
        role=role,
    )
    conn = _get_conn()
    try:
        with conn:
            conn.execute(
                """INSERT INTO members (id, organization_id, user_id, display_name, role, joined_at, is_active)
                   VALUES (?, ?, ?, ?, ?, ?, 1)""",
                (member.id, org_id, user_id, display_name, role.name.lower(), member.joined_at),
            )
    except sqlite3.IntegrityError:
        logger.warning("member %s already in org %s", user_id, org_id)
        return None
    _audit(org_id, actor, "member.add", user_id, {"role": role.name.lower()})
    logger.info("added member %s to %s as %s", user_id, org_id, role.name.lower())
    return member


def remove_member(org_id: str, user_id: str, actor: str = "") -> bool:
    conn = _get_conn()
    with conn:
        cur = conn.execute(
            "DELETE FROM members WHERE organization_id = ? AND user_id = ?",
            (org_id, user_id),
        )
        removed = cur.rowcount > 0
    if removed:
        _audit(org_id, actor, "member.remove", user_id, {})
        logger.info("removed member %s from %s", user_id, org_id)
    return removed


def get_member(org_id: str, user_id: str) -> Optional[Member]:
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM members WHERE organization_id = ? AND user_id = ? AND is_active = 1",
        (org_id, user_id),
    ).fetchone()
    return Member.from_row(dict(row)) if row else None


def list_members(org_id: str) -> List[Member]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM members WHERE organization_id = ? AND is_active = 1 ORDER BY role DESC",
        (org_id,),
    ).fetchall()
    return [Member.from_row(dict(r)) for r in rows]


def get_user_orgs(user_id: str) -> List[Organization]:
    conn = _get_conn()
    rows = conn.execute(
        """SELECT o.* FROM organizations o
           JOIN members m ON m.organization_id = o.id
           WHERE m.user_id = ? AND m.is_active = 1
           ORDER BY o.name""",
        (user_id,),
    ).fetchall()
    return [Organization.from_row(dict(r)) for r in rows]


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


def _audit(org_id: str, actor: str, action: str, target: str, detail: dict) -> None:
    conn = _get_conn()
    conn.execute(
        """INSERT INTO audit_log (org_id, actor, action, target, detail, timestamp)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (org_id, actor, action, target, json.dumps(detail), _now()),
    )
    conn.commit()


def get_audit_log(org_id: str, limit: int = 100) -> List[dict[str, Any]]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM audit_log WHERE org_id = ? ORDER BY timestamp DESC LIMIT ?",
        (org_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Current workspace session state (per-process, per-session_id)
# ---------------------------------------------------------------------------

_current: dict[str, dict[str, Any]] = {}
_current_lock = threading.Lock()


def set_active_workspace(session_id: str, org_id: str, user_id: str, role: Role) -> None:
    with _current_lock:
        _current[session_id] = {"org_id": org_id, "user_id": user_id, "role": role}


def get_active_workspace(session_id: str) -> Optional[dict[str, Any]]:
    return _current.get(session_id)


def clear_active_workspace(session_id: str) -> None:
    with _current_lock:
        _current.pop(session_id, None)
