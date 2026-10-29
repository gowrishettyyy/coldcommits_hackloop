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
    Boxes are [x1, y1, x2, y2] in pixels of `frame`."""
    result = load_model()(frame, conf=conf, imgsz=imgsz, verbose=False)[0]
    people, items, chairs = [], [], []
    for box in result.boxes:
        cls = int(box.cls[0])
        xyxy = [float(v) for v in box.xyxy[0]]
        if cls == PERSON:
            people.append(xyxy)
        elif cls == CHAIR:
            chairs.append(xyxy)
        elif cls in ITEMS:
            items.append(xyxy)
    return {"people": people, "items": items, "chairs": chairs}


def annotate(frame, result, zones=None, seat_states=None):
    """Draws boxes + seat zones on a COPY of the frame, for the operator only.
    This never goes to the browser: the privacy rule is enforced in server.py."""
    import cv2

    view = frame.copy()
    for p in result.get("people", []):
        x1, y1, x2, y2 = [int(v) for v in p]
        cv2.rectangle(view, (x1, y1), (x2, y2), (90, 240, 160), 2)
    for i in result.get("items", []):
        x1, y1, x2, y2 = [int(v) for v in i]
        cv2.rectangle(view, (x1, y1), (x2, y2), (60, 170, 255), 2)
    for seat in zones or []:
        x1, y1, x2, y2 = [int(v) for v in seat["zone"]]
        info = (seat_states or {}).get(seat["id"], {})
        state = info.get("state") if isinstance(info, dict) else None
        colour = {
            "busy": (70, 70, 240),
            "free": (110, 230, 110),
            "held": (60, 170, 255),
            "probably_free": (60, 220, 255),
        }.get(state, (200, 200, 200))
        cv2.rectangle(view, (x1, y1), (x2, y2), colour, 2)
        label = f'{seat["id"]} {state or ""}'
        cv2.putText(view, label, (x1 + 3, max(14, y1 - 5)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, colour, 1, cv2.LINE_AA)
    return view
