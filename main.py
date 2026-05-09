from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse

from database import init_db
from scheduler import scheduler, restore_schedules
from templates_config import templates
from routers import auth, pages, forums, topics, publications, comments, refresh, schedules, admin, mcp_server


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    scheduler.start()
    restore_schedules()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="PulseBoard", lifespan=lifespan)

app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(auth.router)
app.include_router(pages.router)
app.include_router(forums.router)
app.include_router(topics.router)
app.include_router(publications.router)
app.include_router(comments.router)
app.include_router(refresh.router)
app.include_router(schedules.router)
app.include_router(admin.router)
app.include_router(mcp_server.router)


@app.exception_handler(403)
async def forbidden(request: Request, exc):
    from auth import get_current_user
    from routers.pages import _user_forums
    try:
        user = get_current_user(request)
        user_forums = _user_forums(user["user_id"], user.get("is_admin", False))
    except Exception:
        user = None
        user_forums = []
    return templates.TemplateResponse(
        request, "403.html", {"user": user, "user_forums": user_forums}, status_code=403
    )


@app.exception_handler(404)
async def not_found(request: Request, exc):
    from auth import get_current_user
    from routers.pages import _user_forums
    try:
        user = get_current_user(request)
        user_forums = _user_forums(user["user_id"], user.get("is_admin", False))
    except Exception:
        user = None
        user_forums = []
    return templates.TemplateResponse(
        request, "404.html", {"user": user, "user_forums": user_forums}, status_code=404
    )
