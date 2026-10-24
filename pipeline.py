"""GLUE FILE - joins detection.py + logic.py. Written for you; nobody needs to edit it.
Reads a video, and for every half-second yields an annotated picture + seat info.
Check accuracy by eye:  python pipeline.py sample.mp4   (saves pictures in check_frames/)"""
import json
import os
import sys

import cv2
import numpy as np

from detection import detect
from logic import SeatTracker, find_group, count_queue, wait_minutes, FREE, BUSY, HELD, PROBABLY_FREE

COLORS = {FREE: (0, 200, 0), BUSY: (0, 0, 255), HELD: (0, 165, 255), PROBABLY_FREE: (0, 255, 255)}
LABELS = {FREE: "free", BUSY: "busy", HELD: "held", PROBABLY_FREE: "probably free"}


def load_all():
    with open("config.json") as f:
        config = json.load(f)
    with open("seats.json") as f:
        seats = json.load(f)
    return config, seats


def draw_frame(frame, seats, states, found, group, queue_box):
    img = frame.copy()
    for p in found["people"]:
        cv2.rectangle(img, (int(p[0]), int(p[1])), (int(p[2]), int(p[3])), (255, 255, 255), 1)
    for i in found["items"]:
        cv2.rectangle(img, (int(i[0]), int(i[1])), (int(i[2]), int(i[3])), (255, 0, 255), 1)
    for s in seats:
        x1, y1, x2, y2 = s["box"]
        thick = 4 if group and s["id"] in group else 2
        color = (255, 120, 0) if group and s["id"] in group else COLORS[states[s["id"]]]
        cv2.rectangle(img, (x1, y1), (x2, y2), color, thick)
        cv2.putText(img, f'{s["id"]} {LABELS[states[s["id"]]]}', (x1, max(15, y1 - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
    if queue_box:
        cv2.rectangle(img, tuple(queue_box[:2]), tuple(queue_box[2:]), (200, 0, 200), 2)
    return img


def draw_floor_plan(shape, seats, states, group):
    plan = np.full(shape, 30, dtype=np.uint8)
    for s in seats:
        x1, y1, x2, y2 = s["box"]
        cv2.rectangle(plan, (x1, y1), (x2, y2), COLORS[states[s["id"]]], -1)
        if group and s["id"] in group:
            cv2.rectangle(plan, (x1, y1), (x2, y2), (255, 120, 0), 5)
        cv2.putText(plan, s["id"], (x1 + 5, y1 + 25), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2)
    return plan


def run(video_path, config, seats, group_size=4):
    """Generator: gives one result dict per processed moment of the video."""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise FileNotFoundError(f"Cannot open video: {video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    step = max(1, int(fps * config.get("every_seconds", 0.5)))
    tracker = SeatTracker(seats, config["ghost_seconds"], config.get("grace_seconds", 3),
                          config.get("min_overlap", 0.4))
    queue_box = config.get("queue_box")
    i = 0
    while True:
        if i % step != 0:
            if not cap.grab():
                break
            i += 1
            continue
        ok, frame = cap.read()
        if not ok:
            break
        now = i / fps
        found = detect(frame)                       # Person 1's part
        states = tracker.update(found["people"], found["items"], now)   # Person 2's part
        group = find_group(seats, states, group_size)
        q = count_queue(found["people"], queue_box)
        counts = {k: sum(1 for v in states.values() if v == k) for k in LABELS}
        yield {
            "time": now,
            "frame": draw_frame(frame, seats, states, found, group, queue_box),
            "plan": draw_floor_plan(frame.shape, seats, states, group),
            "states": states,
            "counts": counts,
            "total": len(seats),
            "group": group,
            "queue": q,
            "wait": wait_minutes(q, config.get("service_seconds_per_person", 45)),
        }
        i += 1


if __name__ == "__main__":
    video = sys.argv[1] if len(sys.argv) > 1 else None
    config, seats = load_all()
    video = video or config["video"]
    os.makedirs("check_frames", exist_ok=True)
    n = saved = 0
    for r in run(video, config, seats):
        if n % 5 == 0 and saved < 30:
            cv2.imwrite(f"check_frames/frame_{saved:02d}.jpg", r["frame"])
            saved += 1
        n += 1
    print(f"Done. Saved {saved} pictures in check_frames/. Look at them and count how many are right.")
