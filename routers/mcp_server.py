"""
routers/mcp_server.py — Remote MCP server embedded in PulseBoard's FastAPI process.

Users connect from Claude Code with a single config entry:
  {
    "mcpServers": {
      "pulseboard": {
        "url": "http://<host>:8010/mcp/sse",
        "headers": { "Authorization": "Bearer <token>" }
      }
    }
  }

No local Python process. No dependency install. Just a token.
Auth uses the same JWT system as the rest of PulseBoard.
Tools call the DB and services directly — no HTTP roundtrip.
"""

import base64
import json
import re
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Request, Depends
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp import types

from auth import get_current_user
from database import db
from services import file_service

router = APIRouter()

# ── Per-connection user context ───────────────────────────────────────────────
_user_ctx: ContextVar[dict] = ContextVar("pb_mcp_user")


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _ok(data) -> list[types.TextContent]:
    return [types.TextContent(type="text", text=json.dumps(data, indent=2, default=str))]


def _err(msg: str) -> list[types.TextContent]:
    return [types.TextContent(type="text", text=f"ERROR: {msg}")]


def _assert_forum_member(conn, forum_id: int, user: dict):
    if user.get("is_admin"):
        return
    row = conn.execute(
        "SELECT role FROM forum_members WHERE forum_id=? AND user_id=?",
        (forum_id, user["user_id"])
    ).fetchone()
    if not row:
        raise PermissionError("Not a member of this forum")


def _assert_forum_admin(conn, forum_id: int, user: dict):
    if user.get("is_admin"):
        return
    row = conn.execute(
        "SELECT role FROM forum_members WHERE forum_id=? AND user_id=?",
        (forum_id, user["user_id"])
    ).fetchone()
    if not row or row["role"] != "admin":
        raise PermissionError("Forum admin access required")


# ── MCP Server ────────────────────────────────────────────────────────────────

mcp = Server("pulseboard")
sse = SseServerTransport("/mcp/messages/")

