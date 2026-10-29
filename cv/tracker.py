"""
Seat-state logic. Pure Python (standard library only), no camera and no AI in
here, so it can be tested without any footage:   python -m cv.tracker

This is the "thinking" layer. The camera/AI layer only ever produces boxes
([x1, y1, x2, y2] in pixels); this file decides what those boxes mean.
"""
import math

FREE = "free"                    # nothing on the seat
BUSY = "busy"                    # a person is sitting there
HELD = "held"                    # a bag / laptop, owner not seen yet
PROBABLY_FREE = "probably_free"  # bag / laptop, owner gone a long time
UNKNOWN = "unknown"              # no camera watching this seat

AVAILABLE = (FREE, PROBABLY_FREE)


# --------------------------------------------------------------------------- #
# small geometry helpers
# --------------------------------------------------------------------------- #
def _intersection(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)


def covered_by(box, zone):
    """How much of the ZONE is covered by the box (0..1). Used for people:
    a person's body is bigger than the seat, so we ask 'is the seat covered?'"""
    area = (zone[2] - zone[0]) * (zone[3] - zone[1])
    return _intersection(box, zone) / area if area > 0 else 0.0


def inside(box, zone):
    """How much of the BOX sits inside the zone (0..1). Used for bags/laptops,
    which are small objects fully visible on the seat."""
    area = (box[2] - box[0]) * (box[3] - box[1])
    return _intersection(box, zone) / area if area > 0 else 0.0


def center(box):
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


def feet(box):
    """Bottom-centre of a person box: a better 'where they are standing' point
    than the centre, because the head is not above the feet."""
    return ((box[0] + box[2]) / 2.0, box[3])


def distance(p, q):
    return math.hypot(p[0] - q[0], p[1] - q[1])


# --------------------------------------------------------------------------- #
# seat tracking
# --------------------------------------------------------------------------- #
class SeatTracker:
    """Remembers what happened at each seat over time, so a single blurry
    frame cannot flip a seat from busy to free.

    seats: [{"id": "T1S2", "table": "T1", "zone": [x1,y1,x2,y2], "x":.., "y":..}]
    now:   seconds, from ONE clock (the engine's virtual campus clock), so the
           same ghost rules work for a live camera and for the simulator.
    """

    def __init__(self, seats, ghost_seconds=1200, grace_samples=2, min_overlap=0.45):
        self.seats = seats
        self.ghost_seconds = ghost_seconds
        # Grace is counted in MISSED SAMPLES, not seconds, on purpose. The campus
        # clock runs up to 120x, so one processing tick can be half a minute of
        # campus time; a seconds-based window would expire between two ticks and
        # make seats flicker.
        self.grace_samples = max(0, int(grace_samples))
        self.min_overlap = min_overlap
        self.last_person = {}   # seat id -> last time a person was on it
        self.last_item = {}     # seat id -> last time an item was on it
        self.miss_person = {}   # seat id -> consecutive updates with no person
        self.miss_item = {}     # seat id -> consecutive updates with no item
        self.ghost_start = {}   # seat id -> when "item, but no person" started

    def reset(self):
        """Forget every memory of this venue, for when the demo clock is jumped.
        Stale ghost timers from the old time of day must not survive the jump."""
        self.last_person.clear()
        self.last_item.clear()
        self.miss_person.clear()
        self.miss_item.clear()
        self.ghost_start.clear()

    def update(self, people, items, now):
        """Returns {seat_id: {"state": ..., "ghost_age": seconds}}"""
        out = {}
        for seat in self.seats:
            sid, zone = seat["id"], seat["zone"]

            if any(covered_by(p, zone) >= self.min_overlap for p in people):
                self.last_person[sid] = now
                self.miss_person[sid] = 0
            else:
                self.miss_person[sid] = self.miss_person.get(sid, 99) + 1
            if any(inside(i, zone) >= 0.5 for i in items):
                self.last_item[sid] = now
                self.miss_item[sid] = 0
            else:
                self.miss_item[sid] = self.miss_item.get(sid, 99) + 1

            person_recent = self.miss_person.get(sid, 99) <= self.grace_samples
            item_recent = self.miss_item.get(sid, 99) <= self.grace_samples

            if person_recent:
                state, age = BUSY, 0.0
                self.ghost_start.pop(sid, None)
            elif item_recent:
                start = self.ghost_start.setdefault(sid, now)
                age = max(0.0, now - start)
                state = PROBABLY_FREE if age >= self.ghost_seconds else HELD
            else:
                state, age = FREE, 0.0
                self.ghost_start.pop(sid, None)

            out[sid] = {"state": state, "ghost_age": round(age, 1)}
        return out


def summarize(states):
    """Counts per state + a 0..1 crowd ratio."""
    counts = {FREE: 0, BUSY: 0, HELD: 0, PROBABLY_FREE: 0, UNKNOWN: 0}
    for info in states.values():
        counts[info["state"] if isinstance(info, dict) else info] += 1
    total = sum(counts.values())
    occupied = counts[BUSY] + counts[HELD]
    free = counts[FREE] + counts[PROBABLY_FREE]
    return {
        "total": total,
        "free": free,
        "busy": counts[BUSY],
        "held": counts[HELD],
        "probably_free": counts[PROBABLY_FREE],
        "unknown": counts[UNKNOWN],
        "crowd": round(occupied / total, 3) if total else 0.0,
    }


