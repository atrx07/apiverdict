"""Shared test helper: start a fixture server on an ephemeral port."""

import threading

from apiverdict.fixture import create_server


class FixtureServer:
    def __init__(self, mode: str):
        self.server = create_server(mode)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        port = self.server.server_address[1]
        return f"http://127.0.0.1:{port}"

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        return False
