"""FACEIT Connect (OAuth2): "Sign in with FACEIT", so the app knows which FACEIT player is at the
keyboard. It answers only *who* the user is; the Data API key stays the app's own, because FACEIT's
Data API is used "not on behalf of the user" (docs.faceit.com).

Authorization Code flow with PKCE: the only grant type FACEIT's App Studio lets you create, and the
one its docs describe. The code exchange is authenticated with the client secret
(`client_secret_basic`); the PKCE verifier never leaves this app.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable

AUTHORIZE_URL = "https://accounts.faceit.com"
TOKEN_URL = "https://api.faceit.com/auth/v1/oauth/token"
USERINFO_URL = "https://api.faceit.com/auth/v1/resources/userinfo"
SCOPE = "openid profile"
# FACEIT refuses a plain-http redirect URI, and the app listens on http://127.0.0.1:<port>. So the
# redirect goes to this https page (docs/index.html, served by GitHub Pages), which hands FACEIT's
# answer straight back to the app on the port carried at the front of `state`.
RELAY_URL = "https://mohammadam02.github.io/cs2-clipper/"


class OAuthError(Exception):
    """FACEIT refused the sign-in, or could not be reached."""


def new_state() -> str:
    """A one-shot value tying the redirect back to this app (CSRF)."""
    return secrets.token_urlsafe(24)


def new_verifier() -> str:
    """A PKCE code verifier: the secret half of the exchange, kept here until the code comes back."""
    return secrets.token_urlsafe(64)


def challenge(verifier: str) -> str:
    """The PKCE S256 challenge for `verifier` (RFC 7636)."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def authorize_url(client_id: str, redirect_uri: str, state: str, code_challenge: str) -> str:
    """Where to send the browser to ask the user to sign in."""
    query = urllib.parse.urlencode({
        "client_id": client_id, "response_type": "code", "redirect_uri": redirect_uri,
        "scope": SCOPE, "state": state, "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    })
    return f"{AUTHORIZE_URL}?{query}"


def access_token(code: str, client_id: str, client_secret: str, redirect_uri: str, verifier: str,
                 post: Callable[[urllib.request.Request], dict] | None = None) -> str:
    """The access token for an authorization `code`, via the PKCE verifier and the client secret."""
    body = urllib.parse.urlencode({
        "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
        "code_verifier": verifier,
    }).encode("ascii")
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
    request = urllib.request.Request(TOKEN_URL, data=body, method="POST", headers={
        "Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json", "User-Agent": "cs2-clipper"})
    data = (post or _read_json)(request)
    token = data.get("access_token") if isinstance(data, dict) else None
    if not token:
        raise OAuthError("FACEIT did not return an access token")
    return str(token)


def player_id(access_token: str, fetch: Callable[[str], dict] | None = None) -> str:
    """The FACEIT player id the signed-in token belongs to."""
    data = (fetch or userinfo)(access_token)
    for key in ("sub", "guid", "id"):
        value = data.get(key) if isinstance(data, dict) else None
        if value:
            return str(value)
    raise OAuthError("FACEIT did not say which player this is")


def userinfo(access_token: str) -> dict:
    request = urllib.request.Request(USERINFO_URL, headers={
        "Authorization": f"Bearer {access_token}", "Accept": "application/json",
        "User-Agent": "cs2-clipper"})
    return _read_json(request)


def _read_json(request: urllib.request.Request) -> dict:
    try:
        with urllib.request.urlopen(request, timeout=20.0) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise OAuthError(f"FACEIT refused the sign-in (HTTP {exc.code})") from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise OAuthError(f"FACEIT could not be reached: {type(exc).__name__}") from None
