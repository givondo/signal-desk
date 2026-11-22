"""Billing: prices, plan gates, M-Pesa and card checkout, renewals, callbacks and the HTTP routes.

Paystack and Safaricom Daraja are replaced by one local mock server.
"""

import hashlib
import hmac
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from test_agent import DeskServer, sd, seed  # noqa: E402
from test_accounts import Client  # noqa: E402

import northchart_accounts as na  # noqa: E402
import northchart_billing as nbl  # noqa: E402
import northchart_brokers as nb  # noqa: E402

SK = "sk_test_northchart"


class MockPay:
    stk = []            # STK pushes received
    stk_result = {}     # CheckoutRequestID -> ("0", "ok") | ("1032", "cancelled"); missing = still processing
    verify = {}         # reference -> data
    charges = []
    charge_status = "success"
    ratiba = []
    mm = []             # Paystack M-Pesa charges
    srv = None

    @classmethod
    def reset(cls):
        cls.stk, cls.stk_result, cls.verify, cls.charges, cls.ratiba, cls.mm = [], {}, {}, [], [], []
        cls.charge_status = "success"

    @classmethod
    def start(cls):
        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def out(self, obj, st=200):
                b = json.dumps(obj).encode()
                self.send_response(st)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_GET(self):
                if self.path.startswith("/oauth/v1/generate"):
                    return self.out({"access_token": "daraja-token", "expires_in": "3599"})
                if self.path.startswith("/transaction/verify/"):
                    assert self.headers["Authorization"] == "Bearer " + SK
                    ref = self.path.rsplit("/", 1)[1]
                    d = MockPay.verify.get(ref)
                    return self.out({"status": True, "data": d or {"status": "abandoned", "reference": ref}})
                self.out({}, 404)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
                if self.path == "/mpesa/stkpush/v1/processrequest":
                    assert self.headers["Authorization"] == "Bearer daraja-token"
                    cid = f"ws_CO_{len(MockPay.stk) + 1}"
                    MockPay.stk.append({**body, "cid": cid})
                    return self.out({"ResponseCode": "0", "CheckoutRequestID": cid, "MerchantRequestID": "m1",
                                     "CustomerMessage": "Success. Request accepted for processing"})
                if self.path == "/mpesa/stkpushquery/v1/query":
                    r = MockPay.stk_result.get(body["CheckoutRequestID"])
                    if not r:
                        return self.out({"errorCode": "500.001.1001", "errorMessage": "The transaction is being processed"}, 500)
                    return self.out({"ResponseCode": "0", "ResultCode": r[0], "ResultDesc": r[1]})
                if self.path == "/standingorder/v1/createStandingOrderExternal":
                    MockPay.ratiba.append(body)
                    return self.out({"ResponseHeader": {"responseRefID": "rr-1", "responseCode": "200"},
                                     "ResponseBody": {"responseCode": "200", "responseDescription": "Request accepted"}})
                if self.path == "/mpesa/c2b/v2/registerurl":
                    return self.out({"ResponseCode": "0", "ResponseDescription": "Success"})
                if self.path == "/transaction/initialize":
                    return self.out({"status": True, "data": {"authorization_url": "https://checkout.paystack.test/abc",
                                                              "reference": body["reference"]}})
                if self.path == "/charge":
                    assert self.headers["Authorization"] == "Bearer " + SK
                    MockPay.mm.append(body)
                    return self.out({"status": True, "data": {"status": "pay_offline", "reference": body["reference"],
                                                              "display_text": "Please complete authorization on your phone"}})
                if self.path == "/transaction/charge_authorization":
                    MockPay.charges.append(body)
                    return self.out({"status": True, "data": {
                        "status": MockPay.charge_status, "reference": body["reference"], "amount": body["amount"],
                        "currency": body["currency"], "id": 900 + len(MockPay.charges),
                        "authorization": {"authorization_code": body["authorization_code"], "reusable": True,
                                          "last4": "4081", "brand": "visa", "exp_month": "12", "exp_year": "2030"}}})
                self.out({}, 404)

        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{cls.srv.server_address[1]}"


