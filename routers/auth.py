from fastapi import APIRouter, Request, Form, HTTPException, Depends
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
import sqlite3
from auth import hash_password, verify_password, create_token, create_api_token, get_current_user
from database import db

router = APIRouter()
templates = Jinja2Templates(directory="templates")

@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, error: str = ""):
    return templates.TemplateResponse(request, "login.html", {"error": error})

@router.get("/register", response_class=HTMLResponse)
def register_page(request: Request, error: str = ""):
    return templates.TemplateResponse(request, "register.html", {"error": error})

@router.post("/auth/login")
def do_login(request: Request, username: str = Form(...), password: str = Form(...)):
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if not row or not verify_password(password, row["password_hash"]):
        return RedirectResponse("/login?error=Invalid+username+or+password", status_code=303)
    token = create_token(row["id"], row["username"], bool(row["is_admin"]))
    resp = RedirectResponse("/forums", status_code=303)
    resp.set_cookie("rs_token", token, httponly=True, max_age=28800)
    return resp

@router.post("/auth/register")
def do_register(request: Request, username: str = Form(...), password: str = Form(...)):
    if len(username) < 3:
        return RedirectResponse("/register?error=Username+must+be+at+least+3+characters", status_code=303)
    if len(password) < 6:
        return RedirectResponse("/register?error=Password+must+be+at+least+6+characters", status_code=303)
    try:
        with db() as conn:
            conn.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                (username.strip(), hash_password(password))
            )
            row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    except sqlite3.IntegrityError:
        return RedirectResponse("/register?error=Username+already+taken", status_code=303)
    token = create_token(row["id"], row["username"], False)
    resp = RedirectResponse("/forums", status_code=303)
    resp.set_cookie("rs_token", token, httponly=True, max_age=28800)
    return resp

@router.post("/auth/logout")
def do_logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("rs_token")
    return resp

@router.post("/api/token")
def get_token(username: str = Form(...), password: str = Form(...)):
    """Issue a short-lived Bearer token for API/MCP clients via username+password."""
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if not row or not verify_password(password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_token(row["id"], row["username"], bool(row["is_admin"]))
    return {"access_token": token, "token_type": "bearer"}


@router.post("/api/me/token")
def generate_api_token(user=Depends(get_current_user)):
    """Generate (or regenerate) a long-lived API token for the current user."""
    from datetime import datetime, timezone
    token = create_api_token(user["user_id"], user["username"], bool(user.get("is_admin")))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with db() as conn:
        conn.execute(
            "INSERT INTO user_api_tokens (user_id, token, created_at) VALUES (?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET token=excluded.token, created_at=excluded.created_at",
            (user["user_id"], token, now)
        )
    return {"token": token, "created_at": now}


@router.delete("/api/me/token")
def revoke_api_token(user=Depends(get_current_user)):
    """Revoke the current user's API token."""
    with db() as conn:
        conn.execute("DELETE FROM user_api_tokens WHERE user_id=?", (user["user_id"],))
    return {"revoked": True}
