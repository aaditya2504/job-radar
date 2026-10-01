"""fetch -> filter -> dedup -> hard-block -> score -> alert -> report"""
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .filters import DEFAULT_BLOCK_PHRASES, blocked_reason, location_ok, title_ok
from .http import HttpError, request_json, request_raw
from .local_scorer import extract_skills, score_local
from .notify import send_ntfy
from .scorer import score_job
from .sources.simplify import fetch_simplify
from .sources.workday import fetch_workday


@dataclass
class Report:
    fetched: int = 0
    relevant: int = 0
    new: int = 0
    passed: list = field(default_factory=list)
    blocked: list = field(default_factory=list)
    alerts: list = field(default_factory=list)
    notified: int = 0
    suppressed: bool = False
    engine: str = "local"


def _lower(items):
    return [i.lower() for i in items]


def collect(config, http, now, log):
    f = config.get("filters", {})
    include, exclude = _lower(f.get("title_include", [])), _lower(f.get("title_exclude", []))
    jobs = []
    for src in config.get("simplify", []):
        try:
            got = fetch_simplify(src["url"], src.get("max_age_days"), http=http, now=now)
            log(f"simplify: {len(got)} active listings")
            jobs += got
        except (HttpError, ValueError) as e:
            log(f"[warn] simplify source failed: {e}")
    for src in config.get("workday", []):
        require = _lower(src.get("title_require", ["intern", "co-op", "coop"]))
        keep = lambda t, r=require: title_ok(t, include, exclude, r)  # noqa: E731
        try:
            got = fetch_workday(src["company"], src["url"], src.get("search", ""), keep=keep, http=http)
            log(f"workday/{src['company']}: {len(got)} matching postings")
            jobs += got
        except (HttpError, ValueError) as e:
            log(f"[warn] workday/{src.get('company')} failed: {e}")
    return jobs


def run(config, env, state, resume, http=request_json, http_raw=request_raw,
        notify=True, now=None, log=print):
    now = now or datetime.now(timezone.utc)
    f, s, n = config.get("filters", {}), config.get("scoring", {}), config.get("notify", {})
    include, exclude = _lower(f.get("title_include", [])), _lower(f.get("title_exclude", []))
    phrases = _lower(f.get("block_phrases", DEFAULT_BLOCK_PHRASES))
    report = Report()

    jobs = collect(config, http, now, log)
    report.fetched = len(jobs)

    relevant = [j for j in jobs
                if title_ok(j.title, include, exclude) and location_ok(j.location, f.get("locations", []))]
    report.relevant = len(relevant)

    unique = {}
    for job in relevant:                       # first source wins on cross-source duplicates
        unique.setdefault(job.key, job)
    new = [j for k, j in unique.items() if not state.seen(k)]
    report.new = len(new)

    for job in new:
        job.blocked_by = blocked_reason(job, phrases)
        (report.blocked if job.blocked_by else report.passed).append(job)

    # Scoring engine. "local" is free and offline. "claude" uses the API.
    # "auto" (default) uses Claude when a key is set and falls back to local scoring
    # when the key is missing, the budget runs out, or the account has no credits.
    mode = s.get("mode", "auto")
    api_key = env.get("ANTHROPIC_API_KEY")
    engine = "claude" if mode == "claude" or (mode == "auto" and api_key) else "local"
    if engine == "claude" and not api_key:
        log("[warn] scoring mode is 'claude' but ANTHROPIC_API_KEY is not set: jobs left unscored")
    resume_skills = extract_skills(resume)
    budget = s.get("max_per_run", 40)
    report.engine = engine

    for job in report.passed:
        cached = state.cached_score(job.key)
        if cached:
            job.score, job.reasons = cached["score"], cached["reasons"]
            continue
        if engine == "claude" and (not api_key or budget <= 0):
            if mode != "auto":
                continue                      # strict mode: leave unscored, retry next run
            engine = report.engine = "local"
            log("[info] Claude budget used up: scoring the rest locally")
        if engine == "local":
            job.score, job.reasons = score_local(job, resume_skills)
            state.cache_score(job.key, job.score, job.reasons, "local")
            continue
        try:
            job.score, job.reasons = score_job(job, resume, api_key,
                                               s.get("model", "claude-haiku-4-5-20251001"), http=http)
            state.cache_score(job.key, job.score, job.reasons, "claude")
            budget -= 1
        except HttpError as e:
            log(f"[warn] scoring failed for {job.company} / {job.title}: {e}")
            if e.status in (400, 401, 403):   # key or account problem: every call would fail
                if mode == "auto":
                    engine = report.engine = "local"
                    log("[info] Claude unavailable (check key and Console billing): using local scoring")
                    job.score, job.reasons = score_local(job, resume_skills)
                    state.cache_score(job.key, job.score, job.reasons, "local")
                else:
                    log("[error] scoring stopped for this run. Check your API key and Console billing.")
                    break
        except (ValueError, KeyError) as e:
            log(f"[warn] could not parse score for {job.company} / {job.title}: {e}")

    threshold = s.get("threshold", 70)
    report.alerts = sorted((j for j in report.passed if j.score is not None and j.score >= threshold),
                           key=lambda j: -j.score)
    report.suppressed = state.first_run and n.get("suppress_first_run", True)
    topic = env.get("NTFY_TOPIC")
    if notify and n.get("enabled", True) and topic and not report.suppressed:
        for job in report.alerts:
            try:
                send_ntfy(topic, job, n.get("server", "https://ntfy.sh"), http_raw=http_raw)
                report.notified += 1
            except HttpError as e:
                log(f"[warn] notification failed: {e}")

    # Only mark a job seen once it is fully processed. Jobs skipped by the scoring budget
    # or a transient API error stay unseen, so the next run picks them up.
    for job in report.blocked:
        state.mark_seen(job.key)
    for job in report.passed:
        if job.score is not None:
            state.mark_seen(job.key)
    return report


def write_report(report, folder="reports", now=None):
    now = now or datetime.now()
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{now:%Y-%m-%d_%H%M}.md")
    ranked = sorted(report.passed, key=lambda j: -(j.score if j.score is not None else -1))
    lines = [
        f"# job-radar run, {now:%Y-%m-%d %H:%M}",
        "",
        f"Fetched **{report.fetched}** · relevant **{report.relevant}** · new **{report.new}** · "
        f"passed **{len(report.passed)}** · blocked **{len(report.blocked)}** · alerts **{len(report.alerts)}** · "
        f"scoring: **{report.engine}**",
        "",
        "## Ranked postings",
        "",
        "| Score | Company | Title | Location | Why |",
        "|---|---|---|---|---|",
    ]
    for j in ranked:
        score = "-" if j.score is None else j.score
        lines.append(f"| {score} | {j.company} | [{j.title}]({j.url}) | {j.location} | {'; '.join(j.reasons)} |")
    if report.blocked:
        lines += ["", "## Blocked by hard filters", "", "| Company | Title | Reason |", "|---|---|---|"]
        lines += [f"| {j.company} | [{j.title}]({j.url}) | {j.blocked_by} |" for j in report.blocked]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return path