def card_ok(ref, amount_minor, reusable=True):
    MockPay.verify[ref] = {"status": "success", "reference": ref, "amount": amount_minor, "currency": "KES", "id": 777,
                           "customer": {"email": "m@example.com"},
                           "authorization": {"authorization_code": "AUTH_secret_code", "reusable": reusable,
                                             "last4": "4081", "brand": "visa", "exp_month": "12", "exp_year": "2030"}}


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.url = MockPay.start()

    @classmethod
    def tearDownClass(cls):
        MockPay.srv.shutdown()

    def setUp(self):
        MockPay.reset()
        self.env = dict(os.environ)
        os.environ.update({"NORTHCHART_BILLING": "test", "NORTHCHART_KES_PER_USD": "129.4",
                           "PAYSTACK_SECRET_KEY": SK, "PAYSTACK_API_URL": self.url,
                           "DARAJA_CONSUMER_KEY": "k", "DARAJA_CONSUMER_SECRET": "s", "DARAJA_API_URL": self.url,
                           "DARAJA_ENV": "sandbox", "DARAJA_CALLBACK_IPS": "196.201.214.200,127.0.0.0/8",
                           "MPESA_PROVIDER": "daraja"})
        for k in ("PAYSTACK_CURRENCY", "DARAJA_TILL", "NORTHCHART_PRICE_PLUS_MONTH", "NORTHCHART_PRICE_PLUS_MONTH_KES"):
            os.environ.pop(k, None)
        self.dir = tempfile.mkdtemp(prefix="nc-bill-")
        self.acc = na.Accounts(self.dir, lambda: "trader", lambda: "desk-pass-123")
        self.owner = self.acc.setup_owner("owner@example.com", "Owner", "a-long-owner-pass", "desk-pass-123", "127.0.0.1")
        code = self.acc.new_invite(self.owner["uid"])
        self.m = self.acc.signup("m@example.com", "Member One", "member-pass-123", code, "10.0.0.2")
        self.bl = nbl.Billing(self.dir, self.acc, nb.Vault(self.dir))

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.env)

    def mpesa_pay(self, plan="plus", interval="month", phone="0712 345 678"):
        out = self.bl.checkout(self.m, plan, interval, "mpesa", "https://desk.test", phone)
        cid = MockPay.stk[-1]["cid"]
        MockPay.stk_result[cid] = ("0", "The service request is processed successfully.")
        self.bl.mpesa_callback({"Body": {"stkCallback": {
            "CheckoutRequestID": cid, "ResultCode": 0, "CallbackMetadata": {"Item": [
                {"Name": "Amount", "Value": MockPay.stk[-1]["Amount"]}, {"Name": "MpesaReceiptNumber", "Value": "SKL1ABC2DE"},
                {"Name": "PhoneNumber", "Value": 254712345678}]}}}})
        return out


