from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel
from pathlib import Path
from auth import get_current_user
from database import db
from services import file_service
from datetime import datetime, timezone

router = APIRouter(prefix="/api")


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _assert_member(forum_id: int, user: dict):
    if user.get("is_admin"):
        return
    with db() as conn:
        row = conn.execute(
            "SELECT 1 FROM forum_members WHERE forum_id=? AND user_id=?",
            (forum_id, user["user_id"])
        ).fetchone()
    if not row:
        raise HTTPException(403, "Not a forum member")


class PubUpdate(BaseModel):
    title: str
    description: str = ""


@router.get("/topics/{topic_id}/publications")
def list_publications(topic_id: int, user=Depends(get_current_user)):
    with db() as conn:
        topic = conn.execute("SELECT * FROM topics WHERE id=?", (topic_id,)).fetchone()
        if not topic:
            raise HTTPException(404, "Topic not found")
        _assert_member(topic["forum_id"], user)
        rows = conn.execute(
            "SELECT p.*, u.username as author FROM publications p LEFT JOIN users u ON u.id=p.created_by "
            "WHERE p.topic_id=? ORDER BY p.updated_at DESC", (topic_id,)
        ).fetchall()
    return [dict(r) for r in rows]


@router.post("/topics/{topic_id}/publications")
async def upload_publication(
    topic_id: int,
    title: str = Form(...),
    description: str = Form(""),
    file: UploadFile = File(...),
    user=Depends(get_current_user),
):
    with db() as conn:
        topic = conn.execute("SELECT * FROM topics WHERE id=?", (topic_id,)).fetchone()
        if not topic:
            raise HTTPException(404, "Topic not found")
        _assert_member(topic["forum_id"], user)
        cur = conn.execute(
            "INSERT INTO publications (forum_id, topic_id, title, description, file_type, file_path, created_by) "
            "VALUES (?,?,?,?,?,?,?)",
            (topic["forum_id"], topic_id, title, description, "html", "pending", user["user_id"])
        )
        pub_id = cur.lastrowid

    try:
        file_path, file_type = await file_service.save_upload(pub_id, file)
    except ValueError as e:
        with db() as conn:
            conn.execute("DELETE FROM publications WHERE id=?", (pub_id,))
        raise HTTPException(400, str(e))

    with db() as conn:
        conn.execute(
            "UPDATE publications SET file_path=?, file_type=? WHERE id=?",
            (file_path, file_type, pub_id)
        )
    return {"id": pub_id, "title": title, "file_type": file_type}


@router.put("/publications/{pub_id}")
def update_publication(pub_id: int, body: PubUpdate, user=Depends(get_current_user)):
    with db() as conn:
        pub = conn.execute("SELECT * FROM publications WHERE id=?", (pub_id,)).fetchone()
        if not pub:
            raise HTTPException(404)
        _assert_member(pub["forum_id"], user)
        conn.execute(
            "UPDATE publications SET title=?, description=?, updated_at=? WHERE id=?",
            (body.title, body.description, _now(), pub_id)
        )
    return {"ok": True}


@router.delete("/publications/{pub_id}")
def delete_publication(pub_id: int, user=Depends(get_current_user)):
    with db() as conn:
        pub = conn.execute("SELECT * FROM publications WHERE id=?", (pub_id,)).fetchone()
        if not pub:
            raise HTTPException(404)
        if not user.get("is_admin") and pub["created_by"] != user["user_id"]:
            row = conn.execute(
                "SELECT role FROM forum_members WHERE forum_id=? AND user_id=?",
                (pub["forum_id"], user["user_id"])
            ).fetchone()
            if not row or row["role"] != "admin":
                raise HTTPException(403)
        conn.execute("DELETE FROM publications WHERE id=?", (pub_id,))
    file_service.delete_pub_files(pub_id)
    return {"ok": True}


@router.get("/publications/{pub_id}/file")
def get_file(pub_id: int, download: bool = False, user=Depends(get_current_user)):
    with db() as conn:
        pub = conn.execute("SELECT * FROM publications WHERE id=?", (pub_id,)).fetchone()
        if not pub:
            raise HTTPException(404)
        _assert_member(pub["forum_id"], user)
    ext = pub["file_type"]
    filename = f"{pub['title'].replace(' ', '_')}.{ext}"
    return file_service.stream_file(pub["file_path"], filename, force_download=download)


