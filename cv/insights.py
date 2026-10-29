"""
The assistant: answers a student's question from the live campus snapshot.

It is a small, honest rules engine over real numbers — no cloud call, no API key,
nothing to fail on demo day. Every sentence it prints is derived from the data
the engine just computed. Swapping in an LLM later means replacing one function
(`answer`) with a model call that gets the same snapshot as context.
"""
import re

GREET = [
    "Hi! Ask me where to eat, when it is quietest, or for seats together.",
    "I read the live seat and queue data. Try: 'where should I eat now?'",
]

CHIPS = [
    "Where should I eat now?",
    "I need 4 seats together",
    "When will it be empty?",
    "How long is the queue?",
]

# Words that appear in venue names but say nothing about which venue you mean.
# Without this, "which canteen is quietest?" would match "Nitte Main Canteen".
GENERIC = {
    "the", "a", "an", "of", "at", "in", "on", "to", "for", "is", "are", "and",
    "or", "near", "campus", "block", "canteen", "cafeteria",
    "cafe", "food", "court", "mess", "library", "study", "space",
    "nonveg", "bakery",
}

MATTER_NAMES = {"closest": "closest", "quiet": "quietest", "queue": "no queue"}


def _words(text):
    return set(re.findall(r"[a-z0-9]+", (text or "").lower()))


def _has(question, *triggers):
    """Whole-word matching. Plain substring matching is a trap: 'seats' contains
    'eat', so 'any ghost seats?' would be answered as a food question."""
    q = (question or "").lower()
    words = _words(q)
    for t in triggers:
        if " " in t:
            if t in q:
                return True
        elif t in words:
            return True
    return False


def _venues(snapshot):
    return snapshot.get("venues", [])


def _by_id(snapshot, vid):
    return next((v for v in _venues(snapshot) if v["id"] == vid), None)


def _food(snapshot):
    return [v for v in _venues(snapshot) if v.get("kind") != "Library"]


def _matters(matters):
    return [m for m in (matters or []) if m in MATTER_NAMES]


def _score(v, matters=()):
    """The same idea as the app's own ranking, so the chat agrees with the
    answer on screen instead of naming a different place."""
    score = v["free"] * 0.6
    if "closest" in matters:
        score -= v["walk_min"] * 3.0
    if "quiet" in matters:
        rank = {"low": 0, "medium": 1, "high": 2, "packed": 3}.get(v.get("crowd_level"), 1)
        score -= rank * 6
        score -= (1 - (v["free"] / max(1, v["capacity"]))) * 4
    if "queue" in matters:
        score -= v["wait_minutes"] * 1.6 + v["queue"] * 0.3
    return score


def _best(snapshot, group=None, matters=()):
    pool = _food(snapshot)
    if group:
        pool = [v for v in pool if _free_group(v, group)]
    if not pool:
        return None
    return max(pool, key=lambda v: _score(v, matters))


def _free_group(v, n):
    """Find n free seats together in the snapshot's own seat list.

    The assistant used to look for v['group'], which is only present when the
    engine was asked for a group size. /api/chat never asks, so every "4 seats
    together" question answered "no" even when whole tables were empty.
    """
    seats = [s for s in (v.get("seats") or [])
             if s.get("state") in ("free", "probably_free")]
    if len(seats) < n:
        return None
    by_table = {}
    for s in seats:
        by_table.setdefault(s.get("table"), []).append(s)
    for table, group in by_table.items():
        if len(group) >= n:
            return {"seat_ids": [s["id"] for s in group[:n]],
                    "quality": "all at one table", "table": table}
    return {"seat_ids": [s["id"] for s in seats[:n]],
            "quality": "next to each other", "table": None}


def _best_group(snapshot, n, matters=()):
    fits = []
    for v in _food(snapshot):
        group = _free_group(v, n)
        if group:
            fits.append((v, group))
    if not fits:
        return None, None
    return max(fits, key=lambda pair: _score(pair[0], matters))


def _future_window(v, now_minutes=6):
    """Only mention a quieter time if it is actually later and actually quieter.
    The old code compared 44 free now with 46 free "now", which read as nonsense."""
    bw = v.get("best_window")
    if not bw:
        return None
    if bw.get("in_minutes", 0) < now_minutes:
        return None
    if bw.get("free", 0) <= v["free"] + 1:
        return None
    return bw


