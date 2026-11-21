"""Sign in with Google for Northchart (OpenID Connect, authorisation code flow).

* PKCE (S256), a random state bound to the browser by a short-lived HttpOnly cookie,
  and a nonce checked inside the ID token.
* The ID token comes straight from Google's token endpoint over TLS using the
  client secret, so, as OpenID Connect Core 3.1.3.7 allows, TLS stands in for the
  signature check. The issuer, audience, expiry, nonce and email_verified claims are
  still checked.
* Configure with GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET (a "Web application" OAuth
  client in Google Cloud). Its authorised redirect URI must be <desk>/auth/google/callback.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

STATE_TTL = 600
ISSUERS = ("accounts.google.com", "https://accounts.google.com")


def enabled() -> bool:
    return bool(os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET"))


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


class GoogleAuth:
    def __init__(self):
        self.lock = threading.Lock()
        self.pending = {}          # state -> {verifier, nonce, next, invite, mode, uid, exp}

    def _purge(self):
        now = time.time()
        for k in [k for k, v in self.pending.items() if v["exp"] < now]:
            del self.pending[k]

    def start(self, base, next_url="/", invite="", mode="signin", uid=None):
        """Returns (redirect URL, state). The caller sets the state as a cookie."""
        state, verifier, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(48), secrets.token_urlsafe(16)
        with self.lock:
            self._purge()
            self.pending[state] = {"verifier": verifier, "nonce": nonce, "next": next_url, "invite": invite,
                                   "mode": mode, "uid": uid, "exp": time.time() + STATE_TTL}
        params = {"client_id": os.environ["GOOGLE_CLIENT_ID"], "redirect_uri": base + "/auth/google/callback",
                  "response_type": "code", "scope": "openid email profile", "state": state, "nonce": nonce,
                  "code_challenge": _b64url(hashlib.sha256(verifier.encode()).digest()),
                  "code_challenge_method": "S256", "prompt": "select_account"}
        url = os.environ.get("GOOGLE_AUTH_URL", "https://accounts.google.com/o/oauth2/v2/auth")
        return url + "?" + urllib.parse.urlencode(params), state

    def finish(self, base, code, state, cookie_state):
        """Exchange the code. Returns (claims, pending) or raises ValueError with a message for the user."""
        if not state or not cookie_state or not secrets.compare_digest(state, cookie_state):
            raise ValueError("This Google sign-in was started in another browser or tab. Try again.")
        with self.lock:
            p = self.pending.pop(state, None)
        if not p or p["exp"] < time.time():
            raise ValueError("The Google sign-in took too long. Try again.")
        if not code:
            raise ValueError("Google did not complete the sign-in.")
        body = urllib.parse.urlencode({
            "code": code, "client_id": os.environ["GOOGLE_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_CLIENT_SECRET"], "redirect_uri": base + "/auth/google/callback",
            "grant_type": "authorization_code", "code_verifier": p["verifier"]}).encode()
        req = urllib.request.Request(os.environ.get("GOOGLE_TOKEN_URL", "https://oauth2.googleapis.com/token"),
                                     data=body, method="POST",
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                tok = json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise ValueError(f"Google refused the sign-in ({e.code}). Try again.") from None
        except OSError:
            raise ValueError("Could not reach Google. Try again.") from None
        claims = self.claims(tok.get("id_token") or "")
        if claims.get("iss") not in ISSUERS:
            raise ValueError("The sign-in did not come from Google.")
        if claims.get("aud") != os.environ["GOOGLE_CLIENT_ID"]:
            raise ValueError("The Google sign-in was for a different app.")
        if float(claims.get("exp") or 0) < time.time() - 60:
            raise ValueError("The Google sign-in expired. Try again.")
        if not secrets.compare_digest(str(claims.get("nonce") or ""), p["nonce"]):
            raise ValueError("The Google sign-in could not be verified. Try again.")
        if not claims.get("email") or claims.get("email_verified") not in (True, "true"):
            raise ValueError("Your Google account has no verified email address.")
        return claims, p

    @staticmethod
    def claims(id_token):
        try:
            payload = id_token.split(".")[1]
            return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        except Exception:
            raise ValueError("Google sent an unreadable sign-in.") from None
