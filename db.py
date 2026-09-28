"""SQLite storage for Zeitgeist Radar.

The database lives on the Railway persistent volume (RAILWAY_VOLUME_MOUNT_PATH)
so it survives redeploys. Locally it falls back to the app folder.
"""
import os
import sqlite3
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  filename TEXT,
  report_date TEXT,
  window_days INTEGER,
  status TEXT NOT NULL DEFAULT 'processing',   -- processing | done | error
  message TEXT,
  n_rows INTEGER DEFAULT 0,
  n_new_videos INTEGER DEFAULT 0,
  n_in_window INTEGER DEFAULT 0,
  n_outliers INTEGER DEFAULT 0,
  n_classified INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS videos (
  url TEXT PRIMARY KEY,
  platform TEXT,
  title TEXT,
  creator TEXT,
  followers INTEGER,
  genre TEXT,
  views INTEGER,
  engagements INTEGER,
  published_at TEXT,
  duration INTEGER,
  sound_title TEXT,
  first_seen_run INTEGER,
  last_seen_run INTEGER,
  breakout_ratio REAL,
  vs_own_median REAL,
  vs_genre_median REAL,
  outlier_score REAL
);
CREATE INDEX IF NOT EXISTS idx_videos_published ON videos(published_at);
CREATE INDEX IF NOT EXISTS idx_videos_creator ON videos(creator);

-- Which videos counted as outliers in which run (kept so history stays
-- correct even after videos are re-scored in later runs).
CREATE TABLE IF NOT EXISTS run_outliers (
  run_id INTEGER NOT NULL,
  url TEXT NOT NULL,
  rank INTEGER,
  score REAL,
  PRIMARY KEY (run_id, url)
);

-- The canonical mechanic list. Lines on the chart = rows here.
CREATE TABLE IF NOT EXISTS mechanics (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE COLLATE NOCASE,
  description TEXT,
  created_at TEXT,
  created_run INTEGER,
  active INTEGER NOT NULL DEFAULT 1,
  merged_into INTEGER
);

-- One label per video, assigned once and never recomputed.
CREATE TABLE IF NOT EXISTS video_mechanics (
  url TEXT PRIMARY KEY,
  mechanic_id INTEGER,
  skipped INTEGER NOT NULL DEFAULT 0,
  skip_reason TEXT,
  classified_run INTEGER
);
"""


def data_dir():
    return os.environ.get("RAILWAY_VOLUME_MOUNT_PATH") or os.path.dirname(os.path.abspath(__file__))


def db_path():
    return os.environ.get("DB_PATH") or os.path.join(data_dir(), "zeitgeist.db")


@contextmanager
def connect():
    conn = sqlite3.connect(db_path(), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    folder = os.path.dirname(db_path())
    if folder:
        os.makedirs(folder, exist_ok=True)
    with connect() as conn:
        conn.executescript(SCHEMA)
        # A redeploy mid-run leaves a run stuck on "processing" forever.
        conn.execute(
            "UPDATE runs SET status='error', message='Interrupted by a restart - upload the file again.' "
            "WHERE status='processing'"
        )
