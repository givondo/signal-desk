"""Northcharts billing: plans, checkout, renewals and what each plan unlocks.

Standard library only.

Plans (USD list prices; change them with environment variables, see PRICE_ENV):
    Free   desk view, live chart, desk signals, manual journal and statement import
    Plus   $25 a month: automatic broker sync, agent (fair use), Claude journal drafts
    Pro    $200 a month: everything in Plus, more brokers, a much larger agent
           allowance, and Northcharts Connect (ChatGPT and Claude)
    Plans are monthly only.

Payments
    Card     Paystack (hosted checkout, so card numbers never touch Northcharts).
             The first payment saves a reusable card authorisation; renewals charge it
             with /transaction/charge_authorization. Webhooks are checked with
             HMAC-SHA512 over the raw body using the secret key, and every success is
             re-verified with /transaction/verify before it counts.
    M-Pesa   Safaricom Daraja STK push (Lipa na M-Pesa Online) to the desk's Paybill or
             Till. Callbacks are not signed, so a callback only triggers a status query
             (stkpushquery) and the query decides. Renewal: either an STK prompt on the
             due date (default) or an M-Pesa Ratiba standing order the customer approves
             once. Ratiba debits arrive as C2B confirmations, which are accepted only
             from Safaricom's published callback addresses and only for the exact
             account reference and amount.

Prices are set in USD. M-Pesa always charges KES; cards charge KES unless
PAYSTACK_CURRENCY=USD. The KES amount is the USD price at the day's rate
(NORTHCHART_KES_PER_USD, or the desk's USDKES quote), rounded up to the next KES 50,
unless a fixed KES price is set (NORTHCHART_PRICE_PLUS_MONTH_KES and so on).

NORTHCHART_BILLING: "off" (default; nothing is locked), "test" (sandbox keys, locks on)
or "live". The desk owner always has every feature.
"""

from __future__ import annotations

import base64
import calendar
import datetime as dt
import hashlib
import hmac
import ipaddress
import json
import sys
import math
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

PLAN_ORDER = ("free", "plus", "pro")
INTERVALS = ("month",)
GRACE_DAYS = 3
REMIND_DAYS = 3
STK_RETRY_HOURS = 24
STK_MAX_TRIES = 3
PENDING_TTL = 30 * 60
MPESA_MAX_KES = 250_000      # Safaricom's limit for one M-Pesa payment (and for a Ratiba order)

PLANS = {
    "free": {"name": "Free", "usd": {"month": 0},
             "tagline": "The desk, free for every trader.",
             "features": ["Live desk calls and grades on every instrument", "Northcharts chart with drawing tools",
                          "Manual trade journal with statistics", "Import MT4 and MT5 statements",
                          "3 agent questions a day"]},
    "plus": {"name": "Plus", "usd": {"month": 25},
             "tagline": "Your journal fills itself.",
             "features": ["Everything in Free", "Automatic broker sync (2 accounts)",
                          "Northcharts Bridge and TradingView sync", "Agent desk, 60 questions a day",
                          "Claude-written journal notes", "Discipline flags and review queue"]},
    "pro": {"name": "Pro", "usd": {"month": 200},
            "tagline": "For full-time and funded traders.",
            "features": ["Everything in Plus", "Automatic broker sync (20 accounts)",
                         "Agent desk with the specialist team, 1,000 questions a day",
                         "Northcharts Connect for ChatGPT and Claude", "MetaApi cloud sync",
                         "Priority support"]},
}

# feature -> lowest plan that has it
FEATURES = {
    "broker_sync": "plus",       # Bridge EA, MetaApi, TradingView sync tokens
    "metaapi": "pro",
    "journal_drafts": "plus",    # Claude writes the trade notes
    "connect": "pro",            # Northcharts Connect (MCP for ChatGPT and Claude)
}
LIMITS = {
    "free": {"brokers": 0, "agent_day": 3},
    "plus": {"brokers": 2, "agent_day": 60},
    "pro": {"brokers": 20, "agent_day": 1000},
}
FEATURE_TEXT = {
    "broker_sync": "Automatic broker sync",
    "metaapi": "MetaApi cloud sync",
    "journal_drafts": "Claude-written journal notes",
    "connect": "Northcharts Connect",
    "brokers": "Another broker account",
    "agent_day": "More agent questions today",
}

# Safaricom's published callback addresses (C2B and Ratiba). Override with DARAJA_CALLBACK_IPS.
SAFARICOM_IPS = ("196.201.214.200", "196.201.214.206", "196.201.213.114", "196.201.214.207",
                 "196.201.214.208", "196.201.213.44", "196.201.212.127", "196.201.212.138",
                 "196.201.212.129", "196.201.212.136", "196.201.212.74", "196.201.212.69")
SANDBOX_SHORTCODE = "174379"
SANDBOX_PASSKEY = "bfb279f9aa9bdbcf158e97dd71a467cd2e0c893059b10f78e6b72ada1ed2c919"   # Safaricom's public test key


class PaymentError(Exception):
    """Safe to show to the customer."""


class Upgrade(Exception):
    """The current plan does not include this. Carries the plan that does."""

    def __init__(self, feature, need, have):
        self.feature, self.need, self.have = feature, need, have
        name, mine = PLANS[need]["name"], PLANS.get(have, PLANS["free"])["name"]
        if feature == "agent_day":
            self.text = (f"You have used today's {LIMITS[have]['agent_day']:,} agent questions on {mine}. "
                         f"{name} gives you {LIMITS[need]['agent_day']:,} a day.")
        elif feature == "brokers":
            n = LIMITS[have]["brokers"]
            self.text = (f"{mine} connects {n} broker account{'' if n == 1 else 's'} automatically. "
                         f"{name} connects {LIMITS[need]['brokers']}." if n else
                         f"Automatic broker sync is part of Northcharts {name}.")
        else:
            self.text = f"{FEATURE_TEXT.get(feature, feature)} is part of Northcharts {name}."
        super().__init__(self.text + " Upgrade at /billing.")

    def payload(self):
        return {"ok": False, "error": str(self), "upgrade": "/billing?plan=" + self.need,
                "feature": self.feature, "need": self.need, "plan": self.have}


def _now() -> int:
    return int(time.time())


def mode() -> str:
    m = (os.environ.get("NORTHCHART_BILLING") or "off").strip().lower()
    return m if m in ("off", "test", "live") else "off"


