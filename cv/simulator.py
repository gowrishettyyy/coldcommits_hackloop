"""
The crowd simulator.

Why it exists: we have no permission to film the canteen, and a demo that dies
because a camera is unavailable is worthless. So the simulator plays the part of
the camera for every venue we have no camera on.

Important: it does NOT fake the app's data. It emits *fake detections* (person
boxes, item boxes) at exactly the interface a real camera uses, and the very same
seat-tracking logic turns them into seat states. One line in the app tells the
user which venues are live-CV and which are simulated.
"""
import random

# occupancy profile of a day, as (hour, fraction of capacity) control points.
# Lunch is the peak in a campus canteen; the library fills up in the evening.
PROFILES = {
    "Canteen": [(7.5, 0.10), (8.5, 0.55), (9.5, 0.20), (11.5, 0.45),
                (12.5, 0.92), (13.5, 1.00), (14.5, 0.55), (16.5, 0.60),
                (17.5, 0.30), (19.5, 0.85), (20.5, 0.35), (21.5, 0.08)],
    "Restaurant": [(10.5, 0.15), (12.5, 0.60), (13.5, 0.95), (14.5, 0.70),
                   (16.0, 0.25), (19.0, 0.80), (20.5, 1.00), (21.5, 0.75), (22.5, 0.20)],
    "Cafe": [(8.0, 0.30), (10.0, 0.55), (12.0, 0.35), (15.5, 0.70),
             (17.0, 0.95), (18.5, 0.75), (20.0, 0.45), (21.5, 0.15)],
    "Food Court": [(8.0, 0.25), (11.0, 0.40), (12.5, 0.85), (13.5, 1.00),
                   (14.5, 0.65), (16.5, 0.55), (18.5, 0.70), (20.0, 0.35)],
    "Library": [(8.0, 0.35), (10.0, 0.55), (12.0, 0.30), (14.0, 0.50),
                (16.0, 0.95), (17.5, 1.00), (20.0, 0.90), (21.5, 0.55), (22.5, 0.15)],
}

DAY_FACTOR = {"Mon": 1.0, "Tue": 0.98, "Wed": 1.02, "Thu": 0.97, "Fri": 0.95,
              "Sat": 0.72, "Sun": 0.50}

# what a person leaves behind (nothing / a bag / a laptop) when they walk off.
# The "ghost seats" scenario raises this so the abandoned-seat story is easy to film.
ITEM_CHANCE = {None: 0.10, "ghost": 0.35, "rush": 0.12, "empty": 0.05}


def profile_fraction(kind, hour):
    """Piecewise-linear interpolation of the day profile. Deterministic."""
    points = PROFILES.get(kind, PROFILES["Canteen"])
    if hour <= points[0][0] or hour >= points[-1][0]:
        return 0.05
    for (h0, f0), (h1, f1) in zip(points, points[1:]):
        if h0 <= hour <= h1:
            if h1 == h0:
                return f1
            return f0 + (f1 - f0) * (hour - h0) / (h1 - h0)
    return 0.05


