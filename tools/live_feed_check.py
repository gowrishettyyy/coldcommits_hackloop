"""
Point the same detector the app uses at a PUBLIC live camera feed and record
what it sees, so the detection can be checked with your own eyes.

    python tools/live_feed_check.py --url "<hls url>" --seconds 60

Writes:
  artifacts/live-canteen-annotated.mp4   boxes + live count burned in
  artifacts/live-detections.json         per-sample counts and latency
  artifacts/live-frames/frame_XXX.jpg    clean frames for a manual count

No new model, no fine-tuning: this loads yolo11n.pt through cv.detection, the
same code path cv/camera.py uses, at the same confidence the app runs with.
"""
import argparse
import json
import os
import sys
import time

import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from cv.detection import detect, load_model  # noqa: E402


def draw(frame, result, count, ms, fps, conf):
    view = frame.copy()
    for x1, y1, x2, y2 in result.get("people", []):
        x1, y1, x2, y2 = [int(v) for v in (x1, y1, x2, y2)]
        cv2.rectangle(view, (x1, y1), (x2, y2), (90, 205, 125), 2)
        cv2.putText(view, "person", (x1 + 3, max(16, y1 - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (90, 205, 125), 1, cv2.LINE_AA)
    for x1, y1, x2, y2 in result.get("items", []):
        x1, y1, x2, y2 = [int(v) for v in (x1, y1, x2, y2)]
        cv2.rectangle(view, (x1, y1), (x2, y2), (60, 170, 255), 2)

    h = view.shape[0]
    cv2.rectangle(view, (0, 0), (view.shape[1], 46), (28, 34, 32), -1)
    cv2.putText(view, f"LIVE  people in frame: {count}", (14, 31),
                cv2.FONT_HERSHEY_SIMPLEX, 0.82, (255, 255, 255), 2, cv2.LINE_AA)
    cv2.putText(view, f"yolo11n  conf {conf:.2f}  {ms:.0f} ms/frame  {fps:.1f} fps",
                (view.shape[1] - 470, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.52,
                (200, 205, 200), 1, cv2.LINE_AA)
    cv2.putText(view, "PUBLIC LIVE CAMERA - COUNTING ONLY", (14, h - 14),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (210, 220, 210), 1, cv2.LINE_AA)
    return view


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True, help="HLS/MJPEG/direct video URL")
    ap.add_argument("--seconds", type=float, default=60.0)
    ap.add_argument("--fps", type=float, default=4.0, help="frames written per second")
    ap.add_argument("--every", type=int, default=0,
                    help="process every Nth frame (video files); 0 = live clock pacing")
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--conf", type=float, default=0.30)
    ap.add_argument("--out", default=os.path.join(ROOT, "artifacts", "live-canteen-annotated.mp4"))
    ap.add_argument("--frames-dir", default=os.path.join(ROOT, "artifacts", "live-frames"))
    ap.add_argument("--log", default=os.path.join(ROOT, "artifacts", "live-detections.json"))
    ap.add_argument("--sample-every", type=int, default=10, help="save every Nth processed frame")
    ap.add_argument("--save-all", action="store_true", help="keep every processed raw frame for re-scoring")
    ap.add_argument("--raw-dir", default=os.path.join(ROOT, "artifacts", "live-raw"))
    args = ap.parse_args()

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    os.makedirs(args.frames_dir, exist_ok=True)

    print("[feed] loading model", flush=True)
    load_model()

    cap = cv2.VideoCapture(args.url, cv2.CAP_FFMPEG)
    if not cap.isOpened():
        raise SystemExit("could not open the live stream URL")

    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1280)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 720)
    print(f"[feed] opened {width}x{height}", flush=True)

    writer = cv2.VideoWriter(args.out, cv2.VideoWriter_fourcc(*"mp4v"), args.fps, (width, height))
    if not writer.isOpened():
        raise SystemExit("could not open the output video writer")

    is_stream = args.url.lower().startswith(("http://", "https://", "rtsp://", "rtmp://"))
    every = args.every
    if not is_stream and not every:
        source_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        every = max(1, int(round(source_fps / max(0.1, args.fps))))
    print(f"[feed] {'live stream' if is_stream else 'video file'}, every={every or 'clock'}", flush=True)

    started = time.time()
    last_written = 0.0
    processed = 0
    sampled = 0
    read_index = 0
    records = []
    reconnects = 0

    try:
        while time.time() - started < args.seconds:
            ok, frame = cap.read()
            if not ok or frame is None:
                if not is_stream:
                    print("[feed] end of file", flush=True)
                    break
                reconnects += 1
                if reconnects > 40:
                    print("[feed] stream stopped delivering frames", flush=True)
                    break
                cap.release()
                time.sleep(0.4)
                cap = cv2.VideoCapture(args.url, cv2.CAP_FFMPEG)
                continue
            if frame.shape[1] != width or frame.shape[0] != height:
                frame = cv2.resize(frame, (width, height))
            read_index += 1
            if every:
                if read_index % every:
                    continue
            else:
                now = time.time()
                if now - last_written < 1.0 / args.fps:
                    continue
                last_written = now
            processed += 1

            t0 = time.perf_counter()
            result = detect(frame, conf=args.conf, imgsz=args.imgsz)
            ms = (time.perf_counter() - t0) * 1000
            elapsed = max(0.001, time.time() - started)
            people = len(result["people"])
            record = {
                "t": round(now - started, 2),
                "people": people,
                "items": len(result["items"]),
                "ms": round(ms, 1),
                "fps": round(processed / elapsed, 2),
            }
            if args.save_all:
                raw_dir = args.raw_dir
                os.makedirs(raw_dir, exist_ok=True)
                raw_name = f"raw_{processed:04d}.jpg"
                cv2.imwrite(os.path.join(raw_dir, raw_name), frame)
                record["raw"] = os.path.relpath(os.path.join(raw_dir, raw_name), ROOT)
            records.append(record)

            if processed % args.sample_every == 0:
                sampled += 1
                name = f"frame_{sampled:03d}.jpg"
                path = os.path.join(args.frames_dir, name)
                cv2.imwrite(path, frame)
                records[-1]["sample"] = os.path.relpath(path, ROOT)
                records[-1]["sample_people"] = people
                records[-1]["boxes"] = [[round(v, 1) for v in b] for b in result["people"]]

            view = draw(frame, result, people, ms, processed / elapsed, args.conf)
            writer.write(view)
            if processed % 8 == 0:
                print(f"[feed] {elapsed:5.1f}s  {processed} frames  {people} people  "
                      f"{ms:.0f} ms", flush=True)
    finally:
        writer.release()
        cap.release()

    summary = {
        "source": args.url.split("?")[0],
        "model": "yolo11n.pt (COCO pre-trained)",
        "imgsz": args.imgsz,
        "conf": args.conf,
        "seconds": round(time.time() - started, 1),
        "frames": processed,
        "avg_people": round(sum(r["people"] for r in records) / max(1, len(records)), 2),
        "max_people": max([r["people"] for r in records] or [0]),
        "avg_ms": round(sum(r["ms"] for r in records) / max(1, len(records)), 1),
        "effective_fps": round(processed / max(0.001, time.time() - started), 2),
        "framewriter_fps": args.fps,
        "video": os.path.relpath(args.out, ROOT),
        "samples": sampled,
        "records": records,
    }
    with open(args.log, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=1)
    print(json.dumps({k: v for k, v in summary.items() if k != "records"}, indent=2), flush=True)


if __name__ == "__main__":
    main()
