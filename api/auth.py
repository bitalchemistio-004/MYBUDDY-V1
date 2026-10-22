"""
api/auth.py — Authentication and authorization for MBUDDY control endpoints.

Provides bearer token validation and local network access control.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta
from typing import Optional

from aiohttp import web

import utils.logger as log


# Token storage (in production, use Redis or database)
_ACTIVE_TOKENS: dict[str, dict] = {}

# Secret key for signing tokens
_SECRET_KEY = os.getenv("MBUDDY_AUTH_SECRET", secrets.token_hex(32))


def generate_token(name: str = "dashboard", expires_hours: int = 24) -> str:
    """Generate a signed bearer token."""
    token = secrets.token_urlsafe(32)
    expires = datetime.utcnow() + timedelta(hours=expires_hours)

    # Sign the token
    signature = hmac.new(
        _SECRET_KEY.encode(),
        token.encode(),
        hashlib.sha256
    ).hexdigest()[:16]

    signed_token = f"{token}.{signature}"

    _ACTIVE_TOKENS[signed_token] = {
        "name": name,
        "created": datetime.utcnow().isoformat(),
        "expires": expires.isoformat(),
    }

    log.info(f"Generated token for {name}, expires in {expires_hours}h")
    return signed_token


def validate_token(token: str) -> bool:
    """Validate a bearer token."""
    if not token:
        return False

    # Check if token exists and not expired
    if token not in _ACTIVE_TOKENS:
        return False

    token_data = _ACTIVE_TOKENS[token]
    expires = datetime.fromisoformat(token_data["expires"])

    if datetime.utcnow() > expires:
        # Token expired
        del _ACTIVE_TOKENS[token]
        return False

    # Verify signature
    try:
        token_part, signature = token.rsplit(".", 1)
        expected_sig = hmac.new(
            _SECRET_KEY.encode(),
            token_part.encode(),
            hashlib.sha256
        ).hexdigest()[:16]

        return hmac.compare_digest(signature, expected_sig)
    except Exception:
        return False


def revoke_token(token: str) -> bool:
    """Revoke a token."""
    if token in _ACTIVE_TOKENS:
        del _ACTIVE_TOKENS[token]
        log.info(f"Revoked token: {token[:16]}...")
        return True
    return False


def list_tokens() -> list[dict]:
    """List all active tokens (without revealing full token)."""
    now = datetime.utcnow()
    tokens = []

    for token, data in list(_ACTIVE_TOKENS.items()):
        expires = datetime.fromisoformat(data["expires"])
        if now > expires:
            del _ACTIVE_TOKENS[token]
            continue

        tokens.append({
            "name": data["name"],
            "created": data["created"],
            "expires": data["expires"],
            "preview": token[:16] + "...",
        })

    return tokens


def validate_device_token(token: str | None) -> bool:
    """
    Validate the shared device secret sent in a HELLO handshake (Phase 1B).

    Deliberately not the dashboard's generated/expiring bearer-token system
    above — this is one fixed shared secret compared in constant time,
    matching the firmware's existing MBUDDY_OTA_PASSWORD convention rather
    than inventing a per-device credential system.

    The shipped default (MBUDDY_DEVICE_TOKEN_DEFAULT) is fine for local/LAN
    development, where anyone on the network could already reach an
    unauthenticated /ws before Phase 1B — it isn't raising the bar there.
    But once a tunnel makes /ws reachable from the internet (is_tunnel_active,
    set by api/tunnel_manager.py when a tunnel actually starts), authenticating
    against a value published in this repo's own source would be equivalent to
    no auth at all. In that case every HELLO is refused — including ones that
    correctly quote the default — until MBUDDY_DEVICE_TOKEN is set to something
    that isn't the default, forcing that choice rather than silently allowing it.
    """
    from core.config import MBUDDY_DEVICE_TOKEN, MBUDDY_DEVICE_TOKEN_DEFAULT

    if not token or not MBUDDY_DEVICE_TOKEN:
        return False
    if is_tunnel_active() and MBUDDY_DEVICE_TOKEN == MBUDDY_DEVICE_TOKEN_DEFAULT:
        log.warn(
            "Device auth refused: MBUDDY_DEVICE_TOKEN is still the shipped default "
            "while a tunnel is active. Set a real secret (and the firmware's matching "
            "MBUDDY_DEVICE_TOKEN define) before /ws is reachable remotely."
        )
        return False
    return hmac.compare_digest(token, MBUDDY_DEVICE_TOKEN)


def get_bearer_token(request: web.Request) -> Optional[str]:
    """Extract bearer token from Authorization header."""
    auth_header = request.headers.get("Authorization", "")

    if auth_header.startswith("Bearer "):
        return auth_header[7:].strip()

    return None


def is_local_request(request: web.Request) -> bool:
    """Check if request is from localhost."""
    return request.remote in {"127.0.0.1", "::1", "localhost"}


def is_lan_allowed() -> bool:
    """Check if LAN access is allowed via environment variable."""
    return os.getenv("MBUDDY_DASHBOARD_ALLOW_LAN", "0").lower() in {"1", "true", "yes"}


def is_tunnel_active() -> bool:
    """
    Whether /ws or the dashboard could currently be reached from outside the
    LAN. Set by api/tunnel_manager.py when it actually starts/stops a
    tunnel; also settable by hand (e.g. a non-cloudflared deployment) since
    that was the original convention this reuses.
    """
    return os.getenv("MBUDDY_TUNNEL_ACTIVE", "0") == "1"


def require_auth(allow_local: bool = True):
    """
    Decorator to require authentication for a handler.

    Args:
        allow_local: If True, allow localhost requests without token when not tunneled
    """
    def decorator(handler):
        async def wrapper(request: web.Request):
            tunnel_active = is_tunnel_active()

            # If local request and tunnel not active and local allowed, bypass auth
            if allow_local and is_local_request(request) and not tunnel_active:
                return await handler(request)

            # If LAN is explicitly allowed and not tunneled, allow
            if allow_local and is_lan_allowed() and not tunnel_active:
                return await handler(request)

            # Otherwise require token
            token = get_bearer_token(request)

            if not token or not validate_token(token):
                raise web.HTTPUnauthorized(
                    text="Authentication required. Provide a valid bearer token.",
                    headers={"WWW-Authenticate": "Bearer"}
                )

            return await handler(request)

        return wrapper
    return decorator


# Token management endpoints

async def token_create_handler(request: web.Request) -> web.Response:
    """Create a new auth token (only from localhost)."""
    if not is_local_request(request):
        raise web.HTTPForbidden(text="Token creation only allowed from localhost")

    try:
        data = await request.json()
    except Exception:
        data = {}

    name = data.get("name", "dashboard")
    expires_hours = int(data.get("expires_hours", 24))

    if expires_hours < 1 or expires_hours > 168:  # Max 1 week
        raise web.HTTPBadRequest(text="expires_hours must be between 1 and 168")

    token = generate_token(name, expires_hours)

    return web.json_response({
        "ok": True,
        "token": token,
        "name": name,
        "expires_hours": expires_hours,
        "message": "Store this token securely. Use it in Authorization: Bearer <token> header."
    })


async def token_list_handler(request: web.Request) -> web.Response:
    """List active tokens (only from localhost)."""
    if not is_local_request(request):
        raise web.HTTPForbidden(text="Token listing only allowed from localhost")

    tokens = list_tokens()

    return web.json_response({
        "ok": True,
        "tokens": tokens,
        "count": len(tokens)
    })


async def token_revoke_handler(request: web.Request) -> web.Response:
    """Revoke a token (only from localhost)."""
    if not is_local_request(request):
        raise web.HTTPForbidden(text="Token revocation only allowed from localhost")

    try:
        data = await request.json()
    except Exception:
        raise web.HTTPBadRequest(text="Request body must be valid JSON")

    token = data.get("token", "").strip()

    if not token:
        raise web.HTTPBadRequest(text="token field is required")

    revoked = revoke_token(token)

    return web.json_response({
        "ok": True,
        "revoked": revoked
    })
