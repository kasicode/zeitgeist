"""Match outlier videos to a canonical, slowly-growing list of content
"mechanics" (the underlying social/emotional dynamic, not the topic).

This is the piece the whole dashboard depends on: if the model invents a
slightly different name for the same mechanic every run ("public
vulnerability" vs "confessional openness"), the trend line fragments into
noise instead of showing a real line climbing over time. So classification
always shows the model the current canonical list first and asks it to
match against that before proposing anything new — same pattern as
trent-agent's format-database matching.

Requires ANTHROPIC_API_KEY. Videos that don't fit any repeatable pattern
(one-off viral moments, not a copyable mechanic) are explicitly allowed to
be skipped rather than forced into a bucket.
"""
import json
import os
import re

import db

MODEL = os.environ.get("CLASSIFIER_MODEL", "claude-sonnet-5")
BATCH_SIZE = int(os.environ.get("CLASSIFY_BATCH_SIZE", "20"))

SYSTEM_PROMPT = """You analyze short-form social video titles/captions (mostly Dutch, some English) \
for a TV format development team. Your job: identify the underlying SOCIAL, EMOTIONAL, or \
COMPETITIVE MECHANIC each video demonstrates - not its topic.

A mechanic is a repeatable dynamic other creators could copy in a different context - e.g. \
"turning a private struggle into public disclosure", "an ordinary/notable person explaining what \
their job or life is really like", "a stranger being confronted on camera and the reaction being \
filmed". A topic is NOT a mechanic - "dogs", "Barcelona travel tips", "a royal sighting" are topics, \
not mechanics, and should be skipped.

Many videos are one-off idiosyncratic viral moments (a specific joke, a specific song going viral, \
a freak event) with no generalizable mechanic. Skip these explicitly rather than forcing a weak match \
- a false mechanic pollutes the tracking more than a skip does.

You will be given the CURRENT list of known mechanics. Prefer matching an existing mechanic over \
proposing a new one - only propose a new mechanic when the video clearly demonstrates a distinct, \
repeatable dynamic that nothing on the list captures. Keep new mechanic names short (3-6 words), \
generic enough to apply beyond this one video, and in English regardless of the source language."""


def _build_user_prompt(videos, known_mechanics):
    known_block = "\n".join(f"- id={m['id']}: {m['name']} — {m['description']}" for m in known_mechanics) or "(none yet)"
    items = []
    for v in videos:
        items.append({
            "ref": v["url"],
            "platform": v["platform"],
            "genre": v["genre"],
            "title": (v["title"] or "")[:400],
        })
    videos_block = json.dumps(items, ensure_ascii=False, indent=2)
    return f"""KNOWN MECHANICS:
{known_block}

VIDEOS TO CLASSIFY:
{videos_block}

For EACH video (use its "ref" to identify it), respond with one object in a JSON array:
{{"ref": "<the ref>", "action": "match", "mechanic_id": <id>}}
  - use this when it fits an existing mechanic
{{"ref": "<the ref>", "action": "new", "name": "<short mechanic name>", "description": "<one sentence>"}}
  - use this only when no existing mechanic fits
{{"ref": "<the ref>", "action": "skip", "reason": "<why>"}}
  - use this for topic-only or one-off idiosyncratic videos with no repeatable mechanic

Respond with ONLY the JSON array, no other text."""


def _get_client():
    from anthropic import Anthropic
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set.")
    # Explicit timeout so a network hiccup fails a run (visibly, in minutes)
    # instead of leaving it hung on "processing" indefinitely.
    return Anthropic(api_key=api_key, timeout=120.0)


def _extract_json_array(text):
    text = text.strip()
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON array found in model response: {text[:200]}")
    return json.loads(match.group(0))


def _call_model(client, videos, known_mechanics):
    resp = client.messages.create(
        model=MODEL,
        max_tokens=4000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _build_user_prompt(videos, known_mechanics)}],
    )
    text = "".join(block.text for block in resp.content if getattr(block, "type", None) == "text")
    return _extract_json_array(text)


