import re
from fastapi import APIRouter, Request, Depends, HTTPException
from pydantic import BaseModel
from auth import get_current_user
from database import db

router = APIRouter(prefix="/api")


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _check_forum_admin(forum_id: int, user: dict):
    if user.get("is_admin"):
        return
    with db() as conn:
        row = conn.execute(
            "SELECT role FROM forum_members WHERE forum_id=? AND user_id=?",
            (forum_id, user["user_id"])
        ).fetchone()
    if not row or row["role"] != "admin":
        raise HTTPException(403, "Forum admin required")


class ForumIn(BaseModel):
    display_name: str
    description: str = ""

class MemberIn(BaseModel):
    username: str
    role: str = "member"


@router.get("/forums")
def list_forums(user=Depends(get_current_user)):
    with db() as conn:
        if user.get("is_admin"):
            rows = conn.execute("SELECT * FROM forums ORDER BY display_name").fetchall()
        else:
            rows = conn.execute(
                "SELECT f.* FROM forums f JOIN forum_members fm ON fm.forum_id=f.id "
                "WHERE fm.user_id=? ORDER BY f.display_name", (user["user_id"],)
            ).fetchall()
    return [dict(r) for r in rows]


@router.post("/forums")
def create_forum(body: ForumIn, user=Depends(get_current_user)):
    if not user.get("is_admin"):
        raise HTTPException(403, "System admin required")
    slug = _slug(body.display_name)
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO forums (slug, display_name, description, created_by) VALUES (?,?,?,?)",
            (slug, body.display_name, body.description, user["user_id"])
        )
        forum_id = cur.lastrowid
        conn.execute(
            "INSERT INTO forum_members (forum_id, user_id, role) VALUES (?,?,?)",
            (forum_id, user["user_id"], "admin")
        )
    return {"id": forum_id, "slug": slug, "display_name": body.display_name}


@router.put("/forums/{forum_id}")
def update_forum(forum_id: int, body: ForumIn, user=Depends(get_current_user)):
    _check_forum_admin(forum_id, user)
    with db() as conn:
        conn.execute(
            "UPDATE forums SET display_name=?, description=? WHERE id=?",
            (body.display_name, body.description, forum_id)
        )
    return {"ok": True}


@router.delete("/forums/{forum_id}")
def delete_forum(forum_id: int, user=Depends(get_current_user)):
    if not user.get("is_admin"):
        raise HTTPException(403, "System admin required")
    with db() as conn:
        conn.execute("DELETE FROM forums WHERE id=?", (forum_id,))
    return {"ok": True}


@router.get("/forums/{forum_id}/members")
def list_members(forum_id: int, user=Depends(get_current_user)):
    _check_forum_admin(forum_id, user)
    with db() as conn:
        rows = conn.execute(
            "SELECT fm.*, u.username FROM forum_members fm JOIN users u ON u.id=fm.user_id "
            "WHERE fm.forum_id=?", (forum_id,)
        ).fetchall()
    return [dict(r) for r in rows]


@router.post("/forums/{forum_id}/members")
def add_member(forum_id: int, body: MemberIn, user=Depends(get_current_user)):
    _check_forum_admin(forum_id, user)
    with db() as conn:
        target = conn.execute("SELECT id FROM users WHERE username=?", (body.username,)).fetchone()
        if not target:
            raise HTTPException(404, f"User '{body.username}' not found")
        try:
            conn.execute(
                "INSERT INTO forum_members (forum_id, user_id, role) VALUES (?,?,?) "
                "ON CONFLICT(forum_id, user_id) DO UPDATE SET role=excluded.role",
                (forum_id, target["id"], body.role)
            )
        except Exception as e:
            raise HTTPException(400, str(e))
    return {"ok": True}


@router.delete("/forums/{forum_id}/members/{user_id}")
def remove_member(forum_id: int, user_id: int, user=Depends(get_current_user)):
    _check_forum_admin(forum_id, user)
    with db() as conn:
        conn.execute(
            "DELETE FROM forum_members WHERE forum_id=? AND user_id=?", (forum_id, user_id)
        )
    return {"ok": True}
