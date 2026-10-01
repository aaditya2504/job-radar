"""Hard filters. Cheap, deterministic, and run before any paid LLM call."""

# Postings with these phrases are not open to F-1 students on CPT/OPT.
DEFAULT_BLOCK_PHRASES = [
    "permanent work authorization",
    "u.s. person",
    "export control",
    "itar",
    "security clearance",
    "must be a u.s. citizen",
    "u.s. citizenship is required",
    "not eligible for cpt",
    "cpt/opt is not",
    "no cpt",
    "no opt",
]


def title_ok(title, include=(), exclude=(), require_any=()):
    t = (title or "").lower()
    if include and not any(k in t for k in include):
        return False
    if require_any and not any(k in t for k in require_any):
        return False
    return not any(k in t for k in exclude)


def location_ok(location, allowed=()):
    if not allowed:
        return True
    loc = (location or "").lower()
    return any(a.lower() in loc for a in allowed)


def blocked_reason(job, phrases=DEFAULT_BLOCK_PHRASES):
    """Return the first disqualifying phrase found, or None."""
    text = f"{job.description} {job.sponsorship}".lower()
    for phrase in phrases:
        if phrase in text:
            return phrase
    return None
