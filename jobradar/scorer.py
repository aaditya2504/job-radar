"""Score a posting against a resume with the Claude Messages API."""
import json
import re

from .http import request_json

API_URL = "https://api.anthropic.com/v1/messages"
SYSTEM = (
    "You are a technical recruiter screening internship and co-op postings for one candidate. "
    "Compare the posting to the candidate's resume. Score fit on skills overlap, seniority fit, "
    "and domain fit. Do not reward keyword stuffing. Respond with only a JSON object: "
    '{"score": <integer 0-100>, "reasons": [<up to 3 short strings>]}'
)
_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


def parse_score(text):
    match = _JSON_OBJECT.search(text or "")
    if not match:
        raise ValueError(f"No JSON object in model output: {text!r}")
    obj = json.loads(match.group(0))
    score = max(0, min(100, int(obj["score"])))
    reasons = [str(r) for r in (obj.get("reasons") or [])][:3]
    return score, reasons


def score_job(job, resume, api_key, model, http=request_json):
    description = job.description[:6000] or "Not available. Judge from title and company only."
    prompt = (
        f"RESUME:\n{resume}\n\n"
        f"POSTING:\nCompany: {job.company}\nTitle: {job.title}\n"
        f"Location: {job.location}\nDescription: {description}"
    )
    resp = http("POST", API_URL, {
        "model": model,
        "max_tokens": 300,
        "system": SYSTEM,
        "messages": [{"role": "user", "content": prompt}],
    }, {"x-api-key": api_key, "anthropic-version": "2023-06-01"})
    text = "".join(b.get("text", "") for b in (resp or {}).get("content", []) if b.get("type") == "text")
    return parse_score(text)
