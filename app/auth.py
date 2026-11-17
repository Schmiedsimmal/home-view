"""Simple password auth for the web UI.

Signed client-side session cookie (HMAC-SHA256, stdlib only — JetPack 4.x
ships Python 3.6, which current itsdangerous/starlette-session versions no
longer support). Every route except /login and /healthz requires a valid
session. If ``WEB_PASSWORD`` is empty, auth is disabled entirely.
"""
import base64
import hashlib
import hmac
import json
import logging
import time
import urllib.parse
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .config import WebConfig

log = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

# Paths that never require authentication.
_PUBLIC_PATHS = {"/login", "/healthz"}

SESSION_COOKIE = "hm_session"
SESSION_TTL_S = 7 * 24 * 3600  # 7 days


# -- session token -------------------------------------------------------------
def make_session_token(secret: str, ttl_s: int = SESSION_TTL_S) -> str:
    """Create a signed session token: ``base64(payload).hmac_hex``."""
    payload = json.dumps({"exp": time.time() + ttl_s}).encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=") + "." + sig


def verify_session_token(token: str, secret: str) -> bool:
    """Return True if *token* has a valid signature and is not expired."""
    try:
        b64, sig = token.rsplit(".", 1)
        payload = base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4))
        expected = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return False
        data = json.loads(payload.decode("utf-8"))
        return time.time() < float(data.get("exp", 0))
    except Exception:  # noqa: BLE001
        return False


# -- middleware -----------------------------------------------------------------
class AuthMiddleware(BaseHTTPMiddleware):
    """Redirect browsers to /login, return 401 for API/stream clients."""

    def __init__(self, app, password: str, secret: str) -> None:
        super().__init__(app)
        self._password = password
        self._secret = secret

    async def dispatch(self, request: Request, call_next):
        if not self._password:
            return await call_next(request)

        path = request.url.path
        if path in _PUBLIC_PATHS:
            return await call_next(request)

        token = request.cookies.get(SESSION_COOKIE, "")
        if token and verify_session_token(token, self._secret):
            return await call_next(request)

        # API / stream / recording clients get a clean 401.
        if path.startswith("/api/") or path == "/stream" or path.startswith("/recordings/"):
            return JSONResponse({"detail": "Nicht authentifiziert"}, status_code=401)

        return RedirectResponse("/login", status_code=303)


def _render_login(error: str = "") -> HTMLResponse:
    html = (TEMPLATES_DIR / "login.html").read_text(encoding="utf-8")
    banner = f'<div class="error">{error}</div>' if error else ""
    return HTMLResponse(html.replace("{{ERROR}}", banner))


def setup_auth(app: FastAPI, cfg: WebConfig) -> None:
    """Install auth middleware + login/logout routes."""
    app.add_middleware(AuthMiddleware, password=cfg.password, secret=cfg.secret)

    if not cfg.password:
        log.warning("WEB_PASSWORD ist leer — Authentifizierung DEAKTIVIERT!")

    @app.get("/login", response_class=HTMLResponse)
    async def login_form(error: str = "") -> HTMLResponse:
        msg = "Falsches Passwort." if error else ""
        return _render_login(msg)

    @app.post("/login")
    async def login_submit(request: Request) -> RedirectResponse:
        # application/x-www-form-urlencoded body, e.g. "password=...".
        body = (await request.body()).decode("utf-8", errors="replace")
        form = urllib.parse.parse_qs(body)
        password = form.get("password", [""])[0]
        if hmac.compare_digest(password, cfg.password):
            resp = RedirectResponse("/", status_code=303)
            resp.set_cookie(
                SESSION_COOKIE,
                make_session_token(cfg.secret),
                max_age=SESSION_TTL_S,
                httponly=True,
                samesite="lax",
            )
            return resp
        return RedirectResponse("/login?error=1", status_code=303)

    @app.get("/logout")
    async def logout() -> RedirectResponse:
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie(SESSION_COOKIE)
        return resp
