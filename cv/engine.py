"""
The engine: the single source of truth for "what is happening on campus".

It owns one clock (the campus clock, which can run faster than real time), one
simulator and one tracker per venue, and merges in real camera detections where a
camera exists. Everything the app asks for is answered from here.

Privacy rule lives here too: nothing in a snapshot can contain a frame, a
face or a person box. Only seat states, counts, queue length and timers.
"""
import os
import threading
import time

from cv import venues as venues_mod
from cv.simulator import VenueSimulator, profile_fraction, DAY_FACTOR
from cv.tracker import (
    FREE, BUSY, HELD, PROBABLY_FREE, UNKNOWN,
    SeatTracker, count_queue, find_group, summarize, wait_minutes,
)

DAY_START = 8 * 3600          # campus clock starts at 08:00
CAMPUS_DAY_END = 23 * 3600

CROWD_LEVELS = ("low", "medium", "high", "packed")


def _level(free, total):
    if total <= 0:
        return "low"
    ratio = free / total
    if ratio > 0.5:
        return "low"
    if ratio > 0.28:
        return "medium"
    if ratio > 0.10:
        return "high"
    return "packed"


def hhmm(seconds):
    seconds = seconds % 86400
    return f"{int(seconds // 3600):02d}:{int(seconds % 3600 // 60):02d}"


