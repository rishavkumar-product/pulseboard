import os
import sys
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from database import db
from services.file_service import script_dir, get_output_path, backup_output, restore_backup

_executor = ThreadPoolExecutor(max_workers=4)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def run_refresh(pub_id: int):
    with db() as conn:
        pub = conn.execute("SELECT * FROM publications WHERE id=?", (pub_id,)).fetchone()
    if not pub or not pub["has_script"] or not pub["script_path"]:
        return

    backup_output(pub_id)

    with db() as conn:
        conn.execute(
            "UPDATE publications SET refresh_status='running', refresh_log='', updated_at=? WHERE id=?",
            (_now(), pub_id)
        )

    script_path = Path(pub["script_path"])
    output_path = get_output_path(pub_id, pub["file_type"])
    env = os.environ.copy()
    env["RS_OUTPUT_PATH"] = str(output_path.resolve())
    env["RS_PUB_ID"] = str(pub_id)

    try:
        result = subprocess.run(
            [sys.executable, str(script_path)],
            capture_output=True, text=True,
            timeout=300,
            cwd=str(script_path.parent),
            env=env,
        )
        if result.returncode == 0:
            log = (result.stdout or "")[-4000:]
            with db() as conn:
                conn.execute(
                    "UPDATE publications SET refresh_status='success', last_refreshed_at=?, "
                    "refresh_log=?, updated_at=? WHERE id=?",
                    (_now(), log, _now(), pub_id)
                )
        else:
            log = (result.stderr or result.stdout or "")[-4000:]
            restore_backup(pub_id)
            with db() as conn:
                conn.execute(
                    "UPDATE publications SET refresh_status='error', refresh_log=?, updated_at=? WHERE id=?",
                    (log, _now(), pub_id)
                )
    except subprocess.TimeoutExpired:
        restore_backup(pub_id)
        with db() as conn:
            conn.execute(
                "UPDATE publications SET refresh_status='error', refresh_log=?, updated_at=? WHERE id=?",
                ("Script timed out after 5 minutes.", _now(), pub_id)
            )
    except Exception as e:
        restore_backup(pub_id)
        with db() as conn:
            conn.execute(
                "UPDATE publications SET refresh_status='error', refresh_log=?, updated_at=? WHERE id=?",
                (str(e), _now(), pub_id)
            )


def submit_refresh(pub_id: int):
    _executor.submit(run_refresh, pub_id)