class Prices(Base):
    def test_prices_and_rounding(self):
        self.assertEqual(self.bl.usd("plus", "month"), 25)
        self.assertEqual(self.bl.usd("pro", "month"), 200)
        self.assertEqual(self.bl.kes("plus", "month"), 3250)      # 25 x 129.4 = 3235 -> next 50
        self.assertEqual(self.bl.kes("pro", "month"), 25900)
        os.environ["NORTHCHART_PRICE_PLUS_MONTH_KES"] = "2999"
        self.assertEqual(self.bl.kes("plus", "month"), 2999)
        cat = self.bl.catalogue()
        self.assertEqual([p["id"] for p in cat["plans"]], ["free", "plus", "pro"])
        self.assertEqual(list(cat["plans"][1]["usd"]), ["month"])
        with self.assertRaises(nbl.PaymentError):
            self.bl.quote(self.m, "plus", "year")         # monthly only
        self.assertTrue(cat["methods"]["mpesa"] and cat["methods"]["card"] and cat["test"])

    def test_periods_and_phones(self):
        jan31 = int(time.mktime((2026, 1, 31, 12, 0, 0, 0, 0, 0)) - time.timezone)
        feb = time.gmtime(nbl.add_period(jan31, "month"))
        self.assertEqual((feb.tm_mon, feb.tm_mday), (2, 28))
        self.assertEqual(time.gmtime(nbl.add_period(jan31, "year")).tm_year, 2027)
        for raw in ("0712345678", "+254 712 345 678", "712345678", "0110 123 456"):
            self.assertRegex(nbl.phone_254(raw), r"^254[17]\d{8}$")
        with self.assertRaises(nbl.PaymentError):
            nbl.phone_254("0812345678")
        self.assertEqual(nbl.mask_phone("254712345678"), "254712***678")


class Gates(Base):
    def test_off_mode_opens_everything_and_owner_is_always_pro(self):
        self.assertEqual(self.bl.plan_of(self.m), "free")
        self.assertEqual(self.bl.plan_of(self.owner), "pro")
        os.environ["NORTHCHART_BILLING"] = "off"
        self.assertEqual(self.bl.plan_of(self.m), "pro")

    def test_feature_gates_and_limits(self):
        with self.assertRaises(nbl.Upgrade) as e:
            self.bl.require(self.m, "broker_sync")
        self.assertEqual(e.exception.need, "plus")
        self.assertIn("/billing", e.exception.payload()["upgrade"])
        self.bl.grant(self.m["uid"], "plus", 30)
        self.bl.require(self.m, "broker_sync")
        with self.assertRaises(nbl.Upgrade) as e:
            self.bl.require(self.m, "connect")
        self.assertEqual(e.exception.need, "pro")
        self.bl.require_room(self.m, "brokers", 1)
        with self.assertRaises(nbl.Upgrade):
            self.bl.require_room(self.m, "brokers", 2)

    def test_agent_allowance(self):
        for _ in range(3):
            self.bl.use_agent(self.m)
        with self.assertRaises(nbl.Upgrade) as e:
            self.bl.use_agent(self.m)
        self.assertEqual(e.exception.need, "plus")
        self.assertIsNone(self.bl.use_agent(self.owner))


