"""Source: Workday career sites, via the public CXS JSON API that the career pages themselves call."""
import html
import re
from urllib.parse import urlparse

from ..http import HttpError, request_json
from ..models import Job

_LOCALE = re.compile(r"^[a-z]{2}-[A-Z]{2}$")
_TAGS = re.compile(r"<[^>]+>")


def parse_careers_url(url):
    """https://acme.wd5.myworkdayjobs.com/en-US/AcmeCareers -> (base, tenant, site)."""
    parsed = urlparse(url)
    if not parsed.netloc.endswith("myworkdayjobs.com"):
        raise ValueError(f"Not a Workday careers URL: {url}")
    segments = [s for s in parsed.path.split("/") if s and not _LOCALE.match(s)]
    if not segments:
        raise ValueError(f"No career site found in URL: {url}")
    return f"https://{parsed.netloc}", parsed.netloc.split(".")[0], segments[0]


def strip_html(text):
    return " ".join(html.unescape(_TAGS.sub(" ", text or "")).split())


def fetch_workday(company, careers_url, search="", keep=lambda title: True,
                  page_size=20, max_jobs=200, http=request_json):
    base, tenant, site = parse_careers_url(careers_url)
    api = f"{base}/wday/cxs/{tenant}/{site}"
    jobs, offset, total = [], 0, None
    while True:
        resp = http("POST", f"{api}/jobs", {
            "appliedFacets": {}, "limit": page_size, "offset": offset, "searchText": search,
        }) or {}
        postings = resp.get("jobPostings") or []
        if total is None:                      # Workday only reports total on the first page
            total = resp.get("total", 0)
        for p in postings:
            title = p.get("title", "")
            if not keep(title):                # skip detail calls for irrelevant titles
                continue
            path = p.get("externalPath", "")
            url, description = f"{base}/{site}{path}", ""
            if path:
                try:
                    info = (http("GET", f"{api}{path}") or {}).get("jobPostingInfo", {})
                    description = strip_html(info.get("jobDescription", ""))
                    url = info.get("externalUrl") or url
                except HttpError:
                    pass                       # keep the posting; filters see no description
            jobs.append(Job(source="workday", company=company, title=title,
                            location=p.get("locationsText", ""), url=url,
                            posted=p.get("postedOn", ""), description=description))
        offset += page_size
        if not postings or offset >= total or len(jobs) >= max_jobs:
            return jobs[:max_jobs]