def mpesa_via() -> str:
    """Who sends the M-Pesa prompt: "paystack" (default, the Precifarm AI Paystack account) or "daraja"
    (a Paybill or Till of our own on Safaricom Daraja; also the only route for Ratiba standing orders)."""
    v = (os.environ.get("MPESA_PROVIDER") or "paystack").strip().lower()
    return v if v in ("paystack", "daraja") else "paystack"


def _rank(plan):
    return PLAN_ORDER.index(plan) if plan in PLAN_ORDER else 0


def add_period(ts: int, interval: str, n: int = 1) -> int:
    """Same day next month (or year), clamped to the month's last day."""
    d = dt.datetime.fromtimestamp(ts, dt.timezone.utc)
    if interval == "year":
        y, m = d.year + n, d.month
    else:
        y, m = d.year + (d.month - 1 + n) // 12, (d.month - 1 + n) % 12 + 1
    day = min(d.day, calendar.monthrange(y, m)[1])
    return int(d.replace(year=y, month=m, day=day).timestamp())


def phone_254(raw: str) -> str:
    """0712 345 678, +254712345678, 712345678 -> 254712345678. Safaricom and Airtel ranges only."""
    p = re.sub(r"[\s\-()+]", "", str(raw or ""))
    if p.startswith("0") and len(p) == 10:
        p = "254" + p[1:]
    elif len(p) == 9 and p[0] in "17":
        p = "254" + p
    if not re.fullmatch(r"254[17]\d{8}", p):
        raise PaymentError("Enter an M-Pesa number like 0712 345 678.")
    return p


def mask_phone(p: str) -> str:
    return (p[:6] + "***" + p[-3:]) if p and len(p) >= 12 else ""


def http_json(method, url, headers=None, body=None, timeout=30):
    data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
    h = {"Accept": "application/json", "User-Agent": "Northcharts/1.0"}
    if body is not None:
        h["Content-Type"] = "application/json"
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            st = r.status
    except urllib.error.HTTPError as e:
        raw, st = e.read(), e.code
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"payment provider unreachable: {url.split('?')[0]}: {e}", file=sys.stderr, flush=True)
        raise PaymentError("The payment service could not be reached. Nothing was charged. Try again in a minute.")
    try:
        return st, json.loads(raw or b"{}")
    except ValueError:
        return st, {"raw": raw[:300].decode("utf-8", "replace")}


# ------------------------------------------------------------------ providers
class Paystack:
    def __init__(self, secret=None, base=None, currency=None):
        self.secret = secret if secret is not None else os.environ.get("PAYSTACK_SECRET_KEY", "")
        self.base = (base or os.environ.get("PAYSTACK_API_URL") or "https://api.paystack.co").rstrip("/")
        self.currency = (currency or os.environ.get("PAYSTACK_CURRENCY") or "KES").upper()

    def ready(self):
        return bool(self.secret)

    def test_mode(self):
        return self.secret.startswith("sk_test_")

    def _call(self, method, path, body=None):
        st, j = http_json(method, self.base + path, {"Authorization": "Bearer " + self.secret}, body)
        if st >= 400 or not j.get("status"):
            raise PaymentError("Payment could not start: " + str(j.get("message") or f"HTTP {st}"))
        return j.get("data") or {}

    def initialize(self, email, amount_minor, reference, callback_url, metadata):
        return self._call("POST", "/transaction/initialize", {
            "email": email, "amount": int(amount_minor), "currency": self.currency, "reference": reference,
            "callback_url": callback_url, "channels": ["card"], "metadata": metadata})

    def charge_mpesa(self, email, amount_kes, reference, phone_254_, metadata):
        """M-Pesa STK push through Paystack. Always KES. Paystack settles to the payout account on its dashboard."""
        return self._call("POST", "/charge", {
            "email": email, "amount": int(amount_kes) * 100, "currency": "KES", "reference": reference,
            "mobile_money": {"phone": "+" + phone_254_, "provider": "mpesa"}, "metadata": metadata})

    def verify(self, reference):
        return self._call("GET", "/transaction/verify/" + urllib.parse.quote(reference, safe=""))

    def charge_authorization(self, email, amount_minor, authorization_code, reference, metadata):
        return self._call("POST", "/transaction/charge_authorization", {
            "email": email, "amount": int(amount_minor), "currency": self.currency,
            "authorization_code": authorization_code, "reference": reference, "metadata": metadata})

    def signature_ok(self, raw: bytes, sig: str) -> bool:
        if not self.secret or not sig:
            return False
        want = hmac.new(self.secret.encode(), raw, hashlib.sha512).hexdigest()
        return hmac.compare_digest(want, sig.strip().lower())


