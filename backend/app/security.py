"""Authentication helpers based on login-issued API tokens.

``POST /api/login`` returns an opaque ``Auth_Token`` stored in the ``users``
table. Protected endpoints accept that token either as::

    Authorization: Bearer <Auth_Token>
    X-API-Key: <Auth_Token>

Data-pipeline endpoints instead require the service key from ``INGEST_API_KEY``
sent as ``X-API-Key``. Account registration requires ``ADMIN_API_KEY`` sent as
``X-Admin-Key``.
"""

import hashlib
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import import_module

from fastapi import HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from backend.app.db import get_user_by_token

bearer_scheme = HTTPBearer(auto_error=False, scheme_name="BearerAuth")
ingest_key_scheme = APIKeyHeader(
    name="X-API-Key", scheme_name="IngestApiKey", auto_error=False
)
admin_key_scheme = APIKeyHeader(
    name="X-Admin-Key", scheme_name="AdminApiKey", auto_error=False
)


@dataclass(frozen=True)
class AuthUser:
    """Safe subset of a user record (never includes the password hash)."""

    email: str
    first_name: str | None = None
    last_name: str | None = None
    organization: str | None = None
    package: str | None = None

    @property
    def name(self) -> str:
        return f"{self.first_name or ''} {self.last_name or ''}".strip()


def _unauthorized(message: str = "Invalid or expired token") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=message,
        headers={"WWW-Authenticate": "Bearer"},
    )


def generate_api_token() -> str:
    """Generate an opaque bearer token for the login flow."""
    return secrets.token_hex(32)


def hash_password(password: str) -> str:
    bcrypt = import_module("bcrypt")
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    """Check a password against a bcrypt hash or a legacy SHA-256 hex digest."""
    if not hashed:
        return False
    if hashed.startswith("$2"):
        try:
            bcrypt = import_module("bcrypt")
            return bcrypt.checkpw(plain.encode(), hashed.encode())
        except (ImportError, ValueError):
            return False
    return secrets.compare_digest(hashlib.sha256(plain.encode()).hexdigest(), hashed)


def is_expired(expiry) -> bool:
    """Return True when an expiry timestamp is in the past."""
    if expiry is None:
        return False
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return expiry < datetime.now(timezone.utc)


def resolve_user(token: str) -> dict:
    """Look up a user by API token, raising 401 if missing, invalid or expired."""
    if not token:
        raise _unauthorized("Missing API token")

    user = get_user_by_token(token)
    if not user:
        raise _unauthorized()

    if is_expired(user.get("time_expiry")):
        raise _unauthorized()

    return user


async def require_auth(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
) -> AuthUser:
    """FastAPI dependency: require a valid login-issued token."""
    token = credentials.credentials if credentials else request.headers.get("x-api-key", "")
    user = resolve_user(token)
    return AuthUser(
        email=user.get("email") or "",
        first_name=user.get("first_name"),
        last_name=user.get("last_name"),
        organization=user.get("organization"),
        package=user.get("package"),
    )


def _valid_service_key(supplied: str | None, env_name: str) -> bool:
    expected = os.environ.get(env_name, "")
    if not expected or not supplied:
        return False
    return secrets.compare_digest(supplied.encode(), expected.encode())


async def require_ingest_key(api_key: str | None = Security(ingest_key_scheme)) -> None:
    """FastAPI dependency: require the shared ``INGEST_API_KEY`` service key."""
    if not _valid_service_key(api_key, "INGEST_API_KEY"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")


async def require_admin_key(api_key: str | None = Security(admin_key_scheme)) -> None:
    """FastAPI dependency: require ``ADMIN_API_KEY`` for account management."""
    if not os.environ.get("ADMIN_API_KEY"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Registration is disabled",
        )
    if not _valid_service_key(api_key, "ADMIN_API_KEY"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin API key")
