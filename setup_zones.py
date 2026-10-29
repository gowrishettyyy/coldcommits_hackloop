"""
The zone tool. This is what makes the project scale to a new venue: point a camera or
a phone at the room, mark the seats once, done. No retraining, no code changes.

TWO WAYS TO USE IT

  AUTO (recommended - one command, no clicking):
      python setup_zones.py --source table.mp4 --auto
      python setup_zones.py --source http://192.168.1.7:8080/video --auto --seats T1S1,T1S2,T1S3,T1S4
    It runs YOLO once, finds the chairs, throws away duplicate/tiny detections, numbers
    them left-to-right and saves them. It also writes zones_preview.jpg so you can
    check with your eyes before trusting it. If the boxes are off, re-run with a
    different frame:  --frame 60

  MANUAL (when auto gets it wrong, or for a fixed CCTV camera):
      python setup_zones.py --source 0
    A window opens. Click top-left then bottom-right of each seat in order.
    's' saves into data/cameras.json, 'r' redoes the last one, 'q' quits.

Aim the camera from above, looking down. A high angle beats a side angle because people
hide each other less, and chairs are easier for YOLO to see.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

HERE = os.path.dirname(os.path.abspath(__file__))
CAMERAS = os.path.join(HERE, "data", "cameras.json")


# --------------------------------------------------------------------------- #
# the two jobs that need thinking: clean up and order. Pure functions, so they can
# be tested without a camera or the AI packages (see the self-test at the bottom).
# --------------------------------------------------------------------------- #
def _area(box):
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _iom(a, b):
    """Intersection over the SMALLER box's area. A thin slice of a chair has a low
    IoU with the whole chair (they differ a lot in size), but an IOM near 1 - which is
    what tells us it is the same chair seen twice. IoU is the wrong tool here."""
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    smaller = min(_area(a), _area(b))
    return inter / smaller if smaller > 0 else 0.0


def clean_boxes(boxes, frame_w, frame_h, min_frac=0.02, nested=0.8):
    """YOLO says 'chair' two or three times for the same chair, and sometimes once for
    a sliver of tablecloth. Keep the biggest box of each cluster and drop anything too
    small to be a chair. Without this you end up with 24 seats on a 4-chair table."""
    kept = []
    for box in sorted(boxes, key=_area, reverse=True):
        if (box[2] - box[0]) < frame_w * min_frac or (box[3] - box[1]) < frame_h * min_frac:
            continue
        if any(_iom(box, other) > nested for other in kept):
            continue
        kept.append([float(v) for v in box])
    return kept


def order_boxes(boxes, frame_h, row_tol=0.12):
    """Left to right, top to bottom, so seat numbers run in a human order instead of
    whatever order the model happened to return."""
    rows = []
    for box in sorted(boxes, key=lambda b: (b[1] + b[3]) / 2):
        cy = (box[1] + box[3]) / 2
        for row in rows:
            if abs(cy - row["cy"]) <= frame_h * row_tol:
                row["boxes"].append(box)
                row["cy"] = sum((b[1] + b[3]) / 2 for b in row["boxes"]) / len(row["boxes"])
                break
        else:
            rows.append({"cy": cy, "boxes": [box]})
    ordered = []
    for row in sorted(rows, key=lambda r: r["cy"]):
        ordered.extend(sorted(row["boxes"], key=lambda b: (b[0] + b[2]) / 2))
    return ordered


def auto_zones(frame, seat_ids=None, min_frac=0.02):
    """One frame in, numbered seat zones out."""
    from cv.detection import detect          # imported here so manual mode runs without torch

    found = detect(frame)
    chairs = clean_boxes(found["chairs"], frame.shape[1], frame.shape[0], min_frac)
    ordered = order_boxes(chairs, frame.shape[0])
    zones = {}
    for i, box in enumerate(ordered):
        sid = seat_ids[i] if seat_ids and i < len(seat_ids) else f"C{i + 1:02d}"
        zones[sid] = [round(box[0], 1), round(box[1], 1), round(box[2], 1), round(box[3], 1)]
    return zones, ordered, len(found["chairs"])


def load_config(path=CAMERAS):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    return {"cameras": []}


def save_zones(path, camera_id, venue_id, zones, frame_size, label=None):
    """Writes the zones into the camera config the engine reads. Returns the path."""
    config = load_config(path)
    camera = next((c for c in config["cameras"] if c["id"] == camera_id), None)
    if camera is None:
        camera = {"id": camera_id, "venue_id": venue_id, "label": label or camera_id,
                  "seat_ids": [], "zones": {}, "queue_box": None}
        config["cameras"].append(camera)
    camera["seat_ids"] = list(zones.keys())
    camera["zones"] = zones
    camera["frame_size"] = [int(frame_size[0]), int(frame_size[1])]
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(config, fh, indent=2)
    return path


def draw_preview(frame, ordered, path):
    import cv2

    view = frame.copy()
    for i, box in enumerate(ordered, 1):
        x1, y1, x2, y2 = [int(v) for v in box]
        cv2.rectangle(view, (x1, y1), (x2, y2), (110, 240, 170), 2)
        cv2.putText(view, f"C{i:02d}", (x1 + 4, max(16, y1 - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (110, 240, 170), 2, cv2.LINE_AA)
    cv2.imwrite(path, view)
    return path


def open_source(source, frame_no=0):
    """Handles a webcam index, a phone stream URL, or a recorded file."""
    import cv2

    src = int(source) if str(source).isdigit() else source
    cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        return None, None
    if frame_no and not isinstance(src, int):
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_no)
    ok, frame = cap.read()
    if not ok:
        cap.release()
        return None, None
    return cap, frame


def main():
    ap = argparse.ArgumentParser(description="mark seat zones for one camera")
    ap.add_argument("--source", default="0", help="webcam index, phone stream URL, or video file")
    ap.add_argument("--camera-id", default="cam-canteen-t1")
    ap.add_argument("--venue", default="main-canteen", help="which venue these seats belong to")
    ap.add_argument("--seats", default="", help="comma separated ids in order, e.g. T1S1,T1S2,T1S3,T1S4")
    ap.add_argument("--auto", action="store_true", help="find the chairs automatically (one command)")
    ap.add_argument("--frame", type=int, default=None, help="which frame to analyse (default: 10%% in)")
    ap.add_argument("--min-size", type=float, default=0.02, help="ignore boxes smaller than this share of the frame")
    ap.add_argument("--output", default=CAMERAS, help="where to write the zones")
    ap.add_argument("--preview", default=os.path.join(HERE, "zones_preview.jpg"))
    ap.add_argument("--dry-run", action="store_true", help="show what it found, save nothing")
    args = ap.parse_args()

    seat_ids = [s.strip() for s in args.seats.split(",") if s.strip()]
    frame_no = args.frame
    if frame_no is None:
        import cv2

        probe = cv2.VideoCapture(int(args.source) if str(args.source).isdigit() else args.source)
        total = probe.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        probe.release()
        frame_no = int(total * 0.1) if total > 20 else 0

    cap, frame = open_source(args.source, frame_no)
    if frame is None:
        print(f"Could not read a frame from {args.source!r}. For a phone, check the URL in a browser first.")
        return 1
    height, width = frame.shape[:2]
    print(f"frame {width}x{height} from {args.source!r} (frame {frame_no})")

    if args.auto:
        zones, ordered, raw = auto_zones(frame, seat_ids or None, args.min_size)
        print(f"chairs YOLO reported: {raw} -> usable seats after cleanup: {len(ordered)}")
        if not ordered:
            print("No chairs found. Use manual mode (drop --auto) and click the seats yourself.")
            cap.release()
            return 1
        if seat_ids and len(seat_ids) != len(ordered):
            print(f"note: you passed {len(seat_ids)} seat ids for {len(ordered)} detected seats; "
                  "extra seats get C-names")
        for sid, box in zones.items():
            print(f"  {sid}: {box}")
        preview = draw_preview(frame, ordered, args.preview)
        print(f"preview written: {preview}  <- open it and check the boxes")
        if not args.dry_run:
            path = save_zones(args.output, args.camera_id, args.venue, zones,
                              (width, height), label=f"{args.venue} seats")
            print(f"saved {len(zones)} zones to {path}")
            print(f"next:  python run.py --camera {args.source}")
        cap.release()
        return 0

    # ---- manual clicking ----
    import cv2

    clicks, zones, index, done = [], {}, 0, []
    names = seat_ids or [f"C{i + 1:02d}" for i in range(40)]
    window = "click top-left then bottom-right of each seat"
    cv2.namedWindow(window)

    def on_mouse(event, x, y, flags, _):
        if event == cv2.EVENT_LBUTTONDOWN:
            clicks.append((x, y))

    cv2.setMouseCallback(window, on_mouse)
    print("Click top-left then bottom-right of each seat. 'r' undo, 's' save, 'q' quit.")
    while True:
        view = frame.copy()
        for sid, box in zones.items():
            cv2.rectangle(view, (int(box[0]), int(box[1])), (int(box[2]), int(box[3])), (110, 240, 170), 2)
            cv2.putText(view, sid, (int(box[0]) + 4, max(16, int(box[1]) - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (110, 240, 170), 2)
        if len(clicks) == 1:
            cv2.circle(view, clicks[0], 6, (0, 220, 255), -1)
        cv2.putText(view, f"next: {names[index]}   ({len(done)} done)", (12, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2, cv2.LINE_AA)
        cv2.imshow(window, view)

        if len(clicks) == 2:
            (x1, y1), (x2, y2) = clicks
            zones[names[index]] = [float(min(x1, x2)), float(min(y1, y2)),
                                   float(max(x1, x2)), float(max(y1, y2))]
            done.append(names[index])
            index += 1
            clicks.clear()

        key = cv2.waitKey(30) & 0xFF
        if key == ord("q"):
            break
        if key == ord("r") and done:
            zones.pop(done.pop(), None)
            index = max(0, index - 1)
        if key == ord("s") and zones:
            path = save_zones(args.output, args.camera_id, args.venue, zones, (width, height))
            draw_preview(frame, [zones[s] for s in zones], args.preview)
            print(f"saved {len(zones)} zones to {path}")
            break

    cap.release()
    cv2.destroyAllWindows()
    return 0


# --------------------------------------------------------------------------- #
# self-test of the cleanup + ordering with fake boxes - works with no camera and no AI
# --------------------------------------------------------------------------- #
if __name__ == "__main__" and "--self-test" in sys.argv:
    # these are real detections from an earlier version of this project: one chair
    # reported three times, plus a 32x18 sliver of tablecloth
    noisy = [
        [34, 262, 89, 282], [35, 238, 90, 282], [37, 238, 89, 271],   # same chair x3
        [456, 84, 488, 102],                                           # tiny sliver
        [135, 376, 188, 464], [148, 376, 268, 464],                    # two real chairs
    ]
    kept = clean_boxes(noisy, 758, 464)
    assert len(kept) == 4, kept
    assert [35.0, 238.0, 90.0, 282.0] in kept, "the biggest of the duplicates should survive"
    assert [34.0, 262.0, 89.0, 282.0] not in kept, "a thin slice of that chair must go"
    assert [37.0, 238.0, 89.0, 271.0] not in kept, "and so must the third copy"

    ordered = order_boxes(kept, 464)
    centres_y = [(b[1] + b[3]) / 2 for b in ordered]
    assert centres_y == sorted(centres_y), "rows must run top to bottom"
    bottom_row = [b for b in ordered if (b[1] + b[3]) / 2 == max(centres_y)]
    centres_x = [(b[0] + b[2]) / 2 for b in bottom_row]
    assert centres_x == sorted(centres_x), "and left to right inside a row"
    print("setup_zones.py: ALL TESTS PASSED")

elif __name__ == "__main__":
    raise SystemExit(main())
