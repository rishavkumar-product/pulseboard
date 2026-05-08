#!/usr/bin/env python3
"""
pb_push.py — Push a dashboard/report into a PulseBoard forum from the CLI.

Usage:
    python pb_push.py --forum spinny-flex --topic procurement-funnel \
                      --title "CEP Heatmap Apr" --file cep_heatmap.html

    # With an attached refresh script:
    python pb_push.py --forum spinny-flex --topic procurement-funnel \
                      --title "CEP Heatmap" --file cep_heatmap.html \
                      --script build_heatmap.py

Configuration (first-time setup):
    python pb_push.py --configure

    This stores credentials in ~/.pulseboard.json:
    {
        "url": "http://localhost:8010",
        "username": "rishav",
        "password": "yourpassword"
    }
    The token is fetched fresh on each push (no expiry management needed).
"""

import argparse
import json
import os
import sys
from pathlib import Path

CONFIG_PATH = Path.home() / ".pulseboard.json"

# ── helpers ──────────────────────────────────────────────────────────────────

def load_config() -> dict:
    if not CONFIG_PATH.exists():
        print(f"No config found at {CONFIG_PATH}. Run: python pb_push.py --configure")
        sys.exit(1)
    with open(CONFIG_PATH) as f:
        return json.load(f)

def save_config(cfg: dict):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)
    print(f"Config saved to {CONFIG_PATH}")

def get_token(base_url: str, username: str, password: str) -> str:
    """Authenticate and return a Bearer token."""
    try:
        import urllib.request, urllib.parse, urllib.error
    except ImportError:
        pass

    url = f"{base_url.rstrip('/')}/api/token"
    data = urllib.parse.urlencode({"username": username, "password": password}).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req) as resp:
            body = json.loads(resp.read())
            return body["access_token"]
    except urllib.error.HTTPError as e:
        print(f"Auth failed ({e.code}): {e.read().decode()}")
        sys.exit(1)
    except Exception as e:
        print(f"Cannot reach PulseBoard at {base_url}: {e}")
        sys.exit(1)

def resolve_forum_id(base_url: str, token: str, forum_slug: str) -> int:
    """Return the numeric forum id for a slug."""
    import urllib.request, urllib.error
    url = f"{base_url.rstrip('/')}/api/forums"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req) as resp:
        forums = json.loads(resp.read())
    for f in forums:
        if f["slug"] == forum_slug:
            return f["id"]
    slugs = [f["slug"] for f in forums]
    print(f"Forum '{forum_slug}' not found. Available: {slugs}")
    sys.exit(1)

def resolve_topic_id(base_url: str, token: str, forum_id: int, topic_slug: str) -> int:
    """Return the numeric topic id for a slug within a forum."""
    import urllib.request, urllib.error
    url = f"{base_url.rstrip('/')}/api/forums/{forum_id}/topics"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req) as resp:
        topics = json.loads(resp.read())
    for t in topics:
        if t["slug"] == topic_slug:
            return t["id"]
    slugs = [t["slug"] for t in topics]
    print(f"Topic '{topic_slug}' not found in forum. Available: {slugs}")
    sys.exit(1)

def upload_publication(base_url: str, token: str, topic_id: int,
                        title: str, description: str, file_path: Path) -> dict:
    """Upload a new publication and return the created publication dict."""
    import urllib.request, urllib.error
    import mimetypes, uuid

    url = f"{base_url.rstrip('/')}/api/topics/{topic_id}/publications"
    boundary = uuid.uuid4().hex

    file_bytes = file_path.read_bytes()
    mime_type = "text/html" if file_path.suffix.lower() == ".html" else \
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    body_parts = []
    for name, value in [("title", title), ("description", description or "")]:
        body_parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n"
        )
    body_parts.append(
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"\r\n'
        f"Content-Type: {mime_type}\r\n\r\n"
    )
    body = b"".join(p.encode() for p in body_parts) + file_bytes + f"\r\n--{boundary}--\r\n".encode()

    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print(f"Upload failed ({e.code}): {e.read().decode()}")
        sys.exit(1)

def attach_script(base_url: str, token: str, pub_id: int, script_path: Path):
    """Attach a Python refresh script to an existing publication."""
    import urllib.request, urllib.error
    import uuid

    url = f"{base_url.rstrip('/')}/api/publications/{pub_id}/script"
    boundary = uuid.uuid4().hex
    script_bytes = script_path.read_bytes()

    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="script"; filename="{script_path.name}"\r\n'
        f"Content-Type: text/x-python\r\n\r\n"
    ).encode() + script_bytes + f"\r\n--{boundary}--\r\n".encode()

    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print(f"Script attach failed ({e.code}): {e.read().decode()}")
        sys.exit(1)

