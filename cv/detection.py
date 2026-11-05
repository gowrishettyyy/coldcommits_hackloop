"""
The computer-vision layer: ONE picture in, boxes out.

Nothing here knows about seats, venues or the app. It uses YOLO, which is
already trained on 80 everyday objects, so we train nothing. Ultralytics is
imported lazily so the rest of the project still runs if it is not installed.
"""
import os

# COCO class numbers that matter to us
PERSON = 0
CHAIR = 56
# backpack, handbag, suitcase, laptop, book -> "someone left something"
ITEMS = {24, 26, 28, 63, 73}

_model = None


def load_model(path=None):
    path = path or os.environ.get("CAMPUSPULSE_MODEL", "yolo11n.pt")
    """Downloads the weights the first time (~6 MB), then reuses them.
    Imported here (not at module top) so the app still starts if the AI
    packages are not installed yet - the simulator covers that case."""
    global _model
    if _model is None:
        try:
            import torch
            from ultralytics import YOLO
        except ImportError as exc:
            raise RuntimeError(
                "ultralytics is not installed. Run: pip install -r requirements.txt "
                "(the simulator still works without it)") from exc
        # Some virtualised CPUs trap (SIGFPE) inside oneDNN's convolution kernels.
        # Seat counting needs about two frames per second, so the portable CPU path
        # is the right trade: a little slower, and it cannot take the demo down.
        torch.backends.mkldnn.enabled = False
        _model = YOLO(path)
    return _model


def detect(frame, conf=0.30, imgsz=640):
    """Returns {"people": [...], "items": [...], "chairs": [...]}.
    Boxes are [x1, y1, x2, y2] in pixels of `frame`.

    "item_names" is aligned with "items" and carries the COCO label
    (backpack / handbag / suitcase / laptop / book) so the operator view can say
    what the bag actually was instead of just drawing an orange rectangle.
    """
    result = load_model()(frame, conf=conf, imgsz=imgsz, verbose=False)[0]
    people, items, chairs, item_names = [], [], [], []
    for box in result.boxes:
        cls = int(box.cls[0])
        xyxy = [float(v) for v in box.xyxy[0]]
        if cls == PERSON:
            people.append(xyxy)
        elif cls == CHAIR:
            chairs.append(xyxy)
        elif cls in ITEMS:
            items.append(xyxy)
            item_names.append(str(result.names.get(cls, "item")))
    return {"people": people, "items": items, "chairs": chairs,
            "item_names": item_names}


# BGR colours, kept in one place so the video and any future overlay agree
PERSON_COLOUR = (110, 235, 150)     # green
CHAIR_COLOUR = (255, 190, 90)       # blue-ish, deliberately not a "state" colour
ITEM_COLOUR = (60, 170, 255)        # orange
STATE_COLOUR = {
    "busy": (70, 70, 240),          # red
    "free": (110, 230, 110),        # green
    "held": (60, 170, 255),         # orange: a bag is there
    "probably_free": (60, 220, 255),  # amber: bag alone long enough
}
UNKNOWN_COLOUR = (200, 200, 200)


def _text(view, txt, x, y, colour, scale=0.45):
    """Text that stays readable on any background: black outline, coloured fill."""
    import cv2

    cv2.putText(view, txt, (int(x), int(y)), cv2.FONT_HERSHEY_SIMPLEX, scale,
                (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(view, txt, (int(x), int(y)), cv2.FONT_HERSHEY_SIMPLEX, scale,
                colour, 1, cv2.LINE_AA)


def annotate(frame, result, zones=None, seat_states=None, hud=None, labels=True):
    """Draws boxes + seat zones on a COPY of the frame, for the operator only.
    This never goes to the browser: the privacy rule is enforced in server.py.

    Everything drawn here comes from the YOLO result passed in - there is no
    cached artwork and no pre-annotated frame anywhere. If the detector sees
    nothing, the picture is clean.

    hud: optional dict of headline strings drawn in a dark bar at the top, so a
    recorded proof video states which model and how many frames produced it.
    """
    import cv2

    view = frame.copy()

    # chairs first: their boxes are usually behind the people sitting on them
    for c in result.get("chairs", []):
        x1, y1, x2, y2 = [int(v) for v in c]
        cv2.rectangle(view, (x1, y1), (x2, y2), CHAIR_COLOUR, 2)
        if labels:
            _text(view, "chair", x1 + 3, max(13, y1 - 5), CHAIR_COLOUR)

    names = result.get("item_names") or []
    for n, i in enumerate(result.get("items", [])):
        x1, y1, x2, y2 = [int(v) for v in i]
        cv2.rectangle(view, (x1, y1), (x2, y2), ITEM_COLOUR, 2)
        if labels:
            label = names[n] if n < len(names) else "item"
            _text(view, label, x1 + 3, max(13, y1 - 5), ITEM_COLOUR)

    for p in result.get("people", []):
        x1, y1, x2, y2 = [int(v) for v in p]
        cv2.rectangle(view, (x1, y1), (x2, y2), PERSON_COLOUR, 2)
        if labels:
            _text(view, "person", x1 + 3, max(13, y1 - 5), PERSON_COLOUR)

    for seat in zones or []:
        x1, y1, x2, y2 = [int(v) for v in seat["zone"]]
        info = (seat_states or {}).get(seat["id"], {})
        state = info.get("state") if isinstance(info, dict) else None
        colour = STATE_COLOUR.get(state, UNKNOWN_COLOUR)
        cv2.rectangle(view, (x1, y1), (x2, y2), colour, 2)
        label = f'{seat["id"]} {state or ""}'.strip()
        _text(view, label, x1 + 3, max(13, y1 - 5), colour, 0.5)

    if hud:
        lines = [v for v in (hud.get("left"), hud.get("right")) if v]
        if lines:
            bar = view.shape[0]
            cv2.rectangle(view, (0, 0), (view.shape[1], 34), (26, 30, 28), -1)
            _text(view, hud.get("left", ""), 12, 23, (245, 245, 240), 0.58)
            if hud.get("right"):
                scale = 0.58
                size = cv2.getTextSize(hud["right"], cv2.FONT_HERSHEY_SIMPLEX, scale, 1)[0]
                _text(view, hud["right"], max(12, view.shape[1] - size[0] - 14), 23,
                      (120, 235, 150), scale)
            if hud.get("bottom"):
                _text(view, hud["bottom"], 12, bar - 12, (230, 230, 225), 0.5)
    return view
