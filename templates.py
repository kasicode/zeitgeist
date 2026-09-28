"""HTML templates as plain strings, rendered with Flask's render_template_string.
Kept as one file (no templates/ subfolder) so the whole repo stays flat for
GitHub's browser-upload deploy flow.
"""

BASE = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Zeitgeist Radar</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  :root { --bg:#0f1115; --panel:#171a21; --line:#2a2e38; --text:#e8e9ed; --muted:#9aa0ad;
          --accent:#6ea8fe; --good:#4fd1a5; --warn:#f2b350; --bad:#f26d6d; }
  * { box-sizing: border-box; }
  body { background:var(--bg); color:var(--text); font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
         margin:0; padding:0 24px 60px; }
  header { display:flex; align-items:center; justify-content:space-between; padding:24px 0; border-bottom:1px solid var(--line); margin-bottom:24px; }
  h1 { font-size:20px; margin:0; }
  nav a { color:var(--muted); text-decoration:none; margin-left:20px; font-size:14px; }
  nav a:hover, nav a.active { color:var(--text); }
  .panel { background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:20px; margin-bottom:24px; }
  .grid { display:grid; grid-template-columns: 2fr 1fr; gap:24px; }
  @media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th, td { text-align:left; padding:8px 10px; border-bottom:1px solid var(--line); vertical-align:top; }
  th { color:var(--muted); font-weight:500; text-transform:uppercase; font-size:11px; letter-spacing:.04em; }
  a.title-link { color:var(--text); text-decoration:none; }
  a.title-link:hover { color:var(--accent); }
  .muted { color:var(--muted); }
  .tag { display:inline-block; background:#232733; border:1px solid var(--line); border-radius:999px; padding:2px 10px; font-size:12px; }
  .status-done { color:var(--good); }
  .status-processing { color:var(--warn); }
  .status-error { color:var(--bad); }
  .btn { display:inline-block; background:var(--accent); color:#0f1115; border:none; border-radius:6px; padding:9px 16px;
         font-size:14px; font-weight:600; cursor:pointer; text-decoration:none; }
  .btn:hover { opacity:.9; }
  input[type=file] { color:var(--text); }
  .riser { display:flex; justify-content:space-between; padding:8px 0; border-bottom:1px solid var(--line); font-size:14px; }
  .riser:last-child { border-bottom:none; }
  .empty { color:var(--muted); font-size:14px; padding:20px 0; }
  form.upload { display:flex; align-items:center; gap:12px; flex-wrap:wrap; }
</style>
</head>
<body>
<header>
  <h1>Zeitgeist Radar</h1>
  <nav>
    <a href="/" class="{{ 'active' if active=='dashboard' else '' }}">Dashboard</a>
    <a href="/mechanics" class="{{ 'active' if active=='mechanics' else '' }}">Mechanics</a>
    <a href="/runs" class="{{ 'active' if active=='runs' else '' }}">Runs</a>
  </nav>
</header>
{{ body|safe }}
</body>
</html>
"""

DASHBOARD_BODY = """
<div class="panel">
  <form class="upload" action="/upload" method="post" enctype="multipart/form-data">
    <input type="file" name="file" accept=".csv" required>
    <button class="btn" type="submit">Upload Tubular export</button>
    <span class="muted">Trailing window: {{ window_days }} days</span>
  </form>
</div>

{% if latest_run %}
<div class="panel">
  <b>Latest run #{{ latest_run.id }}</b>
  <span class="status-{{ latest_run.status }}">{{ latest_run.status }}</span>
  <span class="muted"> — {{ latest_run.message or '' }}</span>
</div>
{% endif %}

<div class="grid">
  <div class="panel">
    <h3 style="margin-top:0">Mechanic frequency over time</h3>
    {% if chart_labels %}
    <canvas id="trendChart" height="110"></canvas>
    {% else %}
    <div class="empty">No completed runs yet. Upload a Tubular export to get started.</div>
    {% endif %}
  </div>

  <div class="panel">
    <h3 style="margin-top:0">Rising (2+ consecutive runs)</h3>
    {% if risers %}
      {% for r in risers %}
      <div class="riser">
        <span>{{ r.mechanic }}</span>
        <span class="muted">{{ r.counts|join(' → ') }}</span>
      </div>
      {% endfor %}
    {% else %}
      <div class="empty">Nothing rising yet — need at least 3 runs of history.</div>
    {% endif %}
  </div>
</div>

<div class="panel">
  <h3 style="margin-top:0">Latest run — top outliers</h3>
  {% if outliers %}
  <table>
    <tr><th>Mechanic</th><th>Title</th><th>Creator</th><th>Genre</th><th>Views</th><th>Score</th></tr>
    {% for o in outliers %}
    <tr>
      <td>{% if o.mechanic %}<span class="tag">{{ o.mechanic }}</span>{% else %}<span class="muted">unclassified</span>{% endif %}</td>
      <td><a class="title-link" href="{{ o.url }}" target="_blank">{{ o.title[:90] }}</a></td>
      <td>{{ o.creator }}</td>
      <td class="muted">{{ o.genre }}</td>
      <td>{{ '{:,}'.format(o.views or 0) }}</td>
      <td>{{ '%.2f'|format(o.score or 0) }}</td>
    </tr>
    {% endfor %}
  </table>
  {% else %}
  <div class="empty">No outliers yet.</div>
  {% endif %}
</div>

<script>
const labels = {{ chart_labels|tojson }};
const datasets = {{ chart_datasets|tojson }};
if (labels.length) {
  new Chart(document.getElementById('trendChart'), {
    type: 'line',
    data: { labels: labels, datasets: datasets },
    options: {
      responsive: true,
      interaction: { mode: 'index', intersect: false },
      plugins: { legend: { labels: { color: '#e8e9ed' } } },
      scales: {
        x: { ticks: { color: '#9aa0ad' }, grid: { color: '#2a2e38' } },
        y: { ticks: { color: '#9aa0ad' }, grid: { color: '#2a2e38' }, beginAtZero: true }
      }
    }
  });
}
</script>
"""

MECHANICS_BODY = """
<div class="panel">
  <h3 style="margin-top:0">Canonical mechanics ({{ mechanics|length }})</h3>
  <table>
    <tr><th>Name</th><th>Description</th><th>Videos</th><th>Merge into</th></tr>
    {% for m in mechanics %}
    <tr>
      <td><b>{{ m.name }}</b></td>
      <td class="muted">{{ m.description }}</td>
      <td>{{ m.video_count }}</td>
      <td>
        <form action="/mechanics/{{ m.id }}/merge" method="post" style="display:flex; gap:6px;">
          <select name="target_id">
            <option value="">—</option>
            {% for other in mechanics if other.id != m.id %}
            <option value="{{ other.id }}">{{ other.name }}</option>
            {% endfor %}
          </select>
          <button class="btn" type="submit" style="padding:4px 10px; font-size:12px;">Merge</button>
        </form>
      </td>
    </tr>
    {% endfor %}
  </table>
  {% if not mechanics %}<div class="empty">No mechanics classified yet.</div>{% endif %}
</div>
"""

RUNS_BODY = """
<div class="panel">
  <table>
    <tr><th>ID</th><th>Report date</th><th>File</th><th>Status</th><th>Summary</th></tr>
    {% for r in runs %}
    <tr>
      <td><a class="title-link" href="/run/{{ r.id }}">#{{ r.id }}</a></td>
      <td>{{ r.report_date or '' }}</td>
      <td class="muted">{{ r.filename }}</td>
      <td class="status-{{ r.status }}">{{ r.status }}</td>
      <td class="muted">{{ r.message or '' }}</td>
    </tr>
    {% endfor %}
  </table>
  {% if not runs %}<div class="empty">No runs yet.</div>{% endif %}
</div>
"""

RUN_DETAIL_BODY = """
<div class="panel">
  <b>Run #{{ run.id }}</b> — <span class="status-{{ run.status }}">{{ run.status }}</span><br>
  <span class="muted">{{ run.message or '' }}</span>
</div>
<div class="panel">
  <table>
    <tr><th>Rank</th><th>Mechanic</th><th>Title</th><th>Creator</th><th>Views</th><th>Score</th></tr>
    {% for o in outliers %}
    <tr>
      <td>{{ o.rank }}</td>
      <td>{% if o.mechanic %}<span class="tag">{{ o.mechanic }}</span>{% elif o.skipped %}<span class="muted">skipped: {{ o.skip_reason }}</span>{% else %}<span class="muted">—</span>{% endif %}</td>
      <td><a class="title-link" href="{{ o.url }}" target="_blank">{{ (o.title or '')[:90] }}</a></td>
      <td>{{ o.creator }}</td>
      <td>{{ '{:,}'.format(o.views or 0) }}</td>
      <td>{{ '%.2f'|format(o.score or 0) }}</td>
    </tr>
    {% endfor %}
  </table>
</div>
"""
