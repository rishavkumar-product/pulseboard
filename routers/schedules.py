from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from auth import get_current_user
from database import db
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime, timezone

router = APIRouter(prefix="/api")


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class ScheduleIn(BaseModel):
    cron_expression: str


class ScheduleToggle(BaseModel):
    is_active: bool


@router.get("/publications/{pub_id}/schedule")
def get_schedule(pub_id: int, user=Depends(get_current_user)):
    with db() as conn:
        row = conn.execute("SELECT * FROM schedules WHERE publication_id=?", (pub_id,)).fetchone()
    return dict(row) if row else None


@router.post("/publications/{pub_id}/schedule")
def create_schedule(pub_id: int, body: ScheduleIn, user=Depends(get_current_user)):
    try:
        trigger = CronTrigger.from_crontab(body.cron_expression, timezone="UTC")
    except Exception as e:
        raise HTTPException(400, f"Invalid cron expression: {e}")

    from scheduler import scheduler
    from services.refresh_service import run_refresh

    with db() as conn:
        pub = conn.execute("SELECT * FROM publications WHERE id=?", (pub_id,)).fetchone()
        if not pub:
            raise HTTPException(404)
        conn.execute(
            "INSERT INTO schedules (publication_id, cron_expression, is_active, created_by) VALUES (?,?,1,?) "
            "ON CONFLICT(publication_id) DO UPDATE SET cron_expression=excluded.cron_expression, is_active=1",
            (pub_id, body.cron_expression, user["user_id"])
        )

    scheduler.add_job(
        run_refresh, trigger,
        id=f"refresh_{pub_id}", replace_existing=True, args=[pub_id]
    )
    return {"ok": True, "cron_expression": body.cron_expression}


@router.put("/publications/{pub_id}/schedule")
def toggle_schedule(pub_id: int, body: ScheduleToggle, user=Depends(get_current_user)):
    from scheduler import scheduler
    from services.refresh_service import run_refresh

    with db() as conn:
        row = conn.execute("SELECT * FROM schedules WHERE publication_id=?", (pub_id,)).fetchone()
        if not row:
            raise HTTPException(404, "No schedule found")
        conn.execute(
            "UPDATE schedules SET is_active=? WHERE publication_id=?",
            (1 if body.is_active else 0, pub_id)
        )

    job_id = f"refresh_{pub_id}"
    if body.is_active:
        trigger = CronTrigger.from_crontab(row["cron_expression"], timezone="UTC")
        scheduler.add_job(run_refresh, trigger, id=job_id, replace_existing=True, args=[pub_id])
    else:
        try:
            scheduler.remove_job(job_id)
        except Exception:
            pass
    return {"ok": True, "is_active": body.is_active}


@router.delete("/publications/{pub_id}/schedule")
def delete_schedule(pub_id: int, user=Depends(get_current_user)):
    from scheduler import scheduler
    with db() as conn:
        conn.execute("DELETE FROM schedules WHERE publication_id=?", (pub_id,))
    try:
        scheduler.remove_job(f"refresh_{pub_id}")
    except Exception:
        pass
    return {"ok": True}
