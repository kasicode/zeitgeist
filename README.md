# Zeitgeist Radar

Tracks subcultural mechanics (not topics) across biweekly Tubular Labs
exports of small-creator (10k-50k) videos, and charts which mechanics are
rising over time. Feeds trent-agent-style format development.

## How it works

1. **Upload** a Tubular CSV export (Dashboard → upload form).
2. **Score**: every video published within the trailing window
   (default 42 days / 6 weeks) is scored for how much it outperforms —
   relative to its own creator's typical video, its genre cohort, and its
   follower count. Top ~80 outliers per run move to classification.
3. **Classify**: each outlier's title/caption is sent to Claude, which
   matches it against the *existing* canonical mechanic list or proposes a
   new one — genuine one-off viral moments are explicitly skipped rather
   than forced into a bucket. This keeps the mechanic list from
   fragmenting into near-duplicates run over run.
4. **Track**: the dashboard charts mechanic frequency per run over time,
   and flags anything that's risen for 2+ consecutive runs.
5. **Digest**: if `RESEND_API_KEY` and `DIGEST_TO` are set, a summary
   email goes out after each run — but only when something is actually
   rising (quiet runs send nothing).

## Cadence

Pull the export every 2 weeks. Each run rescoring the trailing 6-week
window (not just "since last upload") means each point on the chart still
has real statistical volume behind it, while you still get a new reading
every 2 weeks instead of waiting a full month.

## Setup on Railway

Environment variables:
- `ANTHROPIC_API_KEY` — required for classification
- `RESEND_API_KEY`, `FROM_EMAIL`, `DIGEST_TO` — optional, for the email digest
- `DASH_USER`, `DASH_PASS` — optional, HTTP Basic Auth for the dashboard
- `TRAILING_WINDOW_DAYS` — default 42
- `MIN_VIEWS_FLOOR` — default 50000 (minimum views to be eligible as an outlier)
- `MAX_OUTLIERS_PER_RUN` — default 80

Add a persistent volume mounted at a path, then set
`RAILWAY_VOLUME_MOUNT_PATH` to that path (Railway sets this automatically
once a volume is attached) so `zeitgeist.db` survives redeploys.

Deploy via GitHub browser upload, same as the other tools — this repo is
kept flat (no subfolders) for that reason.

## Files

- `app.py` — Flask routes and dashboard
- `db.py` — SQLite schema and connection helper
- `scoring.py` — Tubular CSV parsing + trailing-window outlier scoring
- `mechanics.py` — Claude-based mechanic classification and matching
- `pipeline.py` — orchestrates one upload end to end
- `trends.py` — aggregates mechanic frequency into chart series, detects risers
- `notify.py` — Resend digest email
- `templates.py` — HTML templates (kept as strings, no templates/ folder)

## Admin

`/mechanics` lists the canonical mechanic list with a merge tool, for when
classification creates a near-duplicate the automatic dedup guard missed.
`/runs` and `/run/<id>` show history and per-run detail.
