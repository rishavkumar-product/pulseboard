from fastapi import APIRouter, Depends, HTTPException
from auth import get_current_user
from database import db

router = APIRouter(prefix="/api/admin")


def _require_admin(user=Depends(get_current_user)):
    if not user.get("is_admin"):
        raise HTTPException(403, "System admin required")
    return user


@router.get("/users")
def list_users(user=Depends(_require_admin)):
    with db() as conn:
        rows = conn.execute("SELECT id, username, is_admin, created_at FROM users ORDER BY username").fetchall()
    return [dict(r) for r in rows]


@router.delete("/users/{user_id}")
def delete_user(user_id: int, user=Depends(_require_admin)):
    if user_id == user["user_id"]:
        raise HTTPException(400, "Cannot delete yourself")
    with db() as conn:
        conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    return {"ok": True}
