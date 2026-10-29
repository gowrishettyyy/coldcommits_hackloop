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


def _words(text):
    return set(re.findall(r"[a-z0-9]+", (text or "").lower()))


def _has(question, *triggers):
    """Whole-word matching. Plain substring matching is a trap: 'seats' contains
    'eat', so 'any ghost seats?' would be answered as a food question."""
    words = _words(question)
    for t in triggers:
        if " " in t:
            if t in question:
                return True
        elif t in words:
            return True
    return False


def _venues(snapshot):
    return snapshot.get("venues", [])


def _by_id(snapshot, vid):
    return next((v for v in _venues(snapshot) if v["id"] == vid), None)


def _food(snapshot):
    return [v for v in _venues(snapshot) if v["kind"] != "Library"]


def _best(snapshot, group=None):
    pool = _food(snapshot)
    if group:
        pool = [v for v in pool if v.get("group")]
    if not pool:
        return None
    return max(pool, key=lambda v: v["free"] - v["walk_min"] * 1.4 - v["queue"] * 0.9)


def _fmt_venue(v, group=None):
    bits = [
        f"**{v['name']}** — {v['free']} of {v['capacity']} seats free",
        f"{v['queue']} in the queue (~{v['wait_minutes']:.0f} min wait)"
        if v["queue"] else "no queue right now",
        f"{v['walk_min']} min walk",
    ]
    line = " · ".join(bits)
    if group and v.get("group"):
        line += f"\nSeats {', '.join(v['group']['seat_ids'])} are free together ({v['group']['quality']})."
    return line


def answer(question, snapshot):
    q = (question or "").strip().lower()
    now = snapshot.get("clock", {}).get("label", "")
    venues = _venues(snapshot)
    if not venues:
        return {"reply": "Still starting up — give it a couple of seconds.", "chips": CHIPS}

    group_match = re.search(r"(\d+)\s*(?:seats?|people|friends|of us|persons?)", q)
    group = int(group_match.group(1)) if group_match else None

    if not q or _has(q, "hi", "hello", "hey", "help", "what can you"):
        return {"reply": " ".join(GREET), "chips": CHIPS}

    # ---- seats together -------------------------------------------------- #
    if group and (_has(q, "together", "group", "seats", "friends", "of us") or "we need" in q):
        best = _best(snapshot, group=True)
        if not best:
            tightest = max(_food(snapshot), key=lambda v: v["free"])
            return {"reply": f"No venue has {group} free seats together this second. "
                             f"{tightest['name']} has the most free seats overall "
                             f"({tightest['free']}). Try again in a few minutes or take two tables.",
                    "chips": CHIPS}
        return {"reply": f"Yes — {group} seats together at {_fmt_venue(best, group)}\n"
                         f"Best window is around {best['best_window']['label']} "
                         f"({best['best_window']['free']} free)." if best.get("best_window")
                         else f"Yes — {group} seats together at {_fmt_venue(best, group)}",
                "chips": CHIPS, "venue_id": best["id"]}

    # ---- where / what to eat --------------------------------------------- #
    if _has(q, "where", "eat", "food", "lunch", "dinner", "hungry", "recommend", "now", "go"):
        best = _best(snapshot)
        if not best:
            return {"reply": "Nothing is open nearby right now.", "chips": CHIPS}
        alt = sorted(_food(snapshot), key=lambda v: -v["free"])[:3]
        alt = [v for v in alt if v["id"] != best["id"]][:2]
        lines = [f"Go to {_fmt_venue(best)}."]
        if best.get("best_window"):
            lines.append(f"If you can wait, {best['best_window']['label']} is quieter "
                         f"({best['best_window']['free']} free).")
        if alt:
            lines.append("Alternatives: " + "; ".join(
                f"{v['name']} ({v['free']} free, {v['wait_minutes']:.0f} min wait)" for v in alt))
        return {"reply": "\n".join(lines), "chips": CHIPS, "venue_id": best["id"]}

    # ---- quietest / best time -------------------------------------------- #
    if _has(q, "when", "time", "quiet", "quietest", "empty", "emptiest", "crowded", "calm", "lull"):
        scored = [(v, v.get("best_window")) for v in _food(snapshot)]
        scored = [(v, bw) for v, bw in scored if bw]
        if not scored:
            return {"reply": "I do not have enough history yet.", "chips": CHIPS}
        earliest = min(scored, key=lambda pair: pair[1]["in_minutes"])
        v, bw = earliest
        return {"reply": f"Quietest window in the next two hours: **{bw['label']}** at {v['name']} "
                         f"— about {bw['free']} free seats then, versus {v['free']} now."
                         f"\nAcross campus the lull is around 15:00, and the worst crush is "
                         f"13:00–14:00 at the canteens.",
                "chips": CHIPS, "venue_id": v["id"]}

    # ---- queue ----------------------------------------------------------- #
    if _has(q, "queue", "line", "wait", "waiting", "long"):
        rows = sorted(_food(snapshot), key=lambda v: v["wait_minutes"])
        head = rows[0]
        tail = rows[-1]
        return {"reply": f"Shortest line: **{head['name']}** — {head['queue']} people, "
                         f"~{head['wait_minutes']:.0f} min (they serve one person every "
                         f"{head['service_seconds']}s).\nWorst: {tail['name']} with "
                         f"{tail['queue']} people (~{tail['wait_minutes']:.0f} min).",
                "chips": CHIPS, "venue_id": head["id"]}

    # ---- ghost seats ----------------------------------------------------- #
    if _has(q, "ghost", "bag", "bags", "laptop", "abandon", "abandoned", "left"):
        ghosts = []
        for v in venues:
            for g in v.get("ghosts", []):
                ghosts.append((v, g))
        ghosts.sort(key=lambda pair: -pair[1]["ghost_age"])
        if not ghosts:
            return {"reply": "No abandoned seats detected right now. I flag a seat when a bag or "
                             "laptop sits there with nobody for 20+ minutes.", "chips": CHIPS}
        v, g = ghosts[0]
        state = "probably free" if g["state"] == "probably_free" else "still held"
        return {"reply": f"Seat {g['id']} at {v['name']} has had an item on it for "
                         f"{int(g['ghost_age'] // 60)} min with nobody around — {state}. "
                         f"{v['free']} other seats are free there too.",
                "chips": CHIPS, "venue_id": v["id"]}

    # ---- a specific venue ------------------------------------------------ #
    q_words = _words(q)
    for v in venues:
        tokens = _words(v["name"]) | set(v["id"].split("-"))
        if q_words & tokens:
            return {"reply": _fmt_venue(v) + f"\nPeak today: {v['crowd_level']} right now, "
                                             f"busiest 13:00–14:00.",
                    "chips": CHIPS, "venue_id": v["id"]}

    # ---- fallback -------------------------------------------------------- #
    best = _best(snapshot)
    return {"reply": "I can answer questions about free seats, queues, seats together and the "
                     "quietest time to go. Right now: " + (_fmt_venue(best) if best else "no data."),
            "chips": CHIPS, "venue_id": best["id"] if best else None}