class Daraja:
    def __init__(self, env=None, key=None, secret=None, shortcode=None, passkey=None, till=None, base=None):
        g = os.environ.get
        self.env = (env or g("DARAJA_ENV") or "sandbox").lower()
        self.key = key if key is not None else g("DARAJA_CONSUMER_KEY", "")
        self.secret = secret if secret is not None else g("DARAJA_CONSUMER_SECRET", "")
        sandbox = self.env != "production"
        self.shortcode = shortcode or g("DARAJA_SHORTCODE") or (SANDBOX_SHORTCODE if sandbox else "")
        self.passkey = passkey or g("DARAJA_PASSKEY") or (SANDBOX_PASSKEY if sandbox else "")
        self.till = till if till is not None else g("DARAJA_TILL", "")
        self.base = (base or g("DARAJA_API_URL") or
                     ("https://sandbox.safaricom.co.ke" if sandbox else "https://api.safaricom.co.ke")).rstrip("/")
        self._tok, self._tok_exp = None, 0
        self.lock = threading.Lock()

    def ready(self):
        return bool(self.key and self.secret and self.shortcode and self.passkey)

    def token(self):
        with self.lock:
            if self._tok and time.time() < self._tok_exp - 60:
                return self._tok
            basic = base64.b64encode(f"{self.key}:{self.secret}".encode()).decode()
            st, j = http_json("GET", self.base + "/oauth/v1/generate?grant_type=client_credentials",
                              {"Authorization": "Basic " + basic})
            if st >= 400 or not j.get("access_token"):
                raise PaymentError("M-Pesa is not reachable right now. Try again in a minute.")
            self._tok = j["access_token"]
            self._tok_exp = time.time() + int(j.get("expires_in") or 3599)
            return self._tok

    def _post(self, path, body):
        return http_json("POST", self.base + path, {"Authorization": "Bearer " + self.token()}, body)

    def _password(self):
        ts = time.strftime("%Y%m%d%H%M%S", time.gmtime(time.time() + 3 * 3600))   # Nairobi time
        return base64.b64encode((self.shortcode + self.passkey + ts).encode()).decode(), ts

    def stk_push(self, phone, amount_kes, account_ref, desc, callback_url):
        pw, ts = self._password()
        st, j = self._post("/mpesa/stkpush/v1/processrequest", {
            "BusinessShortCode": self.shortcode, "Password": pw, "Timestamp": ts,
            "TransactionType": "CustomerBuyGoodsOnline" if self.till else "CustomerPayBillOnline",
            "Amount": int(amount_kes), "PartyA": phone, "PartyB": self.till or self.shortcode,
            "PhoneNumber": phone, "CallBackURL": callback_url,
            "AccountReference": account_ref[:12], "TransactionDesc": desc[:13]})
        if st >= 400 or str(j.get("ResponseCode")) != "0":
            raise PaymentError("M-Pesa did not accept the request: " +
                               str(j.get("errorMessage") or j.get("ResponseDescription") or f"HTTP {st}"))
        return j    # MerchantRequestID, CheckoutRequestID, CustomerMessage

    def stk_query(self, checkout_id):
        """('paid'|'failed'|'pending', description)."""
        pw, ts = self._password()
        st, j = self._post("/mpesa/stkpushquery/v1/query", {
            "BusinessShortCode": self.shortcode, "Password": pw, "Timestamp": ts,
            "CheckoutRequestID": checkout_id})
        code = j.get("ResultCode")
        if code is None:   # "The transaction is being processed" comes back as an error
            return "pending", str(j.get("errorMessage") or "waiting for the customer")
        if str(code) == "0":
            return "paid", str(j.get("ResultDesc") or "")
        if str(code) in ("4999", "500.001.1001"):
            return "pending", str(j.get("ResultDesc") or "")
        return "failed", str(j.get("ResultDesc") or "payment not completed")

    def ratiba(self, phone, amount_kes, account_ref, desc, start_ts, interval, callback_url, name):
        day = lambda t: time.strftime("%Y%m%d", time.gmtime(t + 3 * 3600))
        st, j = self._post("/standingorder/v1/createStandingOrderExternal", {
            "StandingOrderName": name[:40], "StartDate": day(start_ts), "EndDate": day(add_period(start_ts, "year", 5)),
            "BusinessShortCode": self.till or self.shortcode,
            "TransactionType": "Standing Order Customer Pay Marchant" if self.till else "Standing Order Customer Pay Bill",
            "ReceiverPartyIdentifierType": "2" if self.till else "4", "Amount": str(int(amount_kes)),
            "PartyA": phone, "CallBackURL": callback_url, "AccountReference": account_ref[:12],
            "TransactionDesc": desc[:13], "Frequency": "8" if interval == "year" else "4"})
        body = j.get("ResponseBody") or j.get("responseBody") or {}
        code = str(body.get("responseCode") or (j.get("ResponseHeader") or {}).get("responseCode") or "")
        if st >= 400 or code not in ("200", "0"):
            raise PaymentError("M-Pesa could not set up the standing order: " +
                               str(body.get("responseDescription") or j.get("errorMessage") or f"HTTP {st}"))
        return j

    def register_c2b(self, confirm_url, validate_url):
        st, j = self._post("/mpesa/c2b/v2/registerurl", {
            "ShortCode": self.till or self.shortcode, "ResponseType": "Completed",
            "ConfirmationURL": confirm_url, "ValidationURL": validate_url})
        if st >= 400:
            raise PaymentError("Registering the M-Pesa addresses failed: " + str(j.get("errorMessage") or st))
        return j


