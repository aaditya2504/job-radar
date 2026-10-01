import os
import tempfile
import unittest

from jobradar.pipeline import run, write_report
from jobradar.state import State
from tests.fakes import NOW, SIMPLIFY_URL, WD_API, WD_URL, FakeHttp, FakeRaw, simplify_item

CONFIG = {
    "filters": {"title_include": ["data"], "title_exclude": ["senior"]},
    "scoring": {"model": "m", "threshold": 70, "max_per_run": 10},
    "notify": {"suppress_first_run": True},
    "simplify": [{"url": SIMPLIFY_URL, "max_age_days": 14}],
    "workday": [{"company": "Acme", "url": WD_URL}],
}
ENV = {"ANTHROPIC_API_KEY": "k", "NTFY_TOPIC": "t"}


def routes():
    return {
        ("GET", SIMPLIFY_URL): [
            simplify_item("Acme", "Data Engineering Intern"),                 # also on Workday
            simplify_item("Globex", "Data Platform Intern"),
            simplify_item("Initech", "Data Analyst Intern", sponsorship="U.S. Citizenship is Required"),
            simplify_item("Hooli", "Senior Data Engineer"),                   # excluded by title
        ],
        ("POST", f"{WD_API}/jobs"): {"total": 1, "jobPostings": [
            {"title": "Data Engineering Intern", "externalPath": "/job/1", "locationsText": "Boston, MA"}]},
        ("GET", f"{WD_API}/job/1"): {"jobPostingInfo": {"jobDescription": "Kafka and Spark"}},
    }


class TestPipeline(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state_path = os.path.join(self.tmp.name, "state.json")

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, scores, env=ENV, config=CONFIG):
        state = State(self.state_path)
        http, raw = FakeHttp(routes(), scores), FakeRaw()
        report = run(config, env, state, "resume", http=http, http_raw=raw, now=NOW, log=lambda *_: None)
        state.save()
        return report, http, raw

    def test_end_to_end(self):
        scores = {"Data Engineering Intern": 88, "Data Platform Intern": 55}
        report, http, raw = self._run(scores)
        self.assertEqual(report.fetched, 5)
        self.assertEqual(report.relevant, 4)                 # senior role filtered out
        self.assertEqual(report.new, 3)                      # Acme deduped across sources
        self.assertEqual([j.company for j in report.blocked], ["Initech"])
        self.assertEqual([j.title for j in report.alerts], ["Data Engineering Intern"])
        self.assertTrue(report.suppressed)                   # first run: no alerts sent
        self.assertEqual(raw.sent, [])

        # Second run: nothing new, nothing re-scored, alerts no longer suppressed
        report2, http2, _ = self._run(scores)
        self.assertEqual(report2.new, 0)
        self.assertFalse(report2.suppressed)
        self.assertFalse(any("anthropic" in c[1] for c in http2.calls))

    def test_alerts_sent_after_first_run(self):
        self._run({})                                        # first run with scoring failing
        routes_extra = routes()
        routes_extra[("GET", SIMPLIFY_URL)].append(simplify_item("Umbrella", "Data Pipeline Intern"))
        state = State(self.state_path)
        http, raw = FakeHttp(routes_extra, {"Data Pipeline Intern": 91, "Data Engineering Intern": 40,
                                            "Data Platform Intern": 30}), FakeRaw()
        report = run(CONFIG, ENV, state, "r", http=http, http_raw=raw, now=NOW, log=lambda *_: None)
        self.assertEqual(report.notified, 1)
        self.assertIn("Umbrella", raw.sent[0][2]["Title"])

    def test_failed_or_over_budget_scores_are_retried(self):
        config = dict(CONFIG, scoring={"mode": "claude", "threshold": 70, "max_per_run": 1})
        r1, _, _ = self._run({"Data Engineering Intern": 80, "Data Platform Intern": 75}, config=config)
        scored = [j for j in r1.passed if j.score is not None]
        self.assertEqual(len(scored), 1)                     # budget of 1
        r2, _, _ = self._run({"Data Engineering Intern": 80, "Data Platform Intern": 75}, config=config)
        self.assertEqual(r2.new, 1)                          # the unscored job comes back
        self.assertEqual(r2.passed[0].score is not None, True)

    def test_account_error_stops_scoring_after_first_call(self):
        state = State(self.state_path)
        http = FakeHttp(routes(), api_status=400)
        config = dict(CONFIG, scoring={"mode": "claude", "threshold": 70})
        report = run(config, ENV, state, "r", http=http, http_raw=FakeRaw(), now=NOW, log=lambda *_: None)
        self.assertEqual(sum("anthropic" in c[1] for c in http.calls), 1)
        self.assertTrue(all(j.score is None for j in report.passed))
        state.save()
        self.assertEqual(State(self.state_path).data["seen"].keys() & {j.key for j in report.passed}, set())

    def test_auto_mode_falls_back_to_local_on_account_error(self):
        state = State(self.state_path)
        http = FakeHttp(routes(), api_status=400)
        report = run(CONFIG, ENV, state, "Python SQL Spark Kafka", http=http, http_raw=FakeRaw(),
                     now=NOW, log=lambda *_: None)
        self.assertEqual(sum("anthropic" in c[1] for c in http.calls), 1)   # one try, then local
        self.assertEqual(report.engine, "local")
        self.assertTrue(all(j.score is not None for j in report.passed))

    def test_runs_free_without_api_key(self):
        report, http, _ = self._run({}, env={})
        self.assertEqual(report.engine, "local")
        self.assertTrue(all(j.score is not None for j in report.passed))
        self.assertFalse(any("anthropic" in c[1] for c in http.calls))

    def test_report_written(self):
        report, _, _ = self._run({"Data Engineering Intern": 88, "Data Platform Intern": 55})
        path = write_report(report, os.path.join(self.tmp.name, "reports"))
        text = open(path).read()
        self.assertIn("| 88 | Acme |", text)
        self.assertIn("u.s. citizenship is required", text)


if __name__ == "__main__":
    unittest.main()
