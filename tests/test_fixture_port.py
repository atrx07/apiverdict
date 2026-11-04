"""A fixture on a busy port exits 2 with a clean message,
not a socket traceback."""
import socket
import subprocess
import sys
import threading
import unittest
from http.server import HTTPServer, BaseHTTPRequestHandler


class TestFixturePort(unittest.TestCase):
    def test_busy_port_exits_2_cleanly(self):
        class Quiet(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()

            def log_message(self, *a):
                pass

        blocker = HTTPServer(("127.0.0.1", 0), Quiet)
        port = blocker.server_address[1]
        thread = threading.Thread(target=blocker.serve_forever, daemon=True)
        thread.start()
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "apiverdict", "fixture",
                 "--mode", "fixed", "--port", str(port)],
                capture_output=True, text=True, cwd=".", timeout=20)
        finally:
            blocker.shutdown()
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("cannot bind fixture port", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)


if __name__ == "__main__":
    unittest.main()
