from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from auth import get_current_user
from database import db

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def _user_forums(user_id: int, is_admin: bool):
    with db() as conn:
        if is_admin:
            return conn.execute("SELECT * FROM forums ORDER BY display_name").fetchall()
        return conn.execute(
            "SELECT f.* FROM forums f JOIN forum_members fm ON fm.forum_id=f.id "
            "WHERE fm.user_id=? ORDER BY f.display_name", (user_id,)
        ).fetchall()


def _ctx(user: dict, extra: dict = None):
    """Context dict WITHOUT request — Starlette 1.x passes request separately."""
    forums = _user_forums(user["user_id"], user.get("is_admin", False))
    base = {"user": user, "user_forums": forums}
    if extra:
        base.update(extra)
    return base


@router.get("/", response_class=HTMLResponse)
def root(request: Request):
    token = request.cookies.get("rs_token")
    if token:
        return RedirectResponse("/forums")
    return RedirectResponse("/login")


@router.get("/forums", response_class=HTMLResponse)
def forums_home(request: Request, user=Depends(get_current_user)):
    with db() as conn:
        if user.get("is_admin"):
            forums = conn.execute(
                "SELECT f.*, u.username as creator FROM forums f "
                "LEFT JOIN users u ON u.id=f.created_by ORDER BY f.display_name"
            ).fetchall()
        else:
            forums = conn.execute(
                "SELECT f.*, u.username as creator, fm.role FROM forums f "
                "JOIN forum_members fm ON fm.forum_id=f.id "
                "LEFT JOIN users u ON u.id=f.created_by "
                "WHERE fm.user_id=? ORDER BY f.display_name", (user["user_id"],)
            ).fetchall()
    return templates.TemplateResponse(request, "home.html", _ctx(user, {"forums": forums}))


@router.get("/forums/{slug}", response_class=HTMLResponse)
def forum_page(slug: str, request: Request, user=Depends(get_current_user)):
    return RedirectResponse(f"/forums/{slug}/")


@router.get("/forums/{forum_slug}/{topic_slug:path}", response_class=HTMLResponse)
def forum_topic_page(forum_slug: str, topic_slug: str, request: Request, user=Depends(get_current_user)):
    with db() as conn:
        forum = conn.execute("SELECT * FROM forums WHERE slug=?", (forum_slug,)).fetchone()
        if not forum:
            return templates.TemplateResponse(request, "404.html", _ctx(user), status_code=404)

        # membership check
        if not user.get("is_admin"):
            member = conn.execute(
                "SELECT role FROM forum_members WHERE forum_id=? AND user_id=?",
                (forum["id"], user["user_id"])
            ).fetchone()
            if not member:
                return templates.TemplateResponse(request, "403.html", _ctx(user), status_code=403)
            user_role = member["role"]
        else:
            user_role = "admin"

        topics = conn.execute(
            "SELECT t.*, u.username as creator FROM topics t LEFT JOIN users u ON u.id=t.created_by "
            "WHERE t.forum_id=? ORDER BY t.display_name", (forum["id"],)
        ).fetchall()

        active_topic = None
        publications = []
        if topic_slug:
            active_topic = conn.execute(
                "SELECT * FROM topics WHERE forum_id=? AND slug=?", (forum["id"], topic_slug)
            ).fetchone()
            if active_topic:
                publications = conn.execute(
                    "SELECT p.*, u.username as author FROM publications p "
                    "LEFT JOIN users u ON u.id=p.created_by "
                    "WHERE p.topic_id=? ORDER BY p.updated_at DESC", (active_topic["id"],)
                ).fetchall()

        members = conn.execute(
            "SELECT fm.*, u.username FROM forum_members fm JOIN users u ON u.id=fm.user_id "
            "WHERE fm.forum_id=?", (forum["id"],)
        ).fetchall()

    return templates.TemplateResponse(request, "forum.html", _ctx(user, {
        "forum": forum,
        "topics": topics,
        "active_topic": active_topic,
        "active_forum_slug": forum_slug,
        "publications": publications,
        "user_role": user_role,
        "members": members,
    }))


@router.get("/publications/{pub_id}", response_class=HTMLResponse)
def publication_page(pub_id: int, request: Request, user=Depends(get_current_user)):
    with db() as conn:
        pub = conn.execute(
            "SELECT p.*, u.username as author, f.slug as forum_slug, f.display_name as forum_name, "
            "t.slug as topic_slug, t.display_name as topic_name "
            "FROM publications p LEFT JOIN users u ON u.id=p.created_by "
            "LEFT JOIN forums f ON f.id=p.forum_id LEFT JOIN topics t ON t.id=p.topic_id "
            "WHERE p.id=?", (pub_id,)
        ).fetchone()
        if not pub:
            return templates.TemplateResponse(request, "404.html", _ctx(user), status_code=404)

        if not user.get("is_admin"):
            member = conn.execute(
                "SELECT role FROM forum_members WHERE forum_id=? AND user_id=?",
                (pub["forum_id"], user["user_id"])
            ).fetchone()
            if not member:
                return templates.TemplateResponse(request, "403.html", _ctx(user), status_code=403)
            user_role = member["role"]
        else:
            user_role = "admin"

        comments = conn.execute(
            "SELECT c.*, u.username as author FROM comments c LEFT JOIN users u ON u.id=c.author_id "
            "WHERE c.publication_id=? ORDER BY c.created_at", (pub_id,)
        ).fetchall()

        schedule = conn.execute("SELECT * FROM schedules WHERE publication_id=?", (pub_id,)).fetchone()

        docx_preview = None
        if pub["file_type"] == "docx":
            try:
                from docx import Document
                doc = Document(pub["file_path"])
                docx_preview = "\n".join(p.text for p in doc.paragraphs if p.text)[:5000]
            except Exception:
                docx_preview = "[Could not extract text from document]"

    def build_tree(rows, parent_id=None):
        return [
            {**dict(r), "replies": build_tree(rows, r["id"])}
            for r in rows if r["parent_id"] == parent_id
        ]

    comment_tree = build_tree(list(comments))

    return templates.TemplateResponse(request, "publication.html", _ctx(user, {
        "pub": pub,
        "comment_tree": comment_tree,
        "schedule": schedule,
        "docx_preview": docx_preview,
        "user_role": user_role,
        "active_forum_slug": pub["forum_slug"],
    }))


@router.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request, user=Depends(get_current_user)):
    if not user.get("is_admin"):
        return templates.TemplateResponse(request, "403.html", _ctx(user), status_code=403)
    with db() as conn:
        users = conn.execute("SELECT * FROM users ORDER BY username").fetchall()
        forums = conn.execute(
            "SELECT f.*, u.username as creator, COUNT(fm.user_id) as member_count "
            "FROM forums f LEFT JOIN users u ON u.id=f.created_by "
            "LEFT JOIN forum_members fm ON fm.forum_id=f.id "
            "GROUP BY f.id ORDER BY f.display_name"
        ).fetchall()
    return templates.TemplateResponse(request, "admin.html", _ctx(user, {
        "all_users": users, "all_forums": forums
    }))
