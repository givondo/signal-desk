"""Agent desk streaming tests: desk mode, Claude mode against a mock Anthropic
API, and fallback. Stdlib only, no network.

Run: python -m unittest discover -s tests
"""
import base64
import http.client
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="na-test-"))
os.environ["SIGNALDESK_PASS"] = "desk-pass-123"
os.environ["NORTHCHART_STREAM_DELAY"] = "0"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import xauusd_trader as sd  # noqa: E402
from test_connect import fake_signal  # noqa: E402

AUTH = "Basic " + base64.b64encode(b"trader:desk-pass-123").decode()


def seed():
    with sd.state_lock:
        sd.latest["XAUUSD"] = fake_signal()
    t = int(time.time())
    sd.candle_cache["XAUUSD"].update({
        "ohlc": [[t - 900 * (40 - i), 2640 + i * 0.3, 2642 + i * 0.3, 2638 + i * 0.3, 2641 + i * 0.3]
                 for i in range(40)], "source": "GC=F", "ts": "21:00"})


def stream(port, question, symbol="XAUUSD"):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    c.request("POST", "/api/agent/stream", body=json.dumps({"question": question, "symbol": symbol}),
              headers={"Authorization": AUTH, "Content-Type": "application/json"})
    r = c.getresponse()
    assert r.status == 200, r.status
    assert r.getheader("Content-Type").startswith("text/event-stream")
    raw = r.read().decode()
    c.close()
    return [json.loads(chunk[5:].strip()) for chunk in raw.split("\n\n") if chunk.startswith("data:")]


class DeskServer:
    def __enter__(self):
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), sd.Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.port = self.srv.server_address[1]
        return self

    def __exit__(self, *a):
        self.srv.shutdown()
        self.srv.server_close()