# --------------------------------------------------------------------------- #
# group finder
# --------------------------------------------------------------------------- #
def find_group(seats, states, n):
    """The best N seats for a group.

    Priority: one table (a real group wants one table, not 4 seats across the
    room), then the tightest cluster. Returns None if the seats do not exist.
    """
    if n < 1:
        return None

    def available(seat):
        info = states.get(seat["id"])
        state = info["state"] if isinstance(info, dict) else info
        return state in AVAILABLE

    by_table = {}
    for seat in seats:
        if available(seat):
            by_table.setdefault(seat.get("table", seat["id"]), []).append(seat)

    best, best_score = None, None
    for table, group in by_table.items():
        if len(group) < n:
            continue
        group = sorted(group, key=lambda s: (s.get("x", 0), s.get("y", 0)))
        chosen = group[:n]
        spread = max(
            [distance((a.get("x", 0), a.get("y", 0)), (b.get("x", 0), b.get("y", 0)))
             for a in chosen for b in chosen] or [0.0]
        )
        score = spread  # tighter is better
        if best_score is None or score < best_score:
            best, best_score = chosen, score

    if best is not None:
        return {
            "seat_ids": [s["id"] for s in best],
            "table": best[0].get("table"),
            "same_table": True,
            "spread": round(best_score, 2),
        }

    # No single table is big enough: give the tightest cross-table cluster.
    pool = [s for s in seats if available(s)]
    if len(pool) < n:
        return None
    best, best_spread = None, None
    for anchor in pool:
        a = (anchor.get("x", 0), anchor.get("y", 0))
        ordered = sorted(pool, key=lambda s: distance(a, (s.get("x", 0), s.get("y", 0))))
        chosen = ordered[:n]
        spread = max(distance(a, (s.get("x", 0), s.get("y", 0))) for s in chosen)
        if best_spread is None or spread < best_spread:
            best, best_spread = chosen, spread
    return {
        "seat_ids": [s["id"] for s in best],
        "table": None,
        "same_table": False,
        "spread": round(best_spread, 2),
    }


# --------------------------------------------------------------------------- #
# queue
# --------------------------------------------------------------------------- #
def count_queue(people, queue_box):
    """Counts people whose feet are inside the marked queue area."""
    if not queue_box:
        return 0
    n = 0
    for p in people:
        fx, fy = feet(p)
        if queue_box[0] <= fx <= queue_box[2] and queue_box[1] <= fy <= queue_box[3]:
            n += 1
    return n


def wait_minutes(queue_count, service_seconds):
    """Wait = people in front x average seconds to serve each person.
    Honest label: it is an estimate, it is not measured per person."""
    return round(queue_count * service_seconds / 60.0, 1)


# --------------------------------------------------------------------------- #
# self-test with fake data (no camera, no AI needed)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    seats = [
        {"id": f"T{t}S{s}", "table": f"T{t}", "x": t * 100, "y": s * 100,
         "zone": [t * 100, s * 100, t * 100 + 80, s * 100 + 80]}
        for t in range(1, 4) for s in range(1, 5)
    ]
    # table T1's zones are the column x=100..180, y=100/200/300/400 (80x80 each)
    person_on_T1S1 = [[100, 40, 180, 195]]      # body covers T1S1 only
    bag_on_T1S2 = [[112, 214, 168, 266]]        # small object fully inside T1S2

    tr = SeatTracker(seats, ghost_seconds=20, grace_samples=1)
    s = tr.update(person_on_T1S1, bag_on_T1S2, now=0)
    assert s["T1S1"]["state"] == BUSY
    assert s["T1S2"]["state"] == HELD
    assert s["T1S3"]["state"] == FREE

    s = tr.update([], bag_on_T1S2, now=5)
    assert s["T1S1"]["state"] == BUSY, "one missed sample must not flip a busy seat"
    s = tr.update([], bag_on_T1S2, now=10)
    assert s["T1S1"]["state"] == FREE, "two missed samples -> free"
    assert s["T1S2"]["state"] == HELD

    s = tr.update([], bag_on_T1S2, now=25)
    assert s["T1S2"]["state"] == PROBABLY_FREE, s["T1S2"]
    assert s["T1S2"]["ghost_age"] >= 19

    # a flicker must NOT flip the seat: two missed samples are still tolerated
    tr2 = SeatTracker(seats, grace_samples=2)
    tr2.update(person_on_T1S1, [], now=100)
    s = tr2.update([], [], now=101)
    assert s["T1S1"]["state"] == BUSY, "grace window prevents flicker"

    states = {sid: {"state": FREE, "ghost_age": 0} for sid in [x["id"] for x in seats]}
    states["T2S1"] = {"state": BUSY, "ghost_age": 0}
    g = find_group(seats, states, 4)
    assert g["same_table"] and g["table"] == "T1", g
    g3 = find_group(seats, states, 8)
    assert g3 is not None and not g3["same_table"], g3

    assert count_queue([[0, 0, 50, 100], [500, 0, 550, 100]], [0, 0, 200, 200]) == 1
    assert wait_minutes(6, 45) == 4.5
    assert summarize(states)["free"] == 11
    print("tracker.py: ALL TESTS PASSED")
