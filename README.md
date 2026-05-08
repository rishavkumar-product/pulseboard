# PulseBoard

Internal analytics collaboration platform for Spinny. Publish HTML dashboards, reports, and Word documents into team forums, comment on them, and refresh underlying data on demand or on a schedule.

## Features

- **Forums → Sub-topics → Publications** — hierarchical organisation with strict membership
- **HTML & DOCX publishing** — HTML renders inline (iframe); DOCX shows text preview + download
- **Threaded comments** — nested replies on any publication
- **Live data refresh** — attach a Python script to any publication; run it on demand or on a cron schedule
- **Scheduled auto-refresh** — 5-field cron expressions, managed via APScheduler
- **Self-registration** — users register themselves; forum admins add them to forums
- **System admin panel** — manage all users and forums

## Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure environment
Copy `.env.example` to `.env` and edit:
```
SECRET_KEY=<long-random-string>
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<your-admin-password>
TOKEN_EXPIRE_HOURS=8
```

### 3. Start the server

**From the project directory:**
```bash
python launcher.py
```
Or double-click `run.bat` on Windows.

The server starts on `http://0.0.0.0:8000`. Access it at `http://localhost:8000` or via your machine's local IP for network access.

> **Important:** Always start the server from the `RecursiveSeal/` directory so relative paths (templates, storage, database) resolve correctly.

### 4. First login
- Go to `http://localhost:8000`
- Log in as `admin` with the password you set in `.env`
- Create a forum from the Admin panel or the home page
- Add members to the forum

## Writing Refresh Scripts

When you attach a `.py` script to a publication, PulseBoard runs it as a subprocess. Your script must write its output to the path provided via the `RS_OUTPUT_PATH` environment variable:

```python
import os

output_path = os.environ["RS_OUTPUT_PATH"]  # absolute path to output.html or output.docx

# ... run your Doris queries, build your HTML ...

with open(output_path, "w") as f:
    f.write(html_content)
```

The script runs with the same Python environment as the server. Doris credentials from `~/.config/doris/connection.json` and Gmail credentials from `~/.env` are all available.

**Environment variables injected:**
| Variable | Value |
|---|---|
| `RS_OUTPUT_PATH` | Absolute path to write the output file |
| `RS_PUB_ID` | Integer ID of the publication |

**Timeout:** 5 minutes. Scripts that exceed this are killed and the previous output is restored.

## Project Structure

```
RecursiveSeal/
├── main.py              # FastAPI app entry point
├── config.py            # Settings from .env
├── database.py          # SQLite schema + bootstrap
├── auth.py              # JWT auth, bcrypt passwords
├── scheduler.py         # APScheduler cron jobs
├── launcher.py          # Server launcher (sets correct cwd)
├── run.bat              # Windows double-click launcher
├── routers/             # FastAPI routers
│   ├── pages.py         # HTML page routes
│   ├── auth.py          # Login / register / logout
│   ├── forums.py        # Forum + membership API
│   ├── topics.py        # Sub-topic API
│   ├── publications.py  # Publication upload/download API
│   ├── comments.py      # Threaded comments API
│   ├── refresh.py       # Refresh trigger + status poll
│   ├── schedules.py     # Cron schedule API
│   └── admin.py         # System admin API
├── services/
│   ├── file_service.py  # File save/stream/delete helpers
│   └── refresh_service.py  # Subprocess execution
├── templates/           # Jinja2 HTML templates
├── static/              # CSS + vanilla JS
└── storage/             # Runtime: uploaded files (gitignored)
    ├── publications/    # HTML/DOCX artifacts
    └── scripts/         # Attached refresh scripts
```

## Database

SQLite (`pulseboard.db`, gitignored). Tables: `users`, `forums`, `forum_members`, `topics`, `publications`, `comments`, `schedules`.

WAL mode enabled for concurrent reads during refresh operations.

## Deployment

For internal network deployment, just run `python launcher.py` on your machine and share the URL `http://<your-ip>:8000`. For a more permanent setup, wrap in a Windows service or systemd unit.