class VenueSimulator:
    """Plays out one venue's day. Driven by the engine clock, so the whole
    campus day can run at 30x speed and still look believable."""

    def __init__(self, venue, seats, seed=7, day="Mon"):
        self.venue = venue
        self.seats = seats                      # only the seats it is allowed to own
        self.ids = [s["id"] for s in seats]
        self.tables = {}
        for seat in seats:
            self.tables.setdefault(seat.get("table", seat["id"]), []).append(seat["id"])
        self.rng = random.Random(seed)
        self.day = day
        self.sitting = {}       # seat id -> time the person leaves
        self.items = {}         # seat id -> time the item is picked up (or expires)
        self.queue = 0.0        # exact count, rounded for display
        self.pending = 0.0      # fractional arrivals not yet turned into a person
        # In real life staff clear abandoned bags; without a cap a long demo day
        # would end with a bag on every seat. Roughly one in eight seats at most.
        self.max_items = max(2, len(self.ids) // 8)

    # ------------------------------------------------------------------ #
    def _dwell(self):
        """How long a person sits: 12-32 minutes, shorter at peak."""
        return self.rng.uniform(12 * 60, 32 * 60)

    def resync(self, now, scenario=None):
        """Used when the demo clock is jumped: forget the old state and refill the
        venue to roughly its normal level for the new time of day. Without this, a
        jump to 08:00 would still show the 13:00 crowd."""
        self.sitting.clear()
        self.items.clear()
        self.queue = 0.0
        self.pending = 0.0
        target = int(round(min(float(len(self.ids)), self.target_occupancy(now))))
        tables = list(self.tables.values())
        self.rng.shuffle(tables)
        filled = 0
        for ids in tables:                      # whole tables, as in step()
            if filled >= target:
                break
            take = min(len(ids), target - filled)
            for sid in ids[:take]:
                self.sitting[sid] = now + self._dwell()
            filled += take

    def _pick_table(self):
        """A table to seat the next small group at: an untouched table if there is
        one, otherwise whichever table has the most free seats. Bags count as
        taken, because nobody sits down on someone else's belongings."""
        free_by_table = {}
        for table, ids in self.tables.items():
            free = [i for i in ids if i not in self.sitting and i not in self.items]
            if free:
                free_by_table[table] = free
        if not free_by_table:
            return None
        untouched = [free for table, free in free_by_table.items()
                     if len(free) == len(self.tables[table])]
        if untouched:
            return self.rng.choice(untouched)
        return max(free_by_table.values(), key=len)

    def _leave(self, sid, now, scenario):
        """One person walks off. Sometimes they leave a bag or laptop behind, and
        that object keeps the seat occupied: a seat that is free for a human is
        invisible to a plain seat counter, which is the whole point of the feature.
        Every departure path goes through here, including the packed-venue one,
        because a full canteen is exactly where seats get abandoned."""
        self.sitting.pop(sid, None)
        if len(self.items) < self.max_items and self.rng.random() < ITEM_CHANCE.get(scenario, 0.10):
            life = (self.rng.uniform(30, 90) if scenario == "empty"
                    else self.rng.uniform(1800, 3600))
            self.items[sid] = now + life

    def target_occupancy(self, now):
        hour = (now / 3600.0) % 24
        frac = profile_fraction(self.venue.get("kind", "Canteen"), hour)
        pop = float(self.venue.get("popularity", 1.0))
        dayf = DAY_FACTOR.get(self.day, 1.0)
        # gentle live jitter so two venues never move in lockstep
        jitter = 0.94 + 0.12 * self.rng.random()
        return max(0.0, min(float(len(self.ids)), len(self.ids) * frac * pop * dayf * jitter))

    def step(self, now, dt, scenario=None):
        """Advance the simulation by dt seconds of CAMPUS time."""
        occ_ids = [i for i in self.ids if i in self.sitting]
        target = self.target_occupancy(now)

        # --- people leaving (dwell time is up) ---
        for sid in list(self.sitting):
            if now >= self.sitting[sid] or (scenario == "empty" and self.rng.random() < 0.05):
                self._leave(sid, now, scenario)
                occ_ids = [i for i in occ_ids if i != sid]

        # --- item pickups / expiry ---
        for sid in list(self.items):
            if now >= self.items[sid]:
                del self.items[sid]

        # --- people arriving (towards the target for this hour) ---
        gap = target - len(occ_ids)
        if gap > 0:
            rate = gap / 150.0            # take ~2.5 virtual minutes to fill the gap
            expect = rate * dt * (0.6 + 0.8 * self.rng.random())
            self.pending += expect
            while self.pending >= 1.0:
                table = self._pick_table()
                if not table:
                    break
                # people arrive as 1-4 friends and sit together, so a table is
                # mostly full or mostly empty. That is why "we need 4 seats
                # together" is a real question and not just arithmetic.
                size = min(len(table), 1 + int(self.rng.random() * 3.0))
                self.pending = max(0.0, self.pending - size)
                for sid in self.rng.sample(table, size):
                    self.sitting[sid] = now + self._dwell()
                    occ_ids.append(sid)
            self.pending = min(self.pending, 5.0)   # no burst after a full house
        elif gap < -0.001:
            rate = -gap / 120.0
            if self.rng.random() < rate * dt * 0.8 and occ_ids:
                sid = self.rng.choice(occ_ids)
                self._leave(sid, now, scenario)
                occ_ids.remove(sid)

        # --- queue: forms when the venue is nearly full, at meal times ---
        hour = (now / 3600.0) % 24
        meal = 1.0 if (12.0 <= hour <= 14.5 or 19.0 <= hour <= 21.0) else 0.25
        fullness = len(occ_ids) / max(1, len(self.ids))
        # People start queueing well before the room is 100% full: they hover,
        # scan for a table and only then decide to wait in line. 0.62 is where a
        # canteen visibly backs up, which is the problem this project is about.
        q_target = max(0.0, (fullness - 0.62)) * len(self.ids) * 2.2 * meal
        if scenario == "rush":
            q_target *= 1.8
        self.queue += (q_target - self.queue) * min(1.0, dt / 90.0)
        if self.queue < 0.4:
            self.queue = 0.0

    # ------------------------------------------------------------------ #
    def detections(self, now, w=1280, h=720):
        """Fake YOLO output: person boxes and item boxes, in the same shape a
        real camera produces. Positions come from each seat's zone."""
        people, items = [], []
        for seat in self.seats:
            sid = seat["id"]
            x1, y1, x2, y2 = seat["zone"]
            if sid in self.sitting:
                # A person's body is a bit taller than the seat box, but not so much
                # that it covers the seat behind: an oversized box makes the seat
                # behind look occupied too, which would inflate every count.
                people.append([x1 - 3, y1 - (y2 - y1) * 0.55, x2 + 3, y2 + (y2 - y1) * 0.10])
            elif sid in self.items:
                cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
                bw, bh = max(14.0, (x2 - x1) * 0.45), max(12.0, (y2 - y1) * 0.35)
                items.append([cx - bw, cy - bh, cx + bw, cy + bh])
        return people, items

    def queue_boxes(self, count):
        """Where the queue people stand, so the queue counter has something real
        to count instead of trusting a number."""
        if count <= 0:
            return []
        people = []
        for i in range(int(count)):
            x = 0.06 + (i % 6) * 0.055
            y = 0.86 - (i // 6) * 0.03
            people.append([x * 1280, y * 720 - 60, x * 1280 + 40, y * 720])
        return people
