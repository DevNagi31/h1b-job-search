"""Smoke tests for the filter/score/store pipeline. Run: python -m tests.test_pipeline"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jobsearch import db  # noqa: E402
from jobsearch.models import Job  # noqa: E402
from jobsearch.profile import Profile  # noqa: E402
from jobsearch.scoring import Rejected, hard_filter, process  # noqa: E402
from jobsearch.sponsors import SponsorIndex, normalize_employer  # noqa: E402

PROFILE = Profile.load(Path(__file__).parents[1] / "config" / "profile.yaml")
FAILURES: list[str] = []


def check(name: str, cond: bool) -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")
    if not cond:
        FAILURES.append(name)


def job(**kw) -> Job:
    base = dict(source="test", company="Acme", title="Software Engineer",
                url="https://x", location="New York, NY", description="We use Python and AWS.")
    base.update(kw)
    return Job(**base)


def rejects(j: Job) -> bool:
    try:
        hard_filter(j, PROFILE)
        return False
    except Rejected:
        return True


print("hard filters")
check("keeps a matching role", not rejects(job()))
check("drops 'no sponsorship'", rejects(job(description="Python role. No sponsorship available.")))
check("drops citizenship requirement", rejects(job(description="Python. Must be a US citizen.")))
check("drops excluded title", rejects(job(title="Software Engineer Intern")))
check("drops excluded location", rejects(job(location="Bangalore, India")))
check("drops missing must-have skill", rejects(job(description="COBOL and Fortran shop.")))
check("drops over-senior roles", rejects(job(description="Python. 12+ years of experience required.")))
check("keeps in-range experience", not rejects(job(description="Python. 3 years of experience.")))

print("\nscoring")
kept, dropped = process([job(), job(title="Software Engineer Intern")], PROFILE, None)
check("one kept, one dropped", len(kept) == 1 and sum(dropped.values()) == 1)
check("score in [0,1]", 0.0 <= kept[0].score <= 1.0)
strong = process([job(description="Python, Kubernetes, AWS, Postgres, terraform. We sponsor H-1B visas.")], PROFILE, None)[0][0]
check("sponsorship language outscores plain", strong.score > kept[0].score)
check("reasons recorded", any("sponsorship language" in r for r in strong.score_reasons))

print("\nfingerprints")
check("same role dedupes across sources",
      job(source="lever").fingerprint == job(source="greenhouse").fingerprint)
check("different title differs", job(title="Data Engineer").fingerprint != job().fingerprint)

print("\nsponsor normalization")
check("strips suffixes", normalize_employer("Stripe, Inc.") == "stripe")
check("strips LLC", normalize_employer("Acme Holdings LLC") == "acme")

print("\ndatabase")
with tempfile.TemporaryDirectory() as tmp:
    conn = db.connect(Path(tmp) / "t.db")
    conn.execute("INSERT INTO sponsors VALUES (?,?,?,?)", ("meta platforms", "Meta Platforms Inc", 1500, "2024"))
    conn.commit()
    idx = SponsorIndex(conn)
    check("exact sponsor match", idx.lookup("Meta Platforms Inc.")[1] == 1500)
    check("prefix sponsor match", idx.lookup("Meta")[1] == 1500)
    check("no false positive", idx.lookup("Metabase")[0] is None)

    check("insert counts as new", db.upsert_jobs(conn, [job()]) == 1)
    check("re-insert is not new", db.upsert_jobs(conn, [job()]) == 0)
    rows = db.load_jobs(conn, min_score=0.0)
    check("job round-trips", len(rows) == 1 and rows[0]["company"] == "Acme")
    check("reasons round-trip as list", isinstance(rows[0]["score_reasons"], list))
    db.set_status(conn, job().fingerprint, "hidden")
    check("hidden jobs excluded", len(db.load_jobs(conn, min_score=0.0)) == 0)

print("\nnotification state")
with tempfile.TemporaryDirectory() as tmp:
    from jobsearch.notify import count_pending, mark_notified, unnotified_jobs
    conn = db.connect(Path(tmp) / "n.db")
    j = job(); j.score = 0.9
    db.upsert_jobs(conn, [j])
    first = unnotified_jobs(conn, 0.0)
    check("new job is unreported", len(first) == 1)
    mark_notified(conn, first)
    check("not repeated after marking", len(unnotified_jobs(conn, 0.0)) == 0)
    check("pending count drops to zero", count_pending(conn, 0.0) == 0)

    # The bug this guards: re-running the scan re-upserts the same job. That
    # must not resurrect it into the digest.
    db.upsert_jobs(conn, [job()])
    check("re-scan does not re-notify", len(unnotified_jobs(conn, 0.0)) == 0)

    j2 = job(title="Data Engineer"); j2.score = 0.9
    db.upsert_jobs(conn, [j2])
    check("genuinely new job is reported", len(unnotified_jobs(conn, 0.0)) == 1)

print("\nreport")
with tempfile.TemporaryDirectory() as tmp:
    from jobsearch import report
    j = job(); j.score = 0.8
    d = j.to_dict(); d["llm_score"] = None
    html = report.write_html([d], Path(tmp) / "r.html", {"fetched": 1, "new": 1})
    md = report.write_markdown([d], Path(tmp) / "r.md")
    check("html written", html.exists() and "Acme" in html.read_text())
    check("markdown written", md.exists() and "Acme" in md.read_text())

print(f"\n{'ALL PASS' if not FAILURES else str(len(FAILURES)) + ' FAILURES: ' + ', '.join(FAILURES)}")
sys.exit(1 if FAILURES else 0)