def _normalize_name(name):
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def _find_similar_mechanic(conn, name):
    """Cheap guard against near-duplicate names the model proposes across
    separate batches within the same run (e.g. singular/plural, minor
    rewording) before they both land in the table."""
    norm = _normalize_name(name)
    rows = conn.execute("SELECT * FROM mechanics WHERE active=1").fetchall()
    for r in rows:
        if _normalize_name(r["name"]) == norm:
            return dict(r)
    norm_tokens = set(norm.split())
    for r in rows:
        existing_tokens = set(_normalize_name(r["name"]).split())
        if not norm_tokens or not existing_tokens:
            continue
        overlap = len(norm_tokens & existing_tokens) / len(norm_tokens | existing_tokens)
        if overlap >= 0.7:
            return dict(r)
    return None


def classify_batch(conn, videos, run_id, call_model=_call_model):
    """Classify a list of outlier video dicts. Mutates the DB (video_mechanics,
    mechanics). `call_model` is injectable for testing without hitting the API.
    Returns count classified.
    """
    from datetime import datetime
    client = None
    try:
        client = _get_client()
    except RuntimeError:
        if call_model is _call_model:
            raise

    known = [dict(r) for r in conn.execute("SELECT id, name, description FROM mechanics WHERE active=1").fetchall()]
    n_classified = 0

    for i in range(0, len(videos), BATCH_SIZE):
        batch = videos[i:i + BATCH_SIZE]
        # Skip videos already classified (idempotent re-runs).
        already = {row["url"] for row in conn.execute(
            "SELECT url FROM video_mechanics WHERE url IN (%s)" % ",".join("?" * len(batch)),
            [v["url"] for v in batch],
        ).fetchall()}
        pending = [v for v in batch if v["url"] not in already]
        if not pending:
            continue

        results = call_model(client, pending, known)

        for item in results:
            url = item.get("ref")
            action = item.get("action")
            if not url:
                continue
            if action == "match":
                mid = item.get("mechanic_id")
                row = conn.execute("SELECT id FROM mechanics WHERE id=? AND active=1", (mid,)).fetchone()
                if not row:
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO video_mechanics (url, mechanic_id, skipped, classified_run) VALUES (?,?,0,?)",
                    (url, mid, run_id),
                )
                n_classified += 1
            elif action == "new":
                name = (item.get("name") or "").strip()
                desc = (item.get("description") or "").strip()
                if not name:
                    continue
                dup = _find_similar_mechanic(conn, name)
                if dup:
                    mid = dup["id"]
                else:
                    cur = conn.execute(
                        "INSERT INTO mechanics (name, description, created_at, created_run, active) VALUES (?,?,?,?,1)",
                        (name, desc, datetime.utcnow().isoformat(), run_id),
                    )
                    mid = cur.lastrowid
                    known.append({"id": mid, "name": name, "description": desc})
                conn.execute(
                    "INSERT OR REPLACE INTO video_mechanics (url, mechanic_id, skipped, classified_run) VALUES (?,?,0,?)",
                    (url, mid, run_id),
                )
                n_classified += 1
            elif action == "skip":
                conn.execute(
                    "INSERT OR REPLACE INTO video_mechanics (url, mechanic_id, skipped, skip_reason, classified_run) VALUES (?,NULL,1,?,?)",
                    (url, item.get("reason", ""), run_id),
                )
                n_classified += 1

    return n_classified


def merge_mechanics(conn, source_id, target_id):
    """Fold source mechanic into target: reassign all video labels, mark
    source inactive. Used from the /mechanics admin page when the model's
    matching missed a real duplicate."""
    if source_id == target_id:
        return
    conn.execute(
        "UPDATE video_mechanics SET mechanic_id=? WHERE mechanic_id=?",
        (target_id, source_id),
    )
    conn.execute(
        "UPDATE mechanics SET active=0, merged_into=? WHERE id=?",
        (target_id, source_id),
    )
