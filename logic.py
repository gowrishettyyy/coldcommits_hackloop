"""PERSON 2 owns this file.
All the 'thinking': seat busy/free, ghost seats, group finder, queue wait time.
No YOLO in here, so you can test it with fake data:  python logic.py"""
import math

# The 4 states a seat can have
FREE = "free"                 # nobody, nothing
BUSY = "busy"                 # a person is there
HELD = "held"                 # bag/laptop there, nobody around (not long yet)
PROBABLY_FREE = "probably_free"  # bag/laptop there, nobody around for a LONG time


def covered_by(box, zone):
    """How much of the ZONE is covered by the box (0 to 1). Used for people."""
    x1, y1 = max(box[0], zone[0]), max(box[1], zone[1])
    x2, y2 = min(box[2], zone[2]), min(box[3], zone[3])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    area = (zone[2] - zone[0]) * (zone[3] - zone[1])
    return inter / area if area > 0 else 0.0


def inside(box, zone):
    """How much of the BOX is inside the zone (0 to 1). Used for bags/laptops."""
    x1, y1 = max(box[0], zone[0]), max(box[1], zone[1])
    x2, y2 = min(box[2], zone[2]), min(box[3], zone[3])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    area = (box[2] - box[0]) * (box[3] - box[1])
    return inter / area if area > 0 else 0.0


def center(box):
    return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)


class SeatTracker:
    """Remembers what happened at each seat over time."""

    def __init__(self, seats, ghost_seconds=1200, grace_seconds=3, min_overlap=0.4):
        self.seats = seats
        self.ghost_seconds = ghost_seconds   # real life = 1200 (20 min). Demo = 5.
        self.grace_seconds = grace_seconds   # ignore tiny detection flickers
        self.min_overlap = min_overlap
        self.last_person = {}   # seat id -> last time a person was seen
        self.last_item = {}     # seat id -> last time an item was seen
        self.ghost_start = {}   # seat id -> when "item but no person" began

    def update(self, people, items, now):
        """now = time in seconds. Returns {seat_id: state}."""
        states = {}
        for seat in self.seats:
            sid, zone = seat["id"], seat["box"]
            if any(covered_by(p, zone) >= self.min_overlap for p in people):
                self.last_person[sid] = now
            if any(inside(i, zone) >= 0.5 for i in items):
                self.last_item[sid] = now

            person_recent = now - self.last_person.get(sid, -1e9) <= self.grace_seconds
            item_recent = now - self.last_item.get(sid, -1e9) <= self.grace_seconds

            if person_recent:
                states[sid] = BUSY
                self.ghost_start.pop(sid, None)
            elif item_recent:
                start = self.ghost_start.setdefault(sid, now)
                states[sid] = PROBABLY_FREE if now - start >= self.ghost_seconds else HELD
            else:
                states[sid] = FREE
                self.ghost_start.pop(sid, None)
        return states


def find_group(seats, states, n):
    """Best N available seats that are close together. Returns list of ids or None."""
    free = [s for s in seats if states[s["id"]] in (FREE, PROBABLY_FREE)]
    if n < 1 or len(free) < n:
        return None
    best, best_spread = None, None
    for start in free:
        c0 = center(start["box"])
        ordered = sorted(free, key=lambda s: math.dist(c0, center(s["box"])))
        group = ordered[:n]
        spread = max(math.dist(c0, center(s["box"])) for s in group)
        if best is None or spread < best_spread:
            best, best_spread = group, spread
    return [s["id"] for s in best]


def count_queue(people, queue_box):
    """Counts people whose feet are inside the queue box."""
    if not queue_box:
        return 0
    n = 0
    for p in people:
        fx, fy = (p[0] + p[2]) / 2, p[3]
        if queue_box[0] <= fx <= queue_box[2] and queue_box[1] <= fy <= queue_box[3]:
            n += 1
    return n


def wait_minutes(queue_count, service_seconds):
    """Wait time = people in queue x seconds per person."""
    return round(queue_count * service_seconds / 60, 1)


if __name__ == "__main__":
    # ---- quick self-test with FAKE data ----
    seats = [{"id": f"S{i}", "box": [i * 100, 0, i * 100 + 80, 80]} for i in range(1, 7)]
    person_on_S1 = [[100, 0, 180, 120]]
    bag_on_S2 = [[210, 20, 260, 60]]
    t = SeatTracker(seats, ghost_seconds=5, grace_seconds=1)

    s = t.update(person_on_S1, bag_on_S2, now=0)
    assert s["S1"] == BUSY and s["S2"] == HELD and s["S3"] == FREE, s
    s = t.update([], bag_on_S2, now=3)
    assert s["S1"] == FREE and s["S2"] == HELD, s
    s = t.update([], bag_on_S2, now=7)
    assert s["S2"] == PROBABLY_FREE, s
    g = find_group(seats, {"S1": BUSY, "S2": FREE, "S3": FREE, "S4": BUSY, "S5": FREE, "S6": FREE}, 2)
    assert g in (["S2", "S3"], ["S3", "S2"], ["S5", "S6"], ["S6", "S5"]), g
    assert count_queue([[0, 0, 50, 100], [500, 0, 550, 100]], [0, 0, 200, 200]) == 1
    assert wait_minutes(6, 45) == 4.5
    print("ALL TESTS PASSED")