class MpesaCheckout(Base):
    def test_stk_push_callback_and_query(self):
        out = self.mpesa_pay()
        self.assertEqual(out["status"], "pending")
        push = MockPay.stk[0]
        self.assertEqual(push["PartyA"], "254712345678")
        self.assertEqual(push["Amount"], 3250)
        self.assertEqual(push["TransactionType"], "CustomerPayBillOnline")
        self.assertEqual(push["CallBackURL"], "https://desk.test/api/billing/mpesa/callback")
        self.assertLessEqual(len(push["AccountReference"]), 12)
        self.assertEqual(self.bl.plan_of(self.m), "plus")
        st = self.bl.status(self.m)
        self.assertEqual(st["sub"]["phone_masked"], "254712***678")
        p = st["payments"][0]
        self.assertEqual((p["status"], p["receipt"], p["amount"]), ("paid", "SKL1ABC2DE", 3250))
        self.assertGreater(st["sub"]["period_end"] - time.time(), 27 * 86400)
        raw = open(os.path.join(self.dir, "billing", "billing.json")).read()
        self.assertNotIn("254712345678", raw)            # the number is sealed; only the masked form is plain
        with open(os.path.join(self.dir, "accounts", "outbox.log")) as f:
            self.assertIn("Receipt", f.read())

    def test_forged_callback_is_not_believed(self):
        self.bl.checkout(self.m, "plus", "month", "mpesa", "https://desk.test", "0712345678")
        cid = MockPay.stk[-1]["cid"]
        # a callback claiming success, but Safaricom's own status says still processing
        self.bl.mpesa_callback({"Body": {"stkCallback": {"CheckoutRequestID": cid, "ResultCode": 0}}})
        self.assertEqual(self.bl.plan_of(self.m), "free")
        MockPay.stk_result[cid] = ("1032", "Request cancelled by user")
        ref = self.bl.history(self.m["uid"])[0]["ref"]
        self.bl.data["payments"][ref]["created"] -= 20
        self.assertEqual(self.bl.check(ref, self.m["uid"]), "failed")
        self.assertEqual(self.bl.plan_of(self.m), "free")
        self.assertFalse(self.bl.mpesa_callback({"Body": {"stkCallback": {"CheckoutRequestID": "ws_CO_unknown"}}}))

    def test_short_payment_rejected(self):
        self.bl.checkout(self.m, "plus", "month", "mpesa", "https://desk.test", "0712345678")
        cid = MockPay.stk[-1]["cid"]
        MockPay.stk_result[cid] = ("0", "ok")
        self.bl.mpesa_callback({"Body": {"stkCallback": {"CheckoutRequestID": cid, "CallbackMetadata": {
            "Item": [{"Name": "Amount", "Value": 10}, {"Name": "MpesaReceiptNumber", "Value": "X1"}]}}}})
        self.assertEqual(self.bl.plan_of(self.m), "free")

    def test_till_number_uses_buy_goods(self):
        os.environ["DARAJA_TILL"] = "5551234"
        self.bl.daraja = nbl.Daraja()
        self.bl.checkout(self.m, "pro", "month", "mpesa", "https://desk.test", "0712345678")
        self.assertEqual(MockPay.stk[-1]["TransactionType"], "CustomerBuyGoodsOnline")
        self.assertEqual(MockPay.stk[-1]["PartyB"], "5551234")
        self.assertEqual(MockPay.stk[-1]["Amount"], 25900)

    def test_mpesa_limit(self):
        os.environ["NORTHCHART_PRICE_PRO_MONTH_KES"] = "260000"
        with self.assertRaises(nbl.PaymentError) as e:
            self.bl.checkout(self.m, "pro", "month", "mpesa", "https://desk.test", "0712345678")
        self.assertIn("by card", str(e.exception))
        self.assertEqual(MockPay.stk, [])
        os.environ.pop("NORTHCHART_PRICE_PRO_MONTH_KES")


class CardCheckout(Base):
    def test_card_checkout_saves_card_sealed(self):
        out = self.bl.checkout(self.m, "plus", "month", "card", "https://desk.test")
        self.assertEqual(out["redirect"], "https://checkout.paystack.test/abc")
        ref = out["ref"]
        card_ok(ref, 325000)
        self.assertEqual(self.bl.check(ref, self.m["uid"]), "paid")
        s = self.bl.status(self.m)["sub"]
        self.assertEqual(s["card"], {"brand": "Visa", "last4": "4081", "exp": "12/30"})
        raw = open(os.path.join(self.dir, "billing", "billing.json")).read()
        self.assertNotIn("AUTH_secret_code", raw)

    def test_amount_mismatch_fails(self):
        ref = self.bl.checkout(self.m, "plus", "month", "card", "https://desk.test")["ref"]
        card_ok(ref, 100)
        self.assertEqual(self.bl.check(ref), "failed")
        self.assertEqual(self.bl.plan_of(self.m), "free")

    def test_webhook_signature(self):
        ref = self.bl.checkout(self.m, "pro", "month", "card", "https://desk.test")["ref"]
        card_ok(ref, 25900 * 100)
        raw = json.dumps({"event": "charge.success", "data": {"reference": ref}}).encode()
        self.assertFalse(self.bl.paystack_webhook(raw, "bad"))
        self.assertEqual(self.bl.plan_of(self.m), "free")
        sig = hmac.new(SK.encode(), raw, hashlib.sha512).hexdigest()
        self.assertTrue(self.bl.paystack_webhook(raw, sig))
        self.assertEqual(self.bl.plan_of(self.m), "pro")


