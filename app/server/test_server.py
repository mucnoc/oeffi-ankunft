import http.client
import json
import os
import shutil
import tempfile
import threading
import unittest

from server import main_parts, make_notifier, make_server

SNAP = {"now": "07:20", "trips": [{"title": None, "state": "underway"}]}


class StubTracker:
    def snapshot(self):
        return json.loads(json.dumps(SNAP))


class HttpTest(unittest.TestCase):
    def start(self, tracker, config_error=None):
        base = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        static = os.path.join(base, "static")
        os.makedirs(static)
        with open(os.path.join(static, "summary.js"), "w", encoding="utf-8") as f:
            f.write("// Kachel")
        with open(os.path.join(base, "geheim.txt"), "w", encoding="utf-8") as f:
            f.write("nicht ausliefern")
        httpd = make_server(tracker, static, 0, quiet=True, config_error=config_error)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        return httpd.server_address[1]

    def get(self, port, path):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", path)
        answer = conn.getresponse()
        body = answer.read()
        conn.close()
        return answer.status, answer.getheader("Content-Type"), body

    def test_status(self):
        port = self.start(StubTracker())
        status, ctype, body = self.get(port, "/api/status")
        self.assertEqual((status, ctype), (200, "application/json; charset=utf-8"))
        self.assertEqual(json.loads(body), dict(SNAP, error=None))

    def test_no_config(self):
        port = self.start(StubTracker(), config_error="no_config")
        self.assertEqual(json.loads(self.get(port, "/api/status")[2])["error"], "no_config")

    def test_static_and_unknown(self):
        port = self.start(StubTracker())
        status, ctype, _ = self.get(port, "/summary.js")
        self.assertEqual((status, ctype), (200, "text/javascript; charset=utf-8"))
        self.assertEqual(self.get(port, "/api/x")[0], 404)
        self.assertEqual(self.get(port, "/../geheim.txt")[0], 404)
        self.assertEqual(self.get(port, "/%2e%2e/geheim.txt")[0], 404)

    def test_missing_config_gives_empty_tracker(self):
        base = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        tracker, error = main_parts(base, demo=False, log=lambda line: None)
        self.assertEqual((tracker.watches, error), ([], "no_config"))
        with open(os.path.join(base, "config.json"), "w", encoding="utf-8") as f:
            f.write("{kaputt")
        self.assertEqual(main_parts(base, demo=False, log=lambda line: None)[1], "bad_config")

    def test_notifier_posts_and_judges_answers(self):
        sent = []

        def post(url, body):
            sent.append((url, json.loads(body)))
            return answers.pop(0)
        answers = [200, 504, 400, 502]
        notify = make_notifier({"url": "http://melder.test/api", "source": "Pendeln"}, post=post, log=lambda line: None)
        self.assertEqual([notify("a"), notify("b"), notify("c"), notify("d")], [True, True, True, False])
        self.assertEqual(sent[0], ("http://melder.test/api", {"text": "a", "source": "Pendeln"}))

        def broken(url, body):
            raise OSError("weg")
        self.assertFalse(make_notifier({"url": "http://x", "source": "s"}, post=broken, log=lambda line: None)("x"))

        def slow(url, body):
            raise TimeoutError()
        self.assertTrue(make_notifier({"url": "http://x", "source": "s"}, post=slow, log=lambda line: None)("x"))

        def slow_old(url, body):
            import socket
            raise socket.timeout("timed out")  # Python 3.9: kein TimeoutError
        self.assertTrue(make_notifier({"url": "http://x", "source": "s"}, post=slow, log=lambda line: None)("x"))

    def test_main_parts_with_notify(self):
        base = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        with open(os.path.join(base, "config.json"), "w", encoding="utf-8") as f:
            json.dump({"watches": [{"stop_id": "x", "line": "1", "departure": "07:00", "target": "y"}],
                       "notify": {"url": "http://melder.test/api", "source": "Pendeln"}}, f)
        tracker, error = main_parts(base, demo=False, log=lambda line: None)
        self.assertIsNone(error)
        self.assertIsNotNone(tracker._notify)
        self.assertEqual(tracker._state_dir, base)

    def test_demo_shows_a_trip(self):
        tracker, error = main_parts(tempfile.gettempdir(), demo=True, log=lambda line: None)
        tracker.tick()
        trips = tracker.snapshot()["trips"]
        self.assertEqual((error, len(trips), trips[0]["line"]), (None, 1, "42"))


if __name__ == "__main__":
    unittest.main()
