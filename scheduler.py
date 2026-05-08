from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

scheduler = BackgroundScheduler(timezone="UTC")


def restore_schedules():
    from database import db
    from services.refresh_service import run_refresh
    with db() as conn:
        rows = conn.execute(
            "SELECT publication_id, cron_expression FROM schedules WHERE is_active=1"
        ).fetchall()
    for row in rows:
        try:
            scheduler.add_job(
                run_refresh,
                CronTrigger.from_crontab(row["cron_expression"], timezone="UTC"),
                id=f"refresh_{row['publication_id']}",
                replace_existing=True,
                args=[row["publication_id"]],
            )
        except Exception as e:
            print(f"[scheduler] Could not restore job for pub {row['publication_id']}: {e}")