def _fmt_venue(v, group=None):
    bits = [f"**{v['name']}** — {v['free']} of {v['capacity']} seats free"]
    bits.append(f"{v['queue']} in the queue (~{v['wait_minutes']:.0f} min wait)"
                if v["queue"] else "no queue right now")
    bits.append(f"{v['walk_min']} min walk")
    line = " · ".join(bits)
    if group:
        line += (f"\nSeats {', '.join(group['seat_ids'])} are free together "
                 f"({group['quality']}).")
    return line


def _rationale(v, matters, snapshot):
    """Plain-language reasons, used by the 'Ask why' button."""
    lines = [f"**{v['name']}** is the best match right now."]
    lines.append(f"{v['free']} of {v['capacity']} seats are free · "
                 f"{v['walk_min']} min walk · "
                 + (f"{v['queue']} in the queue (~{v['wait_minutes']:.0f} min)"
                    if v["queue"] else "no queue"))
    labels = [MATTER_NAMES[m] for m in _matters(matters)]
    if labels:
        lines.append("It ranks first because you asked for " + " + ".join(labels) + ".")
    bw = _future_window(v, 10)
    if bw:
        lines.append(f"If you can wait, {bw['label']} is quieter ({bw['free']} free).")
    others = sorted((x for x in _food(snapshot) if x["id"] != v["id"]),
                    key=lambda x: -_score(x, matters))[:2]
    if others:
        lines.append("Next best: " + "; ".join(
            f"{x['name']} ({x['free']} free, {x['walk_min']} min walk)" for x in others))
    return "\n".join(lines)


def _find_venue(question, venues):
    """Match a venue by its distinctive name words, never by 'canteen' alone.
    Full name parts ("Food Court") still match, because every word in them is
    generic and word-overlap alone would never find that venue."""
    q = (question or "").lower()
    words = _words(question) - GENERIC
    best, best_hits = None, 0
    for v in venues:
        keys = (_words(v["name"]) | set(v["id"].split("-"))) - GENERIC
        hits = len(words & keys)
        for part in v["name"].split(","):
            if len(part.split()) > 1 and part.strip().lower() in q:
                hits += 1
        if hits > best_hits:
            best, best_hits = v, hits
    return best


