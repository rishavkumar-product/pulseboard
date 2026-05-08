from datetime import datetime, timedelta, timezone
import bcrypt
from jose import jwt, JWTError
from fastapi import Request, HTTPException
from config import settings

def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()

def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())

def create_token(user_id: int, username: str, is_admin: bool) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=settings.token_expire_hours)
    return jwt.encode(
        {"user_id": user_id, "username": username, "is_admin": is_admin, "exp": expire},
        settings.secret_key, algorithm="HS256"
    )

def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.secret_key, algorithms=["HS256"])

def get_current_user(request: Request):
    token = request.cookies.get("rs_token")
    if not token:
        raise HTTPException(303, headers={"Location": "/login"})
    try:
        return decode_token(token)
    except JWTError:
        raise HTTPException(303, headers={"Location": "/login"})

def require_admin(request: Request):
    user = get_current_user(request)
    if not user.get("is_admin"):
        raise HTTPException(403, "Admin required")
    return user
