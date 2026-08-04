"""Command line entry point.

    python -m jobsearch.cli load-sponsors data/h1b_datahubexport-2024.csv
    python -m jobsearch.cli run
    python -m jobsearch.cli run --llm --require-sponsor
    python -m jobsearch.cli report
    python -m jobsearch.cli status <fingerprint> applied
"""

from __future__ import annotations

import argparse
import logging
import sys
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import db, report
from .models import Job
from .profile import Profile
from .scoring import process
from .sources import REGISTRY
from .sponsors import SponsorIndex, load_sponsor_csv

log = logging.getLogger("jobsearch")

DEFAULT_DB = "data/jobs.db"
DEFAULT_SOURCES = "config/sources.yaml"
DEFAULT_PROFILE = "config/profile.yaml"


def _load_sources_config(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"missing {path}")
    with p.open() as fh:
        return yaml.safe_load(fh) or {}


def cmd_run(args: argparse.Namespace) -> int:
    profile = Profile.load(args.profile)
    for problem in profile.validate():
        log.warning("profile: %s", problem)

    cfg = _load_sources_config(args.sources)
    queries = cfg.get("queries") or profile.titles_include
    conn = db.connect(args.db)

    sponsors = SponsorIndex(conn)
    if len(sponsors) == 0:
        log.warning(
            "sponsor table is empty — every job will show 'no H1B history'. "
            "Run: python -m jobsearch.cli load-sponsors <uscis_csv>"
        )

    only = set(args.only or [])
    raw: list[Job] = []
    per_source: dict[str, int] = {}

    for name, scfg in (cfg.get("sources") or {}).items():
        scfg = scfg or {}
        if only and name not in only:
            continue
        if not only and not scfg.get("enabled", False):
            continue
        cls = REGISTRY.get(name)
        if cls is None:
            log.warning("unknown source %r in %s", name, args.sources)
            continue

        log.info("fetching %s…", name)
        try:
            got = list(cls(scfg).fetch(list(queries)))
        except Exception as e:  # noqa: BLE001 - never let one source kill the run
            log.error("source %s failed: %s", name, e)
            continue
        per_source[name] = len(got)
        raw.extend(got)
        log.info("  %s: %d postings", name, len(got))

    if not raw:
        log.error("no postings fetched — check config/sources.yaml and network access")
        return 1

    kept, dropped = process(raw, profile, sponsors, require_sponsor=args.require_sponsor)
    log.info("kept %d/%d after filtering", len(kept), len(raw))
    for reason, n in sorted(dropped.items(), key=lambda kv: -kv[1]):
        log.info("  dropped %4d: %s", n, reason)

    if args.llm and kept:
        from .llm import rerank
        log.info("re-ranking top %d with Claude…", args.llm_limit)
        rerank(kept, profile, limit=args.llm_limit, model=args.model)

    new = db.upsert_jobs(conn, kept)
    db.record_run(
        conn,
        datetime.now(timezone.utc).isoformat(),
        len(raw),
        new,
        ", ".join(f"{k}={v}" for k, v in per_source.items()),
    )
    log.info("%d new, %d updated", new, len(kept) - new)

    _emit(conn, profile, args)
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    profile = Profile.load(args.profile)
    conn = db.connect(args.db)
    _emit(conn, profile, args)
    return 0