TOOLS = [
    # ── Discovery ────────────────────────────────────────────────────────────
    types.Tool(
        name="list_forums",
        description="List all PulseBoard forums the current user has access to.",
        inputSchema={"type": "object", "properties": {}},
    ),
    types.Tool(
        name="list_topics",
        description="List topics (sub-sections) within a forum.",
        inputSchema={
            "type": "object", "required": ["forum_id"],
            "properties": {
                "forum_id": {"type": "integer", "description": "Forum ID from list_forums"},
            },
        },
    ),
    types.Tool(
        name="list_publications",
        description="List publications in a topic. Returns ID, title, type, refresh status.",
        inputSchema={
            "type": "object", "required": ["topic_id"],
            "properties": {
                "topic_id": {"type": "integer", "description": "Topic ID from list_topics"},
            },
        },
    ),
    types.Tool(
        name="get_publication",
        description="Get full metadata for a publication — title, author, file type, refresh status, last refreshed, scripts, schedule.",
        inputSchema={
            "type": "object", "required": ["publication_id"],
            "properties": {
                "publication_id": {"type": "integer"},
            },
        },
    ),
    types.Tool(
        name="resolve_url",
        description=(
            "Resolve a PulseBoard URL (e.g. http://host:8010/publications/6) into full metadata. "
            "Use whenever the user pastes a PulseBoard link — extracts ID, title, scripts, schedule."
        ),
        inputSchema={
            "type": "object", "required": ["url"],
            "properties": {"url": {"type": "string"}},
        },
    ),

    # ── Content ───────────────────────────────────────────────────────────────
    types.Tool(
        name="read_dashboard",
        description=(
            "Return the HTML content of a publication so you can analyse or summarise it. "
            "DOCX files return a plain-text preview."
        ),
        inputSchema={
            "type": "object", "required": ["publication_id"],
            "properties": {
                "publication_id": {"type": "integer"},
                "max_chars": {"type": "integer", "default": 50000},
            },
        },
    ),

    # ── Publishing ────────────────────────────────────────────────────────────
    types.Tool(
        name="publish_dashboard",
        description=(
            "Upload a new HTML or DOCX dashboard to a forum topic. "
            "Pass the file as a base64-encoded string. "
            "Optionally attach Python refresh scripts (list of {filename, content} objects). "
            "First script = refresh entry point."
        ),
        inputSchema={
            "type": "object", "required": ["title", "topic_id", "file_name", "file_content_b64"],
            "properties": {
                "title": {"type": "string"},
                "description": {"type": "string"},
                "topic_id": {"type": "integer"},
                "file_name": {"type": "string", "description": "e.g. dashboard.html or report.docx"},
                "file_content_b64": {"type": "string", "description": "base64-encoded file bytes"},
                "scripts": {
                    "type": "array",
                    "description": "Python refresh scripts to attach. First = primary entry point.",
                    "items": {
                        "type": "object",
                        "required": ["filename", "content"],
                        "properties": {
                            "filename": {"type": "string"},
                            "content": {"type": "string", "description": "Script source code"},
                        },
                    },
                },
            },
        },
    ),
    types.Tool(
        name="update_dashboard_file",
        description="Replace the content file of an existing publication with a new version.",
        inputSchema={
            "type": "object", "required": ["publication_id", "file_name", "file_content_b64"],
            "properties": {
                "publication_id": {"type": "integer"},
                "file_name": {"type": "string"},
                "file_content_b64": {"type": "string"},
            },
        },
    ),
    types.Tool(
        name="update_publication_metadata",
        description="Update the title and/or description of a publication without changing the file.",
        inputSchema={
            "type": "object", "required": ["publication_id", "title"],
            "properties": {
                "publication_id": {"type": "integer"},
                "title": {"type": "string"},
                "description": {"type": "string"},
            },
        },
    ),
    types.Tool(
        name="delete_publication",
        description="Permanently delete a publication and all its files.",
        inputSchema={
            "type": "object", "required": ["publication_id"],
            "properties": {"publication_id": {"type": "integer"}},
        },
    ),

    # ── Scripts ───────────────────────────────────────────────────────────────
    types.Tool(
        name="list_scripts",
        description="List all Python refresh scripts attached to a publication.",
        inputSchema={
            "type": "object", "required": ["publication_id"],
            "properties": {"publication_id": {"type": "integer"}},
        },
    ),
    types.Tool(
        name="read_script",
        description="Read the source code of a refresh script by its ID.",
        inputSchema={
            "type": "object", "required": ["script_id"],
            "properties": {"script_id": {"type": "integer"}},
        },
    ),
    types.Tool(
        name="upload_script",
        description="Upload or replace a Python refresh script on a publication. Pass source code as a string.",
        inputSchema={
            "type": "object", "required": ["publication_id", "filename", "content"],
            "properties": {
                "publication_id": {"type": "integer"},
                "filename": {"type": "string", "description": "e.g. refresh.py"},
                "content": {"type": "string", "description": "Python source code"},
                "is_primary": {"type": "boolean", "description": "Set as refresh entry point (default: true for first script)"},
            },
        },
    ),
    types.Tool(
        name="delete_script",
        description="Remove a script from a publication. If it was primary, the next script is promoted.",
        inputSchema={
            "type": "object", "required": ["script_id"],
            "properties": {"script_id": {"type": "integer"}},
        },
    ),

    # ── Refresh ───────────────────────────────────────────────────────────────
    types.Tool(
        name="trigger_refresh",
        description="Manually trigger a data refresh for a publication that has a script attached.",
        inputSchema={
            "type": "object", "required": ["publication_id"],
            "properties": {"publication_id": {"type": "integer"}},
        },
    ),
    types.Tool(
        name="get_refresh_status",
        description="Poll the refresh status (idle/running/success/error) and log of a publication.",
        inputSchema={
            "type": "object", "required": ["publication_id"],
            "properties": {"publication_id": {"type": "integer"}},
        },
    ),

    # ── Schedules ─────────────────────────────────────────────────────────────
    types.Tool(
        name="get_schedule",
        description="Get the current cron refresh schedule for a publication.",
        inputSchema={
            "type": "object", "required": ["publication_id"],
            "properties": {"publication_id": {"type": "integer"}},
        },
    ),
    types.Tool(
        name="set_schedule",
        description="Create or update a cron refresh schedule. Use 5-field UTC cron, e.g. '0 6 * * 1-5'.",
        inputSchema={
            "type": "object", "required": ["publication_id", "cron_expression"],
            "properties": {
                "publication_id": {"type": "integer"},
                "cron_expression": {"type": "string"},
            },
        },
    ),
    types.Tool(
        name="delete_schedule",
        description="Remove the cron schedule from a publication.",
        inputSchema={
            "type": "object", "required": ["publication_id"],
            "properties": {"publication_id": {"type": "integer"}},
        },
    ),

    # ── Comments ──────────────────────────────────────────────────────────────
    types.Tool(
        name="list_comments",
        description="Read all comments and replies on a publication as a threaded list.",
        inputSchema={
            "type": "object", "required": ["publication_id"],
            "properties": {"publication_id": {"type": "integer"}},
        },
    ),
    types.Tool(
        name="post_comment",
        description="Post a top-level comment on a publication.",
        inputSchema={
            "type": "object", "required": ["publication_id", "body"],
            "properties": {
                "publication_id": {"type": "integer"},
                "body": {"type": "string"},
            },
        },
    ),
    types.Tool(
        name="reply_to_comment",
        description="Reply to an existing comment.",
        inputSchema={
            "type": "object", "required": ["publication_id", "comment_id", "body"],
            "properties": {
                "publication_id": {"type": "integer"},
                "comment_id": {"type": "integer"},
                "body": {"type": "string"},
            },
        },
    ),
    types.Tool(
        name="delete_comment",
        description="Delete a comment.",
        inputSchema={
            "type": "object", "required": ["comment_id"],
            "properties": {"comment_id": {"type": "integer"}},
        },
    ),

    # ── Forum management ──────────────────────────────────────────────────────
    types.Tool(
        name="create_topic",
        description="Create a new topic inside a forum.",
        inputSchema={
            "type": "object", "required": ["forum_id", "display_name"],
            "properties": {
                "forum_id": {"type": "integer"},
                "display_name": {"type": "string"},
                "description": {"type": "string"},
            },
        },
    ),
    types.Tool(
        name="create_forum",
        description="Create a new forum. Requires system admin.",
        inputSchema={
            "type": "object", "required": ["display_name"],
            "properties": {
                "display_name": {"type": "string"},
                "description": {"type": "string"},
            },
        },
    ),
    types.Tool(
        name="add_forum_member",
        description="Add a user to a forum by username. Requires forum admin.",
        inputSchema={
            "type": "object", "required": ["forum_id", "username"],
            "properties": {
                "forum_id": {"type": "integer"},
                "username": {"type": "string"},
                "role": {"type": "string", "enum": ["member", "admin"], "default": "member"},
            },
        },
    ),
    types.Tool(
        name="remove_forum_member",
        description="Remove a user from a forum. Requires forum admin.",
        inputSchema={
            "type": "object", "required": ["forum_id", "user_id"],
            "properties": {
                "forum_id": {"type": "integer"},
                "user_id": {"type": "integer"},
            },
        },
    ),
]


