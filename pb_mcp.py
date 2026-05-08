#!/usr/bin/env python3
"""
pb_mcp.py — Full-capability MCP server for PulseBoard.

Gives Claude Code complete control over PulseBoard:
  • Browse  — forums, topics, publications, comments
  • Read    — dashboard HTML content, refresh script source
  • Publish — create/update/delete dashboards, attach scripts
  • Refresh — trigger manual refresh, poll status, manage cron schedules
  • Comment — post, reply, edit, delete comments
  • Manage  — create/rename/delete forums & topics, manage membership
  • Admin   — list/delete users (system admin only)

Configuration — set env vars in .mcp.json (preferred) or use ~/.pulseboard.json:
  PULSEBOARD_URL       http://localhost:8010
  PULSEBOARD_USERNAME  yourname
  PULSEBOARD_PASSWORD  yourpassword

Register in .mcp.json at your project root:
  {
    "mcpServers": {
      "pulseboard": {
        "command": "python",
        "args": ["C:/Users/Rishav Kumar/Desktop/RecursiveSeal/pb_mcp.py"],
        "env": {
          "PULSEBOARD_URL": "http://localhost:8010",
          "PULSEBOARD_USERNAME": "admin",
          "PULSEBOARD_PASSWORD": "yourpassword"
        }
      }
    }
  }
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp import types
except ImportError:
    print("mcp package not found. Install with: pip install mcp", file=sys.stderr)
    sys.exit(1)

# ── Config ────────────────────────────────────────────────────────────────────

def _load_config() -> dict:
    url = os.environ.get("PULSEBOARD_URL")
    username = os.environ.get("PULSEBOARD_USERNAME")
    password = os.environ.get("PULSEBOARD_PASSWORD")
    if url and username and password:
        return {"url": url.rstrip("/"), "username": username, "password": password}
    cfg_path = Path.home() / ".pulseboard.json"
    if cfg_path.exists():
        with open(cfg_path) as f:
            cfg = json.load(f)
            cfg["url"] = cfg["url"].rstrip("/")
            return cfg
    raise RuntimeError(
        "PulseBoard credentials not configured. "
        "Set PULSEBOARD_URL / _USERNAME / _PASSWORD env vars, "
        "or run: python pb_push.py configure"
    )

# ── HTTP layer ────────────────────────────────────────────────────────────────

def _get_token(cfg: dict) -> str:
    data = urllib.parse.urlencode(
        {"username": cfg["username"], "password": cfg["password"]}
    ).encode()
    req = urllib.request.Request(f"{cfg['url']}/api/token", data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())["access_token"]


def _req(method: str, url: str, token: str, body=None, content_type: str = "application/json") -> bytes:
    data = None
    if body is not None:
        if isinstance(body, bytes):
            data = body
        else:
            data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    if data:
        req.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(req) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} {method} {url}: {e.read().decode()}")


def _get(url: str, token: str):
    return json.loads(_req("GET", url, token))

def _get_text(url: str, token: str) -> str:
    return _req("GET", url, token).decode("utf-8", errors="replace")

def _post(url: str, token: str, body: dict):
    return json.loads(_req("POST", url, token, body))

def _put(url: str, token: str, body: dict):
    return json.loads(_req("PUT", url, token, body))

def _delete(url: str, token: str):
    return json.loads(_req("DELETE", url, token))

def _multipart(method: str, url: str, token: str, fields: dict, files: dict) -> dict:
    boundary = uuid.uuid4().hex
    body = b""
    for name, value in fields.items():
        body += (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n"
        ).encode()
    for name, (filename, file_bytes, mime) in files.items():
        body += (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
            f"Content-Type: {mime}\r\n\r\n"
        ).encode() + file_bytes + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    return json.loads(_req(method, url, token, body, f"multipart/form-data; boundary={boundary}"))

# ── Slug resolution helpers ───────────────────────────────────────────────────

def _forums(cfg, token) -> list:
    return _get(f"{cfg['url']}/api/forums", token)

def _forum_id(cfg, token, slug_or_id: str) -> tuple[int, str]:
    """Return (forum_id, forum_slug). Accepts numeric id or slug."""
    if str(slug_or_id).isdigit():
        forums = _forums(cfg, token)
        f = next((x for x in forums if x["id"] == int(slug_or_id)), None)
        if not f:
            raise ValueError(f"Forum id {slug_or_id} not found")
        return f["id"], f["slug"]
    forums = _forums(cfg, token)
    f = next((x for x in forums if x["slug"] == slug_or_id), None)
    if not f:
        slugs = [x["slug"] for x in forums]
        raise ValueError(f"Forum '{slug_or_id}' not found. Available: {slugs}")
    return f["id"], f["slug"]

def _topics(cfg, token, forum_id: int) -> list:
    return _get(f"{cfg['url']}/api/forums/{forum_id}/topics", token)

def _topic_id(cfg, token, forum_id: int, slug_or_id: str) -> int:
    if str(slug_or_id).isdigit():
        return int(slug_or_id)
    topics = _topics(cfg, token, forum_id)
    t = next((x for x in topics if x["slug"] == slug_or_id), None)
    if not t:
        slugs = [x["slug"] for x in topics]
        raise ValueError(f"Topic '{slug_or_id}' not found. Available: {slugs}")
    return t["id"]

def _pub_id(s) -> int:
    try:
        return int(s)
    except Exception:
        raise ValueError(f"publication_id must be an integer, got: {s!r}")

# ── Tool implementations ──────────────────────────────────────────────────────

# — Browse —

def do_list_forums(cfg, token, _args):
    forums = _forums(cfg, token)
    if not forums:
        return "No forums found."
    lines = [f"{'ID':<6} {'Slug':<32} Name"]
    lines.append("-" * 60)
    for f in forums:
        lines.append(f"{f['id']:<6} {f['slug']:<32} {f['display_name']}")
    return "\n".join(lines)


def do_list_topics(cfg, token, args):
    fid, fslug = _forum_id(cfg, token, args["forum"])
    topics = _topics(cfg, token, fid)
    if not topics:
        return f"No topics in forum '{fslug}'."
    lines = [f"{'ID':<6} {'Slug':<32} Name"]
    lines.append("-" * 60)
    for t in topics:
        lines.append(f"{t['id']:<6} {t['slug']:<32} {t['display_name']}")
    return "\n".join(lines)


def do_list_publications(cfg, token, args):
    fid, _ = _forum_id(cfg, token, args["forum"])
    tid = _topic_id(cfg, token, fid, args["topic"])
    pubs = _get(f"{cfg['url']}/api/topics/{tid}/publications", token)
    if not pubs:
        return "No publications in this topic."
    lines = [f"{'ID':<6} {'Type':<6} {'Status':<10} {'Has Script':<12} Title"]
    lines.append("-" * 70)
    for p in pubs:
        script = "yes" if p.get("has_script") else "no"
        lines.append(
            f"{p['id']:<6} {p['file_type']:<6} {p.get('refresh_status','idle'):<10} "
            f"{script:<12} {p['title']}"
        )
    return "\n".join(lines)


def do_get_publication(cfg, token, args):
    pub = _get(f"{cfg['url']}/api/publications/{_pub_id(args['publication_id'])}", token)
    fields = [
        ("ID", pub["id"]),
        ("Title", pub["title"]),
        ("Description", pub.get("description") or "(none)"),
        ("Author", pub.get("author", "?")),
        ("File type", pub["file_type"]),
        ("Has refresh script", "yes" if pub.get("has_script") else "no"),
        ("Refresh status", pub.get("refresh_status", "idle")),
        ("Last refreshed", pub.get("last_refreshed_at") or "never"),
        ("Created", pub.get("created_at", "?")),
        ("Updated", pub.get("updated_at", "?")),
    ]
    return "\n".join(f"{k:<22}: {v}" for k, v in fields)

# — Content reading —

def do_read_dashboard(cfg, token, args):
    pid = _pub_id(args["publication_id"])
    pub = _get(f"{cfg['url']}/api/publications/{pid}", token)
    if pub["file_type"] == "docx":
        return (
            "This publication is a DOCX file (binary). "
            "Use the download_file tool to save it locally, then open it."
        )
    content = _get_text(f"{cfg['url']}/api/publications/{pid}/file", token)
    max_chars = int(args.get("max_chars", 50000))
    if len(content) > max_chars:
        return (
            f"[Dashboard HTML — showing first {max_chars} of {len(content)} chars]\n\n"
            + content[:max_chars]
            + "\n\n[... truncated. Call again with higher max_chars if needed ...]"
        )
    return f"[Dashboard HTML — {len(content)} chars]\n\n{content}"


def do_read_script(cfg, token, args):
    pid = _pub_id(args["publication_id"])
    source = _get_text(f"{cfg['url']}/api/publications/{pid}/script/source", token)
    return f"[Refresh script source — {len(source)} chars]\n\n```python\n{source}\n```"

# — Publishing —

def do_publish_dashboard(cfg, token, args):
    fid, _ = _forum_id(cfg, token, args["forum"])
    tid = _topic_id(cfg, token, fid, args["topic"])
    fp = Path(args["file_path"])
    if not fp.exists():
        raise FileNotFoundError(f"File not found: {fp}")
    mime = "text/html" if fp.suffix.lower() == ".html" else \
           "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    pub = _multipart(
        "POST", f"{cfg['url']}/api/topics/{tid}/publications", token,
        fields={"title": args["title"], "description": args.get("description", "")},
        files={"file": (fp.name, fp.read_bytes(), mime)},
    )
    pid = pub["id"]
    if args.get("script_path"):
        sp = Path(args["script_path"])
        if not sp.exists():
            raise FileNotFoundError(f"Script not found: {sp}")
        _multipart(
            "POST", f"{cfg['url']}/api/publications/{pid}/script", token,
            fields={},
            files={"file": (sp.name, sp.read_bytes(), "text/x-python")},
        )
    view_url = f"{cfg['url']}/publications/{pid}"
    return (
        f"✓ Published: {args['title']}\n"
        f"  Publication ID : {pid}\n"
        f"  File type      : {pub.get('file_type', fp.suffix.lstrip('.'))}\n"
        f"  Script attached: {'yes' if args.get('script_path') else 'no'}\n"
        f"  URL            : {view_url}"
    )


def do_update_dashboard_file(cfg, token, args):
    pid = _pub_id(args["publication_id"])
    fp = Path(args["file_path"])
    if not fp.exists():
        raise FileNotFoundError(f"File not found: {fp}")
    mime = "text/html" if fp.suffix.lower() == ".html" else \
           "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    _multipart(
        "POST", f"{cfg['url']}/api/publications/{pid}/file", token,
        fields={},
        files={"file": (fp.name, fp.read_bytes(), mime)},
    )
    return f"✓ Publication {pid} file replaced with '{fp.name}'."


def do_update_publication_metadata(cfg, token, args):
    pid = _pub_id(args["publication_id"])
    _put(f"{cfg['url']}/api/publications/{pid}", token,
         {"title": args["title"], "description": args.get("description", "")})
    return f"✓ Publication {pid} metadata updated."


def do_delete_publication(cfg, token, args):
    pid = _pub_id(args["publication_id"])
    _delete(f"{cfg['url']}/api/publications/{pid}", token)
    return f"✓ Publication {pid} deleted."


def do_attach_script(cfg, token, args):
    pid = _pub_id(args["publication_id"])
    sp = Path(args["script_path"])
    if not sp.exists():
        raise FileNotFoundError(f"Script not found: {sp}")
    _multipart(
        "POST", f"{cfg['url']}/api/publications/{pid}/script", token,
        fields={},
        files={"file": (sp.name, sp.read_bytes(), "text/x-python")},
    )
    return f"✓ Refresh script '{sp.name}' attached to publication {pid}."

# — Refresh —

def do_trigger_refresh(cfg, token, args):
    pid = _pub_id(args["publication_id"])
    _post(f"{cfg['url']}/api/publications/{pid}/refresh", token, {})
    return f"✓ Refresh started for publication {pid}. Use get_refresh_status to poll."


def do_get_refresh_status(cfg, token, args):
    pid = _pub_id(args["publication_id"])
    s = _get(f"{cfg['url']}/api/publications/{pid}/refresh/status", token)
    return (
        f"Publication {pid} refresh status: {s.get('refresh_status', 'unknown')}\n"
        f"Last refreshed : {s.get('last_refreshed_at') or 'never'}\n"
        f"Log:\n{s.get('refresh_log') or '(empty)'}"
    )

# — Schedules —

def do_get_schedule(cfg, token, args):
    pid = _pub_id(args["publication_id"])
    s = _get(f"{cfg['url']}/api/publications/{pid}/schedule", token)
    if not s:
        return f"No schedule set for publication {pid}."
    active = "active" if s.get("is_active") else "paused"
    return (
        f"Schedule for publication {pid}:\n"
        f"  Cron       : {s['cron_expression']}\n"
        f"  Status     : {active}\n"
        f"  Next run   : {s.get('next_run_at') or 'unknown'}\n"
        f"  Created    : {s.get('created_at', '?')}"
    )


def do_set_schedule(cfg, token, args):
    pid = _pub_id(args["publication_id"])
    cron = args["cron_expression"]
    _post(f"{cfg['url']}/api/publications/{pid}/schedule", token,
          {"cron_expression": cron})
    return f"✓ Schedule set for publication {pid}: '{cron}' (UTC)."


def do_toggle_schedule(cfg, token, args):
    pid = _pub_id(args["publication_id"])
    active = bool(args["is_active"])
    _put(f"{cfg['url']}/api/publications/{pid}/schedule", token, {"is_active": active})
    state = "resumed" if active else "paused"
    return f"✓ Schedule for publication {pid} {state}."


def do_delete_schedule(cfg, token, args):
    pid = _pub_id(args["publication_id"])
    _delete(f"{cfg['url']}/api/publications/{pid}/schedule", token)
    return f"✓ Schedule for publication {pid} removed."

# — Comments —

def _render_thread(comments: list, parent_id=None, indent=0) -> list:
    lines = []
    children = [c for c in comments if c.get("parent_id") == parent_id]
    for c in children:
        prefix = "  " * indent
        lines.append(
            f"{prefix}[#{c['id']}] {c['author']}  ({c['created_at']})\n"
            f"{prefix}{c['body']}"
        )
        lines.extend(_render_thread(comments, c["id"], indent + 1))
    return lines


def do_list_comments(cfg, token, args):
    pid = _pub_id(args["publication_id"])
    comments = _get(f"{cfg['url']}/api/publications/{pid}/comments", token)
    if not comments:
        return f"No comments on publication {pid}."
    lines = [f"Comments on publication {pid} ({len(comments)} total):", ""]
    lines.extend(_render_thread(comments, parent_id=None))
    return "\n".join(lines)


def do_post_comment(cfg, token, args):
    pid = _pub_id(args["publication_id"])
    r = _post(f"{cfg['url']}/api/publications/{pid}/comments", token,
              {"body": args["body"]})
    return f"✓ Comment #{r['id']} posted on publication {pid}."


def do_reply_to_comment(cfg, token, args):
    pid = _pub_id(args["publication_id"])
    cid = int(args["comment_id"])
    r = _post(f"{cfg['url']}/api/publications/{pid}/comments/{cid}/reply", token,
              {"body": args["body"]})
    return f"✓ Reply #{r['id']} posted to comment #{cid}."


def do_edit_comment(cfg, token, args):
    cid = int(args["comment_id"])
    _put(f"{cfg['url']}/api/comments/{cid}", token, {"body": args["body"]})
    return f"✓ Comment #{cid} updated."


def do_delete_comment(cfg, token, args):
    cid = int(args["comment_id"])
    _delete(f"{cfg['url']}/api/comments/{cid}", token)
    return f"✓ Comment #{cid} deleted."

# — Forum management —

def do_create_forum(cfg, token, args):
    r = _post(f"{cfg['url']}/api/forums", token,
              {"display_name": args["display_name"], "description": args.get("description", "")})
    return f"✓ Forum created: '{r['display_name']}' (slug: {r['slug']}, id: {r['id']})."


def do_update_forum(cfg, token, args):
    fid, _ = _forum_id(cfg, token, args["forum"])
    _put(f"{cfg['url']}/api/forums/{fid}", token,
         {"display_name": args["display_name"], "description": args.get("description", "")})
    return f"✓ Forum {fid} updated."


def do_delete_forum(cfg, token, args):
    fid, fslug = _forum_id(cfg, token, args["forum"])
    _delete(f"{cfg['url']}/api/forums/{fid}", token)
    return f"✓ Forum '{fslug}' (id {fid}) deleted."


def do_create_topic(cfg, token, args):
    fid, _ = _forum_id(cfg, token, args["forum"])
    r = _post(f"{cfg['url']}/api/forums/{fid}/topics", token,
              {"display_name": args["display_name"], "description": args.get("description", "")})
    return f"✓ Topic created: '{r['display_name']}' (slug: {r['slug']}, id: {r['id']})."


def do_update_topic(cfg, token, args):
    fid, _ = _forum_id(cfg, token, args["forum"])
    tid = _topic_id(cfg, token, fid, args["topic"])
    _put(f"{cfg['url']}/api/topics/{tid}", token,
         {"display_name": args["display_name"], "description": args.get("description", "")})
    return f"✓ Topic {tid} updated."


def do_delete_topic(cfg, token, args):
    fid, _ = _forum_id(cfg, token, args["forum"])
    tid = _topic_id(cfg, token, fid, args["topic"])
    _delete(f"{cfg['url']}/api/topics/{tid}", token)
    return f"✓ Topic {tid} deleted."

# — Access management —

def do_list_members(cfg, token, args):
    fid, fslug = _forum_id(cfg, token, args["forum"])
    members = _get(f"{cfg['url']}/api/forums/{fid}/members", token)
    if not members:
        return f"No members in forum '{fslug}'."
    lines = [f"{'User ID':<10} {'Username':<24} Role"]
    lines.append("-" * 44)
    for m in members:
        lines.append(f"{m['user_id']:<10} {m['username']:<24} {m['role']}")
    return "\n".join(lines)


def do_add_member(cfg, token, args):
    fid, fslug = _forum_id(cfg, token, args["forum"])
    role = args.get("role", "member")
    _post(f"{cfg['url']}/api/forums/{fid}/members", token,
          {"username": args["username"], "role": role})
    return f"✓ '{args['username']}' added to forum '{fslug}' as {role}."


def do_remove_member(cfg, token, args):
    fid, fslug = _forum_id(cfg, token, args["forum"])
    # Resolve username → user_id via members list
    members = _get(f"{cfg['url']}/api/forums/{fid}/members", token)
    target_id = None
    username = args.get("username")
    user_id = args.get("user_id")
    if username:
        m = next((x for x in members if x["username"] == username), None)
        if not m:
            raise ValueError(f"'{username}' is not a member of forum '{fslug}'")
        target_id = m["user_id"]
    elif user_id:
        target_id = int(user_id)
    else:
        raise ValueError("Provide either username or user_id")
    _delete(f"{cfg['url']}/api/forums/{fid}/members/{target_id}", token)
    return f"✓ User removed from forum '{fslug}'."

# — Admin —

def do_list_users(cfg, token, _args):
    users = _get(f"{cfg['url']}/api/admin/users", token)
    if not users:
        return "No users found."
    lines = [f"{'ID':<8} {'Username':<24} {'Admin':<8} Created"]
    lines.append("-" * 60)
    for u in users:
        admin = "yes" if u.get("is_admin") else "no"
        lines.append(f"{u['id']:<8} {u['username']:<24} {admin:<8} {u.get('created_at', '?')}")
    return "\n".join(lines)


def do_delete_user(cfg, token, args):
    uid = int(args["user_id"])
    _delete(f"{cfg['url']}/api/admin/users/{uid}", token)
    return f"✓ User {uid} deleted."

# ── Tool registry ─────────────────────────────────────────────────────────────

TOOLS = {
    # Browse & discovery
    "list_forums": {
        "fn": do_list_forums,
        "description": "List all PulseBoard forums the current user has access to.",
        "schema": {"type": "object", "properties": {}},
    },
    "list_topics": {
        "fn": do_list_topics,
        "description": "List all topics (sub-sections) in a PulseBoard forum.",
        "schema": {
            "type": "object", "required": ["forum"],
            "properties": {
                "forum": {"type": "string", "description": "Forum slug or numeric ID (e.g. 'spinny-flex')"},
            },
        },
    },
    "list_publications": {
        "fn": do_list_publications,
        "description": "List all publications in a topic. Shows ID, type, refresh status, and title.",
        "schema": {
            "type": "object", "required": ["forum", "topic"],
            "properties": {
                "forum": {"type": "string", "description": "Forum slug or ID"},
                "topic": {"type": "string", "description": "Topic slug or ID"},
            },
        },
    },
    "get_publication": {
        "fn": do_get_publication,
        "description": "Get full metadata for a single publication (title, author, type, refresh status, timestamps).",
        "schema": {
            "type": "object", "required": ["publication_id"],
            "properties": {
                "publication_id": {"type": "integer", "description": "Numeric publication ID"},
            },
        },
    },

    # Content reading
    "read_dashboard": {
        "fn": do_read_dashboard,
        "description": (
            "Read and return the full HTML content of a publication so you can analyze, "
            "summarize, or describe it. DOCX files cannot be read inline — use the download URL instead."
        ),
        "schema": {
            "type": "object", "required": ["publication_id"],
            "properties": {
                "publication_id": {"type": "integer", "description": "Numeric publication ID"},
                "max_chars": {
                    "type": "integer",
                    "description": "Max characters to return (default 50000). Increase for large dashboards.",
                    "default": 50000,
                },
            },
        },
    },
    "read_script": {
        "fn": do_read_script,
        "description": (
            "Read the Python refresh script source code attached to a publication. "
            "Use this to understand how the dashboard is generated, review data queries, or suggest improvements."
        ),
        "schema": {
            "type": "object", "required": ["publication_id"],
            "properties": {
                "publication_id": {"type": "integer", "description": "Numeric publication ID"},
            },
        },
    },

    # Publishing
    "publish_dashboard": {
        "fn": do_publish_dashboard,
        "description": (
            "Upload a new HTML or DOCX publication to a forum topic. "
            "Optionally attach a Python refresh script. Returns the publication URL."
        ),
        "schema": {
            "type": "object", "required": ["forum", "topic", "title", "file_path"],
            "properties": {
                "forum": {"type": "string", "description": "Forum slug or ID"},
                "topic": {"type": "string", "description": "Topic slug or ID"},
                "title": {"type": "string", "description": "Publication title"},
                "description": {"type": "string", "description": "Optional description"},
                "file_path": {"type": "string", "description": "Absolute path to the .html or .docx file"},
                "script_path": {"type": "string", "description": "Optional absolute path to a Python refresh script"},
            },
        },
    },
    "update_dashboard_file": {
        "fn": do_update_dashboard_file,
        "description": (
            "Replace the content file of an existing publication with a new version. "
            "Use this when you've regenerated a dashboard and want to push the update."
        ),
        "schema": {
            "type": "object", "required": ["publication_id", "file_path"],
            "properties": {
                "publication_id": {"type": "integer", "description": "Numeric publication ID"},
                "file_path": {"type": "string", "description": "Absolute path to the new .html or .docx file"},
            },
        },
    },
    "update_publication_metadata": {
        "fn": do_update_publication_metadata,
        "description": "Update the title and/or description of a publication without changing the file.",
        "schema": {
            "type": "object", "required": ["publication_id", "title"],
            "properties": {
                "publication_id": {"type": "integer", "description": "Numeric publication ID"},
                "title": {"type": "string"},
                "description": {"type": "string"},
            },
        },
    },
    "delete_publication": {
        "fn": do_delete_publication,
        "description": "Permanently delete a publication and its files.",
        "schema": {
            "type": "object", "required": ["publication_id"],
            "properties": {
                "publication_id": {"type": "integer"},
            },
        },
    },
    "attach_script": {
        "fn": do_attach_script,
        "description": (
            "Attach or replace the Python refresh script on an existing publication. "
            "The script must write output to os.environ['RS_OUTPUT_PATH']."
        ),
        "schema": {
            "type": "object", "required": ["publication_id", "script_path"],
            "properties": {
                "publication_id": {"type": "integer"},
                "script_path": {"type": "string", "description": "Absolute path to the .py script"},
            },
        },
    },

    # Refresh
    "trigger_refresh": {
        "fn": do_trigger_refresh,
        "description": "Manually trigger a data refresh for a publication that has a script attached.",
        "schema": {
            "type": "object", "required": ["publication_id"],
            "properties": {
                "publication_id": {"type": "integer"},
            },
        },
    },
    "get_refresh_status": {
        "fn": do_get_refresh_status,
        "description": "Poll the refresh status (idle/running/success/error) and log of a publication.",
        "schema": {
            "type": "object", "required": ["publication_id"],
            "properties": {
                "publication_id": {"type": "integer"},
            },
        },
    },

    # Schedules
    "get_schedule": {
        "fn": do_get_schedule,
        "description": "Get the current cron refresh schedule for a publication.",
        "schema": {
            "type": "object", "required": ["publication_id"],
            "properties": {"publication_id": {"type": "integer"}},
        },
    },
    "set_schedule": {
        "fn": do_set_schedule,
        "description": (
            "Create or update the cron refresh schedule for a publication. "
            "Use standard 5-field cron syntax in UTC (e.g. '0 6 * * 1-5' = 6am UTC Mon–Fri)."
        ),
        "schema": {
            "type": "object", "required": ["publication_id", "cron_expression"],
            "properties": {
                "publication_id": {"type": "integer"},
                "cron_expression": {
                    "type": "string",
                    "description": "5-field cron in UTC, e.g. '*/30 * * * *' for every 30 min",
                },
            },
        },
    },
    "toggle_schedule": {
        "fn": do_toggle_schedule,
        "description": "Pause or resume an existing schedule without deleting it.",
        "schema": {
            "type": "object", "required": ["publication_id", "is_active"],
            "properties": {
                "publication_id": {"type": "integer"},
                "is_active": {"type": "boolean", "description": "true to resume, false to pause"},
            },
        },
    },
    "delete_schedule": {
        "fn": do_delete_schedule,
        "description": "Remove the cron schedule from a publication entirely.",
        "schema": {
            "type": "object", "required": ["publication_id"],
            "properties": {"publication_id": {"type": "integer"}},
        },
    },

    # Comments
    "list_comments": {
        "fn": do_list_comments,
        "description": "Read all comments and replies on a publication, rendered as a threaded tree.",
        "schema": {
            "type": "object", "required": ["publication_id"],
            "properties": {"publication_id": {"type": "integer"}},
        },
    },
    "post_comment": {
        "fn": do_post_comment,
        "description": "Post a top-level comment on a publication.",
        "schema": {
            "type": "object", "required": ["publication_id", "body"],
            "properties": {
                "publication_id": {"type": "integer"},
                "body": {"type": "string", "description": "Comment text (markdown supported)"},
            },
        },
    },
    "reply_to_comment": {
        "fn": do_reply_to_comment,
        "description": "Reply to an existing comment on a publication.",
        "schema": {
            "type": "object", "required": ["publication_id", "comment_id", "body"],
            "properties": {
                "publication_id": {"type": "integer"},
                "comment_id": {"type": "integer", "description": "ID of the comment to reply to"},
                "body": {"type": "string"},
            },
        },
    },
    "edit_comment": {
        "fn": do_edit_comment,
        "description": "Edit the text of a comment you authored.",
        "schema": {
            "type": "object", "required": ["comment_id", "body"],
            "properties": {
                "comment_id": {"type": "integer"},
                "body": {"type": "string"},
            },
        },
    },
    "delete_comment": {
        "fn": do_delete_comment,
        "description": "Delete a comment.",
        "schema": {
            "type": "object", "required": ["comment_id"],
            "properties": {"comment_id": {"type": "integer"}},
        },
    },

    # Forum management
    "create_forum": {
        "fn": do_create_forum,
        "description": "Create a new forum. Requires system admin credentials.",
        "schema": {
            "type": "object", "required": ["display_name"],
            "properties": {
                "display_name": {"type": "string", "description": "Forum display name (slug auto-generated)"},
                "description": {"type": "string"},
            },
        },
    },
    "update_forum": {
        "fn": do_update_forum,
        "description": "Update the display name and/or description of a forum.",
        "schema": {
            "type": "object", "required": ["forum", "display_name"],
            "properties": {
                "forum": {"type": "string", "description": "Forum slug or ID"},
                "display_name": {"type": "string"},
                "description": {"type": "string"},
            },
        },
    },
    "delete_forum": {
        "fn": do_delete_forum,
        "description": "Delete a forum and all its content. Requires system admin.",
        "schema": {
            "type": "object", "required": ["forum"],
            "properties": {"forum": {"type": "string"}},
        },
    },
    "create_topic": {
        "fn": do_create_topic,
        "description": "Create a new topic (sub-section) inside a forum.",
        "schema": {
            "type": "object", "required": ["forum", "display_name"],
            "properties": {
                "forum": {"type": "string"},
                "display_name": {"type": "string"},
                "description": {"type": "string"},
            },
        },
    },
    "update_topic": {
        "fn": do_update_topic,
        "description": "Rename or redescribe an existing topic.",
        "schema": {
            "type": "object", "required": ["forum", "topic", "display_name"],
            "properties": {
                "forum": {"type": "string"},
                "topic": {"type": "string"},
                "display_name": {"type": "string"},
                "description": {"type": "string"},
            },
        },
    },
    "delete_topic": {
        "fn": do_delete_topic,
        "description": "Delete a topic. The topic must be empty (no publications) first.",
        "schema": {
            "type": "object", "required": ["forum", "topic"],
            "properties": {
                "forum": {"type": "string"},
                "topic": {"type": "string"},
            },
        },
    },

    # Access management
    "list_forum_members": {
        "fn": do_list_members,
        "description": "List all members of a forum and their roles (member/admin). Requires forum admin.",
        "schema": {
            "type": "object", "required": ["forum"],
            "properties": {"forum": {"type": "string"}},
        },
    },
    "add_forum_member": {
        "fn": do_add_member,
        "description": "Add a user to a forum. Requires forum admin.",
        "schema": {
            "type": "object", "required": ["forum", "username"],
            "properties": {
                "forum": {"type": "string"},
                "username": {"type": "string"},
                "role": {
                    "type": "string",
                    "enum": ["member", "admin"],
                    "description": "Default: member",
                },
            },
        },
    },
    "remove_forum_member": {
        "fn": do_remove_member,
        "description": "Remove a user from a forum. Requires forum admin.",
        "schema": {
            "type": "object", "required": ["forum"],
            "properties": {
                "forum": {"type": "string"},
                "username": {"type": "string", "description": "Identify by username..."},
                "user_id": {"type": "integer", "description": "...or by numeric user_id"},
            },
        },
    },

    # Admin
    "list_all_users": {
        "fn": do_list_users,
        "description": "List every user registered on PulseBoard. Requires system admin.",
        "schema": {"type": "object", "properties": {}},
    },
    "delete_user": {
        "fn": do_delete_user,
        "description": "Delete a user account from PulseBoard. Requires system admin.",
        "schema": {
            "type": "object", "required": ["user_id"],
            "properties": {"user_id": {"type": "integer"}},
        },
    },
}

# ── MCP server ────────────────────────────────────────────────────────────────

server = Server("pulseboard")


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name=name,
            description=defn["description"],
            inputSchema=defn["schema"],
        )
        for name, defn in TOOLS.items()
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    if name not in TOOLS:
        text = f"Unknown tool: {name}. Available: {list(TOOLS.keys())}"
    else:
        try:
            cfg = _load_config()
            token = _get_token(cfg)
            text = TOOLS[name]["fn"](cfg, token, arguments)
        except Exception as e:
            text = f"Error: {e}"
    return [types.TextContent(type="text", text=str(text))]


async def _main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )

if __name__ == "__main__":
    import asyncio
    asyncio.run(_main())
