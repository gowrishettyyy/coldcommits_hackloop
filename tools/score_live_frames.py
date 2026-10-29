"""
Score saved live-camera frames with one or more YOLO models, and render an
annotated video from the result.

    python tools/score_live_frames.py --frames artifacts/live-raw-bank \
        --models yolo11n.pt yolo11s.pt --imgsz 640 960

It prints a small table (people per frame, latency) and writes
artifacts/live-score.json. Add --video MODEL --video-imgsz N --video-out PATH
to build the clip that is shown to a human for checking.
"""
import argparse
import glob
import json
import os
import sys
import time

import cv2
import torch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from cv.detection import load_model  # noqa: E402


def draw(frame, people, count, ms, tag):
    view = frame.copy()
    for x1, y1, x2, y2 in people:
        x1, y1, x2, y2 = [int(v) for v in (x1, y1, x2, y2)]
        cv2.rectangle(view, (x1, y1), (x2, y2), (90, 205, 125), 2)
        cv2.putText(view, "person", (x1 + 3, max(16, y1 - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (90, 205, 125), 1, cv2.LINE_AA)
    cv2.rectangle(view, (0, 0), (view.shape[1], 46), (28, 34, 32), -1)
    cv2.putText(view, f"LIVE  people in frame: {count}", (14, 31),
                cv2.FONT_HERSHEY_SIMPLEX, 0.82, (255, 255, 255), 2, cv2.LINE_AA)
    cv2.putText(view, f"{tag}  {ms:.0f} ms", (view.shape[1] - 330, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.52, (200, 205, 200), 1, cv2.LINE_AA)
    cv2.putText(view, "PUBLIC LIVE CAMERA - COUNTING ONLY", (14, view.shape[0] - 14),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (210, 220, 210), 1, cv2.LINE_AA)
    return view


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", default=os.path.join(ROOT, "artifacts", "live-raw-bank"))
    ap.add_argument("--models", nargs="+", default=["yolo11n.pt", "yolo11s.pt"])
    ap.add_argument("--imgsz", nargs="+", type=int, default=[640, 960])
    ap.add_argument("--conf", type=float, default=0.30)
    ap.add_argument("--out", default=os.path.join(ROOT, "artifacts", "live-score.json"))
    ap.add_argument("--video", default=None, help="model whose boxes go into the video")
    ap.add_argument("--video-imgsz", type=int, default=640)
    ap.add_argument("--video-out", default=os.path.join(ROOT, "artifacts", "live-canteen-annotated.mp4"))
    ap.add_argument("--fps", type=float, default=1.0)
    args = ap.parse_args()

    paths = sorted(glob.glob(os.path.join(args.frames, "*.jpg")))
    if not paths:
        raise SystemExit(f"no frames in {args.frames}")

    torch.backends.mkldnn.enabled = False
    report = {"frames": len(paths), "conf": args.conf, "settings": {}}
    video_rows = []

    for weights in args.models:
        model = load_model(weights) if weights.endswith("yolo11n.pt") else None
        if model is None:
            from ultralytics import YOLO
            model = YOLO(weights)
        for size in args.imgsz:
            counts, times = [], []
            for path in paths:
                frame = cv2.imread(path)
                t0 = time.perf_counter()
                result = model(frame, conf=args.conf, imgsz=size, verbose=False)[0]
                ms = (time.perf_counter() - t0) * 1000
                people = [list(map(float, b.xyxy[0])) for b in result.boxes if int(b.cls[0]) == 0]
                counts.append(len(people))
                times.append(ms)
                if weights == args.video and size == args.video_imgsz:
                    video_rows.append((frame, people, ms))
            key = f"{weights}@{size}"
            report["settings"][key] = {
                "counts": counts,
                "mean": round(sum(counts) / len(counts), 2),
                "min": min(counts), "max": max(counts),
                "avg_ms": round(sum(times) / len(times), 1),
            }
            print(f"{key:24s} per-frame {counts}  mean {sum(counts)/len(counts):.2f}  "
                  f"{sum(times)/len(times):.0f} ms", flush=True)

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1)
    print("wrote", os.path.relpath(args.out, ROOT))

    if args.video and video_rows:
        h, w = video_rows[0][0].shape[:2]
        writer = cv2.VideoWriter(args.video_out, cv2.VideoWriter_fourcc(*"mp4v"),
                                 args.fps, (w, h))
        tag = f"{args.video}  imgsz {args.video_imgsz}  conf {args.conf:.2f}"
        for frame, people, ms in video_rows:
            writer.write(draw(frame, people, len(people), ms, tag))
        writer.release()
        print(f"wrote {os.path.relpath(args.video_out, ROOT)}  "
              f"({len(video_rows)} frames at {args.fps} fps)")


if __name__ == "__main__":
    main()
