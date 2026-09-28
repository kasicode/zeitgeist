"""Flask app: upload form, dashboard chart, mechanics admin, run history.

Deploy: Railway, Flask, GitHub browser-upload (flat repo — see tech-stack
notes), Resend for the digest, Claude API for mechanic classification.
Same infra pattern as trent-agent / supergaande / social-video-idea-generator.
"""
import os
import threading

from flask import Flask, request, redirect, url_for, render_template_string, jsonify
from werkzeug.security import check_password_hash

import db
import pipeline
import trends
import mechanics
import templates

app = Flask(__name__)
db.init_db()

DASH_USER = os.environ.get("DASH_USER")
DASH_PASS = os.environ.get("DASH_PASS")


@app.before_request
def _optional_basic_auth():
    """If DASH_USER/DASH_PASS are set, gate the whole dashboard behind HTTP
    Basic Auth. If unset, the app is open — matches how the other internal
    tools run (Railway URL, no login)."""
    if not DASH_USER or not DASH_PASS:
        return
    if request.path == "/healthz":
        return
    auth = request.authorization
    if not auth or auth.username != DASH_USER or auth.password != DASH_PASS:
        return ("Authentication required.", 401, {"WWW-Authenticate": 'Basic realm="Zeitgeist Radar"'})


def render(body_template, active, **ctx):
    body = render_template_string(body_template, **ctx)
    return render_template_string(templates.BASE, body=body, active=active)


PALETTE = ["#6ea8fe", "#4fd1a5", "#f2b350", "#f26d6d", "#c792ea", "#7fd1e8",
           "#ffb454", "#82e0aa", "#f48fb1", "#90caf9"]


@app.route("/")
def dashboard():
    with db.connect() as conn:
        latest_run = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        latest_run = dict(latest_run) if latest_run else None

        runs, series = trends.mechanic_series(conn)
        chart_labels = [r["report_date"][:10] for r in runs]
        chart_datasets = []
        for i, (name, points) in enumerate(series.items()):
            color = PALETTE[i % len(PALETTE)]
            chart_datasets.append({
                "label": name,
                "data": [p["count"] for p in points],
                "borderColor": color,
                "backgroundColor": color,
                "tension": 0.25,
            })

        risers = trends.rising_mechanics(conn)

        last_done = conn.execute(
            "SELECT id FROM runs WHERE status='done' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        outliers = []
        if last_done:
            outliers = [dict(r) for r in conn.execute(
                """SELECT v.title, v.creator, v.genre, v.views, v.url, ro.score,
                          m.name as mechanic
                   FROM run_outliers ro
                   JOIN videos v ON v.url = ro.url
                   LEFT JOIN video_mechanics vm ON vm.url = ro.url
                   LEFT JOIN mechanics m ON m.id = vm.mechanic_id
                   WHERE ro.run_id=? ORDER BY ro.rank LIMIT 40""",
                (last_done["id"],),
            ).fetchall()]

    import scoring
    return render(
        templates.DASHBOARD_BODY, "dashboard",
        latest_run=latest_run, chart_labels=chart_labels, chart_datasets=chart_datasets,
        risers=risers, outliers=outliers, window_days=scoring.TRAILING_WINDOW_DAYS,
    )


@app.route("/upload", methods=["POST"])
def upload():
    f = request.files.get("file")
    if not f or not f.filename:
        return redirect(url_for("dashboard"))
    file_bytes = f.read()
    filename = f.filename

    def _run():
        try:
            pipeline.run_pipeline(file_bytes, filename)
        except Exception:
            pass  # already recorded on the run row

    threading.Thread(target=_run, daemon=True).start()
    return redirect(url_for("runs"))


@app.route("/runs")
def runs():
    with db.connect() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 50").fetchall()]
    return render(templates.RUNS_BODY, "runs", runs=rows)


@app.route("/run/<int:run_id>")
def run_detail(run_id):
    with db.connect() as conn:
        run = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not run:
            return "Run not found.", 404
        outliers = [dict(r) for r in conn.execute(
            """SELECT ro.rank, ro.score, v.title, v.creator, v.views, v.url,
                      m.name as mechanic, vm.skipped, vm.skip_reason
               FROM run_outliers ro
               JOIN videos v ON v.url = ro.url
               LEFT JOIN video_mechanics vm ON vm.url = ro.url
               LEFT JOIN mechanics m ON m.id = vm.mechanic_id
               WHERE ro.run_id=? ORDER BY ro.rank""",
            (run_id,),
        ).fetchall()]
    return render(templates.RUN_DETAIL_BODY, "runs", run=dict(run), outliers=outliers)


@app.route("/mechanics")
def mechanics_list():
    with db.connect() as conn:
        rows = [dict(r) for r in conn.execute(
            """SELECT m.*, (SELECT COUNT(*) FROM video_mechanics vm WHERE vm.mechanic_id=m.id) as video_count
               FROM mechanics m WHERE m.active=1 ORDER BY video_count DESC"""
        ).fetchall()]
    return render(templates.MECHANICS_BODY, "mechanics", mechanics=rows)


@app.route("/mechanics/<int:source_id>/merge", methods=["POST"])
def mechanics_merge(source_id):
    target_id = request.form.get("target_id")
    if target_id:
        with db.connect() as conn:
            mechanics.merge_mechanics(conn, source_id, int(target_id))
    return redirect(url_for("mechanics_list"))


@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=debug)