class MockAnthropic(BaseHTTPRequestHandler):
    """Turn 1: ask for get_candles. Turn 2: stream a short answer."""
    calls = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        MockAnthropic.calls.append(body)
        if not body.get("stream"):                      # a specialist: one JSON reply
            reply = {"content": [{"type": "text", "text": json.dumps({
                "verdict": "bullish", "confidence": 70, "summary": "Claude specialist view",
                "points": ["point one", "point two"]})}]}
            out = json.dumps(reply).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
            return
        if "lead analyst" in (body.get("system") or ""):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for e in [{"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                      {"type": "content_block_delta", "index": 0,
                       "delta": {"type": "text_delta", "text": "Lead view: all four specialists agree."}},
                      {"type": "message_delta", "delta": {"stop_reason": "end_turn"}}]:
                self.wfile.write(f"data: {json.dumps(e)}\n\n".encode())
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        last = body["messages"][-1]
        if isinstance(last["content"], list) and last["content"][0].get("type") == "tool_result":
            events = [
                {"type": "message_start", "message": {"id": "m2"}},
                {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Gold is **LONG** — "}},
                {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "the chart is above."}},
                {"type": "content_block_stop", "index": 0},
                {"type": "message_delta", "delta": {"stop_reason": "end_turn"}},
                {"type": "message_stop"}]
        else:
            events = [
                {"type": "message_start", "message": {"id": "m1"}},
                {"type": "content_block_start", "index": 0,
                 "content_block": {"type": "tool_use", "id": "tu1", "name": "get_candles", "input": {}}},
                {"type": "content_block_delta", "index": 0,
                 "delta": {"type": "input_json_delta", "partial_json": "{\"symbol\": \"XAU"}},
                {"type": "content_block_delta", "index": 0,
                 "delta": {"type": "input_json_delta", "partial_json": "USD\"}"}},
                {"type": "content_block_stop", "index": 0},
                {"type": "message_delta", "delta": {"stop_reason": "tool_use"}},
                {"type": "message_stop"}]
        for e in events:
            self.wfile.write(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n".encode())


class AgentStreaming(unittest.TestCase):
    def setUp(self):
        seed()
        os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ.pop("NORTHCHART_ANTHROPIC_URL", None)

    def test_desk_mode_streams_tools_chart_then_text(self):
        with DeskServer() as s:
            evs = stream(s.port, "why no trade on gold?")
        kinds = [e["type"] for e in evs]
        self.assertEqual(kinds[0], "start")
        self.assertEqual(evs[0]["mode"], "desk")
        self.assertEqual(kinds[-1], "done")
        done_tools = [e["name"] for e in evs if e["type"] == "tool" and e["status"] == "done"]
        self.assertEqual(done_tools, ["get_market_snapshot", "get_signal_detail", "get_candles", "ask_desk"])
        chart = next(e["chart"] for e in evs if e["type"] == "chart")
        self.assertEqual(chart["kind"], "candles")
        self.assertEqual(len(chart["bars"]), 40)
        self.assertTrue(any(lv["label"] == "Stop" for lv in chart["levels"]))
        self.assertLess(kinds.index("chart"), kinds.index("text"))     # chart arrives before the words
        text = "".join(e["delta"] for e in evs if e["type"] == "text")
        self.assertIn("2,650.50", text)
        self.assertNotIn("—", text)
        self.assertGreater(sum(1 for k in kinds if k == "text"), 20)    # really streamed, not one blob

    def test_overview_draws_a_bar_chart(self):
        with DeskServer() as s:
            evs = stream(s.port, "scan all markets")
        chart = next(e["chart"] for e in evs if e["type"] == "chart")
        self.assertEqual(chart["kind"], "bars")

    def test_symbol_words_switch_instrument(self):
        with DeskServer() as s:
            evs = stream(s.port, "how is the yen doing", symbol="XAUUSD")
        self.assertEqual(evs[0]["symbol"], "USDJPY")

    def test_claude_mode_runs_tools_and_streams(self):
        mock = ThreadingHTTPServer(("127.0.0.1", 0), MockAnthropic)
        threading.Thread(target=mock.serve_forever, daemon=True).start()
        MockAnthropic.calls = []
        os.environ["ANTHROPIC_API_KEY"] = "test-key"
        os.environ["NORTHCHART_ANTHROPIC_URL"] = f"http://127.0.0.1:{mock.server_address[1]}/v1/messages"
        try:
            with DeskServer() as s:
                evs = stream(s.port, "chart gold for me")
        finally:
            mock.shutdown()
            mock.server_close()
        self.assertEqual(evs[0]["mode"], "claude")
        self.assertEqual(len(MockAnthropic.calls), 2)
        self.assertTrue(all(t["name"] != "submit_order" for t in MockAnthropic.calls[0]["tools"]))
        self.assertTrue(any(e["type"] == "tool" and e.get("name") == "get_candles" and e["status"] == "done"
                            for e in evs))
        self.assertTrue(any(e["type"] == "chart" for e in evs))
        text = "".join(e["delta"] for e in evs if e["type"] == "text")
        self.assertIn("LONG", text)
        self.assertNotIn("—", text)                                       # house style enforced on model output
        result = MockAnthropic.calls[1]["messages"][-1]["content"][0]
        self.assertEqual(result["tool_use_id"], "tu1")

    def test_desk_team_reports_then_lead(self):
        with DeskServer() as s:
            evs = stream(s.port, "why no trade on gold?")
        done = {e["id"]: e for e in evs if e["type"] == "agent" and e["status"] == "done"}
        self.assertEqual(set(done), {"market", "macro", "news", "risk"})
        self.assertTrue(all(e["verdict"] in ("bullish", "bearish", "neutral", "clear", "blocked", "caution",
                                             "no read") for e in done.values()))
        kinds = [e["type"] for e in evs]
        self.assertLess(kinds.index("team"), kinds.index("text"))
        text = "".join(e["delta"] for e in evs if e["type"] == "text")
        self.assertIn("specialists agree", text)
        self.assertIn("Risk manager", text)

    def test_claude_team_runs_specialists_in_parallel_then_streams_lead(self):
        mock = ThreadingHTTPServer(("127.0.0.1", 0), MockAnthropic)
        threading.Thread(target=mock.serve_forever, daemon=True).start()
        MockAnthropic.calls = []
        os.environ["ANTHROPIC_API_KEY"] = "test-key"
        os.environ["NORTHCHART_ANTHROPIC_URL"] = f"http://127.0.0.1:{mock.server_address[1]}/v1/messages"
        try:
            with DeskServer() as s:
                evs = stream(s.port, "why no trade on gold?")
        finally:
            mock.shutdown()
            mock.server_close()
        specialists = [c for c in MockAnthropic.calls if not c.get("stream")]
        self.assertEqual(len(specialists), 4)
        done = [e for e in evs if e["type"] == "agent" and e["status"] == "done"]
        self.assertTrue(all(e["source"] == "claude" for e in done))
        text = "".join(e["delta"] for e in evs if e["type"] == "text")
        self.assertIn("Lead view", text)
        self.assertFalse(any(e["type"] == "notice" for e in evs))

    def test_claude_failure_falls_back_to_desk(self):
        os.environ["ANTHROPIC_API_KEY"] = "test-key"
        os.environ["NORTHCHART_ANTHROPIC_URL"] = "http://127.0.0.1:9/v1/messages"
        with DeskServer() as s:
            evs = stream(s.port, "why no trade on gold?")
        self.assertTrue(any(e["type"] == "notice" for e in evs))
        self.assertEqual(evs[-1]["type"], "done")
        text = "".join(e["delta"] for e in evs if e["type"] == "text")
        self.assertIn("What the team found", text)          # rule-based team still answered

    def test_stream_requires_desk_login(self):
        with DeskServer() as s:
            c = http.client.HTTPConnection("127.0.0.1", s.port, timeout=10)
            c.request("POST", "/api/agent/stream", body="{}", headers={"Content-Type": "application/json"})
            self.assertEqual(c.getresponse().status, 401)
            c.close()


if __name__ == "__main__":
    unittest.main()


class DesktopApp(unittest.TestCase):
    def test_pwa_assets_are_public_and_valid(self):
        with DeskServer() as s:
            c = http.client.HTTPConnection("127.0.0.1", s.port, timeout=10)
            c.request("GET", "/manifest.webmanifest")
            r = c.getresponse()
            self.assertEqual(r.status, 200)
            m = json.loads(r.read())
            self.assertEqual(m["display"], "standalone")
            self.assertEqual({i["sizes"] for i in m["icons"]}, {"192x192", "512x512"})
            for path in ("/sw.js", "/icons/icon-192.png", "/icons/maskable-512.png"):
                c.request("GET", path)
                r = c.getresponse()
                body = r.read()
                self.assertEqual(r.status, 200, path)
                if path.endswith(".png"):
                    self.assertTrue(body.startswith(b"\x89PNG"))
            c.request("GET", "/")
            self.assertEqual(c.getresponse().status, 401)   # the app itself still needs the desk login
            c.close()


class SharedDesign(unittest.TestCase):
    def test_tracker_and_router_get_the_shared_header_and_theme(self):
        with DeskServer() as s:
            for path, active in (("/tracker", "Tracker"), ("/router", "Router")):
                c = http.client.HTTPConnection("127.0.0.1", s.port, timeout=10)
                c.request("GET", path, headers={"Authorization": AUTH})
                r = c.getresponse()
                page = r.read().decode()
                c.close()
                self.assertEqual(r.status, 200)
                self.assertNotIn("<!--NC:", page)                      # both markers replaced
                self.assertIn('class="nc-head"', page)
                self.assertIn("sd-theme-v2", page)                     # same theme switch as the home page
                self.assertIn(f'class="nc-pill on" href="{path}" aria-current="page">{active}<', page)
