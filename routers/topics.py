import re
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from auth import get_current_user
from database import db

router = APIRouter(prefix="/api")


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


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


class TopicIn(BaseModel):
    display_name: str
    description: str = ""


@router.get("/forums/{forum_id}/topics")
def list_topics(forum_id: int, user=Depends(get_current_user)):
    _assert_member(forum_id, user)
    with db() as conn:
        rows = conn.execute(
            "SELECT t.*, u.username as creator FROM topics t LEFT JOIN users u ON u.id=t.created_by "
            "WHERE t.forum_id=? ORDER BY t.display_name", (forum_id,)
        ).fetchall()
    return [dict(r) for r in rows]


@router.post("/forums/{forum_id}/topics")
def create_topic(forum_id: int, body: TopicIn, user=Depends(get_current_user)):
    _assert_member(forum_id, user)
    slug = _slug(body.display_name)
    with db() as conn:
        forum = conn.execute("SELECT slug FROM forums WHERE id=?", (forum_id,)).fetchone()
        if not forum:
            raise HTTPException(404, "Forum not found")
        try:
            cur = conn.execute(
                "INSERT INTO topics (forum_id, slug, display_name, description, created_by) VALUES (?,?,?,?,?)",
                (forum_id, slug, body.display_name, body.description, user["user_id"])
            )
        except Exception:
            raise HTTPException(400, f"Topic slug '{slug}' already exists in this forum")
    return {"id": cur.lastrowid, "slug": slug, "forum_slug": forum["slug"], "display_name": body.display_name}


@router.put("/topics/{topic_id}")
def update_topic(topic_id: int, body: TopicIn, user=Depends(get_current_user)):
    with db() as conn:
        topic = conn.execute("SELECT * FROM topics WHERE id=?", (topic_id,)).fetchone()
        if not topic:
            raise HTTPException(404, "Topic not found")
        _assert_member(topic["forum_id"], user)
        conn.execute(
            "UPDATE topics SET display_name=?, description=? WHERE id=?",
            (body.display_name, body.description, topic_id)
        )
    return {"ok": True}


@router.delete("/topics/{topic_id}")
def delete_topic(topic_id: int, user=Depends(get_current_user)):
    with db() as conn:
        topic = conn.execute("SELECT * FROM topics WHERE id=?", (topic_id,)).fetchone()
        if not topic:
            raise HTTPException(404, "Topic not found")
        if not user.get("is_admin"):
            row = conn.execute(
                "SELECT role FROM forum_members WHERE forum_id=? AND user_id=?",
                (topic["forum_id"], user["user_id"])
            ).fetchone()
            if not row or row["role"] != "admin":
                raise HTTPException(403, "Forum admin required")
        pubs = conn.execute("SELECT COUNT(*) FROM publications WHERE topic_id=?", (topic_id,)).fetchone()[0]
        if pubs > 0:
            raise HTTPException(400, "Delete all publications in this topic first")
        conn.execute("DELETE FROM topics WHERE id=?", (topic_id,))
    return {"ok": True}
