"""Tests for clipper.faceit_oauth: the FACEIT Connect sign-in (spec: Settings, Sign in with FACEIT)."""
import base64
import hashlib
import urllib.error
import urllib.parse

import pytest

from clipper import faceit_oauth
from clipper.faceit_oauth import (
    OAuthError, access_token, authorize_url, challenge, new_state, new_verifier, player_id, userinfo,
)


def test_the_authorize_url_asks_for_a_code_with_pkce_and_carries_the_state():
    url = authorize_url("client-1", "https://localhost:8765/settings", "state-1", "chal-1")
    assert url.startswith(faceit_oauth.AUTHORIZE_URL + "?")
    query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
    assert query == {"client_id": "client-1", "response_type": "code",
                     "redirect_uri": "https://localhost:8765/settings",
                     "scope": "openid profile", "state": "state-1",
                     "code_challenge": "chal-1", "code_challenge_method": "S256"}


def test_each_state_and_verifier_is_different():
    assert new_state() != new_state()
    assert new_verifier() != new_verifier()


def test_the_pkce_challenge_is_the_sha256_of_the_verifier_without_padding():
    verifier = new_verifier()
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    assert challenge(verifier) == base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    assert 43 <= len(verifier) <= 128                 # RFC 7636's range for a verifier


def test_the_code_exchange_sends_the_verifier_and_the_client_secret_in_the_basic_header():
    seen = {}

    def post(request):
        seen["url"] = request.full_url
        seen["auth"] = request.get_header("Authorization")
        seen["body"] = dict(urllib.parse.parse_qsl(request.data.decode("ascii")))
        return {"access_token": "tok-1"}

    token = access_token("code-1", "client-1", "s3cret", "https://x/", "ver-1", post=post)

    assert token == "tok-1"
    assert seen["url"] == faceit_oauth.TOKEN_URL
    assert seen["auth"] == "Basic " + base64.b64encode(b"client-1:s3cret").decode("ascii")
    assert seen["body"] == {"grant_type": "authorization_code", "code": "code-1",
                            "redirect_uri": "https://x/", "code_verifier": "ver-1"}


def test_an_exchange_without_a_token_is_an_oauth_error():
    with pytest.raises(OAuthError, match="access token"):
        access_token("c", "i", "s", "r", "v", post=lambda request: {"error": "invalid_grant"})


@pytest.mark.parametrize("data", [{"sub": "p-1"}, {"guid": "p-1"}, {"id": "p-1"}])
def test_player_id_reads_whichever_key_faceit_uses(data):
    assert player_id("tok", fetch=lambda token: data) == "p-1"


def test_a_userinfo_answer_without_an_id_is_an_oauth_error():
    with pytest.raises(OAuthError, match="which player"):
        player_id("tok", fetch=lambda token: {"email": "a@b.c"})


def test_userinfo_sends_the_token_bearer_and_parses_json(monkeypatch):
    seen = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b'{"sub": "p-1"}'

    def urlopen(request, timeout):
        seen["auth"] = request.get_header("Authorization")
        seen["url"] = request.full_url
        return Response()

    monkeypatch.setattr(faceit_oauth.urllib.request, "urlopen", urlopen)

    assert userinfo("tok") == {"sub": "p-1"}
    assert seen == {"auth": "Bearer tok", "url": faceit_oauth.USERINFO_URL}


def test_a_rejected_sign_in_becomes_an_oauth_error_without_the_token(monkeypatch):
    def urlopen(request, timeout):
        raise urllib.error.HTTPError("u", 401, "Unauthorized", None, None)

    monkeypatch.setattr(faceit_oauth.urllib.request, "urlopen", urlopen)

    with pytest.raises(OAuthError) as caught:
        userinfo("secret-token")

    assert "401" in str(caught.value)
    assert "secret-token" not in str(caught.value)
