"""Aggregate mechanic frequency/score across runs into the time series the
dashboard charts, and detect what's risen for consecutive periods.
"""
import db


def runs_timeline(conn, limit=26):
    """Completed runs, oldest first, for use as the chart's x-axis."""
    rows = conn.execute(
        "SELECT id, report_date, filename FROM runs WHERE status='done' AND report_date IS NOT NULL "
        "ORDER BY report_date DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return list(reversed([dict(r) for r in rows]))


def mechanic_series(conn, limit_runs=26):
    """Returns {mechanic_name: [{report_date, count, avg_score}, ...]} aligned
    to runs_timeline(), plus the timeline itself. Zero-fills runs where a
    mechanic didn't appear, so line charts don't skip points."""
    runs = runs_timeline(conn, limit_runs)
    if not runs:
        return runs, {}

    run_ids = [r["id"] for r in runs]
    placeholders = ",".join("?" * len(run_ids))
    rows = conn.execute(
        f"""SELECT vm.classified_run AS run_id, m.name AS mechanic, m.id as mechanic_id,
                   COUNT(*) AS cnt, AVG(v.outlier_score) AS avg_score
            FROM video_mechanics vm
            JOIN mechanics m ON m.id = vm.mechanic_id
            JOIN videos v ON v.url = vm.url
            WHERE vm.classified_run IN ({placeholders}) AND vm.skipped=0
            GROUP BY vm.classified_run, m.id""",
        run_ids,
    ).fetchall()

    by_run = {}
    mechanic_names = set()
    for r in rows:
        by_run.setdefault(r["run_id"], {})[r["mechanic"]] = {"count": r["cnt"], "avg_score": r["avg_score"]}
        mechanic_names.add(r["mechanic"])

    series = {}
    for name in sorted(mechanic_names):
        points = []
        for run in runs:
            cell = by_run.get(run["id"], {}).get(name)
            points.append({
                "run_id": run["id"],
                "report_date": run["report_date"],
                "count": cell["count"] if cell else 0,
                "avg_score": round(cell["avg_score"], 2) if cell else None,
            })
        series[name] = points
    return runs, series


def rising_mechanics(conn, min_consecutive=2, limit_runs=26):
    """Mechanics whose count has strictly increased for the last
    `min_consecutive` runs (and is non-zero in the latest run) - the set
    worth flagging in a digest or highlighting on the dashboard."""
    runs, series = mechanic_series(conn, limit_runs)
    if len(runs) < min_consecutive + 1:
        return []

    risers = []
    for name, points in series.items():
        tail = points[-(min_consecutive + 1):]
        counts = [p["count"] for p in tail]
        if counts[-1] == 0:
            continue
        if all(counts[i] < counts[i + 1] for i in range(len(counts) - 1)):
            risers.append({
                "mechanic": name,
                "counts": counts,
                "latest_count": counts[-1],
            })
    risers.sort(key=lambda r: r["latest_count"], reverse=True)
    return risers
