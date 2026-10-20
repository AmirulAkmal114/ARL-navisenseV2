"""Authentication, session and account-management endpoints."""

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from httpx import HTTPStatusError

from backend.app.db import (
    clear_user_token,
    create_user,
    get_user_by_email,
    update_user_password,
    update_user_token,
)
from backend.app.security import (
    AuthUser,
    generate_api_token,
    hash_password,
    is_expired,
    require_admin_key,
    require_auth,
    resolve_user,
    verify_password,
)
from backend.app.services import utc_now

router = APIRouter(prefix="/api", tags=["auth"])


def _validate_new_password(password: str) -> None:
    if len(password) < 8:
        raise HTTPException(status_code=422, detail="Password must be at least 8 characters")
    if len(password.encode("utf-8")) > 72:
        raise HTTPException(status_code=422, detail="Password must be at most 72 bytes")


@router.post("/login")
async def login(request: Request):
    body = await request.json()
    email = body.get("email")
    email = email.strip() if isinstance(email, str) else ""
    password = body.get("password")
    password = password if isinstance(password, str) else ""

    if not email or not password:
        raise HTTPException(status_code=400, detail="Email and password are required")

    user = get_user_by_email(email)
    if not user and email.lower() != email:
        user = get_user_by_email(email.lower())
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    email = user.get("email") or email

    stored = user.get("password_hash") or ""
    if not verify_password(password, stored):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = user.get("auth_token")
    time_created = user.get("time_created")
    time_expiry = user.get("time_expiry")

    now = utc_now()
    refresh = not token or time_expiry is None or is_expired(time_expiry)

    if refresh:
        token = generate_api_token()
        time_created = now
        time_expiry = now + timedelta(days=30)
        update_user_token(email, token, time_created, time_expiry)

    return {
        "message": "Login successful",
        "user_info": [{
            "FirstName": user.get("first_name"),
            "LastName": user.get("last_name"),
            "Organization": user.get("organization"),
            "Email": user.get("email"),
            "Auth_Token": token,
            "Time_Created": time_created.isoformat() if time_created else None,
            "Time_Expiry": time_expiry.isoformat() if time_expiry else None,
            "Package": user.get("package")
        }]
    }


@router.get("/login")
async def validate_token(request: Request, token: str = Query(None)):
    if not token:
        raise HTTPException(status_code=400, detail="Token is required")

    user = resolve_user(token)

    return {
        "Username": f"{user.get('first_name') or ''} {user.get('last_name') or ''}".strip(),
        "Email": user.get("email")
    }


@router.post("/logout")
async def logout(user: AuthUser = Depends(require_auth)):
    """Revoke the caller's token (requires ``Authorization: Bearer``)."""
    clear_user_token(user.email)
    return {"message": "Logged out"}


@router.post("/change-password")
async def change_password(request: Request, user: AuthUser = Depends(require_auth)):
    """Change the caller's password and issue a fresh token."""
    body = await request.json()
    current_password = body.get("current_password")
    current_password = current_password if isinstance(current_password, str) else ""
    new_password = body.get("new_password")
    new_password = new_password if isinstance(new_password, str) else ""

    if not current_password or not new_password:
        raise HTTPException(
            status_code=400,
            detail="current_password and new_password are required",
        )
    _validate_new_password(new_password)

    account = get_user_by_email(user.email)
    if not account or not verify_password(
        current_password, account.get("password_hash") or ""
    ):
        raise HTTPException(status_code=401, detail="Current password is incorrect")

    update_user_password(user.email, hash_password(new_password))

    now = utc_now()
    time_expiry = now + timedelta(days=30)
    token = generate_api_token()
    update_user_token(user.email, token, now, time_expiry)

    return {
        "message": "Password updated",
        "Auth_Token": token,
        "Time_Created": now.isoformat(),
        "Time_Expiry": time_expiry.isoformat(),
    }


@router.post("/register", status_code=201)
async def register(request: Request, _: None = Depends(require_admin_key)):
    """Create an account (requires ``X-Admin-Key``)."""
    body = await request.json()
    email = body.get("email")
    email = email.strip().lower() if isinstance(email, str) else ""
    password = body.get("password")
    password = password if isinstance(password, str) else ""

    if not email or "@" not in email or " " in email:
        raise HTTPException(status_code=422, detail="A valid email is required")
    _validate_new_password(password)

    if get_user_by_email(email):
        raise HTTPException(status_code=409, detail="Email already registered")

    try:
        create_user(
            email,
            hash_password(password),
            first_name=body.get("first_name"),
            last_name=body.get("last_name"),
            organization=body.get("organization"),
            package=body.get("package"),
        )
    except HTTPStatusError as exc:
        if exc.response.status_code == 409:
            raise HTTPException(status_code=409, detail="Email already registered")
        raise HTTPException(status_code=502, detail="Failed to create user")

    return {"message": "User created", "email": email}
