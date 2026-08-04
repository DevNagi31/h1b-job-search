# h1b-job-search

A personal job aggregator that pulls postings from across the web and keeps only
the ones that (a) fit your profile and (b) come from employers who actually
sponsor H-1B visas.

The core idea: **the USCIS H-1B filing record is the filter.** Any job board can
tell you a company is hiring. Only the federal disclosure data tells you whether
that company has ever put someone on an H-1B. Every posting gets checked against
that list, and postings that explicitly rule out sponsorship are dropped outright.

```
sources ──► hard filters ──► weighted score ──► [optional Claude rerank] ──► SQLite ──► HTML + Markdown
```

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# 1. Load the sponsor whitelist (see "Sponsor data" below for the download)
.venv/bin/python -m jobsearch.cli load-sponsors data/h1b_datahubexport-2024.csv

# 2. Edit config/profile.yaml — this drives everything
# 3. Run
.venv/bin/python -m jobsearch.cli run --open
```

The report lands at `data/report.html` (filterable, sortable, works in light and
dark) and `data/report.md`.

## Configuration

**`config/profile.yaml`** is the file that matters. It defines your target
titles, skills in three weight tiers, location and salary floors, and — most
importantly — the `sponsorship.blockers` phrase list that hard-drops any posting
saying it won't sponsor. Tune `scoring.min_score` to control how much survives.

**`config/sources.yaml`** picks which sources run. For the ATS sources you add
board tokens taken straight from a company's careers URL:

| URL | token |
|---|---|
| `boards.greenhouse.io/stripe` | `stripe` |
| `jobs.lever.co/netflix` | `netflix` |
| `jobs.ashbyhq.com/ramp` | `ramp` |
| `jobs.smartrecruiters.com/Visa` | `Visa` |

Adding board tokens is the single highest-leverage thing you can do — cross-
reference the USCIS top-sponsors list against companies you'd work for, and add
every one that uses a supported ATS.

## Sponsor data

Download a fiscal-year CSV from the [USCIS H-1B Employer Data
Hub](https://www.uscis.gov/tools/reports-and-studies/h-1b-employer-data-hub) and
load it. DOL LCA disclosure files work too — the loader sniffs column names
rather than assuming a fixed schema, and loading multiple years accumulates
approval counts per employer.

Company names are normalized (`Stripe, Inc.` → `stripe`) before matching, with
prefix matching so `Meta` finds `Meta Platforms` but `Metabase` doesn't.

Loading sponsor data does not retroactively rescore jobs already in the DB — the
next `run` does that.

## Sources

| Source | Auth | Notes |
|---|---|---|
| Greenhouse, Lever, Ashby, SmartRecruiters | none | **Best signal.** Public board APIs, full job descriptions — which is what sponsorship detection needs. |
| RemoteOK, Arbeitnow | none | Keyless aggregators. RemoteOK's free feed is mostly non-technical; low yield. |
| Hacker News "Who is Hiring" | none | Noisy, but startups that sponsor often post here first. |
| Adzuna | free API key | `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`. 250 calls/day. |
| USAJOBS | free API key | `USAJOBS_API_KEY`, `USAJOBS_EMAIL`. Mostly citizens-only; cap-exempt research roles do appear. |
| LinkedIn, Indeed | opt-in | See the warning below. |

Missing API keys cause a source to skip silently rather than fail the run, and
any single source erroring is logged and stepped over.

### About LinkedIn and Indeed

You asked for these and they're implemented, but they're **disabled by default**
and you should know what you're enabling:

- Scraping either violates their Terms of Service. That's a contract matter, not
  a criminal one, but they enforce it with IP blocks and account bans. Don't run
  them from an IP you care about, and never with cookies from an account you need.
- Both are aggressively bot-defended. Expect periodic zero-result runs and
  selector rot. Indeed blocks plain HTTP clients entirely, so that adapter
  requires `INDEED_PROXY_URL` and raises a clear error instead of silently
  returning nothing.
- LinkedIn's guest endpoint returns no job description, so sponsorship blockers
  can't be detected on those rows — they lean entirely on employer filing history.

The ATS sources are the ones that will still work in six months. Enable in
`config/sources.yaml` if you want them anyway.

## Scoring

A posting is dropped outright if it has an excluded title term, contains a
sponsorship blocker phrase, sits in an excluded location, has none of your
must-have skills, demands more years than your cap, or lists a salary below your
floor. Survivors get a 0–1 score from weighted signals: title match, skills by
tier (capped per tier so keyword-stuffed JDs can't win), USCIS filing history,
explicit sponsorship language, location, salary, and freshness.

`--llm` then sends the shortlist to Claude for a 0–100 fit score, a
likely/unclear/unlikely sponsorship read, and one line of reasoning. It runs only
over jobs that already passed the cheap filters, so cost stays bounded. Needs
`ANTHROPIC_API_KEY` and `pip install anthropic`.

`--require-sponsor` is the strictest mode: drops anything with neither filing
history nor sponsorship language.

## Commands

```bash
jobsearch.cli run                      # fetch, filter, score, store, report
jobsearch.cli run --llm --require-sponsor
jobsearch.cli run --only greenhouse ashby   # ignore `enabled`, run just these
jobsearch.cli report --open            # regenerate from stored jobs, no fetching
jobsearch.cli load-sponsors <csv...>   # import USCIS/DOL disclosure data
jobsearch.cli sources                  # list sources and their on/off state
jobsearch.cli digest                   # markdown list of jobs not yet reported
jobsearch.cli digest --mark            # ...and stamp them so they don't repeat
jobsearch.cli status <fingerprint> applied   # new|seen|applied|rejected|hidden
```

Jobs are deduped by a `company + title + location` fingerprint, so the same role
appearing on three boards is one row. The SQLite DB at `data/jobs.db` is the
memory: it tracks what you've already seen and what you've applied to, and
`hidden`/`rejected` jobs stay out of future reports.

## Automation

`.github/workflows/scan.yml` scans **every 3 hours on weekdays** (11:00–23:00
UTC) plus once each weekend morning, and does two things with the results:

- **Alerts you.** New matches are filed as a GitHub issue labeled `job-alert`,
  which GitHub emails you about. This is the "ASAP" path.
- **Publishes a dashboard.** The report is committed to `docs/` and served at
  `https://<user>.github.io/<repo>/`.

Dashboard: **https://devnagi31.github.io/h1b-job-search/**

New-ness is tracked per job in `jobs.notified_at`, not by a time window — a
window re-reports the same job on every run that overlaps it, which produces
duplicate alerts whenever the schedule is tighter than the window. Jobs are only
stamped as reported *after* the issue posts successfully, so a failed alert
means they roll into the next digest rather than vanishing unseen. Each digest
is capped at 25 jobs; the rest queue up and arrive next run.

The dedupe DB lives on an orphan `state` branch rather than in the Actions
cache, because caches evict after 7 days without a hit and would silently reset
your history — making everything look new again.

**Cadence caveat:** GitHub's cron is best-effort and can lag 5–20 minutes under
load. Every 3 hours is about as tight as this gets before you'd want a real
always-on host. Don't go below hourly; you'll burn Actions minutes for very
little, since ATS boards don't refresh that fast.

To trigger a scan by hand: `gh workflow run "Job scan"` — or the Actions tab.

## Tests

```bash
.venv/bin/python tests/test_pipeline.py
```

Covers the hard filters, scoring monotonicity, fingerprint dedupe, employer-name
normalization, DB round-trips, and report generation.
