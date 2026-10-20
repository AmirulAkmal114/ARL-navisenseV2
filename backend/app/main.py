import base64
import binascii
import os
import secrets
import smtplib
from email.mime.text import MIMEText

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from backend.app.routers import auth, health, ingest, positions, stats, vessels

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "public")

app = FastAPI(title="NaviSense API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

DOCS_PATHS = {"/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"}


@app.middleware("http")
async def protect_docs(request: Request, call_next):
    """Require HTTP Basic auth for API docs, or hide them when not configured."""
    path = request.url.path.rstrip("/") or "/"
    if path not in DOCS_PATHS:
        return await call_next(request)

    username = os.environ.get("DOCS_USERNAME", "")
    password = os.environ.get("DOCS_PASSWORD", "")
    if not username or not password:
        return Response(status_code=404)

    scheme, _, encoded = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "basic" or not encoded:
        return Response(
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="NaviSense docs"'},
        )

    try:
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
        supplied_user, _, supplied_pass = decoded.partition(":")
    except (binascii.Error, UnicodeDecodeError):
        supplied_user = supplied_pass = ""

    if not (
        secrets.compare_digest(supplied_user.encode(), username.encode())
        and secrets.compare_digest(supplied_pass.encode(), password.encode())
    ):
        return Response(
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="NaviSense docs"'},
        )

    return await call_next(request)

# Routers must be included before the catch-all static mount below, otherwise
# the mount at "/" would intercept /api requests.
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(vessels.router)
app.include_router(positions.router)
app.include_router(stats.router)
app.include_router(ingest.router)


@app.post("/api/demo-request")
async def demo_request(request: Request):
    body = await request.json()

    smtp_server = os.environ.get("SMTP_SERVER", "smtp.hostinger.com")
    try:
        smtp_port = int(os.environ.get("SMTP_PORT", "587") or "587")
    except ValueError:
        smtp_port = 587
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_password = os.environ.get("SMTP_PASSWORD", "")
    admin_emails = [e.strip() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()]

    if not smtp_user or not smtp_password or not admin_emails:
        raise HTTPException(status_code=500, detail="SMTP is not configured")

    text = (
        "New demo request:\n\n"
        f"Name: {body.get('first_name')} {body.get('last_name')}\n"
        f"Organization: {body.get('organization')}\n"
        f"Email: {body.get('email')}"
    )

    msg = MIMEText(text)
    msg["Subject"] = "New Demo Request"
    msg["From"] = smtp_user
    msg["To"] = ", ".join(admin_emails)

    try:
        with smtplib.SMTP(smtp_server, smtp_port, timeout=15) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.sendmail(smtp_user, admin_emails, msg.as_string())
    except Exception as e:
        print("EMAIL ERROR:", str(e))
        raise HTTPException(status_code=500, detail="Failed to send email")

    return {"status": "success"}


if os.path.isdir(STATIC_DIR):
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