def answer(question, snapshot, group=None, matters=None, focus=None):
    raw = (question or "").strip()
    q = raw.lower()
    venues = _venues(snapshot)
    if not venues:
        return {"reply": "Still starting up — give it a couple of seconds.", "chips": CHIPS}

    matters = _matters(matters)

    # ---- work out the group size ----------------------------------------- #
    group_match = (re.search(r"(\d+)\s*(?:seats?|people|friends|of us|persons?)", q)
                   or re.search(r"(?:table|group)\s*(?:for|of)\s*(\d+)", q))
    asked_group = int(group_match.group(1)) if group_match else None
    group_intent = bool(asked_group) and _has(
        q, "together", "table", "seats", "people", "friends", "group", "us", "we")
    n = asked_group or (int(group) if group else None)

    if not q or _has(raw, "hi", "hello", "hey", "help", "what can you"):
        return {"reply": " ".join(GREET), "chips": CHIPS}

    # ---- why this pick? (the 'Ask why' button) --------------------------- #
    if focus and "why" in _words(q):
        v = _by_id(snapshot, focus)
        if v:
            return {"reply": _rationale(v, matters, snapshot), "chips": CHIPS,
                    "venue_id": v["id"]}

    # ---- a named venue --------------------------------------------------- #
    named = _find_venue(q, venues)
    if named:
        group = _free_group(named, n) if group_intent and n else None
        reply = _fmt_venue(named, group)
        if group_intent and n and not group:
            reply += f"\nNo {n} seats together there this second."
        elif group_intent and n:
            pass
        bw = _future_window(named, 10)
        if bw:
            reply += f"\nQuieter around {bw['label']} ({bw['free']} free)."
        return {"reply": reply, "chips": CHIPS, "venue_id": named["id"]}

    # ---- seats together -------------------------------------------------- #
    if group_intent or (n and _has(q, "seats", "table", "group", "together")):
        if not n:
            return {"reply": "How many of you are there? Tell me a number and I will "
                             "find that many seats together.", "chips": CHIPS}
        best, seats = _best_group(snapshot, n, matters)
        if not best:
            roomiest = max(_food(snapshot), key=lambda v: v["free"])
            return {"reply": f"No venue has {n} free seats together this second. "
                             f"{roomiest['name']} has the most free seats overall "
                             f"({roomiest['free']}). Try again in a few minutes.",
                    "chips": CHIPS}
        reply = f"Yes — {n} seats together at {_fmt_venue(best, seats)}"
        bw = _future_window(best, 10)
        if bw:
            reply += f"\nIf you can wait, {bw['label']} is quieter ({bw['free']} free)."
        return {"reply": reply, "chips": CHIPS, "venue_id": best["id"]}

    # ---- quietest / best time -------------------------------------------- #
    if _has(q, "when", "time", "quiet", "quietest", "empty", "emptiest",
            "crowded", "calm", "lull"):
        candidates = []
        for v in _food(snapshot):
            bw = _future_window(v, 6)
            if bw:
                candidates.append((v, bw))
        if not candidates:
            best = _best(snapshot, None, matters)
            return {"reply": "Right now is already the quietest point in the next two "
                             f"hours. {_fmt_venue(best) if best else ''}",
                    "chips": CHIPS, "venue_id": best["id"] if best else None}
        v, bw = min(candidates, key=lambda pair: pair[1]["in_minutes"])
        return {"reply": f"Quietest window in the next two hours: **{bw['label']}** at "
                         f"{v['name']} — about {bw['free']} free seats then, versus "
                         f"{v['free']} now.",
                "chips": CHIPS, "venue_id": v["id"]}

    # ---- queue ----------------------------------------------------------- #
    if _has(q, "queue", "line", "wait", "waiting", "long"):
        rows = sorted(_food(snapshot), key=lambda v: v["wait_minutes"])
        open_rows = [v for v in rows if v["queue"] > 0]
        if not open_rows:
            names = ", ".join(v["name"] for v in rows[:3])
            return {"reply": f"No queue anywhere right now — {names} are all serving "
                             "straight away.", "chips": CHIPS,
                    "venue_id": rows[0]["id"] if rows else None}
        head, tail = open_rows[0], open_rows[-1]
        reply = (f"Shortest line: **{head['name']}** — {head['queue']} people, "
                 f"~{head['wait_minutes']:.0f} min (one person every "
                 f"{head['service_seconds']}s).")
        if tail["id"] != head["id"]:
            reply += (f"\nWorst: {tail['name']} with {tail['queue']} people "
                      f"(~{tail['wait_minutes']:.0f} min).")
        return {"reply": reply, "chips": CHIPS, "venue_id": head["id"]}

    # ---- ghost seats ----------------------------------------------------- #
    if _has(q, "ghost", "bag", "bags", "laptop", "abandon", "abandoned", "left"):
        ghosts = []
        for v in venues:
            for g in v.get("ghosts", []):
                ghosts.append((v, g))
        ghosts.sort(key=lambda pair: -pair[1]["ghost_age"])
        if not ghosts:
            return {"reply": "No abandoned seats detected right now. I flag a seat when "
                             "a bag or laptop sits there with nobody for 20+ minutes.",
                    "chips": CHIPS}
        v, g = ghosts[0]
        state = "probably free" if g["state"] == "probably_free" else "still held"
        return {"reply": f"Seat {g['id']} at {v['name']} has had an item on it for "
                         f"{int(g['ghost_age'] // 60)} min with nobody around — {state}. "
                         f"{v['free']} other seats are free there too.",
                "chips": CHIPS, "venue_id": v["id"]}

    # ---- where / what to eat --------------------------------------------- #
    if _has(q, "where", "eat", "food", "lunch", "dinner", "hungry", "recommend",
            "now", "go", "best"):
        best = _best(snapshot, n if group_intent else None, matters)
        if not best:
            return {"reply": "Nothing is open nearby right now.", "chips": CHIPS}
        lines = [f"Go to {_fmt_venue(best)}."]
        bw = _future_window(best, 10)
        if bw:
            lines.append(f"If you can wait, {bw['label']} is quieter "
                         f"({bw['free']} free).")
        alt = sorted((v for v in _food(snapshot) if v["id"] != best["id"]),
                     key=lambda v: -_score(v, matters))[:2]
        if alt:
            lines.append("Alternatives: " + "; ".join(
                f"{v['name']} ({v['free']} free, {v['wait_minutes']:.0f} min wait)"
                for v in alt))
        return {"reply": "\n".join(lines), "chips": CHIPS, "venue_id": best["id"]}

    # ---- fallback -------------------------------------------------------- #
    best = _best(snapshot, None, matters)
    return {"reply": "I can answer questions about free seats, queues, seats together "
                     "and the quietest time to go. Right now: "
                     + (_fmt_venue(best) if best else "no data."),
            "chips": CHIPS, "venue_id": best["id"] if best else None}
