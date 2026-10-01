import os
import tempfile
import unittest

from jobradar.local_scorer import extract_skills, score_local, title_tier
from jobradar.filters import blocked_reason, location_ok, title_ok
from jobradar.models import Job
from jobradar.notify import send_ntfy
from jobradar.scorer import parse_score, score_job
from jobradar.sources.simplify import fetch_simplify
from jobradar.sources.workday import fetch_workday, parse_careers_url, strip_html
from jobradar.state import State
from tests.fakes import NOW, SIMPLIFY_URL, WD_API, WD_URL, FakeHttp, FakeRaw, simplify_item


def job(**kw):
    base = dict(source="t", company="Acme", title="Data Engineering Intern", location="Boston, MA", url="u")
    base.update(kw)
    return Job(**base)


class TestDedupKey(unittest.TestCase):
    def test_same_job_across_sources_matches(self):
        a = job(company="Acme, Inc.", title="Data Engineering Intern - Summer 2027")
        b = job(company="acme", title="data engineering intern (summer 2027)", location="Boston, MA; Remote")
        self.assertEqual(a.key, b.key)

    def test_different_location_differs(self):
        self.assertNotEqual(job().key, job(location="Austin, TX").key)


class TestFilters(unittest.TestCase):
    def test_title_rules(self):
        inc, exc = ["data engineer"], ["senior"]
        self.assertTrue(title_ok("Data Engineer Intern", inc, exc))
        self.assertFalse(title_ok("Senior Data Engineer", inc, exc))
        self.assertFalse(title_ok("Marketing Intern", inc, exc))
        self.assertFalse(title_ok("Data Engineer", inc, exc, require_any=["intern"]))

    def test_location(self):
        self.assertTrue(location_ok("Boston, MA", []))
        self.assertTrue(location_ok("Remote in USA", ["boston", "remote"]))
        self.assertFalse(location_ok("Austin, TX", ["boston"]))

    def test_blocked_phrases(self):
        self.assertEqual(blocked_reason(job(description="Requires active Security Clearance.")),
                         "security clearance")
        self.assertEqual(blocked_reason(job(sponsorship="U.S. Citizenship is Required")),
                         "u.s. citizenship is required")
        self.assertIsNone(blocked_reason(job(description="Build Spark pipelines.")))


class TestSimplify(unittest.TestCase):
    def test_parses_and_filters(self):
        items = [simplify_item("Acme", "Data Eng Intern", locations=("Boston, MA", "Remote")),
                 simplify_item("Old", "Data Eng Intern", days_ago=30),
                 simplify_item("Closed", "Data Eng Intern", active=False)]
        http = FakeHttp({("GET", SIMPLIFY_URL): items})
        jobs = fetch_simplify(SIMPLIFY_URL, max_age_days=14, http=http, now=NOW)
        self.assertEqual([j.company for j in jobs], ["Acme"])
        self.assertEqual(jobs[0].location, "Boston, MA; Remote")
        self.assertEqual(jobs[0].posted, "2026-09-30")


class TestWorkday(unittest.TestCase):
    def test_parse_url(self):
        self.assertEqual(parse_careers_url(WD_URL),
                         ("https://acme.wd5.myworkdayjobs.com", "acme", "AcmeCareers"))
        with self.assertRaises(ValueError):
            parse_careers_url("https://example.com/careers")

    def test_pagination_uses_first_page_total(self):
        def page(body):
            n = 20 if body["offset"] == 0 else 5
            postings = [{"title": f"Data Intern {body['offset'] + i}", "externalPath": "",
                         "locationsText": "Boston", "postedOn": "Posted Today"} for i in range(n)]
            # Real Workday quirk: later pages report total = 0
            return {"total": 25 if body["offset"] == 0 else 0, "jobPostings": postings}
        http = FakeHttp({("POST", f"{WD_API}/jobs"): page})
        jobs = fetch_workday("Acme", WD_URL, http=http)
        self.assertEqual(len(jobs), 25)
        self.assertEqual(len([c for c in http.calls if c[0] == "POST"]), 2)

    def test_details_only_for_kept_titles_and_errors_tolerated(self):
        postings = [{"title": "Data Engineering Intern", "externalPath": "/job/a"},
                    {"title": "Sales Intern", "externalPath": "/job/b"},
                    {"title": "Data Platform Intern", "externalPath": "/job/c"}]
        http = FakeHttp({
            ("POST", f"{WD_API}/jobs"): {"total": 3, "jobPostings": postings},
            ("GET", f"{WD_API}/job/a"): {"jobPostingInfo": {"jobDescription": "<p>Spark &amp; SQL</p>",
                                                             "externalUrl": "https://x/a"}},
            # no route for /job/c: the detail call raises HttpError, which must be tolerated
        })
        jobs = fetch_workday("Acme", WD_URL, keep=lambda t: "data" in t.lower(), http=http)
        self.assertEqual([j.title for j in jobs], ["Data Engineering Intern", "Data Platform Intern"])
        self.assertEqual(jobs[0].description, "Spark & SQL")
        self.assertEqual(jobs[0].url, "https://x/a")
        self.assertNotIn(("GET", f"{WD_API}/job/b"), [(c[0], c[1]) for c in http.calls])

    def test_strip_html(self):
        self.assertEqual(strip_html("<ul><li>A&nbsp;B</li>\n<li>C</li></ul>"), "A B C")


