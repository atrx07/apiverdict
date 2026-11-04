"""Redirect-chain check: a URL's redirect chain must be finite,
acyclic, and must not downgrade https to http.

One question only: is the CHAIN sane? The final destination's
status is reported in evidence but not judged — a chain that
lands on a 404 has a dead destination, which is a different
check's question; a chain that loops, runs past the hop limit,
downgrades to http, or ends in a 3xx with no Location is a
chain bug, and fails here.

Read-only (GET, no body read — only status + Location
headers), so no remote guard. Follows nothing automatically:
each hop is fetched with a no-follow opener and the Location
resolved against the URL just fetched, so relative Locations
work and cycles are visible instead of hidden by the client.
"""

import urllib.error
import urllib.parse
import urllib.request

from .http_util import DEFAULT_TIMEOUT
from .runner import Verdict

REDIRECT_CODES = {301, 302, 303, 307, 308}


class _NoFollow(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _fetch(url, headers, timeout):
    """One hop: return (status, location-or-None). Transport
    failures raise, so the caller can turn them INCONCLUSIVE."""
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    opener = urllib.request.build_opener(_NoFollow)
    try:
        resp = opener.open(req, timeout=timeout)
        try:
            return resp.status, resp.headers.get("Location")
        finally:
            resp.close()
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.headers.get("Location")
        finally:
            e.close()


def run_redirect_check(url, max_hops=10, headers=None,
                       timeout=DEFAULT_TIMEOUT):
    """Walk the redirect chain from url. PASS: the chain ends
    in a non-redirect response without a cycle, hop-limit
    breach, or https downgrade. FAIL: cycle, too many hops,
    downgrade, or a dangling 3xx with no Location.
    INCONCLUSIVE: transport failure, or a bad max_hops."""
    if max_hops is None or max_hops < 1:
        return Verdict("redirect", "INCONCLUSIVE",
                       "max_hops must be >= 1", {})
    seen = set()
    current = url
    hops = 0
    try:
        while True:
            key = urllib.parse.urldefrag(current)[0]
            if key in seen:
                return Verdict("redirect", "FAIL",
                               f"redirect cycle at {current}",
                               {"url": url, "hops": hops,
                                "cycle_at": current})
            seen.add(key)
            status, location = _fetch(current, headers, timeout)
            if status not in REDIRECT_CODES:
                return Verdict("redirect", "PASS",
                               f"chain ends at HTTP {status} after "
                               f"{hops} hop(s)",
                               {"url": url, "hops": hops,
                                "final_status": status,
                                "final_url": current})
            if not location:
                return Verdict("redirect", "FAIL",
                               f"dangling redirect: HTTP {status} "
                               f"with no Location at {current}",
                               {"url": url, "hops": hops,
                                "final_status": status,
                                "final_url": current})
            nxt = urllib.parse.urljoin(current, location)
            if (urllib.parse.urlparse(current).scheme == "https"
                    and urllib.parse.urlparse(nxt).scheme == "http"):
                return Verdict("redirect", "FAIL",
                               f"https downgrade: {current} -> {nxt}",
                               {"url": url, "hops": hops,
                                "downgrade_to": nxt})
            hops += 1
            if hops > max_hops:
                return Verdict("redirect", "FAIL",
                               f"more than {max_hops} redirect hops",
                               {"url": url, "hops": hops,
                                "final_url": current})
            current = nxt
    except Exception as e:
        return Verdict("redirect", "INCONCLUSIVE",
                       f"transport failure: {e}",
                       {"url": url, "hops": hops})