def _emit(conn, profile: Profile, args: argparse.Namespace) -> None:
    jobs = db.load_jobs(conn, min_score=profile.min_score, limit=args.limit)
    stats_row = conn.execute("SELECT fetched, new_jobs FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    stats = {"fetched": stats_row["fetched"], "new": stats_row["new_jobs"]} if stats_row else {}

    html_path = report.write_html(jobs, args.out, stats)
    md_path = report.write_markdown(jobs, Path(args.out).with_suffix(".md"))
    log.info("wrote %s (%d jobs) and %s", html_path, len(jobs), md_path)
    if getattr(args, "open", False):
        webbrowser.open(html_path.resolve().as_uri())


def cmd_load_sponsors(args: argparse.Namespace) -> int:
    conn = db.connect(args.db)
    total = 0
    for path in args.paths:
        n = load_sponsor_csv(conn, path, args.fiscal_year)
        log.info("loaded %d rows from %s", n, path)
        total += n
    log.info("sponsor index now holds %d distinct employers", len(SponsorIndex(conn)))
    return 0 if total else 1


def cmd_digest(args: argparse.Namespace) -> int:
    """Digest of jobs not yet reported. Empty output means nothing new."""
    from .notify import count_pending, digest_markdown, mark_notified, unnotified_jobs

    profile = Profile.load(args.profile)
    conn = db.connect(args.db)
    pending = count_pending(conn, profile.min_score)
    jobs = unnotified_jobs(conn, profile.min_score, args.limit)
    text = digest_markdown(jobs, args.dashboard_url, pending)

    if text:
        if args.out_file:
            Path(args.out_file).write_text(text, encoding="utf-8")
        else:
            print(text)
    # Only stamp after the digest is safely written, so a crash mid-write
    # doesn't silently swallow jobs you were never shown.
    if jobs and args.mark:
        mark_notified(conn, jobs)
    log.info("%d unreported job(s) (%d pending total)", len(jobs), pending)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    conn = db.connect(args.db)
    ok = db.set_status(conn, args.fingerprint, args.new_status)
    if not ok:
        log.error("no job with fingerprint %s", args.fingerprint)
        return 1
    log.info("%s -> %s", args.fingerprint, args.new_status)
    return 0


def cmd_sources(args: argparse.Namespace) -> int:
    cfg = _load_sources_config(args.sources)
    configured = cfg.get("sources") or {}
    for name, cls in REGISTRY.items():
        scfg = configured.get(name) or {}
        state = "on" if scfg.get("enabled") else "off"
        flag = " [opt-in: ToS risk]" if cls.requires_optin else ""
        print(f"  {state:>3}  {name}{flag}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="jobsearch", description="H1B-sponsored job aggregator")
    p.add_argument("--db", default=DEFAULT_DB)
    p.add_argument("--profile", default=DEFAULT_PROFILE)
    p.add_argument("--sources", default=DEFAULT_SOURCES)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_report_args(sp):
        sp.add_argument("--out", default="data/report.html")
        sp.add_argument("--limit", type=int, default=500)
        sp.add_argument("--open", action="store_true", help="open the report in a browser")

    run = sub.add_parser("run", help="fetch, filter, score, store, report")
    run.add_argument("--only", nargs="*", help="run just these sources, ignoring `enabled`")
    run.add_argument("--require-sponsor", action="store_true",
                     help="drop jobs with no H1B filing history and no sponsorship language")
    run.add_argument("--llm", action="store_true", help="re-rank the shortlist with Claude")
    run.add_argument("--llm-limit", type=int, default=40)
    run.add_argument("--model", default="claude-sonnet-5")
    add_report_args(run)
    run.set_defaults(func=cmd_run)

    rep = sub.add_parser("report", help="regenerate the report from stored jobs")
    add_report_args(rep)
    rep.set_defaults(func=cmd_report)

    ls = sub.add_parser("load-sponsors", help="import USCIS/DOL disclosure CSVs")
    ls.add_argument("paths", nargs="+")
    ls.add_argument("--fiscal-year", default="")
    ls.set_defaults(func=cmd_load_sponsors)

    dg = sub.add_parser("digest", help="markdown digest of not-yet-reported jobs")
    dg.add_argument("--limit", type=int, default=25)
    dg.add_argument("--mark", action="store_true",
                    help="stamp the listed jobs as reported so they don't repeat")
    dg.add_argument("--dashboard-url", default="")
    dg.add_argument("--out-file", default="", help="write to a file instead of stdout")
    dg.set_defaults(func=cmd_digest)

    st = sub.add_parser("status", help="mark a job applied/rejected/hidden")
    st.add_argument("fingerprint")
    st.add_argument("new_status", choices=["new", "seen", "applied", "rejected", "hidden"])
    st.set_defaults(func=cmd_status)

    sr = sub.add_parser("sources", help="list registered sources and their state")
    sr.set_defaults(func=cmd_sources)

    args = p.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)-7s %(message)s",
    )
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