@router.post("/publications/{pub_id}/script")
async def upload_script(
    pub_id: int,
    file: UploadFile = File(...),
    user=Depends(get_current_user),
):
    """Legacy single-script endpoint — also writes to publication_scripts table."""
    with db() as conn:
        pub = conn.execute("SELECT * FROM publications WHERE id=?", (pub_id,)).fetchone()
        if not pub:
            raise HTTPException(404)
        _assert_member(pub["forum_id"], user)

    content = await file.read()
    filename = Path(file.filename).name if file.filename else "refresh.py"
    script_path = file_service.save_named_script(pub_id, "refresh.py", content)

    with db() as conn:
        conn.execute(
            "UPDATE publications SET has_script=1, script_path=? WHERE id=?",
            (script_path, pub_id)
        )
        # Unset previous primary
        conn.execute(
            "UPDATE publication_scripts SET is_primary=0 WHERE publication_id=?", (pub_id,)
        )
        conn.execute(
            "INSERT INTO publication_scripts (publication_id, filename, file_path, is_primary, uploaded_by) "
            "VALUES (?,?,?,1,?) "
            "ON CONFLICT(publication_id, filename) DO UPDATE SET "
            "file_path=excluded.file_path, is_primary=1, uploaded_by=excluded.uploaded_by, "
            "uploaded_at=datetime('now')",
            (pub_id, filename, script_path, user["user_id"])
        )
    return {"ok": True, "script_path": script_path}


# ── Multi-script package endpoints ────────────────────────────────────────────

@router.get("/publications/{pub_id}/scripts")
def list_scripts(pub_id: int, user=Depends(get_current_user)):
    """List all script files attached to a publication."""
    with db() as conn:
        pub = conn.execute("SELECT * FROM publications WHERE id=?", (pub_id,)).fetchone()
        if not pub:
            raise HTTPException(404)
        _assert_member(pub["forum_id"], user)
        rows = conn.execute(
            "SELECT ps.*, u.username as uploaded_by_name FROM publication_scripts ps "
            "LEFT JOIN users u ON u.id=ps.uploaded_by "
            "WHERE ps.publication_id=? ORDER BY ps.is_primary DESC, ps.uploaded_at ASC",
            (pub_id,)
        ).fetchall()
    return [dict(r) for r in rows]


@router.post("/publications/{pub_id}/scripts")
async def upload_script_file(
    pub_id: int,
    file: UploadFile = File(...),
    is_primary: bool = Form(False),
    user=Depends(get_current_user),
):
    """Upload a script file. First script uploaded auto-becomes primary if none exists."""
    with db() as conn:
        pub = conn.execute("SELECT * FROM publications WHERE id=?", (pub_id,)).fetchone()
        if not pub:
            raise HTTPException(404)
        _assert_member(pub["forum_id"], user)

    filename = Path(file.filename).name if file.filename else "script.py"
    if not filename.endswith(".py"):
        raise HTTPException(400, "Only .py files are allowed as scripts")

    content = await file.read()
    file_path = file_service.save_named_script(pub_id, filename, content)

    with db() as conn:
        # Auto-promote to primary if this pub has no script yet
        has_any = conn.execute(
            "SELECT 1 FROM publication_scripts WHERE publication_id=?", (pub_id,)
        ).fetchone()
        make_primary = is_primary or (not has_any and not pub["has_script"])

        if make_primary:
            conn.execute(
                "UPDATE publication_scripts SET is_primary=0 WHERE publication_id=?", (pub_id,)
            )
        conn.execute(
            "INSERT INTO publication_scripts (publication_id, filename, file_path, is_primary, uploaded_by) "
            "VALUES (?,?,?,?,?) "
            "ON CONFLICT(publication_id, filename) DO UPDATE SET "
            "file_path=excluded.file_path, is_primary=excluded.is_primary, "
            "uploaded_by=excluded.uploaded_by, uploaded_at=datetime('now')",
            (pub_id, filename, file_path, 1 if make_primary else 0, user["user_id"])
        )
        if make_primary:
            conn.execute(
                "UPDATE publications SET has_script=1, script_path=? WHERE id=?",
                (file_path, pub_id)
            )

    return {"ok": True, "filename": filename, "is_primary": make_primary}


@router.get("/publications/{pub_id}/scripts/{script_id}/source")
def get_script_file_source(pub_id: int, script_id: int, user=Depends(get_current_user)):
    """Return a specific script file's source as plain text."""
    from fastapi.responses import PlainTextResponse
    import os
    with db() as conn:
        pub = conn.execute("SELECT * FROM publications WHERE id=?", (pub_id,)).fetchone()
        if not pub:
            raise HTTPException(404)
        _assert_member(pub["forum_id"], user)
        script = conn.execute(
            "SELECT * FROM publication_scripts WHERE id=? AND publication_id=?",
            (script_id, pub_id)
        ).fetchone()
    if not script:
        raise HTTPException(404, "Script not found")
    if not os.path.exists(script["file_path"]):
        raise HTTPException(404, "Script file missing on disk")
    with open(script["file_path"], "r", encoding="utf-8") as f:
        return PlainTextResponse(f.read())


