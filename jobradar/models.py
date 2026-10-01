import hashlib
import re
from dataclasses import dataclass, field

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_COMPANY_SUFFIX = re.compile(r"\b(inc|llc|ltd|corp|corporation|co)\b")


def normalize(text):
    return _NON_ALNUM.sub(" ", (text or "").lower()).strip()


def normalize_company(name):
    return " ".join(_COMPANY_SUFFIX.sub(" ", normalize(name)).split())


@dataclass
class Job:
    source: str
    company: str
    title: str
    location: str
    url: str
    posted: str = ""
    description: str = ""
    sponsorship: str = ""
    score: int | None = None
    reasons: list = field(default_factory=list)
    blocked_by: str | None = None

    @property
    def key(self):
        """Stable cross-source identity: same company + title + primary location = same job."""
        primary_loc = normalize(self.location.split(";")[0])
        raw = f"{normalize_company(self.company)}|{normalize(self.title)}|{primary_loc}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
