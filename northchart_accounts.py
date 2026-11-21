"""Northchart accounts: sign in, sign up and per-user data.

Standard library only.

* Passwords: scrypt (n=2**14, r=8, p=1) with a per-user salt, or PBKDF2-SHA256 with
  600,000 rounds where scrypt is missing. Only the hash is stored.
* Sessions: a random 32-byte token in an HttpOnly, SameSite=Lax cookie (Secure over
  HTTPS). The server keeps only its SHA-256, so a stolen sessions file cannot be
  replayed. 30 days, sliding.
* CSRF: a cookie-authenticated POST must carry an Origin or Referer header that
  matches the host. SameSite=Lax already blocks cross-site cookie POSTs; this is
  the second lock.
* Lockout: five failed sign-ins per email and per IP address in 15 minutes.
* Sign-up mode (NORTHCHART_SIGNUP): "invite" (default; a code from the owner),
  "open", or "closed".
* Owner: the first account. It is created at /setup, which needs the existing desk
  password when one is set (SIGNALDESK_PASS), or a request from this computer
  when none is. The owner keeps the desk's existing journal; every other user
  gets an empty journal under DATA_DIR/users/<uid>/.
* Scripts: TradingView sync and the Northchart Bridge send a per-user ingest token
  (Bearer nci_...) that can only add trades, or the account email and password as
  HTTP Basic. The old desk password keeps working as the owner for one release
  (NORTHCHART_LEGACY_BASIC=0 turns it off).
* Email: verification and reset links go out over SMTP when SMTP_HOST is set
  (SMTP_PORT, SMTP_USER, SMTP_PASS, SMTP_FROM). Without it they are written to the
  server log and DATA_DIR/accounts/outbox.log, so the owner can pass them on.
"""

from __future__ import annotations

import base64
import contextvars
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import smtplib
import sys
import threading
import time
from email.message import EmailMessage

SESSION_TTL = 30 * 86400
RESET_TTL = 3600
VERIFY_TTL = 3 * 86400
INVITE_TTL = 14 * 86400
COOKIE = "nc_s"
MIN_PASSWORD = 10
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[A-Za-z]{2,}$")
COMMON = {"password12", "password123", "1234567890", "qwertyuiop", "northchart", "trader1234",
          "letmein123", "iloveyou12", "0123456789", "passw0rd12", "administrator"}

current_user = contextvars.ContextVar("northchart_user", default=None)


def _now() -> int:
    return int(time.time())


def _h(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    try:
        dk = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
        return "scrypt$16384$8$1$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()
    except (AttributeError, ValueError):
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600_000)
        return "pbkdf2$600000$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        parts = stored.split("$")
        if parts[0] == "scrypt":
            n, r, p = int(parts[1]), int(parts[2]), int(parts[3])
            salt, want = base64.b64decode(parts[4]), base64.b64decode(parts[5])
            got = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=len(want))
        elif parts[0] == "pbkdf2":
            salt, want = base64.b64decode(parts[2]), base64.b64decode(parts[3])
            got = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(parts[1]))
        else:
            return False
        return hmac.compare_digest(got, want)
    except Exception:
        return False


def password_problem(password: str, email: str = "") -> str | None:
    if len(password or "") < MIN_PASSWORD:
        return f"Use at least {MIN_PASSWORD} characters."
    if len(password) > 200:
        return "That password is too long."
    low = password.lower()
    if low in COMMON or len(set(low)) < 4:
        return "That password is too easy to guess."
    if email and low == email.lower():
        return "Do not use your email address as the password."
    return None


