"""Response plumbing: security headers, gzip, static caching, friendly 404 and the crash safety net."""
import gzip
import http.client
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_agent import DeskServer, sd, seed  # noqa: E402


def get(port, path, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    c.request("GET", path, headers=headers or {})
    r = c.getresponse()
    body = r.read()
    c.close()
    return r, body


class Plumbing(unittest.TestCase):
    def setUp(self):
        seed()

    def test_security_headers_on_every_answer(self):
        with DeskServer() as s:
            for path in ("/signin", "/api/health", "/nope"):
                r, _ = get(s.port, path)
                self.assertEqual(r.getheader("X-Content-Type-Options"), "nosniff", path)
                self.assertEqual(r.getheader("X-Frame-Options"), "SAMEORIGIN", path)
                self.assertTrue(r.getheader("Referrer-Policy"), path)

    def test_pages_are_gzipped_when_asked(self):
        with DeskServer() as s:
            r, plain = get(s.port, "/signin")
            self.assertIsNone(r.getheader("Content-Encoding"))
            r, packed = get(s.port, "/signin", {"Accept-Encoding": "gzip, deflate"})
            self.assertEqual(r.getheader("Content-Encoding"), "gzip")
            self.assertEqual(gzip.decompress(packed), plain)
            self.assertLess(len(packed), len(plain))

    def test_static_files_cache_with_one_header(self):
        with DeskServer() as s:
            r, _ = get(s.port, "/static/klinecharts.min.js", {"Authorization": "Basic " + sd.base64.b64encode(
                f"{sd.AUTH_USER}:{sd.AUTH_PASS}".encode()).decode()} if sd.AUTH_PASS else {})
            if r.status == 200:
                cc = r.headers.get_all("Cache-Control")
                self.assertEqual(cc, ["public, max-age=86400"])

    def test_unknown_page_is_friendly(self):
        with DeskServer() as s:
            r, body = get(s.port, "/api/nothing-here")
            self.assertIn(r.status, (401, 404))
            r, body = get(s.port, "/legal/terms")
            self.assertEqual(r.status, 200)

    def test_a_crash_answers_500_instead_of_dropping(self):
        orig = sd.Handler._billing_public

        def boom(self, method, parsed):
            if parsed.path == "/api/boom":
                raise RuntimeError("bug")
            return orig(self, method, parsed)
        sd.Handler._billing_public = boom
        try:
            with DeskServer() as s:
                r, body = get(s.port, "/api/boom")
                self.assertEqual(r.status, 500)
                self.assertIn(b"Something went wrong", body)
        finally:
            sd.Handler._billing_public = orig


if __name__ == "__main__":
    unittest.main()