# ── configure ────────────────────────────────────────────────────────────────

def cmd_configure():
    print("PulseBoard CLI setup")
    print("--------------------")
    url = input("PulseBoard URL (e.g. http://localhost:8010): ").strip().rstrip("/")
    username = input("Username: ").strip()
    password = input("Password: ").strip()
    # Quick auth test
    print("Testing connection...")
    get_token(url, username, password)
    save_config({"url": url, "username": username, "password": password})
    print("[OK] Connection successful. You're ready to push dashboards.")

# ── push ─────────────────────────────────────────────────────────────────────

def cmd_push(args):
    cfg = load_config()
    base_url = cfg["url"]

    print(f"Authenticating as {cfg['username']}...")
    token = get_token(base_url, cfg["username"], cfg["password"])

    print(f"Resolving forum '{args.forum}'...")
    forum_id = resolve_forum_id(base_url, token, args.forum)

    print(f"Resolving topic '{args.topic}'...")
    topic_id = resolve_topic_id(base_url, token, forum_id, args.topic)

    file_path = Path(args.file)
    if not file_path.exists():
        print(f"File not found: {file_path}")
        sys.exit(1)

    print(f"Uploading '{file_path.name}'...")
    pub = upload_publication(base_url, token, topic_id,
                              args.title, args.description or "", file_path)
    pub_id = pub["id"]
    pub_url = f"{base_url}/publications/{pub_id}"
    print(f"[OK] Published: {pub_url}")

    if args.script:
        script_path = Path(args.script)
        if not script_path.exists():
            print(f"Script not found: {script_path}")
            sys.exit(1)
        print(f"Attaching refresh script '{script_path.name}'...")
        attach_script(base_url, token, pub_id, script_path)
        print("[OK] Refresh script attached.")

    print(f"\nDone! View at: {pub_url}")

# ── list helpers ─────────────────────────────────────────────────────────────

def cmd_list_forums(args):
    cfg = load_config()
    token = get_token(cfg["url"], cfg["username"], cfg["password"])
    import urllib.request
    url = f"{cfg['url'].rstrip('/')}/api/forums"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req) as resp:
        forums = json.loads(resp.read())
    print(f"{'ID':<6} {'Slug':<30} Name")
    print("-" * 60)
    for f in forums:
        print(f"{f['id']:<6} {f['slug']:<30} {f['display_name']}")

def cmd_list_topics(args):
    cfg = load_config()
    token = get_token(cfg["url"], cfg["username"], cfg["password"])
    import urllib.request
    forum_id = resolve_forum_id(cfg["url"], token, args.forum)
    url = f"{cfg['url'].rstrip('/')}/api/forums/{forum_id}/topics"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req) as resp:
        topics = json.loads(resp.read())
    print(f"Topics in '{args.forum}':")
    print(f"{'ID':<6} {'Slug':<30} Name")
    print("-" * 60)
    for t in topics:
        print(f"{t['id']:<6} {t['slug']:<30} {t['display_name']}")

# ── main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Push dashboards and reports into PulseBoard forums.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="cmd")

    # configure
    sub.add_parser("configure", help="Save PulseBoard URL and credentials to ~/.pulseboard.json")

    # push
    p_push = sub.add_parser("push", help="Upload a file to a forum topic")
    p_push.add_argument("--forum", required=True, help="Forum slug (e.g. spinny-flex)")
    p_push.add_argument("--topic", required=True, help="Topic slug (e.g. procurement-funnel)")
    p_push.add_argument("--title", required=True, help="Publication title")
    p_push.add_argument("--description", default="", help="Optional description")
    p_push.add_argument("--file", required=True, help="Path to .html or .docx file")
    p_push.add_argument("--script", default=None, help="Optional Python refresh script to attach")

    # list-forums
    sub.add_parser("list-forums", help="List all forums you have access to")

    # list-topics
    p_lt = sub.add_parser("list-topics", help="List topics in a forum")
    p_lt.add_argument("--forum", required=True, help="Forum slug")

    # top-level --configure shortcut (legacy)
    parser.add_argument("--configure", action="store_true", help=argparse.SUPPRESS)

    args = parser.parse_args()

    if args.configure or args.cmd == "configure":
        cmd_configure()
    elif args.cmd == "push":
        cmd_push(args)
    elif args.cmd == "list-forums":
        cmd_list_forums(args)
    elif args.cmd == "list-topics":
        cmd_list_topics(args)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