class TestScorer(unittest.TestCase):
    def test_parse_fenced_and_clamped(self):
        self.assertEqual(parse_score('```json\n{"score": 140, "reasons": ["a","b","c","d"]}\n```'),
                         (100, ["a", "b", "c"]))
        with self.assertRaises(ValueError):
            parse_score("I think it's a good fit")

    def test_request_shape(self):
        http = FakeHttp({}, scores={"Data Engineering Intern": 82})
        score, reasons = score_job(job(), "resume", "key-123", "test-model", http=http)
        self.assertEqual(score, 82)
        _, url, body, headers = http.calls[0]
        self.assertEqual(body["model"], "test-model")
        self.assertEqual(headers["x-api-key"], "key-123")
        self.assertIn("anthropic-version", headers)


class TestLocalScorer(unittest.TestCase):
    def test_skill_extraction_respects_word_boundaries(self):
        self.assertEqual(extract_skills("PySpark, MySQL and Kafka Streams"), {"Spark", "MySQL", "Kafka"})
        self.assertNotIn("SQL", extract_skills("MySQL only"))
        self.assertIn("CDC", extract_skills("change data capture pipelines"))

    def test_title_tiers(self):
        self.assertEqual(title_tier("Data Engineering Intern"), "core")
        self.assertEqual(title_tier("Data Analyst Co-op"), "adjacent")
        self.assertEqual(title_tier("Marketing Intern"), "other")

    def test_description_based_score(self):
        resume = extract_skills("Python SQL Spark Kafka dbt AWS")
        j = job(description="Build pipelines with Python, Spark, Kafka and Scala on AWS.")
        score, reasons = score_local(j, resume)
        self.assertEqual(score, 40 + round(60 * 4 / 5))           # core title + 4 of 5 skills
        self.assertIn("Gaps: Scala", reasons)

    def test_title_only_score(self):
        score, reasons = score_local(job(), extract_skills("Python"))
        self.assertEqual(score, 70)
        self.assertIn("title-based", reasons[-1])


class TestState(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sub", "state.json")
            s = State(path)
            self.assertTrue(s.first_run)
            s.mark_seen("k1")
            s.cache_score("k1", 77, ["r"])
            s.save()
            s2 = State(path)
            self.assertFalse(s2.first_run)
            self.assertTrue(s2.seen("k1"))
            self.assertEqual(s2.cached_score("k1")["score"], 77)
            self.assertEqual([f for f in os.listdir(os.path.dirname(path))], ["state.json"])


class TestNotify(unittest.TestCase):
    def test_headers_are_latin1_safe(self):
        raw = FakeRaw()
        j = job(company="Zürich AG 🚀", score=90, reasons=["Spark match"])
        send_ntfy("topic123", j, http_raw=raw)
        url, body, headers = raw.sent[0]
        self.assertEqual(url, "https://ntfy.sh/topic123")
        headers["Title"].encode("latin-1")            # must not raise
        self.assertEqual(headers["Priority"], "high")
        self.assertIn("Spark match", body)


if __name__ == "__main__":
    unittest.main()