@mcp.list_tools()
async def list_tools() -> list[types.Tool]:
    return TOOLS


@mcp.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    user = _user_ctx.get()
    try:
        return await _dispatch(name, arguments, user)
    except PermissionError as e:
        return _err(f"Permission denied: {e}")
    except Exception as e:
        return _err(str(e))


async def _dispatch(name: str, args: dict, user: dict) -> list[types.TextContent]:

    # ── list_forums ──────────────────────────────────────────────────────────
    if name == "list_forums":
        with db() as conn:
            if user.get("is_admin"):
                rows = conn.execute(
                    "SELECT f.id, f.slug, f.display_name, f.description, "
                    "(SELECT COUNT(*) FROM topics t WHERE t.forum_id=f.id) as topic_count "
                    "FROM forums f ORDER BY f.display_name"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT f.id, f.slug, f.display_name, f.description, fm.role, "
                    "(SELECT COUNT(*) FROM topics t WHERE t.forum_id=f.id) as topic_count "
                    "FROM forums f JOIN forum_members fm ON fm.forum_id=f.id "
                    "WHERE fm.user_id=? ORDER BY f.display_name",
                    (user["user_id"],)
                ).fetchall()
        return _ok([dict(r) for r in rows])

    # ── list_topics ──────────────────────────────────────────────────────────
    elif name == "list_topics":
        forum_id = args["forum_id"]
        with db() as conn:
            _assert_forum_member(conn, forum_id, user)
            rows = conn.execute(
                "SELECT t.id, t.slug, t.display_name, t.description, "
                "(SELECT COUNT(*) FROM publications p WHERE p.topic_id=t.id) as pub_count "
                "FROM topics t WHERE t.forum_id=? ORDER BY t.display_name",
                (forum_id,)
            ).fetchall()
        return _ok([dict(r) for r in rows])

    # ── list_publications ─────────────────────────────────────────────────────
    elif name == "list_publications":
        topic_id = args["topic_id"]
        with db() as conn:
            topic = conn.execute("SELECT * FROM topics WHERE id=?", (topic_id,)).fetchone()
            if not topic:
                return _err("Topic not found")
            _assert_forum_member(conn, topic["forum_id"], user)
            rows = conn.execute(
                "SELECT p.id, p.title, p.description, p.file_type, p.has_script, "
                "p.refresh_status, p.last_refreshed_at, p.created_at, u.username as author "
                "FROM publications p LEFT JOIN users u ON u.id=p.created_by "
                "WHERE p.topic_id=? ORDER BY p.updated_at DESC",
                (topic_id,)
            ).fetchall()
        return _ok([dict(r) for r in rows])

    # ── get_publication ───────────────────────────────────────────────────────
    elif name == "get_publication":
        pub_id = args["publication_id"]
        with db() as conn:
            pub = conn.execute(
                "SELECT p.*, u.username as author, f.slug as forum_slug, f.display_name as forum_name, "
                "t.slug as topic_slug, t.display_name as topic_name "
                "FROM publications p LEFT JOIN users u ON u.id=p.created_by "
                "LEFT JOIN forums f ON f.id=p.forum_id LEFT JOIN topics t ON t.id=p.topic_id "
                "WHERE p.id=?", (pub_id,)
            ).fetchone()
            if not pub:
                return _err("Publication not found")
            _assert_forum_member(conn, pub["forum_id"], user)
            scripts = conn.execute(
                "SELECT id, filename, is_primary, uploaded_at FROM publication_scripts "
                "WHERE publication_id=? ORDER BY is_primary DESC",
                (pub_id,)
            ).fetchall()
            schedule = conn.execute(
                "SELECT cron_expression, is_active, next_run_at FROM schedules WHERE publication_id=?",
                (pub_id,)
            ).fetchone()
        result = dict(pub)
        result["scripts"] = [dict(s) for s in scripts]
        result["schedule"] = dict(schedule) if schedule else None
        return _ok(result)

    # ── resolve_url ───────────────────────────────────────────────────────────
    elif name == "resolve_url":
        url = args.get("url", "")
        m = re.search(r"/publications/(\d+)", url)
        if not m:
            return _err(f"No /publications/{{id}} found in URL: {url!r}")
        return await _dispatch("get_publication", {"publication_id": int(m.group(1))}, user)

    # ── read_dashboard ────────────────────────────────────────────────────────
    elif name == "read_dashboard":
        pub_id = args["publication_id"]
        max_chars = int(args.get("max_chars", 50000))
        with db() as conn:
            pub = conn.execute("SELECT * FROM publications WHERE id=?", (pub_id,)).fetchone()
            if not pub:
                return _err("Publication not found")
            _assert_forum_member(conn, pub["forum_id"], user)
        if pub["file_type"] == "docx":
            try:
                from docx import Document
                doc = Document(pub["file_path"])
                preview = "\n".join(p.text for p in doc.paragraphs if p.text)[:max_chars]
                return _ok({"file_type": "docx", "preview": preview})
            except Exception as e:
                return _err(f"Could not read DOCX: {e}")
        try:
            content = Path(pub["file_path"]).read_text(encoding="utf-8", errors="replace")
            if len(content) > max_chars:
                content = content[:max_chars] + "\n\n[...truncated...]"
            return _ok({"file_type": "html", "content": content})
        except Exception as e:
            return _err(f"Could not read file: {e}")

    # ── publish_dashboard ─────────────────────────────────────────────────────
    elif name == "publish_dashboard":
        topic_id = args["topic_id"]
        file_name = args["file_name"]
        ext = Path(file_name).suffix.lower()
        if ext not in (".html", ".htm", ".docx"):
            return _err("file_name must end in .html or .docx")
        file_type = "html" if ext in (".html", ".htm") else "docx"
        try:
            file_bytes = base64.b64decode(args["file_content_b64"])
        except Exception:
            return _err("file_content_b64 is not valid base64")

        with db() as conn:
            topic = conn.execute("SELECT * FROM topics WHERE id=?", (topic_id,)).fetchone()
            if not topic:
                return _err("Topic not found")
            _assert_forum_member(conn, topic["forum_id"], user)

            # Save file (pub_id=0 placeholder, then re-save)
            saved = file_service.save_upload(0, file_type, file_bytes)
            now = _now()
            cur = conn.execute(
                "INSERT INTO publications (forum_id, topic_id, title, description, file_type, "
                "file_path, created_by, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (topic["forum_id"], topic_id, args["title"], args.get("description", ""),
                 file_type, saved, user["user_id"], now, now)
            )
            pub_id = cur.lastrowid
            # Re-save to correct pub directory
            correct = file_service.save_upload(pub_id, file_type, file_bytes)
            conn.execute("UPDATE publications SET file_path=? WHERE id=?", (correct, pub_id))

            # Attach scripts
            scripts = args.get("scripts", [])
            primary_path = None
            for i, s in enumerate(scripts):
                sc_path = file_service.save_named_script(pub_id, s["filename"], s["content"].encode())
                is_primary = 1 if i == 0 else 0
                conn.execute(
                    "INSERT OR REPLACE INTO publication_scripts "
                    "(publication_id, filename, file_path, is_primary, uploaded_by, uploaded_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (pub_id, s["filename"], sc_path, is_primary, user["user_id"], now)
                )
                if is_primary:
                    primary_path = sc_path
            if primary_path:
                conn.execute(
                    "UPDATE publications SET has_script=1, script_path=? WHERE id=?",
                    (primary_path, pub_id)
                )

        return _ok({"publication_id": pub_id, "title": args["title"], "url": f"/publications/{pub_id}"})

    # ── update_dashboard_file ─────────────────────────────────────────────────
    elif name == "update_dashboard_file":
        pub_id = args["publication_id"]
        ext = Path(args["file_name"]).suffix.lower()
        file_type = "html" if ext in (".html", ".htm") else "docx"
        try:
            file_bytes = base64.b64decode(args["file_content_b64"])
        except Exception:
            return _err("file_content_b64 is not valid base64")
        with db() as conn:
            pub = conn.execute("SELECT * FROM publications WHERE id=?", (pub_id,)).fetchone()
            if not pub:
                return _err("Publication not found")
            _assert_forum_member(conn, pub["forum_id"], user)
            new_path = file_service.save_upload(pub_id, file_type, file_bytes)
            conn.execute(
                "UPDATE publications SET file_path=?, file_type=?, updated_at=? WHERE id=?",
                (new_path, file_type, _now(), pub_id)
            )
        return _ok({"updated": pub_id})

    # ── update_publication_metadata ───────────────────────────────────────────
    elif name == "update_publication_metadata":
        pub_id = args["publication_id"]
        with db() as conn:
            pub = conn.execute("SELECT forum_id FROM publications WHERE id=?", (pub_id,)).fetchone()
            if not pub:
                return _err("Publication not found")
            _assert_forum_member(conn, pub["forum_id"], user)
            conn.execute(
                "UPDATE publications SET title=?, description=?, updated_at=? WHERE id=?",
                (args["title"], args.get("description", ""), _now(), pub_id)
            )
        return _ok({"updated": pub_id})

    # ── delete_publication ────────────────────────────────────────────────────
    elif name == "delete_publication":
        pub_id = args["publication_id"]
        with db() as conn:
            pub = conn.execute("SELECT * FROM publications WHERE id=?", (pub_id,)).fetchone()
            if not pub:
                return _err("Publication not found")
            _assert_forum_member(conn, pub["forum_id"], user)
            conn.execute("DELETE FROM publications WHERE id=?", (pub_id,))
        return _ok({"deleted": pub_id})

    # ── list_scripts ──────────────────────────────────────────────────────────
    elif name == "list_scripts":
        pub_id = args["publication_id"]
        with db() as conn:
            rows = conn.execute(
                "SELECT ps.id, ps.filename, ps.is_primary, ps.uploaded_at, u.username as uploaded_by "
                "FROM publication_scripts ps LEFT JOIN users u ON u.id=ps.uploaded_by "
                "WHERE ps.publication_id=? ORDER BY ps.is_primary DESC, ps.uploaded_at",
                (pub_id,)
            ).fetchall()
        return _ok([dict(r) for r in rows])

    # ── read_script ───────────────────────────────────────────────────────────
    elif name == "read_script":
        script_id = args["script_id"]
        with db() as conn:
            row = conn.execute("SELECT * FROM publication_scripts WHERE id=?", (script_id,)).fetchone()
            if not row:
                return _err("Script not found")
        try:
            source = Path(row["file_path"]).read_text(encoding="utf-8")
        except Exception as e:
            return _err(f"Could not read file: {e}")
        return _ok({"filename": row["filename"], "is_primary": bool(row["is_primary"]), "source": source})

    # ── upload_script ─────────────────────────────────────────────────────────
    elif name == "upload_script":
        pub_id = args["publication_id"]
        filename = args["filename"]
        content = args["content"]
        with db() as conn:
            pub = conn.execute("SELECT * FROM publications WHERE id=?", (pub_id,)).fetchone()
            if not pub:
                return _err("Publication not found")
            _assert_forum_member(conn, pub["forum_id"], user)
            # Determine is_primary
            existing = conn.execute(
                "SELECT COUNT(*) FROM publication_scripts WHERE publication_id=?", (pub_id,)
            ).fetchone()[0]
            is_primary = 1 if (args.get("is_primary", existing == 0)) else 0
            sc_path = file_service.save_named_script(pub_id, filename, content.encode())
            conn.execute(
                "INSERT OR REPLACE INTO publication_scripts "
                "(publication_id, filename, file_path, is_primary, uploaded_by, uploaded_at) "
                "VALUES (?,?,?,?,?,?)",
                (pub_id, filename, sc_path, is_primary, user["user_id"], _now())
            )
            if is_primary:
                conn.execute(
                    "UPDATE publications SET has_script=1, script_path=? WHERE id=?",
                    (sc_path, pub_id)
                )
        return _ok({"uploaded": filename, "is_primary": bool(is_primary), "publication_id": pub_id})

    # ── delete_script ─────────────────────────────────────────────────────────
    elif name == "delete_script":
        script_id = args["script_id"]
        with db() as conn:
            row = conn.execute("SELECT * FROM publication_scripts WHERE id=?", (script_id,)).fetchone()
            if not row:
                return _err("Script not found")
            pub_id = row["publication_id"]
            try:
                Path(row["file_path"]).unlink(missing_ok=True)
            except Exception:
                pass
            conn.execute("DELETE FROM publication_scripts WHERE id=?", (script_id,))
            if row["is_primary"]:
                nxt = conn.execute(
                    "SELECT * FROM publication_scripts WHERE publication_id=? ORDER BY uploaded_at LIMIT 1",
                    (pub_id,)
                ).fetchone()
                if nxt:
                    conn.execute("UPDATE publication_scripts SET is_primary=1 WHERE id=?", (nxt["id"],))
                    conn.execute("UPDATE publications SET script_path=?, has_script=1 WHERE id=?",
                                 (nxt["file_path"], pub_id))
                else:
                    conn.execute("UPDATE publications SET script_path=NULL, has_script=0 WHERE id=?", (pub_id,))
        return _ok({"deleted": script_id})

    # ── trigger_refresh ───────────────────────────────────────────────────────
    elif name == "trigger_refresh":
        pub_id = args["publication_id"]
        from services.refresh_service import run_refresh
        from concurrent.futures import ThreadPoolExecutor
        with db() as conn:
            pub = conn.execute("SELECT * FROM publications WHERE id=?", (pub_id,)).fetchone()
            if not pub:
                return _err("Publication not found")
            if not pub["has_script"]:
                return _err("No script attached to this publication")
            if pub["refresh_status"] == "running":
                return _err("Refresh already running")
            conn.execute("UPDATE publications SET refresh_status='running', refresh_log='' WHERE id=?", (pub_id,))
        executor = ThreadPoolExecutor(max_workers=1)
        executor.submit(run_refresh, pub_id)
        return _ok({"status": "running", "publication_id": pub_id})

    # ── get_refresh_status ────────────────────────────────────────────────────
    elif name == "get_refresh_status":
        pub_id = args["publication_id"]
        with db() as conn:
            pub = conn.execute(
                "SELECT refresh_status, last_refreshed_at, refresh_log FROM publications WHERE id=?",
                (pub_id,)
            ).fetchone()
            if not pub:
                return _err("Publication not found")
        return _ok(dict(pub))

    # ── get_schedule ──────────────────────────────────────────────────────────
    elif name == "get_schedule":
        pub_id = args["publication_id"]
        with db() as conn:
            row = conn.execute("SELECT * FROM schedules WHERE publication_id=?", (pub_id,)).fetchone()
        if not row:
            return _ok({"schedule": None, "publication_id": pub_id})
        return _ok(dict(row))

    # ── set_schedule ──────────────────────────────────────────────────────────
    elif name == "set_schedule":
        pub_id = args["publication_id"]
        cron = args["cron_expression"]
        from scheduler import scheduler as apscheduler
        from apscheduler.triggers.cron import CronTrigger
        from services.refresh_service import run_refresh
        fields = cron.split()
        if len(fields) != 5:
            return _err("cron_expression must have exactly 5 fields")
        with db() as conn:
            pub = conn.execute("SELECT * FROM publications WHERE id=?", (pub_id,)).fetchone()
            if not pub:
                return _err("Publication not found")
            existing = conn.execute("SELECT id FROM schedules WHERE publication_id=?", (pub_id,)).fetchone()
            now = _now()
            if existing:
                conn.execute(
                    "UPDATE schedules SET cron_expression=?, is_active=1, created_at=? WHERE publication_id=?",
                    (cron, now, pub_id)
                )
            else:
                conn.execute(
                    "INSERT INTO schedules (publication_id, cron_expression, is_active, created_by, created_at) VALUES (?,?,1,?,?)",
                    (pub_id, cron, user["user_id"], now)
                )
            job_id = f"pub_{pub_id}"
            try:
                apscheduler.remove_job(job_id)
            except Exception:
                pass
            apscheduler.add_job(run_refresh, CronTrigger.from_crontab(cron),
                                args=[pub_id], id=job_id, replace_existing=True)
        return _ok({"schedule_set": cron, "publication_id": pub_id})

    # ── delete_schedule ───────────────────────────────────────────────────────
    elif name == "delete_schedule":
        pub_id = args["publication_id"]
        from scheduler import scheduler as apscheduler
        with db() as conn:
            conn.execute("DELETE FROM schedules WHERE publication_id=?", (pub_id,))
        try:
            apscheduler.remove_job(f"pub_{pub_id}")
        except Exception:
            pass
        return _ok({"deleted": True, "publication_id": pub_id})

    # ── list_comments ─────────────────────────────────────────────────────────
    elif name == "list_comments":
        pub_id = args["publication_id"]
        with db() as conn:
            rows = conn.execute(
                "SELECT c.*, u.username as author FROM comments c LEFT JOIN users u ON u.id=c.author_id "
                "WHERE c.publication_id=? ORDER BY c.created_at",
                (pub_id,)
            ).fetchall()

        def thread(comments, parent_id=None):
            return [
                {**dict(c), "replies": thread(comments, c["id"])}
                for c in comments if c["parent_id"] == parent_id
            ]
        return _ok(thread(list(rows)))

    # ── post_comment ──────────────────────────────────────────────────────────
    elif name == "post_comment":
        pub_id = args["publication_id"]
        with db() as conn:
            pub = conn.execute("SELECT forum_id FROM publications WHERE id=?", (pub_id,)).fetchone()
            if not pub:
                return _err("Publication not found")
            _assert_forum_member(conn, pub["forum_id"], user)
            now = _now()
            cur = conn.execute(
                "INSERT INTO comments (publication_id, author_id, body, created_at, updated_at) VALUES (?,?,?,?,?)",
                (pub_id, user["user_id"], args["body"], now, now)
            )
        return _ok({"comment_id": cur.lastrowid})

    # ── reply_to_comment ──────────────────────────────────────────────────────
    elif name == "reply_to_comment":
        pub_id = args["publication_id"]
        with db() as conn:
            now = _now()
            cur = conn.execute(
                "INSERT INTO comments (publication_id, parent_id, author_id, body, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                (pub_id, args["comment_id"], user["user_id"], args["body"], now, now)
            )
        return _ok({"comment_id": cur.lastrowid})

    # ── delete_comment ────────────────────────────────────────────────────────
    elif name == "delete_comment":
        with db() as conn:
            conn.execute("DELETE FROM comments WHERE id=? AND author_id=?",
                         (args["comment_id"], user["user_id"]))
        return _ok({"deleted": args["comment_id"]})

    # ── create_topic ──────────────────────────────────────────────────────────
    elif name == "create_topic":
        forum_id = args["forum_id"]
        with db() as conn:
            _assert_forum_member(conn, forum_id, user)
            slug = re.sub(r"[^a-z0-9]+", "-", args["display_name"].lower()).strip("-")
            now = _now()
            cur = conn.execute(
                "INSERT INTO topics (forum_id, slug, display_name, description, created_by, created_at) VALUES (?,?,?,?,?,?)",
                (forum_id, slug, args["display_name"], args.get("description", ""), user["user_id"], now)
            )
        return _ok({"topic_id": cur.lastrowid, "slug": slug})

    # ── create_forum ──────────────────────────────────────────────────────────
    elif name == "create_forum":
        if not user.get("is_admin"):
            return _err("System admin required")
        with db() as conn:
            slug = re.sub(r"[^a-z0-9]+", "-", args["display_name"].lower()).strip("-")
            now = _now()
            cur = conn.execute(
                "INSERT INTO forums (slug, display_name, description, created_by, created_at) VALUES (?,?,?,?,?)",
                (slug, args["display_name"], args.get("description", ""), user["user_id"], now)
            )
            fid = cur.lastrowid
            conn.execute(
                "INSERT INTO forum_members (forum_id, user_id, role) VALUES (?,?,'admin')",
                (fid, user["user_id"])
            )
        return _ok({"forum_id": fid, "slug": slug})

    # ── add_forum_member ──────────────────────────────────────────────────────
    elif name == "add_forum_member":
        forum_id = args["forum_id"]
        with db() as conn:
            _assert_forum_admin(conn, forum_id, user)
            target = conn.execute("SELECT id FROM users WHERE username=?", (args["username"],)).fetchone()
            if not target:
                return _err(f"User '{args['username']}' not found")
            role = args.get("role", "member")
            conn.execute(
                "INSERT OR REPLACE INTO forum_members (forum_id, user_id, role) VALUES (?,?,?)",
                (forum_id, target["id"], role)
            )
        return _ok({"added": args["username"], "role": role})

    # ── remove_forum_member ───────────────────────────────────────────────────
    elif name == "remove_forum_member":
        forum_id = args["forum_id"]
        with db() as conn:
            _assert_forum_admin(conn, forum_id, user)
            conn.execute(
                "DELETE FROM forum_members WHERE forum_id=? AND user_id=?",
                (forum_id, args["user_id"])
            )
        return _ok({"removed": args["user_id"]})

    return _err(f"Unknown tool: {name}")


# ── FastAPI routes ────────────────────────────────────────────────────────────

@router.get("/mcp/sse")
async def mcp_sse_endpoint(request: Request, user=Depends(get_current_user)):
    """SSE stream — Claude Code connects here once and holds the connection."""
    token = _user_ctx.set(user)
    try:
        async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
            await mcp.run(streams[0], streams[1], mcp.create_initialization_options())
    finally:
        _user_ctx.reset(token)


@router.post("/mcp/messages/")
async def mcp_messages_endpoint(request: Request):
    """Client → server MCP messages (tool calls arrive here)."""
    await sse.handle_post_message(request.scope, request.receive, request._send)