@router.delete("/publications/{pub_id}/scripts/{script_id}")
def delete_script_file(pub_id: int, script_id: int, user=Depends(get_current_user)):
    """Delete a specific script file from a publication's package."""
    import os
    with db() as conn:
        pub = conn.execute("SELECT * FROM publications WHERE id=?", (pub_id,)).fetchone()
        if not pub:
            raise HTTPException(404)
        _assert_member(pub["forum_id"], user)
        if not user.get("is_admin") and pub["created_by"] != user["user_id"]:
            row = conn.execute(
                "SELECT role FROM forum_members WHERE forum_id=? AND user_id=?",
                (pub["forum_id"], user["user_id"])
            ).fetchone()
            if not row or row["role"] != "admin":
                raise HTTPException(403)
        script = conn.execute(
            "SELECT * FROM publication_scripts WHERE id=? AND publication_id=?",
            (script_id, pub_id)
        ).fetchone()
        if not script:
            raise HTTPException(404, "Script not found")

        if os.path.exists(script["file_path"]):
            os.remove(script["file_path"])
        conn.execute("DELETE FROM publication_scripts WHERE id=?", (script_id,))

        if script["is_primary"]:
            # Promote the next oldest script to primary, or clear has_script
            next_s = conn.execute(
                "SELECT * FROM publication_scripts WHERE publication_id=? ORDER BY uploaded_at ASC LIMIT 1",
                (pub_id,)
            ).fetchone()
            if next_s:
                conn.execute(
                    "UPDATE publication_scripts SET is_primary=1 WHERE id=?", (next_s["id"],)
                )
                conn.execute(
                    "UPDATE publications SET script_path=? WHERE id=?",
                    (next_s["file_path"], pub_id)
                )
            else:
                conn.execute(
                    "UPDATE publications SET has_script=0, script_path=NULL WHERE id=?", (pub_id,)
                )
    return {"ok": True}


@router.get("/publications/{pub_id}")
def get_publication(pub_id: int, user=Depends(get_current_user)):
    """Return full metadata for a single publication."""
    with db() as conn:
        pub = conn.execute(
            "SELECT p.*, u.username as author FROM publications p "
            "LEFT JOIN users u ON u.id=p.created_by WHERE p.id=?", (pub_id,)
        ).fetchone()
        if not pub:
            raise HTTPException(404)
        _assert_member(pub["forum_id"], user)
    return dict(pub)


@router.get("/publications/{pub_id}/script/source")
def get_script_source(pub_id: int, user=Depends(get_current_user)):
    """Return the Python refresh script source as plain text (for AI agents to read/analyze)."""
    from fastapi.responses import PlainTextResponse
    import os
    with db() as conn:
        pub = conn.execute("SELECT * FROM publications WHERE id=?", (pub_id,)).fetchone()
        if not pub:
            raise HTTPException(404)
        _assert_member(pub["forum_id"], user)
    if not pub["has_script"] or not pub["script_path"]:
        raise HTTPException(404, "No refresh script attached")
    if not os.path.exists(pub["script_path"]):
        raise HTTPException(404, "Script file missing on disk")
    with open(pub["script_path"], "r", encoding="utf-8") as f:
        source = f.read()
    return PlainTextResponse(source)


@router.post("/publications/{pub_id}/file")
async def replace_file(
    pub_id: int,
    file: UploadFile = File(...),
    user=Depends(get_current_user),
):
    """Replace the content file of an existing publication (re-publish with updated dashboard)."""
    with db() as conn:
        pub = conn.execute("SELECT * FROM publications WHERE id=?", (pub_id,)).fetchone()
        if not pub:
            raise HTTPException(404)
        _assert_member(pub["forum_id"], user)
        if not user.get("is_admin") and pub["created_by"] != user["user_id"]:
            row = conn.execute(
                "SELECT role FROM forum_members WHERE forum_id=? AND user_id=?",
                (pub["forum_id"], user["user_id"])
            ).fetchone()
            if not row or row["role"] != "admin":
                raise HTTPException(403)

    file_service.backup_output(pub_id)
    try:
        file_path, file_type = await file_service.save_upload(pub_id, file)
    except ValueError as e:
        raise HTTPException(400, str(e))

    with db() as conn:
        conn.execute(
            "UPDATE publications SET file_path=?, file_type=?, updated_at=? WHERE id=?",
            (file_path, file_type, _now(), pub_id)
        )
    return {"ok": True, "file_type": file_type}
