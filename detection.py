"""PERSON 1 owns this file.
Looks at ONE picture (a video frame) and tells us where people, chairs and
belongings are. Uses YOLO, which is already trained - we train nothing."""
from ultralytics import YOLO

# Numbers YOLO uses for objects (COCO list)
PERSON = 0
CHAIR = 56
# backpack, handbag, suitcase, laptop, book
ITEMS = {24, 26, 28, 63, 73}

_model = None


def load_model(path="yolo11n.pt"):
    global _model
    if _model is None:
        _model = YOLO(path)  # downloads itself the first time
    return _model


def detect(frame, conf=0.25):
    """Returns {"people": [...], "items": [...], "chairs": [...]}
    Each box is [x1, y1, x2, y2] in pixels."""
    result = load_model()(frame, conf=conf, verbose=False)[0]
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
