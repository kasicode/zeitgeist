"""Parse Tubular Labs exports and score videos for subcultural-velocity outliers.

Design notes (why it works this way):
- Tubular's "Topics" columns are empty in practice, so mechanic detection has
  to run off the title/caption text via Claude, not off any built-in tagging.
- Videos are keyed by Video_URL so the same video re-appearing in a later
  export updates its stats instead of duplicating.
- Scoring is done over a trailing window (default 42 days / 6 weeks), not
  the whole file, so a biweekly pull with overlapping history stays
  statistically meaningful instead of getting noisier every run.
"""
import csv
import io
import math
import os
import statistics
from datetime import datetime, timedelta

import db

TRAILING_WINDOW_DAYS = int(os.environ.get("TRAILING_WINDOW_DAYS", "42"))
MIN_VIEWS_FLOOR = int(os.environ.get("MIN_VIEWS_FLOOR", "50000"))
MAX_OUTLIERS_PER_RUN = int(os.environ.get("MAX_OUTLIERS_PER_RUN", "80"))

# Generic tags that show up on nearly everything and carry no signal.
GENERIC_HASHTAGS = {"fyp", "foryou", "foryoupage", "voorjou", "viral", "trending", "fy", "pov"}


def _to_float(v):
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (ValueError, TypeError):
        return None


def _to_int(v):
    f = _to_float(v)
    return int(f) if f is not None else None


def _parse_report_date(first_line, fallback_rows):
    """Tubular exports start with a 'report run at <timestamp>' line."""
    if first_line:
        for fmt in ("report run at %Y-%m-%d %H:%M:%S", "report run at %Y-%m-%d"):
            try:
                return datetime.strptime(first_line.strip(), fmt)
            except ValueError:
                continue
    # Fallback: latest Published_Date seen in the file.
    dates = [r for r in fallback_rows if r]
    if dates:
        return max(dates)
    return datetime.utcnow()


def _parse_published(raw):
    if not raw:
        return None
    raw = raw.strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def parse_tubular_csv(file_bytes):
    """Returns (report_date, list[dict]) — one dict per video row."""
    text = file_bytes.decode("utf-8-sig", errors="replace")
    lines = text.splitlines()
    if not lines:
        raise ValueError("Empty file.")

    first_line = lines[0]
    body = "\n".join(lines[1:]) if first_line.lower().startswith("report run at") else text
    if not first_line.lower().startswith("report run at"):
        first_line = None

    reader = csv.DictReader(io.StringIO(body))
    if reader.fieldnames is None:
        raise ValueError("Could not find a header row in this file.")

    rows = []
    published_dates = []
    for raw in reader:
        published = _parse_published(raw.get("Published_Date"))
        if published:
            published_dates.append(published)
        url = (raw.get("Video_URL") or "").strip()
        if not url:
            continue
        rows.append({
            "url": url,
            "platform": (raw.get("Platform") or "").strip().lower(),
            "title": (raw.get("Video_Title") or "").strip(),
            "creator": (raw.get("Creator") or "").strip(),
            "followers": _to_int(raw.get("Creator_FollowerCount_or_YouTube_Subscribers")),
            "genre": (raw.get("Creator_Genre") or "").strip() or "Unknown",
            "views": _to_int(raw.get("Views")),
            "engagements": _to_int(raw.get("Total_Engagements")),
            "published_at": published.isoformat() if published else None,
            "duration": _to_int(raw.get("Duration (seconds)")),
            "sound_title": (raw.get("Sound Title") or "").strip(),
        })

    report_date = _parse_report_date(first_line, published_dates)
    return report_date, rows


def upsert_videos(conn, rows, run_id):
    """Insert new videos, update stats on ones we've seen before.

    Batched as a single executemany() with an ON CONFLICT upsert rather than
    a per-row SELECT-then-INSERT/UPDATE loop. The per-row version does two
    round trips per video (40,000+ for a 20k-row export) against the Railway
    volume, which is network-attached storage - that's the difference
    between this finishing in seconds versus effectively hanging for hours.
    """
    before = conn.execute("SELECT COUNT(*) c FROM videos").fetchone()["c"]
    conn.executemany(
        """INSERT INTO videos
           (url, platform, title, creator, followers, genre, views, engagements,
            published_at, duration, sound_title, first_seen_run, last_seen_run)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(url) DO UPDATE SET
             views=excluded.views,
             engagements=excluded.engagements,
             last_seen_run=excluded.last_seen_run""",
        [(r["url"], r["platform"], r["title"], r["creator"], r["followers"], r["genre"],
          r["views"], r["engagements"], r["published_at"], r["duration"], r["sound_title"],
          run_id, run_id) for r in rows],
    )
    after = conn.execute("SELECT COUNT(*) c FROM videos").fetchone()["c"]
    return after - before