class Changes(Base):
    def test_upgrade_credit_and_downgrade_later(self):
        self.mpesa_pay("plus", "month")
        q = self.bl.quote(self.m, "pro", "month")
        self.assertGreater(q["credit"], 3000)                  # nearly a full unused month of Plus
        self.assertEqual(q["amount"], q["kes"] - q["credit"])
        self.mpesa_pay("pro", "month")
        self.assertEqual(self.bl.plan_of(self.m), "pro")
        out = self.bl.checkout(self.m, "plus", "month", "mpesa", "https://desk.test", "0712345678")
        self.assertTrue(out["scheduled"])
        self.assertEqual(self.bl.plan_of(self.m), "pro")       # until the end of the paid period
        self.assertEqual(self.bl.status(self.m)["sub"]["next_plan"], "plus")
        with self.assertRaises(nbl.PaymentError):
            self.bl.checkout(self.owner, "plus", "month", "mpesa", "https://desk.test", "0712345678")

    def test_cancel_and_resume(self):
        self.mpesa_pay()
        self.bl.cancel(self.m)
        self.assertTrue(self.bl.status(self.m)["sub"]["cancel_at_period_end"])
        self.bl.resume(self.m)
        self.assertFalse(self.bl.status(self.m)["sub"]["cancel_at_period_end"])


class Renewals(Base):
    def test_card_renews_itself(self):
        ref = self.bl.checkout(self.m, "plus", "month", "card", "https://desk.test")["ref"]
        card_ok(ref, 325000)
        self.bl.check(ref)
        end = self.bl.data["subs"][self.m["uid"]]["period_end"]
        self.assertIn(("" or self.m["uid"], "reminded"), self.bl.tick("https://desk.test", now=end - 86400))
        done = self.bl.tick("https://desk.test", now=end + 60)
        self.assertIn((self.m["uid"], "renewed"), done)
        self.assertEqual(MockPay.charges[-1]["authorization_code"], "AUTH_secret_code")
        self.assertEqual(MockPay.charges[-1]["amount"], 325000)
        self.assertGreater(self.bl.data["subs"][self.m["uid"]]["period_end"], end + 27 * 86400)

    def test_mpesa_prompt_then_lapse(self):
        self.mpesa_pay()
        s = self.bl.data["subs"][self.m["uid"]]
        end = s["period_end"]
        n = len(MockPay.stk)
        done = self.bl.tick("https://desk.test", now=end + 60)
        self.assertIn((self.m["uid"], "stk_sent"), done)
        self.assertEqual(len(MockPay.stk), n + 1)
        self.assertEqual(s["status"], "past_due")
        self.bl.tick("https://desk.test", now=end + 3600)        # no second prompt within a day
        self.assertEqual(len(MockPay.stk), n + 1)
        with unittest.mock.patch.object(nbl, "_now", lambda: end + 2 * 86400):
            self.assertEqual(self.bl.plan_of(self.m), "plus")    # grace days
        self.bl.tick("https://desk.test", now=end + 4 * 86400)
        self.assertEqual(s["status"], "expired")
        self.assertEqual(self.bl.plan_of(self.m), "free")

    def test_paying_in_grace_keeps_the_anniversary(self):
        self.mpesa_pay()
        s = self.bl.data["subs"][self.m["uid"]]
        end = s["period_end"]
        s["period_end"] = int(time.time()) - 86400     # due yesterday
        s["status"] = "past_due"
        due = s["period_end"]
        self.mpesa_pay()
        self.assertEqual(s["period_start"], due)
        self.assertEqual(s["status"], "active")
        self.assertLess(s["period_end"], end + 5 * 86400)

    def test_ratiba_and_c2b(self):
        self.mpesa_pay()
        out = self.bl.set_autopay(self.m, "ratiba", "https://desk.test")
        self.assertEqual(out["ratiba"]["state"], "requested")
        r = MockPay.ratiba[-1]
        self.assertEqual((r["Frequency"], r["TransactionType"], r["ReceiverPartyIdentifierType"]),
                         ("4", "Standing Order Customer Pay Bill", "4"))
        self.bl.ratiba_callback({"responseHeader": {"requestRefID": "rr-1", "responseCode": "0"},
                                 "responseBody": {"responseData": [{"name": "responseCode", "value": "0"}]}})
        s = self.bl.data["subs"][self.m["uid"]]
        self.assertEqual(s["ratiba"]["state"], "active")
        end = s["period_end"]
        n = len(MockPay.stk)
        self.bl.tick("https://desk.test", now=end + 60)
        self.assertEqual(len(MockPay.stk), n)                    # standing order pays; no prompt
        acct = self.bl._account_ref(self.m["uid"])
        pay = {"TransID": "SKR9XYZ", "TransAmount": "3250", "BillRefNumber": acct, "BusinessShortCode": "174379"}
        self.assertFalse(self.bl.c2b_confirmation(pay, "8.8.8.8"))     # not Safaricom
        self.assertEqual(s["status"], "past_due")
        self.assertTrue(self.bl.c2b_confirmation(pay, "196.201.214.200"))
        self.assertEqual(s["status"], "active")
        self.assertGreater(s["period_end"], end)
        before = s["period_end"]
        self.bl.c2b_confirmation(pay, "196.201.214.200")              # same TransID twice: counted once
        self.assertEqual(s["period_end"], before)