# ------------------------------------------------------------------ billing
class Billing:
    def __init__(self, data_dir, accounts, vault=None, fx_fn=None, paystack=None, daraja=None):
        self.dir = os.path.join(data_dir, "billing")
        self.path = os.path.join(self.dir, "billing.json")
        self.accounts = accounts
        self.vault = vault
        self.fx_fn = fx_fn or (lambda: None)
        self.paystack = paystack or Paystack()
        self.daraja = daraja or Daraja()
        self.lock = threading.RLock()
        self.base = (os.environ.get("NORTHCHART_PUBLIC_URL") or "").rstrip("/")
        self.data = self._load()

    # ---------------------------------------------------------- storage
    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            d = {}
        for k in ("subs", "payments", "usage", "seen"):
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

    def _seal(self, obj):
        return self.vault.seal(obj) if self.vault else {"plain": obj}

    def _open(self, box):
        if not box:
            return None
        if isinstance(box, dict) and "plain" in box:
            return box["plain"]
        return self.vault.open(box) if self.vault else None

    # ---------------------------------------------------------- prices
    def rate(self):
        fixed = os.environ.get("NORTHCHART_KES_PER_USD")
        if fixed:
            try:
                return float(fixed)
            except ValueError:
                pass
        try:
            r = float(self.fx_fn() or 0)
            if 80 < r < 300:
                return r
        except (TypeError, ValueError):
            pass
        return 129.0

    def usd(self, plan, interval):
        v = os.environ.get(f"NORTHCHART_PRICE_{plan.upper()}_{interval.upper()}")
        try:
            return float(v) if v else float(PLANS[plan]["usd"][interval])
        except ValueError:
            return float(PLANS[plan]["usd"][interval])

    def kes(self, plan, interval):
        v = os.environ.get(f"NORTHCHART_PRICE_{plan.upper()}_{interval.upper()}_KES")
        if v and v.isdigit():
            return int(v)
        return int(math.ceil(self.usd(plan, interval) * self.rate() / 50.0) * 50)

    def catalogue(self):
        rate = self.rate()
        plans = []
        for p in PLAN_ORDER:
            plans.append({"id": p, "name": PLANS[p]["name"], "tagline": PLANS[p]["tagline"],
                          "features": PLANS[p]["features"], "limits": LIMITS[p],
                          "usd": {i: self.usd(p, i) for i in INTERVALS},
                          "kes": {i: self.kes(p, i) for i in INTERVALS}})
        return {"plans": plans, "rate": round(rate, 2), "mode": mode(),
                "methods": {"mpesa": self.mpesa_ready(), "card": self.paystack.ready(),
                            "ratiba": mpesa_via() == "daraja" and self.daraja.ready()},
                "mpesa_via": mpesa_via(), "card_currency": self.paystack.currency, "grace_days": GRACE_DAYS,
                "test": mode() == "test" or self.paystack.test_mode()
                        or (mpesa_via() == "daraja" and self.daraja.env != "production")}

    def mpesa_ready(self):
        return self.paystack.ready() if mpesa_via() == "paystack" else self.daraja.ready()

    def _prompt(self, pay, phone, email, base):
        """Send the M-Pesa prompt for a pending payment through the configured provider."""
        uid, amount = pay["uid"], int(pay["amount"])
        if mpesa_via() == "paystack":
            pay["via"] = "paystack"
            d = self.paystack.charge_mpesa(email, amount, pay["ref"], phone,
                                           {"uid": uid, "plan": pay["plan"], "interval": pay["interval"]})
            if str(d.get("status") or "") in ("failed", "abandoned"):
                raise PaymentError(d.get("gateway_response") or d.get("message") or "M-Pesa prompt failed.")
            return
        pay["via"] = "daraja"
        r = self.daraja.stk_push(phone, amount, self._account_ref(uid), "Northcharts",
                                 base + "/api/billing/mpesa/callback")
        pay["checkout_id"] = r.get("CheckoutRequestID")

    # ---------------------------------------------------------- entitlements
    def _owner(self, u):
        return not u or u.get("role") == "owner" or u.get("uid") == "owner"

    def plan_of(self, u) -> str:
        if mode() == "off" or self._owner(u):
            return "pro"
        s = self.data["subs"].get(u["uid"])
        if not s:
            return "free"
        if s.get("status") in ("active", "past_due") and s.get("period_end", 0) + GRACE_DAYS * 86400 > _now():
            return s.get("plan") or "free"
        return "free"

    def limit(self, u, key):
        return LIMITS[self.plan_of(u)][key]

    def require(self, u, feature):
        have = self.plan_of(u)
        need = FEATURES[feature]
        if _rank(have) < _rank(need):
            raise Upgrade(feature, need, have)

    def require_room(self, u, key, used):
        have = self.plan_of(u)
        if used >= LIMITS[have][key]:
            need = next((p for p in PLAN_ORDER if LIMITS[p][key] > used), "pro")
            raise Upgrade(key, need, have)

    def use_agent(self, u):
        """Count one agent question. Raises Upgrade when today's allowance is spent."""
        if mode() == "off" or self._owner(u):
            return None
        day = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 3 * 3600))
        with self.lock:
            rec = self.data["usage"].setdefault(u["uid"], {})
            if rec.get("day") != day:
                rec.clear()
                rec["day"], rec["agent"] = day, 0
            cap = self.limit(u, "agent_day")
            if rec["agent"] >= cap:
                have = self.plan_of(u)
                raise Upgrade("agent_day", "pro" if have == "plus" else "plus", have)
            rec["agent"] += 1
            self._save()
            return cap - rec["agent"]

    def agent_left(self, u):
        """(questions left today, daily allowance) for a member; (None, None) when there is no limit."""
        if not u or mode() == "off" or self._owner(u):
            return None, None
        day = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 3 * 3600))
        cap = self.limit(u, "agent_day")
        rec = self.data["usage"].get(u["uid"]) or {}
        used = rec.get("agent", 0) if rec.get("day") == day else 0
        return max(0, cap - used), cap

    # ---------------------------------------------------------- views
    def status(self, u):
        s = dict(self.data["subs"].get(u["uid"]) or {}) if u else {}
        plan = self.plan_of(u)
        out = {"plan": plan, "plan_name": PLANS[plan]["name"], "owner": self._owner(u), "mode": mode(),
               "limits": LIMITS[plan], "usage": dict(self.data["usage"].get(u["uid"] if u else "", {}))}
        if s:
            out["sub"] = {k: s.get(k) for k in ("plan", "interval", "status", "period_end", "started", "method",
                                                "cancel_at_period_end", "next_plan", "card", "phone_masked",
                                                "ratiba", "comp", "amount", "currency")}
            out["sub"]["renews"] = (s.get("status") == "active" and not s.get("cancel_at_period_end"))
        out["payments"] = self.history(u["uid"]) if u else []
        return out

    def history(self, uid, n=24):
        rows = [p for p in self.data["payments"].values() if p["uid"] == uid and p["status"] != "abandoned"]
        rows.sort(key=lambda p: p["created"], reverse=True)
        keep = ("ref", "plan", "interval", "method", "amount", "currency", "usd", "status", "created", "paid_at",
                "receipt", "purpose", "note", "credit")
        return [{k: p.get(k) for k in keep} for p in rows[:n]]

    def payment(self, ref, uid=None):
        p = self.data["payments"].get(ref)
        if not p or (uid and p["uid"] != uid):
            return None
        return p

    # ---------------------------------------------------------- checkout
    def quote(self, u, plan, interval):
        """What the person pays now, after credit for unused time on a lower plan."""
        if plan not in ("plus", "pro") or interval not in INTERVALS:
            raise PaymentError("Pick Plus or Pro.")
        kes = self.kes(plan, interval)
        usd = self.usd(plan, interval)
        credit = 0
        s = self.data["subs"].get(u["uid"])
        if s and s.get("status") == "active" and s.get("period_end", 0) > _now() and not s.get("comp"):
            if _rank(plan) < _rank(s["plan"]) or (plan == s["plan"] and interval in (s.get("interval"), "month")):
                pass    # downgrade or renewal: no credit, it starts at the end of the period
            elif s.get("amount") and s.get("currency") == "KES":
                left = (s["period_end"] - _now()) / max(1, s["period_end"] - s.get("period_start", s["period_end"] - 30 * 86400))
                credit = int(max(0, min(1, left)) * s["amount"])
        amount = max(kes - credit, 0)
        if amount and amount < 10:
            amount = 10
        return {"plan": plan, "interval": interval, "kes": kes, "usd": usd, "credit": credit, "amount": amount}

    def _new_ref(self):
        return "NC" + secrets.token_hex(5).upper()

    def _account_ref(self, uid):
        return ("NC" + re.sub(r"[^A-Za-z0-9]", "", uid))[:12].upper()

    def checkout(self, u, plan, interval, method, base, phone=""):
        if mode() == "off":
            raise PaymentError("Payments are not switched on for this desk yet.")
        if self._owner(u):
            raise PaymentError("The desk owner already has every feature.")
        self.base = base or self.base
        q = self.quote(u, plan, interval)
        s = self.data["subs"].get(u["uid"]) or {}
        if s.get("status") == "active" and s.get("plan") == plan and s.get("interval") == interval \
                and s.get("period_end", 0) - _now() > 7 * 86400 and not s.get("comp"):
            raise PaymentError(f"You are already on {PLANS[plan]['name']}. It renews on "
                               f"{time.strftime('%d %b %Y', time.gmtime(s['period_end']))}.")
        later = _rank(plan) < _rank(s.get("plan")) or (plan == s.get("plan") and interval == "month"
                                                         and s.get("interval") == "year")
        if s.get("status") == "active" and later and s.get("period_end", 0) > _now() and not s.get("comp"):
            with self.lock:
                s["next_plan"], s["next_interval"] = plan, interval
                self._save()
            return {"scheduled": True, "message": f"You move to {PLANS[plan]['name']} on "
                                                   f"{time.strftime('%d %b %Y', time.gmtime(s['period_end']))}."}
        ref = self._new_ref()
        pay = {"ref": ref, "uid": u["uid"], "plan": plan, "interval": interval, "method": method,
               "amount": q["amount"], "currency": "KES", "usd": q["usd"], "credit": q["credit"],
               "status": "pending", "created": _now(), "purpose": "upgrade" if q["credit"] else "new"}
        if q["amount"] == 0:    # covered by credit
            with self.lock:
                self.data["payments"][ref] = pay
            pay["method"] = "credit"
            self._paid(ref, "", note="Covered by unused time on your previous plan")
            return {"ref": ref, "status": "paid"}
        if method == "card":
            if not self.paystack.ready():
                raise PaymentError("Card payments are not switched on yet. Use M-Pesa.")
            minor = q["amount"] * 100
            if self.paystack.currency == "USD":
                minor, pay["currency"], pay["amount"] = int(round(q["usd"] * 100)), "USD", q["usd"]
                if q["credit"]:
                    minor = int(round(q["amount"] / self.rate() * 100))
                    pay["amount"] = round(minor / 100, 2)
            with self.lock:
                self.data["payments"][ref] = pay
                self._save()
            d = self.paystack.initialize(u["email"], minor, ref, base + "/billing/return",
                                         {"uid": u["uid"], "plan": plan, "interval": interval,
                                          "custom_fields": [{"display_name": "Plan", "variable_name": "plan",
                                                             "value": f"Northcharts {PLANS[plan]['name']} ({interval}ly)"}]})
            return {"ref": ref, "redirect": d.get("authorization_url")}
        if method == "mpesa":
            if not self.mpesa_ready():
                raise PaymentError("M-Pesa is not switched on yet. Use a card.")
            if q["amount"] > MPESA_MAX_KES:
                raise PaymentError(f"M-Pesa takes up to KES {MPESA_MAX_KES:,} in one payment. Pay this plan by card, "
                                   "or choose monthly.")
            p = phone_254(phone)
            pay["phone_masked"] = mask_phone(p)
            with self.lock:
                self.data["payments"][ref] = pay
                pay["phone"] = self._seal(p)
                self._save()
            try:
                self._prompt(pay, p, u["email"], base)
            except PaymentError as e:
                self._failed(ref, str(e))
                raise
            with self.lock:
                self._save()
            return {"ref": ref, "status": "pending",
                    "message": "Check your phone and enter your M-Pesa PIN to pay KES "
                               f"{q['amount']:,}."}
        raise PaymentError("Pick M-Pesa or card.")

    def check(self, ref, uid=None):
        """Bring a pending payment up to date with the provider. Returns its status."""
        p = self.payment(ref, uid)
        if not p:
            raise PaymentError("Payment not found.")
        if p["status"] != "pending":
            return p["status"]
        if p["method"] == "card":
            d = self.paystack.verify(ref)
            self._card_result(ref, d)
        elif p["method"] == "mpesa" and p.get("via") == "paystack" and _now() - p["created"] >= 8:
            d = self.paystack.verify(ref)
            if d.get("status") in ("success", "failed", "reversed"):     # "ongoing"/"pending": still waiting
                self._card_result(ref, d)
        elif p["method"] == "mpesa" and p.get("checkout_id") and _now() - p["created"] >= 8:
            st, desc = self.daraja.stk_query(p["checkout_id"])
            if st == "paid":
                self._paid(ref, p.get("receipt") or "", phone=self._open(p.get("phone")))
            elif st == "failed":
                self._failed(ref, desc)
        p = self.payment(ref)
        if p["status"] == "pending" and _now() - p["created"] > PENDING_TTL:
            self._failed(ref, "No payment received in 30 minutes.", "abandoned")
        return self.payment(ref)["status"]

    # ---------------------------------------------------------- provider callbacks
    def paystack_webhook(self, raw: bytes, sig: str):
        if not self.paystack.signature_ok(raw, sig):
            return False
        try:
            ev = json.loads(raw)
        except ValueError:
            return False
        if ev.get("event") == "charge.success":
            ref = str((ev.get("data") or {}).get("reference") or "")
            if ref in self.data["payments"] and self.data["payments"][ref]["status"] == "pending":
                self._card_result(ref, self.paystack.verify(ref))     # never trust the webhook body alone
        return True

    def _card_result(self, ref, d):
        p = self.data["payments"][ref]
        status = d.get("status")
        if status == "success":
            paid_minor = int(d.get("amount") or 0)
            want = int(round(float(p["amount"]) * 100))
            if paid_minor < want or str(d.get("currency") or "").upper() != p["currency"]:
                return self._failed(ref, f"Amount mismatch: {paid_minor / 100:.2f} {d.get('currency')}")
            a = d.get("authorization") or {}
            card = None
            if p["method"] == "mpesa":
                return self._paid(ref, str(d.get("receipt_number") or (d.get("metadata") or {}).get("receipt")
                                           or d.get("id") or ""), phone=self._open(p.get("phone")))
            if a.get("reusable") and a.get("authorization_code"):
                card = {"brand": (a.get("brand") or a.get("card_type") or "card").title(),
                        "last4": a.get("last4") or "", "exp": f"{a.get('exp_month', '')}/{str(a.get('exp_year', ''))[-2:]}",
                        "auth": self._seal({"code": a["authorization_code"],
                                            "email": (d.get("customer") or {}).get("email") or ""})}
            self._paid(ref, str(d.get("id") or ""), card=card)
        elif status in ("failed", "abandoned", "reversed"):
            self._failed(ref, d.get("gateway_response") or status, "failed" if status != "abandoned" else "abandoned")

    def mpesa_callback(self, body: dict):
        cb = ((body or {}).get("Body") or {}).get("stkCallback") or {}
        cid = cb.get("CheckoutRequestID")
        ref = next((r for r, p in self.data["payments"].items() if p.get("checkout_id") == cid and cid), None)
        if not ref:
            return False
        p = self.data["payments"][ref]
        if p["status"] != "pending":
            return True
        items = {i.get("Name"): i.get("Value") for i in ((cb.get("CallbackMetadata") or {}).get("Item") or [])}
        with self.lock:
            if items.get("MpesaReceiptNumber"):
                p["receipt"] = str(items["MpesaReceiptNumber"])[:20]
            p["cb_amount"] = items.get("Amount")
            self._save()
        # the callback is unsigned: ask Safaricom what really happened
        st, desc = self.daraja.stk_query(cid)
        if st == "paid":
            if p.get("cb_amount") is not None and int(float(p["cb_amount"])) < int(p["amount"]):
                return self._failed(ref, "Amount paid was less than the price.") or True
            self._paid(ref, p.get("receipt") or "", phone=self._open(p.get("phone")))
        elif st == "failed":
            self._failed(ref, desc)
        return True

    def ratiba_callback(self, body: dict):
        data = {(x.get("name") or ""): x.get("value") for x in
                (((body or {}).get("responseBody") or {}).get("responseData") or [])}
        rid = ((body or {}).get("responseHeader") or {}).get("requestRefID") or ""
        uid = next((k for k, s in self.data["subs"].items() if (s.get("ratiba") or {}).get("request") == rid and rid), None)
        if not uid:
            return False
        ok = str(data.get("responseCode") or (body.get("responseHeader") or {}).get("responseCode")) == "0"
        with self.lock:
            r = self.data["subs"][uid]["ratiba"]
            r["state"] = "active" if ok else "failed"
            r["note"] = str((body.get("responseHeader") or {}).get("responseDescription") or "")[:120]
            r["updated"] = _now()
            self._save()
        return True

    def c2b_confirmation(self, body: dict, ip: str):
        """A Paybill or Till payment, such as a Ratiba debit. Only Safaricom's addresses are believed."""
        if not self._safaricom_ip(ip):
            return False
        tid = str(body.get("TransID") or "")
        if not tid or tid in self.data["seen"]:
            return bool(tid)
        acc = str(body.get("BillRefNumber") or "").upper()
        uid = next((k for k in self.data["subs"] if self._account_ref(k) == acc), None)
        with self.lock:
            self.data["seen"][tid] = _now()
            self._save()
        if not uid:
            return True
        s = self.data["subs"][uid]
        amount = int(float(body.get("TransAmount") or 0))
        plan = s.get("plan") or "plus"
        interval = s.get("interval") or "month"
        if amount < self.kes(plan, interval) * 0.97:     # a little room for a rate change since the order was set
            return True
        ref = self._new_ref()
        with self.lock:
            self.data["payments"][ref] = {"ref": ref, "uid": uid, "plan": plan, "interval": interval, "method": "mpesa",
                                          "amount": amount, "currency": "KES", "usd": self.usd(plan, interval),
                                          "status": "pending", "created": _now(), "purpose": "renewal",
                                          "note": "M-Pesa Ratiba"}
        self._paid(ref, tid)
        return True

    def _safaricom_ip(self, ip):
        allowed = [x.strip() for x in (os.environ.get("DARAJA_CALLBACK_IPS") or ",".join(SAFARICOM_IPS)).split(",") if x.strip()]
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        for a in allowed:
            try:
                if addr in ipaddress.ip_network(a, strict=False):
                    return True
            except ValueError:
                continue
        return False

    # ---------------------------------------------------------- outcomes
    def _paid(self, ref, provider_id, card=None, phone=None, note=None):
        with self.lock:
            p = self.data["payments"][ref]
            if p["status"] == "paid":
                return
            p["status"], p["paid_at"] = "paid", _now()
            if provider_id:
                p["receipt"] = p.get("receipt") or provider_id
            if note:
                p["note"] = note
            p.pop("phone", None)
            uid = p["uid"]
            s = self.data["subs"].setdefault(uid, {"started": _now()})
            now = _now()
            pe = s.get("period_end", 0)
            same = s.get("plan") == p["plan"] and s.get("interval") == p["interval"] \
                and s.get("status") in ("active", "past_due") and not s.get("comp")
            # same plan: keep the renewal date (paid early adds on; paid in the grace days keeps the anniversary)
            start = pe if same and pe > now - (GRACE_DAYS + 1) * 86400 else now
            s.update({"plan": p["plan"], "interval": p["interval"], "status": "active", "period_start": start,
                      "period_end": add_period(start, p["interval"]),
                      "amount": p["amount"] + (p.get("credit") or 0), "currency": p["currency"],
                      "cancel_at_period_end": False, "stk_tries": 0, "last_stk": 0, "reminded": 0, "comp": False})
            if p["method"] in ("card", "mpesa"):
                s["method"] = p["method"]
            for k in ("next_plan", "next_interval", "card_tried"):
                s.pop(k, None)
            if card:
                s["card"] = {k: card[k] for k in ("brand", "last4", "exp")}
                s["auth"] = card["auth"]
            if phone:
                s["phone"] = self._seal(phone)
                s["phone_masked"] = mask_phone(phone)
            self._save()
        u = self.accounts.get(uid) if self.accounts else None
        if u and self.accounts:
            end = time.strftime("%d %B %Y", time.gmtime(s["period_end"]))
            self.accounts.mail(u["email"], f"Receipt {ref}: Northcharts {PLANS[p['plan']]['name']}",
                               f"Thank you. We received {p['currency']} {p['amount']:,} for Northcharts "
                               f"{PLANS[p['plan']]['name']} ({p['interval']}ly).\n\nReceipt: {ref}"
                               f"{' (' + p['receipt'] + ')' if p.get('receipt') and p['receipt'] != ref else ''}\n"
                               f"Paid by: {'M-Pesa' if p['method'] == 'mpesa' else 'card' if p['method'] == 'card' else p['method']}\n"
                               f"Your plan runs until {end}.\n\nManage your plan: {self.base}/billing\n")

    def _failed(self, ref, why, status="failed"):
        with self.lock:
            p = self.data["payments"][ref]
            if p["status"] != "pending":
                return
            p["status"], p["note"] = status, str(why or "")[:160]
            p.pop("phone", None)
            self._save()

    # ---------------------------------------------------------- self-service
    def cancel(self, u):
        with self.lock:
            s = self.data["subs"].get(u["uid"])
            if not s or s.get("status") != "active":
                raise PaymentError("There is no active plan to cancel.")
            s["cancel_at_period_end"] = True
            self._save()
        return s["period_end"]

    def resume(self, u):
        with self.lock:
            s = self.data["subs"].get(u["uid"])
            if not s or s.get("status") != "active":
                raise PaymentError("There is no active plan to resume.")
            s["cancel_at_period_end"] = False
            self._save()

    def set_autopay(self, u, method, base, phone=""):
        """Choose how renewals are paid: 'card' (saved card), 'mpesa' (prompt on the due date)
        or 'ratiba' (an M-Pesa standing order the customer approves once)."""
        with self.lock:
            s = self.data["subs"].get(u["uid"])
            if not s or s.get("status") not in ("active", "past_due"):
                raise PaymentError("Start a plan first.")
            if method == "card":
                if not s.get("auth"):
                    raise PaymentError("Pay once by card to save it for renewals.")
                s["method"] = "card"
            elif method == "mpesa":
                if phone:
                    p = phone_254(phone)
                    s["phone"], s["phone_masked"] = self._seal(p), mask_phone(p)
                if not s.get("phone"):
                    raise PaymentError("Enter the M-Pesa number to prompt on renewal.")
                s["method"] = "mpesa"
            elif method == "ratiba":
                p = phone_254(phone) if phone else self._open(s.get("phone"))
                if not p:
                    raise PaymentError("Enter the M-Pesa number for the standing order.")
                if mpesa_via() != "daraja" or not self.daraja.ready():
                    raise PaymentError("M-Pesa standing orders are not available yet. Pick the M-Pesa prompt or a card.")
                amount = self.kes(s["plan"], s.get("interval") or "month")
                if amount > MPESA_MAX_KES:
                    raise PaymentError(f"A standing order can be at most KES {MPESA_MAX_KES:,}. Use a card for this plan.")
                r = self.daraja.ratiba(p, amount, self._account_ref(u["uid"]), "Northcharts", s["period_end"],
                                       s.get("interval") or "month", base + "/api/billing/mpesa/ratiba",
                                       f"Northcharts {PLANS[s['plan']]['name']} {u['uid'][:8]}")
                hdr = r.get("ResponseHeader") or r.get("responseHeader") or {}
                s["ratiba"] = {"state": "requested", "request": hdr.get("responseRefID") or "", "amount": amount,
                               "created": _now()}
                s["phone"], s["phone_masked"], s["method"] = self._seal(p), mask_phone(p), "mpesa"
            else:
                raise PaymentError("Unknown payment method.")
            self._save()
            return {k: s.get(k) for k in ("method", "phone_masked", "ratiba", "card")}

    # ---------------------------------------------------------- owner tools
    def grant(self, uid, plan, days, note=""):
        if plan not in PLAN_ORDER or not self.accounts.get(uid):
            raise PaymentError("Unknown member or plan.")
        with self.lock:
            if plan == "free":
                self.data["subs"].pop(uid, None)
            else:
                end = _now() + int(days) * 86400
                self.data["subs"][uid] = {"plan": plan, "interval": "month", "status": "active", "started": _now(),
                                          "period_start": _now(), "period_end": end, "method": "comp", "comp": True,
                                          "cancel_at_period_end": True, "note": note[:80]}
            self._save()

    def overview(self):
        """Revenue and subscribers for the desk owner."""
        now = _now()
        subs = []
        mrr = 0.0
        for uid, s in self.data["subs"].items():
            u = self.accounts.get(uid) or {}
            live = s.get("status") in ("active", "past_due") and s.get("period_end", 0) + GRACE_DAYS * 86400 > now
            if live and not s.get("comp") and s.get("plan") in ("plus", "pro"):
                usd = self.usd(s["plan"], s.get("interval") or "month")
                mrr += usd / (12 if s.get("interval") == "year" else 1)
            subs.append({"uid": uid, "email": u.get("email") or "(deleted)", "name": u.get("name") or "",
                         "plan": s.get("plan"), "interval": s.get("interval"), "status": s.get("status") if live else "expired",
                         "period_end": s.get("period_end"), "method": s.get("method"), "comp": bool(s.get("comp")),
                         "cancelling": bool(s.get("cancel_at_period_end"))})
        paid = [p for p in self.data["payments"].values() if p["status"] == "paid" and p.get("method") != "credit"]
        month = time.strftime("%Y-%m", time.gmtime(now))
        rev = {"KES": 0, "USD": 0.0}
        rev_month = {"KES": 0, "USD": 0.0}
        for p in paid:
            rev[p["currency"]] = rev.get(p["currency"], 0) + p["amount"]
            if time.strftime("%Y-%m", time.gmtime(p.get("paid_at") or 0)) == month:
                rev_month[p["currency"]] = rev_month.get(p["currency"], 0) + p["amount"]
        recent = sorted(self.data["payments"].values(), key=lambda p: p["created"], reverse=True)[:30]
        members = [{"uid": u["uid"], "email": u["email"]} for u in self.accounts.data["users"].values()
                   if u.get("role") != "owner"]
        return {"subs": sorted(subs, key=lambda x: x["period_end"] or 0, reverse=True),
                "active": sum(1 for x in subs if x["status"] in ("active", "past_due") and not x["comp"]),
                "mrr_usd": round(mrr, 2), "revenue": rev, "revenue_month": rev_month,
                "recent": [{k: p.get(k) for k in ("ref", "uid", "plan", "interval", "method", "amount", "currency",
                                                  "status", "created", "receipt", "note")} for p in recent],
                "members": members, "catalogue": self.catalogue()}

    # ---------------------------------------------------------- renewals (call every few minutes)
    def tick(self, base, now=None):
        self.base = base or self.base
        now = now or _now()
        done = []
        for uid in list(self.data["subs"]):
            s = self.data["subs"][uid]
            try:
                done += self._tick_one(uid, s, base, now)
            except Exception as e:     # one customer's failure must not stop the rest
                done.append((uid, "error", str(e)))
        # pending payments that were never finished
        for ref, p in list(self.data["payments"].items()):
            if p["status"] == "pending" and now - p["created"] > PENDING_TTL:
                try:
                    self.check(ref)
                except Exception:
                    self._failed(ref, "No payment received in 30 minutes.", "abandoned")
        return done

    def _tick_one(self, uid, s, base, now):
        out = []
        u = self.accounts.get(uid)
        if not u:
            return out
        end = s.get("period_end", 0)
        if s.get("status") not in ("active", "past_due"):
            return out
        name = PLANS.get(s.get("plan"), PLANS["plus"])["name"]
        # 1. reminder a few days before
        if s.get("status") == "active" and 0 < end - now <= REMIND_DAYS * 86400 and not s.get("reminded") \
                and not s.get("comp"):
            when = time.strftime("%d %B", time.gmtime(end))
            if s.get("cancel_at_period_end"):
                text = f"Your Northcharts {name} plan ends on {when}. Renew any time at {base}/billing."
            elif s.get("method") == "card" and s.get("card"):
                text = (f"Your Northcharts {name} plan renews on {when}. We will charge your "
                        f"{s['card']['brand']} ending {s['card']['last4']}. Manage it at {base}/billing.")
            elif (s.get("ratiba") or {}).get("state") == "active":
                text = f"Your Northcharts {name} plan renews on {when} through your M-Pesa Ratiba standing order."
            else:
                text = (f"Your Northcharts {name} plan renews on {when}. We will send an M-Pesa request to "
                        f"{s.get('phone_masked') or 'your phone'} that day, or pay now at {base}/billing.")
            self.accounts.mail(u["email"], f"Northcharts {name} renews on {when}", text)
            with self.lock:
                s["reminded"] = now
                self._save()
            out.append((uid, "reminded"))
        if now < end:
            return out
        # 2. due: ended, scheduled change, or try to collect
        if s.get("comp") or s.get("cancel_at_period_end"):
            with self.lock:
                s["status"] = "cancelled"
                self._save()
            self.accounts.mail(u["email"], f"Your Northcharts {name} plan has ended",
                               f"Your plan has ended and your account is on Free. Your journal and data are kept. "
                               f"Come back any time at {base}/billing.")
            return out + [(uid, "ended")]
        plan, interval = s.get("next_plan") or s["plan"], s.get("next_interval") or s.get("interval") or "month"
        if s.get("next_plan") and s.get("status") == "active":
            with self.lock:
                s["plan"], s["interval"] = plan, interval
                s.pop("next_plan", None)
                s.pop("next_interval", None)
                self._save()
        if s.get("status") == "active":
            with self.lock:
                s["status"] = "past_due"
                self._save()
        if (s.get("ratiba") or {}).get("state") == "active":
            pass        # the standing order pays on the due date; the C2B confirmation renews
        elif s.get("method") == "card" and s.get("auth") and not s.get("card_tried"):
            a = self._open(s["auth"]) or {}
            ref = self._new_ref()
            amount = self.kes(plan, interval)
            minor = amount * 100
            cur = "KES"
            if self.paystack.currency == "USD":
                minor, cur, amount = int(round(self.usd(plan, interval) * 100)), "USD", self.usd(plan, interval)
            with self.lock:
                self.data["payments"][ref] = {"ref": ref, "uid": uid, "plan": plan, "interval": interval,
                                              "method": "card", "amount": amount, "currency": cur,
                                              "usd": self.usd(plan, interval), "status": "pending", "created": now,
                                              "purpose": "renewal"}
                s["card_tried"] = now
                self._save()
            try:
                d = self.paystack.charge_authorization(a.get("email") or u["email"], minor, a.get("code"), ref,
                                                       {"uid": uid, "plan": plan, "interval": interval, "renewal": True})
                self._card_result(ref, d)
            except PaymentError as e:
                self._failed(ref, str(e))
            if self.data["payments"][ref]["status"] == "paid":
                with self.lock:
                    s.pop("card_tried", None)
                    self._save()
                return out + [(uid, "renewed")]
            self.accounts.mail(u["email"], f"Northcharts {name}: card renewal failed",
                               f"We could not charge your card for Northcharts {name}. You have {GRACE_DAYS} days "
                               f"to pay with M-Pesa or another card at {base}/billing before the account moves to Free.")
            out.append((uid, "card_failed"))
        elif s.get("phone") and s.get("stk_tries", 0) < STK_MAX_TRIES and self.kes(plan, interval) <= MPESA_MAX_KES \
                and now - s.get("last_stk", 0) >= STK_RETRY_HOURS * 3600 and self.mpesa_ready():
            phone = self._open(s["phone"])
            ref = self._new_ref()
            amount = self.kes(plan, interval)
            with self.lock:
                self.data["payments"][ref] = {"ref": ref, "uid": uid, "plan": plan, "interval": interval,
                                              "method": "mpesa", "amount": amount, "currency": "KES",
                                              "usd": self.usd(plan, interval), "status": "pending", "created": now,
                                              "purpose": "renewal", "phone_masked": mask_phone(phone)}
                s["stk_tries"] = s.get("stk_tries", 0) + 1
                s["last_stk"] = now
                self._save()
            try:
                with self.lock:
                    self.data["payments"][ref]["phone"] = self._seal(phone)
                self._prompt(self.data["payments"][ref], phone, u["email"], base)
                with self.lock:
                    self._save()
                out.append((uid, "stk_sent"))
            except PaymentError as e:
                self._failed(ref, str(e))
                out.append((uid, "stk_failed"))
        # 3. out of grace
        if now >= end + GRACE_DAYS * 86400 and s.get("status") == "past_due":
            with self.lock:
                s["status"] = "expired"
                self._save()
            self.accounts.mail(u["email"], f"Your Northcharts {name} plan has lapsed",
                               f"We did not receive the renewal, so your account is now on Free. Your journal and "
                               f"data are kept. Pick up where you left off at {base}/billing.")
            out.append((uid, "expired"))
        return out
