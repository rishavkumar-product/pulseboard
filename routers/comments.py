from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from auth import get_current_user
from database import db
from datetime import datetime, timezone

router = APIRouter(prefix="/api")


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class CommentIn(BaseModel):
    body: str


@router.get("/publications/{pub_id}/comments")
def get_comments(pub_id: int, user=Depends(get_current_user)):
    with db() as conn:
        rows = conn.execute(
            "SELECT c.*, u.username as author FROM comments c LEFT JOIN users u ON u.id=c.author_id "
            "WHERE c.publication_id=? ORDER BY c.created_at", (pub_id,)
        ).fetchall()
    return [dict(r) for r in rows]


@router.post("/publications/{pub_id}/comments")
def add_comment(pub_id: int, body: CommentIn, user=Depends(get_current_user)):
    if not body.body.strip():
        raise HTTPException(400, "Comment cannot be empty")
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO comments (publication_id, author_id, body) VALUES (?,?,?)",
            (pub_id, user["user_id"], body.body.strip())
        )
    return {"id": cur.lastrowid}


@router.post("/publications/{pub_id}/comments/{comment_id}/reply")
def reply_comment(pub_id: int, comment_id: int, body: CommentIn, user=Depends(get_current_user)):
    if not body.body.strip():
        raise HTTPException(400, "Reply cannot be empty")
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO comments (publication_id, parent_id, author_id, body) VALUES (?,?,?,?)",
            (pub_id, comment_id, user["user_id"], body.body.strip())
        )
    return {"id": cur.lastrowid}


@router.put("/comments/{comment_id}")
def edit_comment(comment_id: int, body: CommentIn, user=Depends(get_current_user)):
    with db() as conn:
        c = conn.execute("SELECT * FROM comments WHERE id=?", (comment_id,)).fetchone()
        if not c:
            raise HTTPException(404)
        if c["author_id"] != user["user_id"] and not user.get("is_admin"):
            raise HTTPException(403)
        conn.execute(
            "UPDATE comments SET body=?, updated_at=? WHERE id=?",
            (body.body.strip(), _now(), comment_id)
        )
    return {"ok": True}


@router.delete("/comments/{comment_id}")
def delete_comment(comment_id: int, user=Depends(get_current_user)):
    with db() as conn:
        c = conn.execute("SELECT * FROM comments WHERE id=?", (comment_id,)).fetchone()
        if not c:
            raise HTTPException(404)
        if c["author_id"] != user["user_id"] and not user.get("is_admin"):
            raise HTTPException(403)
        conn.execute("DELETE FROM comments WHERE id=?", (comment_id,))
    return {"ok": True}