class Http(Base):
    def setUp(self):
        super().setUp()
        seed()
        self._saved = (sd._accounts, sd._billing)
        sd._accounts = self.acc
        sd._billing = self.bl
        sd._user_journals.clear()

    def tearDown(self):
        sd._accounts, sd._billing = self._saved
        sd._user_journals.clear()
        sd.enter_user(None)
        super().tearDown()

    def member(self, port):
        c = Client(port)
        st, out, _ = c.req("POST", "/api/auth/signin", {"email": "m@example.com", "password": "member-pass-123"})
        self.assertEqual(st, 200, out)
        return c

    def test_pages_gates_and_checkout(self):
        with DeskServer() as s:
            anon = Client(s.port)
            st, body, _ = anon.req("GET", "/pricing", headers={"Accept": "text/html"})
            self.assertEqual(st, 200)
            self.assertIn(b"Lipa na M-Pesa", body)
            c = self.member(s.port)
            st, me, _ = c.req("GET", "/api/auth/me")
            self.assertEqual((me["plan"], me["billing"]), ("free", "test"))
            st, body, _ = c.req("GET", "/billing", headers={"Accept": "text/html"})
            self.assertEqual(st, 200)
            # free plan: automatic sync is locked, with a way to upgrade
            st, out, _ = c.req("POST", "/api/auth/tokens", {"label": "x"})
            self.assertEqual(st, 402)
            self.assertEqual(out["need"], "plus")
            st, out, _ = c.req("POST", "/api/brokers/metaapi", {"login": "1", "server": "s", "password": "p"})
            self.assertEqual(st, 402)
            st, out, _ = c.req("POST", "/api/journal/draft", {"id": "x"})
            self.assertEqual(st, 402)
            st, out, _ = c.req("GET", "/api/billing/admin")
            self.assertEqual(st, 403)
            # pay with M-Pesa through the routes, callback included
            st, q, _ = c.req("GET", "/api/billing/quote?plan=plus&interval=month")
            self.assertEqual(q["amount"], 3250)
            st, out, _ = c.req("POST", "/api/billing/checkout", {"plan": "plus", "interval": "month", "method": "mpesa",
                                                                 "phone": "0712345678"})
            self.assertEqual(st, 200, out)
            cid = MockPay.stk[-1]["cid"]
            MockPay.stk_result[cid] = ("0", "ok")
            st, _, _ = anon.req("POST", "/api/billing/mpesa/callback", {"Body": {"stkCallback": {
                "CheckoutRequestID": cid, "ResultCode": 0, "CallbackMetadata": {"Item": [
                    {"Name": "Amount", "Value": 3250}, {"Name": "MpesaReceiptNumber", "Value": "SKL0000001"}]}}}},
                headers={"Origin": "https://safaricom.example"})
            self.assertEqual(st, 200)
            for _ in range(40):
                if self.bl.plan_of(self.m) == "plus":
                    break
                time.sleep(0.05)
            st, out, _ = c.req("GET", "/api/billing/status?ref=" + out["ref"])
            self.assertEqual(out["status"], "paid")
            st, out, _ = c.req("POST", "/api/auth/tokens", {"label": "Laptop"})
            self.assertEqual(st, 200)
            st, state, _ = c.req("GET", "/api/billing/state")
            ref = state["payments"][0]["ref"]
            st, body, _ = c.req("GET", "/billing/receipt?ref=" + ref, headers={"Accept": "text/html"})
            self.assertEqual(st, 200)
            self.assertIn(b"SKL0000001", body)
            # another member cannot open this receipt
            code = self.acc.new_invite(self.owner["uid"])
            self.acc.signup("x@example.com", "X", "another-pass-123", code, "10.0.0.3")
            o = Client(s.port)
            o.req("POST", "/api/auth/signin", {"email": "x@example.com", "password": "another-pass-123"})
            st, _, _ = o.req("GET", "/billing/receipt?ref=" + ref, headers={"Accept": "text/html"})
            self.assertEqual(st, 404)

    def test_webhook_route_and_owner_overview(self):
        with DeskServer() as s:
            ref = self.bl.checkout(self.m, "plus", "month", "card", "https://desk.test")["ref"]
            card_ok(ref, 325000)
            raw = json.dumps({"event": "charge.success", "data": {"reference": ref}})
            st, _, _ = Client(s.port).req("POST", "/api/billing/paystack/webhook", json.loads(raw),
                                          headers={"x-paystack-signature": "nope"})
            self.assertEqual(st, 401)
            self.assertEqual(self.bl.plan_of(self.m), "free")
            own = Client(s.port)
            st, out, _ = own.req("POST", "/api/auth/signin", {"email": "owner@example.com", "password": "a-long-owner-pass"})
            self.assertEqual(st, 200, out)
            self.bl.check(ref)
            st, o, _ = own.req("GET", "/api/billing/admin")
            self.assertEqual(st, 200)
            self.assertEqual((o["active"], o["mrr_usd"]), (1, 25.0))
            self.assertTrue(o["setup"]["paystack_test"])
            st, out, _ = own.req("POST", "/api/billing/admin/grant", {"uid": self.m["uid"], "plan": "pro", "days": 14})
            self.assertEqual(st, 200)
            self.assertEqual(self.bl.plan_of(self.m), "pro")

    def test_agent_stream_shows_upgrade_after_free_allowance(self):
        with DeskServer() as s:
            c = self.member(s.port)
            self.bl.data["usage"][self.m["uid"]] = {"day": time.strftime("%Y-%m-%d", time.gmtime(time.time() + 3 * 3600)),
                                                    "agent": 3}
            st, raw, _ = c.req("POST", "/api/agent/stream", {"question": "Analyse gold", "symbol": "XAUUSD"})
            self.assertEqual(st, 200)
            text = raw if isinstance(raw, bytes) else json.dumps(raw).encode()
            self.assertIn(b'"type": "upgrade"', text)
            self.assertIn(b"/billing?plan=plus", text)


