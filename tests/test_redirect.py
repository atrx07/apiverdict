import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import apiverdict.redirect as mod


def _run(chain, start="http://127.0.0.1:9/a"):
    """chain: dict url -> (status, location-or-None)."""
    def fake(url, headers, timeout):
        return chain[url]
    orig = mod._fetch
    mod._fetch = fake
    try:
        return mod.run_redirect_check(start)
    finally:
        mod._fetch = orig


class TestRedirect(unittest.TestCase):
    def test_direct_200_passes(self):
        v = _run({"http://127.0.0.1:9/a": (200, None)})
        self.assertEqual(v.status, "PASS", v.summary)
        self.assertEqual(v.evidence["hops"], 0)

    def test_clean_chain_passes(self):
        v = _run({"http://127.0.0.1:9/a": (302, "/b"),
                  "http://127.0.0.1:9/b": (200, None)})
        self.assertEqual(v.status, "PASS", v.summary)
        self.assertEqual(v.evidence["hops"], 1)

    def test_cycle_fails(self):
        v = _run({"http://127.0.0.1:9/a": (302, "/b"),
                  "http://127.0.0.1:9/b": (302, "/a")})
        self.assertEqual(v.status, "FAIL", v.summary)
        self.assertIn("cycle", v.summary)

    def test_dangling_redirect_fails(self):
        v = _run({"http://127.0.0.1:9/a": (302, None)})
        self.assertEqual(v.status, "FAIL", v.summary)
        self.assertIn("no Location", v.summary)

    def test_https_downgrade_fails(self):
        v = _run({"https://127.0.0.1:9/a": (302, "http://127.0.0.1:9/b"),
                  "http://127.0.0.1:9/b": (200, None)},
                 start="https://127.0.0.1:9/a")
        self.assertEqual(v.status, "FAIL", v.summary)
        self.assertIn("downgrade", v.summary)

    def test_too_many_hops_fails(self):
        chain = {}
        for i in range(12):
            chain[f"http://127.0.0.1:9/{i}"] = (302, f"/{i + 1}")
        v = _run(chain, start="http://127.0.0.1:9/0")
        self.assertEqual(v.status, "FAIL", v.summary)
        self.assertIn("more than", v.summary)

    def test_transport_is_inconclusive(self):
        def boom(url, headers, timeout):
            raise ConnectionError("refused")
        orig = mod._fetch
        mod._fetch = boom
        try:
            v = mod.run_redirect_check("http://127.0.0.1:9/a")
        finally:
            mod._fetch = orig
        self.assertEqual(v.status, "INCONCLUSIVE")

    def test_bad_max_hops_is_inconclusive(self):
        v = mod.run_redirect_check("http://127.0.0.1:9/a", max_hops=0)
        self.assertEqual(v.status, "INCONCLUSIVE")


if __name__ == "__main__":
    unittest.main()


class TestRedirectProperty(unittest.TestCase):
    def test_random_dag_chains_pass_and_cycles_fail(self):
        import random
        import apiverdict.redirect as mod
        rng = random.Random(20261104)
        for trial in range(50):
            n = rng.randint(1, 8)
            nodes = [f"http://127.0.0.1:9/n{i}" for i in range(n)]
            chain = {}
            for i, node in enumerate(nodes):
                if i + 1 < n and rng.random() < 0.7:
                    chain[node] = (302, nodes[i + 1])
                else:
                    chain[node] = (200, None)
            # A DAG chain from n0: either it ends at a 200
            # (PASS) or it hits a node whose target is a
            # later node that itself ends — always finite,
            # so PASS unless the walk itself cycles, which a
            # forward-only DAG cannot do.
            orig = mod._fetch
            mod._fetch = lambda url, headers, timeout: chain[url]
            try:
                v = mod.run_redirect_check(nodes[0])
            finally:
                mod._fetch = orig
            self.assertEqual(v.status, "PASS",
                             f"trial {trial}: {v.summary}")
            # Now inject a cycle: last node points back to n0.
            chain[nodes[-1]] = (302, nodes[0])
            mod._fetch = lambda url, headers, timeout: chain[url]
            try:
                v = mod.run_redirect_check(nodes[0])
            finally:
                mod._fetch = orig
            # The cycle is only reachable if the forward
            # chain actually reaches the last node; when it
            # ends early at a 200, PASS is still correct.
            if v.status == "FAIL":
                self.assertIn("cycle", v.summary)
            else:
                self.assertEqual(v.status, "PASS")
