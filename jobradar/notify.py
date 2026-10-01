"""Push alerts through ntfy.sh. Keep your topic name secret: ntfy topics are public."""
from .http import request_raw


def _header_safe(text):
    return (text or "").encode("latin-1", "ignore").decode("latin-1")


def send_ntfy(topic, job, server="https://ntfy.sh", http_raw=request_raw):
    body = f"{job.location}\n" + "\n".join(f"- {r}" for r in job.reasons)
    headers = {
        "Title": _header_safe(f"{job.score} | {job.company}: {job.title}")[:200],
        "Click": job.url,
        "Tags": "briefcase",
        "Priority": "high" if (job.score or 0) >= 85 else "default",
    }
    http_raw("POST", f"{server.rstrip('/')}/{topic}", body.encode("utf-8"), headers)
