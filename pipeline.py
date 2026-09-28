"""Orchestrates one upload end to end: parse -> store -> score -> classify ->
digest. Kept as a single entry point (`run_pipeline`) so app.py's upload
route and any future scheduled/CLI trigger call the same code path.
"""
import sys
import traceback
from datetime import datetime

import db
import mechanics
import scoring
import notify


def _log(run_id, msg):
    # Printed (not just stored) so a stalled run is visible in Railway's
    # deploy logs in real time, not just inferred after the fact from the
    # runs table sitting on "processing" with no explanation.
    print(f"[run {run_id}] {msg}", flush=True, file=sys.stderr)


def run_pipeline(file_bytes, filename, send_digest=True):
    """Runs synchronously. Intended to be called from a background thread by
    app.py so the upload request returns immediately."""
    with db.connect() as conn:
        cur = conn.execute(
            "INSERT INTO runs (created_at, filename, status) VALUES (?,?,?)",
            (datetime.utcnow().isoformat(), filename, "processing"),
        )
        run_id = cur.lastrowid

    try:
        _log(run_id, f"parsing {filename} ({len(file_bytes)} bytes)")
        report_date, rows = scoring.parse_tubular_csv(file_bytes)
        _log(run_id, f"parsed {len(rows)} rows, report_date={report_date}")

        with db.connect() as conn:
            n_new = scoring.upsert_videos(conn, rows, run_id)
            conn.execute(
                "UPDATE runs SET report_date=?, window_days=?, n_rows=?, n_new_videos=? WHERE id=?",
                (report_date.isoformat(), scoring.TRAILING_WINDOW_DAYS, len(rows), n_new, run_id),
            )
        _log(run_id, f"upserted videos ({n_new} new)")

        with db.connect() as conn:
            scored = scoring.score_trailing_window(conn, report_date)
            outliers = scoring.select_outliers(scored)
            conn.executemany(
                "INSERT OR REPLACE INTO run_outliers (run_id, url, rank, score) VALUES (?,?,?,?)",
                [(run_id, v["url"], rank, v["outlier_score"]) for rank, v in enumerate(outliers, start=1)],
            )
            conn.execute(
                "UPDATE runs SET n_in_window=?, n_outliers=? WHERE id=?",
                (len(scored), len(outliers), run_id),
            )
        _log(run_id, f"scored {len(scored)} videos in window, {len(outliers)} outliers selected")

        with db.connect() as conn:
            n_classified = mechanics.classify_batch(conn, outliers, run_id)
            conn.execute("UPDATE runs SET n_classified=? WHERE id=?", (n_classified, run_id))
        _log(run_id, f"classified {n_classified} outliers")

        with db.connect() as conn:
            conn.execute(
                "UPDATE runs SET status='done', message=? WHERE id=?",
                (f"{len(rows)} rows, {len(scored)} in window, {len(outliers)} outliers, {n_classified} classified.", run_id),
            )
        _log(run_id, "done")

        if send_digest:
            try:
                notify.send_digest(run_id)
            except Exception:
                # A failed email should never mark a successful run as failed.
                with db.connect() as conn:
                    conn.execute(
                        "UPDATE runs SET message = message || ' (digest email failed)' WHERE id=?",
                        (run_id,),
                    )
                _log(run_id, "digest email failed:\n" + traceback.format_exc())

    except Exception as e:
        _log(run_id, f"FAILED: {e}\n{traceback.format_exc()}")
        with db.connect() as conn:
            conn.execute(
                "UPDATE runs SET status='error', message=? WHERE id=?",
                (f"{e}\n{traceback.format_exc()[-1500:]}", run_id),
            )
        raise

    return run_id