import unittest.mock  # noqa: E402

if __name__ == "__main__":
    unittest.main()


class PaystackMpesa(Base):
    """Default route: M-Pesa through the Paystack account, no Daraja keys needed."""

    def setUp(self):
        super().setUp()
        os.environ["MPESA_PROVIDER"] = "paystack"
        for k in ("DARAJA_CONSUMER_KEY", "DARAJA_CONSUMER_SECRET"):
            os.environ.pop(k, None)

    def _settle(self, ref, amount_kes, status="success"):
        MockPay.verify[ref] = {"status": status, "reference": ref, "amount": amount_kes * 100, "currency": "KES",
                               "id": 4242, "channel": "mobile_money", "receipt_number": "TKA1BC2DEF",
                               "authorization": {"authorization_code": "AUTH_mm", "reusable": False, "channel": "mobile_money"}}
        p = self.bl.data["payments"][ref]
        p["created"] -= 10
        return self.bl.check(ref, self.m["uid"])

    def test_default_route_and_checkout(self):
        os.environ.pop("MPESA_PROVIDER")
        self.assertEqual(nbl.mpesa_via(), "paystack")
        cat = self.bl.catalogue()
        self.assertTrue(cat["methods"]["mpesa"])
        self.assertFalse(cat["methods"]["ratiba"])
        out = self.bl.checkout(self.m, "plus", "month", "mpesa", "https://desk.test", "0712 345 678")
        self.assertEqual(out["status"], "pending")
        c = MockPay.mm[-1]
        self.assertEqual((c["currency"], c["amount"]), ("KES", 325000))
        self.assertEqual(c["mobile_money"], {"phone": "+254712345678", "provider": "mpesa"})
        self.assertEqual(MockPay.stk, [])                       # Daraja never called
        self.assertEqual(self._settle(out["ref"], 3250), "paid")
        st = self.bl.status(self.m)
        self.assertEqual(st["plan"], "plus")
        self.assertEqual(self.bl.payment(out["ref"])["receipt"], "TKA1BC2DEF")
        self.assertNotIn("card", self.bl.data["subs"][self.m["uid"]])   # M-Pesa never saves a card

    def test_short_payment_and_pending(self):
        out = self.bl.checkout(self.m, "plus", "month", "mpesa", "https://desk.test", "0712345678")
        MockPay.verify[out["ref"]] = {"status": "ongoing", "reference": out["ref"]}
        self.bl.data["payments"][out["ref"]]["created"] -= 10
        self.assertEqual(self.bl.check(out["ref"], self.m["uid"]), "pending")
        self.assertEqual(self._settle(out["ref"], 100), "failed")

    def test_ratiba_needs_daraja(self):
        out = self.bl.checkout(self.m, "plus", "month", "mpesa", "https://desk.test", "0712345678")
        self._settle(out["ref"], 3250)
        with self.assertRaises(nbl.PaymentError):
            self.bl.set_autopay(self.m, "ratiba", "https://desk.test", "0712345678")

    def test_renewal_prompt_goes_through_paystack(self):
        out = self.bl.checkout(self.m, "plus", "month", "mpesa", "https://desk.test", "0712345678")
        self._settle(out["ref"], 3250)
        s = self.bl.data["subs"][self.m["uid"]]
        done = self.bl.tick("https://desk.test", now=s["period_end"] + 60)
        self.assertIn((self.m["uid"], "stk_sent"), done)
        self.assertEqual(len(MockPay.mm), 2)
        self.assertEqual(MockPay.mm[-1]["mobile_money"]["phone"], "+254712345678")

    def test_unreachable_provider_is_a_clear_error(self):
        os.environ["PAYSTACK_API_URL"] = "http://127.0.0.1:9"          # nothing listens here
        self.bl.paystack = nbl.Paystack()
        with self.assertRaises(nbl.PaymentError) as e:
            self.bl.checkout(self.m, "plus", "month", "mpesa", "https://desk.test", "0712345678")
        self.assertIn("could not be reached", str(e.exception))
        self.assertEqual([p["status"] for p in self.bl.data["payments"].values()], ["failed"])
