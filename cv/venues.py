"""
Venue + seat geometry.

A venue is described once in data/venues.json (how many tables, how many seats,
how popular it is). From that we generate:
  * seats with a normalised floor-plan position (x, y in 0..1) -> the app draws
    a stylised seat map. The user NEVER sees camera footage, only this map.
  * seats with a zone box in the camera/simulation frame -> the detection layer
    needs pixel boxes to measure overlap.

Some seats are watched by a real camera (data/cameras.json). Those seats get
their zone from the camera setup instead, and the simulator must not touch them.
"""
import json
import math
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

# the frame the simulator pretends to look at, in pixels
VIRTUAL_W, VIRTUAL_H = 1280, 720

KIND_ACCENT = {
    "Canteen": "mint",
    "Cafe": "amber",
    "Restaurant": "violet",
    "Library": "blue",
    "Food Court": "rose",
}


def _ring_positions(n, shape="square"):
    """Where the seats sit around one table, as offsets from the table centre
    in units of 'half a table'. Square tables get 4 sides, long tables 2."""
    if shape == "long":
        return [(i / max(1, n - 1) * 2 - 1, 1.0 if i % 2 == 0 else -1.0) for i in range(n)]
    if n <= 1:
        return [(0.0, 0.0)]
    if n == 2:
        return [(0.0, -1.0), (0.0, 1.0)]
    if n <= 4:
        base = [(-1.0, -1.0), (1.0, -1.0), (1.0, 1.0), (-1.0, 1.0)]
    elif n <= 6:
        base = [(-1.0, -1.0), (0.0, -1.0), (1.0, -1.0), (1.0, 1.0), (0.0, 1.0), (-1.0, 1.0)]
    else:
        base = [(-1.0, -1.0), (-0.33, -1.0), (0.33, -1.0), (1.0, -1.0),
                (1.0, 1.0), (0.33, 1.0), (-0.33, 1.0), (-1.0, 1.0)]
    return base[:n] if n <= len(base) else base


def build_venue(spec):
    """Returns the venue dict the engine uses, with seats filled in."""
    plan = spec.get("plan", {})
    cols = int(plan.get("cols", 4))
    rows = int(plan.get("rows", 3))
    tables = int(plan.get("tables", cols * rows))
    seats_per_table = int(plan.get("seats_per_table", 4))
    shape = plan.get("shape", "square")

    # generous margins so the drawn map is not glued to the edges
    pad_x, pad_y = 0.06, 0.08
    cell_w = (1.0 - 2 * pad_x) / cols
    cell_h = (1.0 - 2 * pad_y) / rows
    ring = _ring_positions(seats_per_table, shape)

    seats = []
    for t in range(tables):
        col, row = t % cols, t // cols
        tx = pad_x + (col + 0.5) * cell_w
        ty = pad_y + (row + 0.5) * cell_h
        table_id = f"T{t + 1}"
        for s in range(seats_per_table):
            ox, oy = ring[s]
            x = tx + ox * cell_w * 0.30
            y = ty + oy * cell_h * 0.30
            # The box a camera would need to watch this seat, in PIXELS of the
            # virtual camera frame, centred on the seat and never smaller than a
            # minimum. Detection boxes and zones must share one unit and a zone
            # must never collapse to zero width: a zero-width zone silently matches
            # nothing at all, which looks exactly like an empty venue.
            zone_w = max(56.0, cell_w * VIRTUAL_W * 0.30)
            zone_h = max(44.0, cell_h * VIRTUAL_H * 0.30)
            cx, cy = x * VIRTUAL_W, y * VIRTUAL_H
            zone = [round(cx - zone_w / 2, 1), round(cy - zone_h / 2, 1),
                    round(cx + zone_w / 2, 1), round(cy + zone_h / 2, 1)]
            seats.append({
                "id": f"{table_id}S{s + 1}",
                "table": table_id,
                "x": round(x, 4),
                "y": round(y, 4),
                "zone": zone,
            })

    venue = dict(spec)
    venue["seats"] = seats
    venue["capacity"] = len(seats)
    venue["accent"] = spec.get("accent") or KIND_ACCENT.get(spec.get("kind", ""), "mint")
    return venue


def load_world(path=None, cameras_path=None):
    """Loads every venue + the camera list. cameras_path exists so a zone file
    produced by setup_zones.py can be validated without overwriting the real one."""
    venues_path = path or os.path.join(DATA, "venues.json")
    with open(venues_path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)

    venues = [build_venue(v) for v in raw["venues"]]
    by_id = {v["id"]: v for v in venues}

    cameras_path = cameras_path or os.path.join(DATA, "cameras.json")
    cameras = []
    if os.path.exists(cameras_path):
        with open(cameras_path, "r", encoding="utf-8") as fh:
            cameras = json.load(fh).get("cameras", [])

    for cam in cameras:
        venue = by_id.get(cam["venue_id"])
        if not venue:
            continue
        cam.setdefault("seat_ids", [s["id"] for s in venue["seats"]][:4])
        cam.setdefault("ghost_seconds", 1200)
        cam.setdefault("service_seconds", venue.get("service_seconds", 45))
        # attach the camera zone to each watched seat
        for seat in venue["seats"]:
            zone = cam.get("zones", {}).get(seat["id"])
            if zone:
                seat["camera"], seat["zone"] = cam["id"], zone
        venue["camera_ids"] = sorted({s.get("camera") for s in venue["seats"] if s.get("camera")})

    return venues, cameras


def default_zones(seat_ids, spacing=0.02):
    """A ready-to-use 2x2 zone layout for a 1280x720 frame, so the demo works
    before anyone marks real zones with cv/setup_zones.py."""
    cols, w, h = 2, 0.22, 0.30
    zones = {}
    for i, sid in enumerate(seat_ids):
        cx = 0.20 + (i % cols) * (w + spacing + 0.10)
        cy = 0.30 + (i // cols) * (h + spacing + 0.12)
        zones[sid] = [round(cx * VIRTUAL_W, 1), round(cy * VIRTUAL_H, 1),
                      round((cx + w) * VIRTUAL_W, 1), round((cy + h) * VIRTUAL_H, 1)]
    return zones
