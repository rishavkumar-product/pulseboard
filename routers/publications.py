from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel
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
def get_file(pub_id: int, user=Depends(get_current_user)):
    with db() as conn:
        pub = conn.execute("SELECT * FROM publications WHERE id=?", (pub_id,)).fetchone()
        if not pub:
            raise HTTPException(404)
        _assert_member(pub["forum_id"], user)
    ext = pub["file_type"]
    filename = f"{pub['title'].replace(' ', '_')}.{ext}"
    return file_service.stream_file(pub["file_path"], filename)


@router.post("/publications/{pub_id}/script")
async def upload_script(
    pub_id: int,
    file: UploadFile = File(...),
    user=Depends(get_current_user),
):
    with db() as conn:
        pub = conn.execute("SELECT * FROM publications WHERE id=?", (pub_id,)).fetchone()
        if not pub:
            raise HTTPException(404)
        _assert_member(pub["forum_id"], user)

    script_path = await file_service.save_script(pub_id, file)
    with db() as conn:
        conn.execute(
            "UPDATE publications SET has_script=1, script_path=? WHERE id=?",
            (script_path, pub_id)
        )
    return {"ok": True, "script_path": script_path}
