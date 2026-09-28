"""Resend email digest after each run - same pattern as trent-agent."""
import os

import db
import trends


def send_digest(run_id):
    api_key = os.environ.get("RESEND_API_KEY")
    to_addrs = [a.strip() for a in os.environ.get("DIGEST_TO", "").split(",") if a.strip()]
    from_addr = os.environ.get("FROM_EMAIL", "onboarding@resend.dev")
    if not api_key or not to_addrs:
        return  # Not configured - silently skip, same as a dry run.

    import resend
    resend.api_key = api_key

    with db.connect() as conn:
        run = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not run:
            return
        risers = trends.rising_mechanics(conn)
        top_outliers = conn.execute(
            """SELECT v.title, v.creator, v.url, ro.score, m.name as mechanic
               FROM run_outliers ro
               JOIN videos v ON v.url = ro.url
               LEFT JOIN video_mechanics vm ON vm.url = ro.url
               LEFT JOIN mechanics m ON m.id = vm.mechanic_id
               WHERE ro.run_id=? ORDER BY ro.rank LIMIT 10""",
            (run_id,),
        ).fetchall()

    if not risers:
        return  # Quiet run, no notification - matches the "no news = no email" preference from format-news-watch.

    riser_html = "".join(
        f"<li><b>{r['mechanic']}</b> — {' → '.join(str(c) for c in r['counts'])} outliers over the last "
        f"{len(r['counts'])} runs</li>"
        for r in risers
    )
    outlier_html = "".join(
        f"<li>{(o['mechanic'] or 'unclassified')} — <a href='{o['url']}'>{(o['title'] or '')[:80]}</a> "
        f"({o['creator']})</li>"
        for o in top_outliers
    )
    html = f"""
    <h2>Zeitgeist Radar — run #{run_id}</h2>
    <p>{run['message'] or ''}</p>
    <h3>Rising mechanics</h3>
    <ul>{riser_html}</ul>
    <h3>This run's top outliers</h3>
    <ul>{outlier_html}</ul>
    """

    resend.Emails.send({
        "from": from_addr,
        "to": to_addrs,
        "subject": f"Zeitgeist Radar: {len(risers)} mechanic(s) rising",
        "html": html,
    })