def _median(values):
    values = [v for v in values if v is not None and v > 0]
    return statistics.median(values) if values else None


def score_trailing_window(conn, report_date, window_days=TRAILING_WINDOW_DAYS):
    """Recompute outlier scores for every video published within the trailing
    window ending at report_date. Returns the list of video rows in-window,
    each dict augmented with its scores, sorted by outlier_score descending.
    """
    window_start = (report_date - timedelta(days=window_days)).isoformat()
    window_end = report_date.isoformat()

    videos = conn.execute(
        """SELECT * FROM videos
           WHERE published_at IS NOT NULL AND published_at BETWEEN ? AND ?""",
        (window_start, window_end),
    ).fetchall()
    videos = [dict(v) for v in videos]

    if not videos:
        return []

    # Baselines computed over the window population only, so scoring reflects
    # "unusual for this 6-week period," not all-time history.
    by_creator = {}
    by_genre = {}
    for v in videos:
        by_creator.setdefault(v["creator"], []).append(v["views"])
        by_genre.setdefault(v["genre"], []).append(v["views"])
    creator_median = {c: _median(vs) for c, vs in by_creator.items()}
    genre_median = {g: _median(vs) for g, vs in by_genre.items()}

    breakout_vals, own_vals, genre_vals = [], [], []
    for v in videos:
        views = v["views"] or 0
        followers = v["followers"] or None
        v["breakout_ratio"] = (views / followers) if followers else None
        cm = creator_median.get(v["creator"])
        v["vs_own_median"] = (views / cm) if cm else None
        gm = genre_median.get(v["genre"])
        v["vs_genre_median"] = (views / gm) if gm else None
        if v["breakout_ratio"]:
            breakout_vals.append(math.log1p(v["breakout_ratio"]))
        if v["vs_own_median"]:
            own_vals.append(math.log1p(v["vs_own_median"]))
        if v["vs_genre_median"]:
            genre_vals.append(math.log1p(v["vs_genre_median"]))

    def zscorer(vals):
        if len(vals) < 2:
            return lambda x: 0.0
        mean = statistics.mean(vals)
        stdev = statistics.pstdev(vals) or 1.0
        return lambda x: (x - mean) / stdev

    z_breakout = zscorer(breakout_vals)
    z_own = zscorer(own_vals)
    z_genre = zscorer(genre_vals)

    for v in videos:
        parts = []
        if v["breakout_ratio"]:
            parts.append(z_breakout(math.log1p(v["breakout_ratio"])))
        if v["vs_own_median"]:
            parts.append(z_own(math.log1p(v["vs_own_median"])))
        if v["vs_genre_median"]:
            parts.append(z_genre(math.log1p(v["vs_genre_median"])))
        v["outlier_score"] = sum(parts) / len(parts) if parts else None

    # Persist scores for anything scored this run (keeps /run/<id> and the
    # dashboard's video table showing current numbers). Batched - see the
    # note in upsert_videos about why a per-row loop is the wrong call here.
    conn.executemany(
        "UPDATE videos SET breakout_ratio=?, vs_own_median=?, vs_genre_median=?, outlier_score=? WHERE url=?",
        [(v["breakout_ratio"], v["vs_own_median"], v["vs_genre_median"], v["outlier_score"], v["url"]) for v in videos],
    )

    scored = [v for v in videos if v["outlier_score"] is not None and (v["views"] or 0) >= MIN_VIEWS_FLOOR]
    scored.sort(key=lambda v: v["outlier_score"], reverse=True)
    return scored


def select_outliers(scored_videos, limit=MAX_OUTLIERS_PER_RUN):
    return scored_videos[:limit]


def extract_hashtags(title):
    import re
    if not title:
        return []
    tags = [h.lower() for h in re.findall(r"#(\w+)", title)]
    return [t for t in tags if t not in GENERIC_HASHTAGS]
