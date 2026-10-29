"""
Prove the computer-vision part works on YOUR machine, in about a minute.

    python check_cv.py                     # laptop webcam, 8 seconds
    python check_cv.py --source clip.mp4   # a video file instead
    python check_cv.py --show              # also open the operator window
    python check_cv.py --record proof.mp4  # save an annotated proof video

It prints what YOLO found per frame and how fast it ran. If this prints numbers,
the detection side of the project is working, and `python run.py --camera 0`
will make the four camera seats in the app go live.
"""
import argparse
import sys
import time

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="0",
                    help="webcam index (0), a video file, or a phone stream URL")
    ap.add_argument("--seconds", type=float, default=8.0)
    ap.add_argument("--fps", type=float, default=4.0)
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--record", default=None)
    args = ap.parse_args()

    import cv2

    from cv.detection import annotate, detect, load_model

    print("loading yolo11n.pt (downloads ~6 MB the first time) ...")
    load_model()

    src = int(args.source) if args.source.isdigit() else args.source
    cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        print(f"could not open {args.source!r}. On Windows, try --source 1.")
        return 1

    writer = None
    frames = 0
    total_ms = 0.0
    people_total = 0
    started = time.time()
    interval = 1.0 / max(0.5, args.fps)

    while time.time() - started < args.seconds:
        ok, frame = cap.read()
        if not ok or frame is None:
            print("no frame from the source")
            break
        t0 = time.time()
        res = detect(frame)
        total_ms += (time.time() - t0) * 1000
        frames += 1
        people_total += len(res["people"])

        if args.show or args.record:
            view = annotate(frame, res)
            if args.record:
                if writer is None:
                    h, w = view.shape[:2]
                    writer = cv2.VideoWriter(args.record, cv2.VideoWriter_fourcc(*"mp4v"), 8.0, (w, h))
                writer.write(view)
            if args.show:
                cv2.imshow("operator view (never sent to the app)", view)
                if cv2.waitKey(1) & 0xFF == 27:
                    break
        if time.time() - t0 < interval:
            time.sleep(max(0.0, interval - (time.time() - t0)))

    cap.release()
    if writer is not None:
        writer.release()
    if args.show:
        cv2.destroyAllWindows()

    if frames == 0:
        print("no frames processed")
        return 1
    avg = total_ms / frames
    print(f"frames: {frames}   avg inference: {avg:.0f} ms   throughput: {1000 / avg:.1f} fps (CPU)")
    print(f"people seen in total across frames: {people_total}")
    print("OK - detection is working. Now run:  python run.py --camera", args.source, "--show")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
