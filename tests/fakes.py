"""Offline fakes: route HTTP calls to fixtures so tests never touch the network."""
import json
from datetime import datetime, timezone

from jobradar.http import HttpError

NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)
DAY = 86400
SIMPLIFY_URL = "https://example.test/listings.json"
WD_URL = "https://acme.wd5.myworkdayjobs.com/en-US/AcmeCareers"
WD_API = "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/AcmeCareers"


def simplify_item(company, title, days_ago=1, locations=("Boston, MA",), active=True, sponsorship=""):
    return {"company_name": company, "title": title, "locations": list(locations),
            "url": f"https://jobs.test/{company}/{title}".replace(" ", "-"),
            "date_posted": int(NOW.timestamp()) - days_ago * DAY,
            "active": active, "is_visible": True, "sponsorship": sponsorship}


class FakeHttp:
    def __init__(self, routes, scores=None, api_status=None):
        self.routes = routes            # {(method, url): response or callable(body) or Exception}
        self.scores = scores or {}      # {title: score} for the Claude API
        self.api_status = api_status    # force every Claude API call to fail with this status
        self.calls = []

    def __call__(self, method, url, body=None, headers=None, **_):
        self.calls.append((method, url, body, headers))
        if url == "https://api.anthropic.com/v1/messages":
            if self.api_status:
                raise HttpError("forced failure", self.api_status)
            prompt = body["messages"][0]["content"]
            title = prompt.split("Title: ")[1].split("\n")[0]
            if title not in self.scores:
                raise HttpError("model unavailable", 529)
            text = json.dumps({"score": self.scores[title], "reasons": [f"fit for {title}"]})
            return {"content": [{"type": "text", "text": text}]}
        route = self.routes.get((method, url))
        if route is None:
            raise HttpError(f"no route for {method} {url}", 404)
        if isinstance(route, Exception):
            raise route
        return route(body) if callable(route) else route


class FakeRaw:
    def __init__(self):
        self.sent = []

    def __call__(self, method, url, data=b"", headers=None, **_):
        self.sent.append((url, data.decode(), headers))