def is_loopback(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_loopback
    except ValueError:
        return False


class AuthError(ValueError):
    """Safe to show to the user."""


class Accounts:
    def __init__(self, data_dir: str, legacy_user_fn=None, legacy_pass_fn=None):
        self.data_dir = data_dir
        self.dir = os.path.join(data_dir, "accounts")
        self.path = os.path.join(self.dir, "accounts.json")
        self.legacy_user = legacy_user_fn or (lambda: "trader")
        self.legacy_pass = legacy_pass_fn or (lambda: None)
        self.lock = threading.RLock()
        self.fails: dict[str, list[float]] = {}
        self.data = self._load()

    # ---------------------------------------------------------- storage
    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            d = {}
        for k in ("users", "sessions", "invites", "tokens", "ingest"):
            d.setdefault(k, {})
        return d

    def _save(self):
        os.makedirs(self.dir, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=1, sort_keys=True)
        os.replace(tmp, self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def _purge(self):
        now = _now()
        for k in ("sessions", "invites", "tokens"):
            for key in [key for key, v in self.data[k].items() if v.get("exp", now + 1) < now]:
                del self.data[k][key]

    # ---------------------------------------------------------- facts
    def has_users(self) -> bool:
        return bool(self.data["users"])

    def signup_mode(self) -> str:
        m = (os.environ.get("NORTHCHART_SIGNUP") or "invite").lower()
        return m if m in ("invite", "open", "closed") else "invite"

    def owner(self):
        return next((u for u in self.data["users"].values() if u.get("role") == "owner"), None)

    def by_email(self, email):
        email = (email or "").strip().lower()
        return next((u for u in self.data["users"].values() if u["email"] == email), None)

    def get(self, uid):
        return self.data["users"].get(uid)

    @staticmethod
    def public(u):
        if not u:
            return None
        return {"uid": u["uid"], "email": u["email"], "name": u.get("name") or "", "role": u.get("role"),
                "verified": bool(u.get("verified")), "created": u.get("created")}

    def user_dir(self, u) -> str:
        """Where this user's journal lives. The owner keeps the desk's existing journal."""
        if not u or u.get("role") == "owner" or u.get("uid") == "owner":
            return self.data_dir
        return os.path.join(self.data_dir, "users", u["uid"])

    def smtp_ready(self) -> bool:
        return bool(os.environ.get("SMTP_HOST"))

    # ---------------------------------------------------------- rate limits
    def _limited(self, *keys):
        now = time.time()
        with self.lock:
            for k in keys:
                self.fails[k] = [t for t in self.fails.get(k, []) if now - t < 900]
                if len(self.fails[k]) >= 5:
                    return True
        return False

    def _fail(self, *keys):
        with self.lock:
            for k in keys:
                self.fails.setdefault(k, []).append(time.time())

    # ---------------------------------------------------------- creating accounts
    def _new_user(self, email, name, password, role, verified):
        email = (email or "").strip().lower()
        name = (name or "").strip()[:80]
        if not EMAIL_RE.match(email):
            raise AuthError("Enter a valid email address.")
        prob = password_problem(password, email)
        if prob:
            raise AuthError(prob)
        if self.by_email(email):
            raise AuthError("An account with this email already exists. Sign in instead.")
        uid = "u_" + secrets.token_hex(8)
        u = {"uid": uid, "email": email, "name": name, "role": role, "created": _now(),
             "verified": bool(verified), "pw": hash_password(password)}
        self.data["users"][uid] = u
        return u

    def setup_owner(self, email, name, password, legacy_password, ip):
        """First account on this desk. Needs the desk password, or this computer when none is set."""
        with self.lock:
            if self.has_users():
                raise AuthError("This desk already has an owner. Sign in instead.")
            legacy = self.legacy_pass()
            if legacy:
                if self._limited("setup:" + ip):
                    raise AuthError("Too many attempts. Wait 15 minutes.")
                if not hmac.compare_digest((legacy_password or "").encode(), legacy.encode()):
                    self._fail("setup:" + ip)
                    raise AuthError("That is not the current desk password.")
            elif not is_loopback(ip):
                raise AuthError("Create the owner account from the computer running the desk "
                                "(http://localhost:8899/setup), or set SIGNALDESK_PASS first.")
            u = self._new_user(email, name, password, "owner", verified=True)
            self._save()
            return u

    def signup(self, email, name, password, invite, ip):
        with self.lock:
            if not self.has_users():
                raise AuthError("This desk has no owner yet. The owner signs up first at /setup.")
            mode = self.signup_mode()
            if mode == "closed":
                raise AuthError("Sign-up is closed on this desk.")
            if self._limited("signup:" + ip):
                raise AuthError("Too many attempts. Wait 15 minutes.")
            inv_key = None
            if mode == "invite":
                inv_key = _h((invite or "").strip())
                inv = self.data["invites"].get(inv_key)
                if not inv or inv.get("used_by") or inv.get("exp", 0) < _now():
                    self._fail("signup:" + ip)
                    raise AuthError("That invite code is not valid. Ask the desk owner for a new one.")
                if inv.get("email") and inv["email"] != (email or "").strip().lower():
                    raise AuthError("This invite was issued for a different email address.")
            u = self._new_user(email, name, password, "member", verified=False)
            if inv_key:
                self.data["invites"][inv_key]["used_by"] = u["uid"]
                self.data["invites"][inv_key]["used_at"] = _now()
            self._save()
            return u

    # ---------------------------------------------------------- sessions
    def login(self, email, password, ip, ua=""):
        email = (email or "").strip().lower()
        if self._limited("email:" + email, "ip:" + ip):
            raise AuthError("Too many attempts. Wait 15 minutes and try again.")
        with self.lock:
            u = self.by_email(email)
            ok = verify_password(password or "", u["pw"]) if u else verify_password(password or "", _DUMMY)
            if not u or not ok:
                self._fail("email:" + email, "ip:" + ip)
                raise AuthError("Wrong email or password.")
            return u, self.new_session(u["uid"], ip, ua)

    def new_session(self, uid, ip, ua=""):
        tok = secrets.token_urlsafe(32)
        with self.lock:
            self._purge()
            self.data["sessions"][_h(tok)] = {"uid": uid, "created": _now(), "seen": _now(),
                                              "exp": _now() + SESSION_TTL, "ip": ip, "ua": (ua or "")[:160],
                                              "sid": secrets.token_hex(6)}
            self._save()
        return tok

    def session_user(self, token):
        if not token:
            return None
        with self.lock:
            s = self.data["sessions"].get(_h(token))
            if not s or s["exp"] < _now():
                return None
            u = self.get(s["uid"])
            if not u:
                return None
            if _now() - s.get("seen", 0) > 3600:        # sliding expiry, written at most hourly
                s["seen"], s["exp"] = _now(), _now() + SESSION_TTL
                self._save()
            return u

    def logout(self, token):
        with self.lock:
            if self.data["sessions"].pop(_h(token or ""), None):
                self._save()

    def sessions(self, uid, current_token=None):
        cur = _h(current_token or "")
        return [{"sid": s["sid"], "created": s["created"], "seen": s.get("seen"), "ip": s.get("ip"),
                 "ua": s.get("ua"), "current": k == cur}
                for k, s in self.data["sessions"].items() if s["uid"] == uid and s["exp"] >= _now()]

    def revoke_sessions(self, uid, sid=None, keep_token=None):
        keep = _h(keep_token or "")
        with self.lock:
            for k in [k for k, s in self.data["sessions"].items()
                      if s["uid"] == uid and (s["sid"] == sid if sid else k != keep)]:
                del self.data["sessions"][k]
            self._save()

    # ---------------------------------------------------------- scripts: Basic and ingest tokens
    def basic_user(self, header: str):
        """HTTP Basic: account email and password, or the old desk password as the owner."""
        try:
            u_name, _, pw = base64.b64decode(header[6:]).decode().partition(":")
        except Exception:
            return None
        legacy = self.legacy_pass()
        if legacy and os.environ.get("NORTHCHART_LEGACY_BASIC", "1") != "0" \
                and hmac.compare_digest(u_name.encode(), self.legacy_user().encode()) \
                & hmac.compare_digest(pw.encode(), legacy.encode()):
            return self.owner() or {"uid": "owner", "email": "", "name": "Desk owner", "role": "owner", "verified": True}
        if "@" not in u_name or self._limited("email:" + u_name.lower()):
            return None
        u = self.by_email(u_name)
        if u and verify_password(pw, u["pw"]):
            return u
        self._fail("email:" + u_name.lower())
        return None

    def new_ingest_token(self, uid, label):
        tok = "nci_" + secrets.token_urlsafe(30)
        with self.lock:
            self.data["ingest"][_h(tok)] = {"uid": uid, "label": (label or "Script")[:60], "created": _now(),
                                            "id": secrets.token_hex(5), "last": None}
            self._save()
        return tok

    def ingest_user(self, token):
        if not token or not token.startswith("nci_"):
            return None
        with self.lock:
            rec = self.data["ingest"].get(_h(token))
            if not rec:
                return None
            if not rec.get("last") or _now() - rec["last"] > 600:
                rec["last"] = _now()
                self._save()
            return self.get(rec["uid"]) or (self.owner() if rec["uid"] == "owner" else None)

    def ingest_tokens(self, uid):
        return [{"id": r["id"], "label": r["label"], "created": r["created"], "last": r.get("last")}
                for r in self.data["ingest"].values() if r["uid"] == uid]

    def revoke_ingest(self, uid, tid):
        with self.lock:
            for k in [k for k, r in self.data["ingest"].items() if r["uid"] == uid and r["id"] == tid]:
                del self.data["ingest"][k]
            self._save()

    # ---------------------------------------------------------- invites
    def new_invite(self, by_uid, email=None):
        code = "-".join(secrets.token_hex(2).upper() for _ in range(3))
        with self.lock:
            self._purge()
            self.data["invites"][_h(code)] = {"by": by_uid, "email": (email or "").strip().lower() or None,
                                              "created": _now(), "exp": _now() + INVITE_TTL,
                                              "id": secrets.token_hex(4), "hint": code[:4] + "…"}
            self._save()
        return code

    def invites(self):
        out = []
        for v in self.data["invites"].values():
            used = self.get(v.get("used_by")) if v.get("used_by") else None
            out.append({"id": v["id"], "email": v.get("email"), "hint": v.get("hint"), "created": v["created"],
                        "exp": v["exp"], "used_by": used["email"] if used else None})
        return sorted(out, key=lambda x: -x["created"])

    def revoke_invite(self, iid):
        with self.lock:
            for k in [k for k, v in self.data["invites"].items() if v["id"] == iid and not v.get("used_by")]:
                del self.data["invites"][k]
            self._save()

    # ---------------------------------------------------------- email links
    def _link(self, kind, uid, ttl):
        tok = secrets.token_urlsafe(32)
        with self.lock:
            self._purge()
            for k in [k for k, v in self.data["tokens"].items() if v["uid"] == uid and v["kind"] == kind]:
                del self.data["tokens"][k]
            self.data["tokens"][_h(tok)] = {"uid": uid, "kind": kind, "exp": _now() + ttl}
            self._save()
        return tok

    def _take(self, kind, tok):
        with self.lock:
            rec = self.data["tokens"].get(_h(tok or ""))
            if not rec or rec["kind"] != kind or rec["exp"] < _now():
                return None
            del self.data["tokens"][_h(tok)]
            self._save()
            return self.get(rec["uid"])

    def send_verify(self, u, base):
        tok = self._link("verify", u["uid"], VERIFY_TTL)
        self.mail(u["email"], "Confirm your Northchart email",
                  f"Hello {u.get('name') or ''},\n\nConfirm your email for Northchart:\n{base}/verify?token={tok}\n\n"
                  "The link works for three days. If you did not sign up, ignore this email.\n")

    def verify(self, tok):
        u = self._take("verify", tok)
        if not u:
            raise AuthError("This confirmation link is invalid or has expired.")
        with self.lock:
            u["verified"] = True
            self._save()
        return u

    def start_reset(self, email, base, ip):
        """Always answers the same way, so it cannot be used to find out who has an account."""
        if self._limited("reset:" + ip):
            raise AuthError("Too many requests. Wait 15 minutes.")
        self._fail("reset:" + ip)
        u = self.by_email(email)
        if not u:
            return
        tok = self._link("reset", u["uid"], RESET_TTL)
        self.mail(u["email"], "Reset your Northchart password",
                  f"Someone asked to reset the password for {u['email']}.\n\nSet a new password here:\n"
                  f"{base}/reset?token={tok}\n\nThe link works for one hour. If it was not you, ignore this email; "
                  "your password has not changed.\n")

    def finish_reset(self, tok, password):
        with self.lock:
            rec = self.data["tokens"].get(_h(tok or ""))
            u = self.get(rec["uid"]) if rec and rec["kind"] == "reset" and rec["exp"] >= _now() else None
            if not u:
                raise AuthError("This reset link is invalid or has expired. Ask for a new one.")
            prob = password_problem(password, u["email"])
            if prob:
                raise AuthError(prob)
            self._take("reset", tok)
            u["pw"] = hash_password(password)
            u["verified"] = True                       # they proved they read the inbox
            self.revoke_sessions(u["uid"])             # sign out everywhere
            self._save()
            return u

    def change_password(self, uid, old, new, keep_token=None):
        with self.lock:
            u = self.get(uid)
            if not u or not verify_password(old or "", u["pw"]):
                raise AuthError("Your current password is not right.")
            prob = password_problem(new, u["email"])
            if prob:
                raise AuthError(prob)
            u["pw"] = hash_password(new)
            self.revoke_sessions(uid, keep_token=keep_token)
            self._save()

    def update_profile(self, uid, name):
        with self.lock:
            u = self.get(uid)
            u["name"] = (name or "").strip()[:80]
            self._save()
            return u

    def delete(self, uid, password):
        with self.lock:
            u = self.get(uid)
            if not u or not verify_password(password or "", u["pw"]):
                raise AuthError("Your password is not right.")
            if u.get("role") == "owner":
                raise AuthError("The owner account holds the desk itself and cannot be deleted here.")
            del self.data["users"][uid]
            for k in ("sessions", "tokens", "ingest"):
                for key in [key for key, v in self.data[k].items() if v.get("uid") == uid]:
                    del self.data[k][key]
            self._save()
        import shutil
        shutil.rmtree(self.user_dir(u), ignore_errors=True)

    def mail(self, to, subject, text):
        if self.smtp_ready():
            msg = EmailMessage()
            msg["From"] = os.environ.get("SMTP_FROM") or os.environ.get("SMTP_USER") or "Northchart <no-reply@localhost>"
            msg["To"], msg["Subject"] = to, subject
            msg.set_content(text)
            port = int(os.environ.get("SMTP_PORT") or 587)
            try:
                if port == 465:
                    srv = smtplib.SMTP_SSL(os.environ["SMTP_HOST"], port, timeout=20)
                else:
                    srv = smtplib.SMTP(os.environ["SMTP_HOST"], port, timeout=20)
                    srv.starttls()
                with srv:
                    if os.environ.get("SMTP_USER"):
                        srv.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASS", ""))
                    srv.send_message(msg)
                return True
            except Exception as e:
                print(f"accounts: email to {to} failed: {e}", file=sys.stderr)
        os.makedirs(self.dir, exist_ok=True)
        with open(os.path.join(self.dir, "outbox.log"), "a", encoding="utf-8") as f:
            f.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} to {to}: {subject}\n{text}\n")
        print(f"accounts: no SMTP configured; email to {to} ({subject}) written to accounts/outbox.log",
              file=sys.stderr)
        return False


_DUMMY = hash_password(secrets.token_hex(8))      # equal work for unknown emails
