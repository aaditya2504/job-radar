"""Minimal stdlib HTTP helpers with retries. No third-party dependencies."""
import json
import time
import urllib.error
import urllib.request

RETRYABLE = {429, 500, 502, 503, 504}
USER_AGENT = "job-radar/1.0"


class HttpError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def _send(method, url, data, headers, timeout, retries):
    hdrs = {"User-Agent": USER_AGENT}
    hdrs.update(headers or {})
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code in RETRYABLE and attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            try:
                detail = e.read().decode("utf-8", "replace")[:300]
            except Exception:
                detail = ""
            msg = f"{method} {url} -> HTTP {e.code}" + (f": {detail}" if detail else "")
            raise HttpError(msg, e.code) from e
        except urllib.error.URLError as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise HttpError(f"{method} {url} -> {e.reason}") from e


def request_json(method, url, body=None, headers=None, timeout=30, retries=3):
    """Send a request and parse a JSON response."""
    hdrs = {"Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    hdrs.update(headers or {})
    raw = _send(method, url, data, hdrs, timeout, retries)
    return json.loads(raw) if raw else None


def request_raw(method, url, data=b"", headers=None, timeout=30, retries=3):
    """Send a request with a raw body (used for ntfy)."""
    _send(method, url, data, headers, timeout, retries)
