"""idempotency-failed-retry: a failed attempt must not poison the key.

Research-backed gap (Stripe/AWS retry guidance): a server that
records an idempotency key as merely "seen" on the first attempt
replays the stored failure when the client corrects and retries
with the same key. Fixed fixture never records a key on a failed
send (stores only on 201); naive fixture replays the stored 400.
"""
import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixture_util import FixtureServer
from apiverdict.idempotency import run_idempotency_failed_retry_check
import subprocess

VALID = {"slot": "fr-slot"}
INVALID = {"__invalid__": True}


def _run(base, key, mode_slot="fr-slot"):
    return run_idempotency_failed_retry_check(
        url=f"{base}/api/book",
        payload={"slot": mode_slot},
        invalid_payload=INVALID,
        idempotency_key=key,
        verify_url=f"{base}/api/count?slot={mode_slot}")


class TestFailedRetryFixture(unittest.TestCase):
    def test_naive_fails_fixed_passes(self):
        with FixtureServer("naive") as base:
            naive = _run(base, "fr-key-1")
        with FixtureServer("fixed") as base:
            fixed = _run(base, "fr-key-1")
        self.assertEqual(naive.status, "FAIL", naive.summary)
        self.assertIn("poisoned", naive.summary)
        self.assertEqual(naive.evidence["retry_status"], 400)
        self.assertEqual(naive.evidence["delta"], 0)
        self.assertEqual(fixed.status, "PASS", fixed.summary)
        self.assertEqual(fixed.evidence["delta"], 1)

    def test_poison_is_key_scoped_not_global(self):
        # Naive: an invalid send with K1 poisons K1, but a valid send
        # with a DIFFERENT key K2 must still create — the poison
        # follows the key, not the endpoint.
        from apiverdict.http_util import request_json
        with FixtureServer("naive") as base:
            bad, _b, _h = request_json(
                "POST", f"{base}/api/book", INVALID,
                {"Idempotency-Key": "fr-key-poison"}, timeout=5)
            self.assertEqual(bad, 400)
            good, _b2, _h2 = request_json(
                "POST", f"{base}/api/book", {"slot": "fr-slot-fresh"},
                {"Idempotency-Key": "fr-key-fresh"}, timeout=5)
            self.assertEqual(good, 201)

    def test_no_valid_payload_is_inconclusive(self):
        # A missing valid payload would make the retry itself an
        # invalid send; its 400 must not misreport as "poisoned".
        with FixtureServer("fixed") as base:
            verdict = run_idempotency_failed_retry_check(
                url=f"{base}/api/book", payload=None,
                invalid_payload=INVALID,
                verify_url=f"{base}/api/count?slot=fr-slot")
        self.assertEqual(verdict.status, "INCONCLUSIVE")
        self.assertIn("no valid payload", verdict.summary)

    def test_remote_guard_and_no_verify(self):
        verdict = run_idempotency_failed_retry_check(
            url="http://example.com/api/book", payload=VALID,
            invalid_payload=INVALID,
            verify_url="http://example.com/api/count")
        self.assertEqual(verdict.status, "INCONCLUSIVE")
        with FixtureServer("fixed") as base:
            no_verify = run_idempotency_failed_retry_check(
                url=f"{base}/api/book", payload=VALID,
                invalid_payload=INVALID)
        self.assertEqual(no_verify.status, "INCONCLUSIVE")


class TestFailedRetryAcceptedInvalidIsInconclusive(unittest.TestCase):
    """If the server accepts the invalid input (2xx), there is no
    failed attempt to judge — INCONCLUSIVE, never a verdict on
    poisoned state we cannot trust."""

    def test_accepted_invalid_is_inconclusive(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                body = json.dumps({"ok": True}).encode()
                self.send_response(201)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):  # noqa: N802
                body = json.dumps({"count": 0}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            base = f"http://127.0.0.1:{server.server_address[1]}"
            verdict = run_idempotency_failed_retry_check(
                url=f"{base}/api/book", payload=VALID,
                invalid_payload=INVALID,
                verify_url=f"{base}/api/count")
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(verdict.status, "INCONCLUSIVE")
        self.assertIn("accepted", verdict.summary)


class TestFailedRetryCli(unittest.TestCase):
    def _cli(self, base, slot):
        return subprocess.run(
            [sys.executable, "-m", "apiverdict", "idempotency-failed-retry",
             "--url", f"{base}/api/book",
             "--payload", json.dumps({"slot": slot}),
             "--verify-url", f"{base}/api/count?slot={slot}"],
            capture_output=True, text=True, cwd=".", timeout=60)

    def test_cli_naive_exit_1_fixed_exit_0(self):
        with FixtureServer("naive") as base:
            naive = self._cli(base, "fr-cli-naive")
        with FixtureServer("fixed") as base:
            fixed = self._cli(base, "fr-cli-fixed")
        self.assertEqual(naive.returncode, 1, naive.stdout + naive.stderr)
        self.assertIn("poisoned", naive.stdout)
        self.assertEqual(fixed.returncode, 0, fixed.stdout + fixed.stderr)


if __name__ == "__main__":
    unittest.main()
