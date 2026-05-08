from fastapi import APIRouter, Depends, HTTPException
from auth import get_current_user
from database import db
from services.refresh_service import submit_refresh

router = APIRouter(prefix="/api")


@router.post("/publications/{pub_id}/refresh")
def trigger_refresh(pub_id: int, user=Depends(get_current_user)):
    with db() as conn:
        pub = conn.execute("SELECT * FROM publications WHERE id=?", (pub_id,)).fetchone()
    if not pub:
        raise HTTPException(404)
    if not pub["has_script"]:
        raise HTTPException(400, "No refresh script attached to this publication")
    if pub["refresh_status"] == "running":
        raise HTTPException(409, "Refresh already in progress")

    submit_refresh(pub_id)
    return {"refresh_status": "running", "refresh_log": ""}


@router.get("/publications/{pub_id}/refresh/status")
def refresh_status(pub_id: int, user=Depends(get_current_user)):
    with db() as conn:
        pub = conn.execute(
            "SELECT refresh_status, refresh_log, last_refreshed_at FROM publications WHERE id=?",
            (pub_id,)
        ).fetchone()
    if not pub:
        raise HTTPException(404)
    return dict(pub)
