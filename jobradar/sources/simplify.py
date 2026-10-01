"""Source: SimplifyJobs-format listings.json (the data behind the GitHub internship trackers)."""
from datetime import datetime, timezone

from ..http import request_json
from ..models import Job


def fetch_simplify(url, max_age_days=None, http=request_json, now=None):
    now = now or datetime.now(timezone.utc)
    items = http("GET", url) or []
    jobs = []
    for item in items:
        if not item.get("active", True) or not item.get("is_visible", True):
            continue
        posted_ts = item.get("date_posted")
        posted = ""
        if posted_ts:
            posted_dt = datetime.fromtimestamp(int(posted_ts), tz=timezone.utc)
            if max_age_days is not None and (now - posted_dt).days > max_age_days:
                continue
            posted = posted_dt.date().isoformat()
        jobs.append(Job(
            source="simplify",
            company=item.get("company_name", ""),
            title=item.get("title", ""),
            location="; ".join(item.get("locations") or []),
            url=item.get("url", ""),
            posted=posted,
            sponsorship=item.get("sponsorship", "") or "",
        ))
    return jobs
