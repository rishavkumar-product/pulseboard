from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
import sqlite3
from auth import hash_password, verify_password, create_token
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