class Engine:
    def __init__(self, speed=30.0, scenario=None, day="Mon", seed=7, cameras=None):
        self.venues, self.camera_specs = venues_mod.load_world()
        self.cameras = cameras                       # CameraManager or None
        self.speed = float(speed)
        self.scenario = scenario
        self.day = day if day in DAY_FACTOR else "Mon"

        self.clock = float(DAY_START)
        self.wall_start = time.time()
        self.lock = threading.RLock()
        self.running = True

        self.sim, self.sim_cam = {}, {}
        self.tracker_sim, self.tracker_cam = {}, {}
        self.states = {}
        self.live_history = {}      # venue id -> [(campus seconds, free seats)]
        self.tickets = {}
        self.claims = {}
        self._last_push = 0.0

        for i, venue in enumerate(self.venues):
            vid = venue["id"]
            cam_seats = [s for s in venue["seats"] if s.get("camera")]
            sim_seats = [s for s in venue["seats"] if not s.get("camera")]
            ghost = float(venue.get("ghost_seconds", 1200))
            grace = int(venue.get("grace_samples", 2))
            self.tracker_sim[vid] = SeatTracker(sim_seats, ghost_seconds=ghost,
                                                grace_samples=grace)
            # real detections are noisier than the simulator, so allow one more miss
            self.tracker_cam[vid] = (SeatTracker(cam_seats, ghost_seconds=ghost,
                                                 grace_samples=grace + 1)
                                     if cam_seats else None)
            self.sim[vid] = VenueSimulator(venue, sim_seats, seed=seed + i * 13, day=self.day)
            # camera seats get a simulator too: it plays them only while no real
            # camera is delivering frames, so the venue never shows grey holes
            self.sim_cam[vid] = (VenueSimulator(venue, cam_seats, seed=seed + i * 29, day=self.day)
                                 if cam_seats else None)
            self.states[vid] = {}
            self.live_history[vid] = []

        # one tick immediately, so the very first snapshot already has real seat
        # states instead of "0 free seats" for half a second
        self.step(self.now(), 1.0)

    # ------------------------------------------------------------------ #
    # clock + main loop
    # ------------------------------------------------------------------ #
    def now(self):
        """Campus time in seconds since midnight. Advances `speed` x real time."""
        elapsed = (time.time() - self.wall_start) * self.speed
        return (self.clock + elapsed) % 86400

    def loop(self, tick=0.5):
        last = self.now()
        while self.running:
            now = self.now()
            dt = max(0.0, (now - last) % 86400) or tick * self.speed
            last = now
            try:
                self.step(now, dt)
            except Exception as exc:                    # never kill the demo
                print(f"[engine] step failed: {exc!r}")
            time.sleep(tick)

    def start(self):
        t = threading.Thread(target=self.loop, daemon=True, name="engine")
        t.start()
        return t

    def step(self, now, dt):
        with self.lock:
            for venue in self.venues:
                vid = venue["id"]

                # 1. simulated half of the venue
                sim = self.sim[vid]
                sim.step(now, dt, scenario=self.scenario)
                people, items = sim.detections(now)
                states = self.tracker_sim[vid].update(people, items, now)

                # 2. camera half of the venue: real CV if frames are arriving,
                #    otherwise the stand-in simulator keeps those seats moving
                cam_ids = [c["id"] for c in self.camera_specs if c["venue_id"] == vid]
                cam_people, cam_items, cam_live = [], [], False
                for cam_id in cam_ids:
                    bucket = self.cameras.latest(cam_id) if self.cameras else None
                    if not bucket or not bucket.get("ready"):
                        continue
                    cam_live = True
                    cam_people.extend(bucket["people"])
                    cam_items.extend(bucket["items"])
                    if bucket.get("queue_count") is not None:
                        sim.queue = float(bucket["queue_count"])

                tracker_cam = self.tracker_cam[vid]
                if tracker_cam:
                    if cam_live:
                        states.update(tracker_cam.update(cam_people, cam_items, now))
                    elif self.sim_cam[vid]:
                        self.sim_cam[vid].step(now, dt, scenario=self.scenario)
                        fp, fi = self.sim_cam[vid].detections(now)
                        states.update(tracker_cam.update(fp, fi, now))

                # 3. seat states carry their provenance, so the UI can be honest
                for seat in venue["seats"]:
                    info = states.get(seat["id"])
                    if info is None:
                        info = states[seat["id"]] = {"state": UNKNOWN, "ghost_age": 0.0}
                    info["camera"] = bool(seat.get("camera"))
                    info["live"] = bool(seat.get("camera")) and cam_live

                self.states[vid] = states
                self.live_history[vid].append((now, summarize(states)["free"]))
                if len(self.live_history[vid]) > 240:
                    self.live_history[vid].pop(0)

    # ------------------------------------------------------------------ #
    # derived data
    # ------------------------------------------------------------------ #
    def forecast(self, venue, now, hours=2.0, step_min=15):
        """Expected free seats over the next couple of hours, from the venue's
        own day profile (simulated history) + today's live position."""
        out = []
        t = now
        end = now + hours * 3600
        while t <= end:
            hour = (t / 3600.0) % 24
            frac = profile_fraction(venue.get("kind", "Canteen"), hour)
            frac *= float(venue.get("popularity", 1.0)) * DAY_FACTOR.get(self.day, 1.0)
            frac = min(1.0, max(0.0, frac))
            free = int(round(venue["capacity"] * (1 - frac)))
            out.append({"t": round(t), "label": hhmm(t), "free": free, "level": _level(free, venue["capacity"])})
            t += step_min * 60
        return out

    def best_window(self, venue, now, hours=2.0):
        fc = self.forecast(venue, now, hours)
        if not fc:
            return None
        best = max(fc, key=lambda p: p["free"])
        return {"label": best["label"], "free": best["free"],
                "in_minutes": max(0, int((best["t"] - now) / 60))}

    def venue_state(self, venue, now, group_size=None):
        vid = venue["id"]
        states = self.states.get(vid, {})
        counts = summarize(states)
        free_now = counts["free"] + counts["probably_free"]

        hot = [{"id": sid, **info} for sid, info in states.items()
               if info["state"] in (HELD, PROBABLY_FREE) and info.get("ghost_age", 0) > 60]
        hot.sort(key=lambda s: -s["ghost_age"])

        seconds = int(venue.get("service_seconds", 45))
        queue = int(round(self.sim[vid].queue)) if vid in self.sim else 0
        wait = wait_minutes(queue, seconds)

        live_seats = sum(1 for s in venue["seats"] if s.get("live"))
        data = {
            "id": vid,
            "name": venue["name"],
            "kind": venue["kind"],
            "area": venue.get("area", ""),
            "accent": venue["accent"],
            "open": venue.get("open", ""),
            "distance_m": venue.get("distance_m", 0),
            "walk_min": venue.get("walk_min", 0),
            "map": venue.get("map", {"x": 50, "y": 50}),
            "capacity": venue["capacity"],
            "tables": len({s["table"] for s in venue["seats"]}),
            "seats_per_table": venue.get("plan", {}).get("seats_per_table", 4),
            "plan": venue.get("plan", {}),
            "counts": counts,
            "free": free_now,
            "queue": queue,
            "wait_minutes": wait,
            "service_seconds": seconds,
            "crowd_level": _level(free_now, counts["total"] or venue["capacity"]),
            "forecast": self.forecast(venue, now),
            "best_window": self.best_window(venue, now),
            "seats": [{"id": s["id"], "table": s["table"], "x": s["x"], "y": s["y"],
                       "state": states.get(s["id"], {"state": UNKNOWN})["state"],
                       "ghost_age": states.get(s["id"], {}).get("ghost_age", 0),
                       "camera": bool(s.get("camera")),
                       "live": bool(states.get(s["id"], {}).get("live"))}
                      for s in venue["seats"]],
            "ghosts": hot[:6],
            "camera_seats": sum(1 for s in venue["seats"] if s.get("camera")),
            "live_seats": live_seats,
            "sparkline": [f for _, f in self.live_history.get(vid, [])][-60:],
        }
        if group_size:
            group = find_group(venue["seats"], states, int(group_size))
            if group:
                group["quality"] = ("one table" if group["same_table"] else "next to each other")
            data["group"] = group
            data["group_size"] = int(group_size)
        return data

    def recommendation(self, now):
        """'Where should I eat right now?' — best venue for a walk-up student."""
        best, best_score = None, None
        for venue in self.venues:
            vs = self.venue_state(venue, now)
            if vs["kind"] == "Library":
                continue
            # free seats matter, a long walk and a long queue cost
            score = vs["free"] * 1.0 - vs["walk_min"] * 1.4 - vs["queue"] * 0.9
            if vs["free"] < 2:
                score -= 12
            if best_score is None or score > best_score:
                best, best_score = vs, score
        if not best:
            return None
        reason = (f"{best['free']} seats free, {best['queue']} in the queue "
                  f"({best['walk_min']} min walk)")
        return {"venue_id": best["id"], "name": best["name"], "free": best["free"],
                "queue": best["queue"], "wait_minutes": best["wait_minutes"],
                "walk_min": best["walk_min"], "crowd_level": best["crowd_level"],
                "reason": reason, "best_window": best["best_window"],
                "accent": best["accent"]}

    def alerts(self, now):
        """Leave-now style alerts, computed from today's expected curve."""
        out = []
        for venue in self.venues:
            if venue["kind"] == "Library":
                continue
            fc = self.forecast(venue, now, 1.5)
            if len(fc) < 3:
                continue
            now_free = fc[0]["free"]
            worst = min(fc[1:], key=lambda p: p["free"])
            if worst["free"] < now_free * 0.6 and now_free - worst["free"] >= 4:
                mins = int((worst["t"] - now) / 60)
                out.append({
                    "id": f"{venue['id']}-rush",
                    "kind": "rush",
                    "venue_id": venue["id"],
                    "title": f"{venue['name']} is about to fill up",
                    "body": f"Expected to drop to {worst['free']} free seats by "
                            f"{worst['label']}. Leave in the next {max(1, mins - 2)} min to beat it.",
                    "severity": "warn",
                    "when": worst["label"],
                })
        ghosts = []
        for venue in self.venues:
            for g in self.venue_state(venue, now)["ghosts"]:
                if g["state"] == PROBABLY_FREE:
                    ghosts.append({"id": f"{venue['id']}-{g['id']}", "kind": "ghost",
                                   "venue_id": venue["id"],
                                   "title": f"Seat {g['id']} looks abandoned",
                                   "body": f"Bag or laptop left alone for {int(g['ghost_age'] // 60)} min "
                                           f"at {venue['name']} — probably free.",
                                   "severity": "info", "when": hhmm(now)})
        out.extend(ghosts[:3])

        best = self.recommendation(now)
        if best:
            out.insert(0, {
                "id": "best-now", "kind": "advice", "venue_id": best["venue_id"],
                "title": f"Go to {best['name']} now",
                "body": best["reason"] + ". " + (
                    f"Quietest window is around {best['best_window']['label']}."
                    if best.get("best_window") else ""),
                "severity": "good", "when": hhmm(now),
            })
        return out

    def staff(self, now):
        """Staff / canteen-manager view: peaks, staffing, waste risk."""
        hours = list(range(7, 23))
        heat = {}
        for vid_venue in self.venues:
            row = []
            for h in hours:
                frac = profile_fraction(vid_venue.get("kind", "Canteen"), h + 0.5)
                frac *= float(vid_venue.get("popularity", 1.0))
                row.append(round(min(1.0, frac), 3))
            heat[vid_venue["id"]] = row

        peak_rows = []
        for venue in self.venues:
            row = heat[venue["id"]]
            peak = max(range(len(row)), key=lambda i: row[i])
            avg = sum(row) / len(row)
            peak_seats = int(round(row[peak] * venue["capacity"]))
            # staffing: one counter can serve ~14 people / 15 min
            staff = max(1, int(round(peak_seats / 14)))
            waste = round(max(0.0, min(1.0, 1.0 - avg / max(0.01, row[peak]))) * 100)
            served = sum(int(round(row[i] * venue["capacity"] * 0.55)) for i in range(len(row)))
            peak_rows.append({
                "venue_id": venue["id"], "name": venue["name"], "kind": venue["kind"],
                "peak_hour": f"{hours[peak]:02d}:00",
                "peak_label": f"{hours[peak]:02d}:00–{hours[peak] + 1:02d}:00",
                "peak_occupancy": f"{int(row[peak] * 100)}%",
                "quietest_hour": f"{hours[min(range(len(row)), key=lambda i: row[i])]:02d}:00",
                "suggested_staff": staff,
                "food_waste_risk": f"{waste}%",
                "est_served_today": served,
                "heat": row,
            })
        total_served = sum(r["est_served_today"] for r in peak_rows)
        return {
            "hours": [f"{h:02d}" for h in hours],
            "venues": peak_rows,
            "headline": {
                "est_served_today": total_served,
                "minutes_saved": total_served * 4,          # ~4 min not spent hunting a seat
                "peak_window": "13:00–14:00",
                "staff_suggestion": "Add 2 counters and 1 clearing person at the 13:00 canteen peak.",
                "waste_note": "Prepare ~18% less food after 14:30; demand falls off a cliff.",
            },
            "note": "Built from simulated campus history (14 days). No real students were tracked.",
        }

    def snapshot(self, group_size=None):
        with self.lock:
            now = self.now()
            venues = [self.venue_state(v, now, group_size) for v in self.venues]
            cam_status = self.cameras.status() if self.cameras else []
            return {
                "clock": {"seconds": round(now), "label": hhmm(now), "day": self.day,
                          "speed": self.speed, "progress": round(now / 86400.0, 4)},
                "mode": "live-cv" if any(c.get("ready") and c.get("frames", 0) > 0 for c in cam_status) else "simulated",
                "cameras": cam_status,
                "venues": venues,
                "recommendation": self.recommendation(now),
                "alerts": self.alerts(now),
                "staff": self.staff(now),
                "group_size": group_size,
                "privacy": "Frames are processed on the device and discarded. Only seat states, counts and timers leave this process.",
            }

    # ------------------------------------------------------------------ #
    # controls + small in-memory state
    # ------------------------------------------------------------------ #
    def configure(self, speed=None, scenario=..., day=None, time_of_day=None):
        """Live controls from the app's demo panel.
        time_of_day is "HH:MM" and jumps the campus clock (e.g. straight to the
        13:00 rush) without waiting for the accelerated clock to get there."""
        with self.lock:
            if speed:
                self.now_anchor(self.clock + (time.time() - self.wall_start) * self.speed)
                self.speed = float(speed)
            if scenario is not ...:
                self.scenario = scenario
            jumped = False
            if day:
                self.day = day
                for s in self.sim.values():
                    s.day = day
                jumped = True
            if time_of_day:
                hh, _, mm = str(time_of_day).partition(":")
                self.now_anchor((int(hh) * 3600 + int(mm or 0) * 60) % 86400)
                jumped = True
            if jumped:
                self.resync()

    def _camera_ready(self, venue_id):
        if not self.cameras:
            return False
        for cam in self.camera_specs:
            if cam["venue_id"] != venue_id:
                continue
            bucket = self.cameras.latest(cam["id"])
            if bucket and bucket.get("ready"):
                return True
        return False

    def resync(self):
        """The world must match the clock after a jump: refill every venue to its
        normal level for the new time, and drop stale ghost timers and sparklines."""
        now = self.now()
        for venue in self.venues:
            vid = venue["id"]
            if vid in self.sim:
                self.sim[vid].resync(now, self.scenario)
            # a live camera owns its seats; only the stand-in is refilled
            if self.sim_cam.get(vid) and not self._camera_ready(vid):
                self.sim_cam[vid].resync(now, self.scenario)
            self.tracker_sim[vid].reset()
            if self.tracker_cam.get(vid):
                self.tracker_cam[vid].reset()
            self.live_history[vid] = []
        self.step(now, 1.0)

    def now_anchor(self, clock):
        self.clock = clock % 86400
        self.wall_start = time.time()

    def join_queue(self, venue_id):
        venue = next((v for v in self.venues if v["id"] == venue_id), None)
        if venue is None:
            raise ValueError(f"unknown venue {venue_id!r}")
        if venue_id not in self.sim:
            raise ValueError(f"venue {venue_id!r} has no simulator attached")
        with self.lock:
            queue = int(round(self.sim[venue_id].queue))
            position = queue + 1
            ticket = {
                "id": f"{venue_id[:3].upper()}-{int(time.time()) % 10000}",
                "venue_id": venue_id, "venue": venue["name"],
                "position": position,
                "eta_minutes": wait_minutes(queue, venue.get("service_seconds", 45)),
                "issued": hhmm(self.now()),
            }
            self.tickets[ticket["id"]] = ticket
            return ticket

    def ticket(self, ticket_id):
        with self.lock:
            ticket = dict(self.tickets.get(ticket_id) or {})
            if not ticket:
                return None
            moved = len(self.tickets) and int(time.time()) % 7     # simple live demotion
            ticket["position"] = max(1, ticket["position"] - moved)
            ticket["eta_minutes"] = round(ticket["position"] * 0.75, 1)
            ticket["called"] = ticket["position"] <= 1
            return ticket

    def claim(self, venue_id, seat_id):
        with self.lock:
            self.claims[seat_id] = {"venue_id": venue_id, "at": hhmm(self.now())}
            return {"ok": True, "seat_id": seat_id, "venue_id": venue_id,
                    "note": "Seat reserved for you for 10 minutes (demo behaviour)."}

    def squad_code(self, venue_id, seats):
        """A short code a group can share.

        Two things were wrong here and both bit intermittently. The alphabet is 28
        characters, but the code was built in base 31, so any result landing on index 28,
        29 or 30 raised IndexError - which killed the request and made 'hold these seats'
        look like it did nothing. And the seed came from hash(), which Python salts per
        process, so the same seats produced a different code after every restart. The
        alphabet length drives the arithmetic now, and a digest makes the code stable.
        """
        import hashlib

        letters = "ACDEFGHJKLMNPQRTUVWXY3456789"
        base = len(letters)                       # 28, and the arithmetic uses exactly this
        key = (str(venue_id) + "|" + ",".join(sorted(seats or []))).encode()
        seed = int.from_bytes(hashlib.sha256(key).digest()[:6], "big")
        code = "".join(letters[(seed // (base ** i)) % base] for i in range(5))
        return {"code": code, "seats": sorted(seats or []), "venue_id": venue_id,
                "valid_minutes": 10}


def load_engine_with_cameras(speed=30.0, scenario=None, day="Mon", camera_source=None):
    """Builds the engine and, if asked, attaches real camera workers."""
    from cv.camera import CameraManager

    manager = CameraManager()
    engine = Engine(speed=speed, scenario=scenario, day=day, cameras=manager)
    if camera_source:
        for cam in engine.camera_specs:
            if camera_source == "sim":
                continue
            manager.start(cam, camera_source, engine)
    return engine
