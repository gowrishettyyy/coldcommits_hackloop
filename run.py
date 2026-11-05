"""
Start CampusPulse.

    python run.py                  # simulator only (works with nothing plugged in)
    python run.py --camera 0       # ALSO run real YOLO on your laptop webcam
    python run.py --camera 0 --show   # ...and show the operator window with boxes
    python run.py --camera clip.mp4 --record operator.mp4   # annotated proof video
    python run.py --camera table.mp4                      # a clip your phone recorded
    python run.py --camera http://192.168.1.7:8080/video --show   # phone, live (IP Webcam / DroidCam)

Open http://localhost:8000 after it starts.
"""
import argparse
import os
import sys
import threading
import time
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cv.camera import CameraManager          # noqa: E402
from cv.engine import Engine                 # noqa: E402
from server import serve                     # noqa: E402

BANNER = """
  ┌──────────────────────────────────────────────────────────────┐
  │  CampusPulse                                                 │
  │  live seats + queues, computed from a camera, never filmed   │
  └──────────────────────────────────────────────────────────────┘
"""


def main():
    ap = argparse.ArgumentParser(description="CampusPulse demo server")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--speed", type=float, default=30.0,
                    help="campus-time multiplier (30 = one campus minute per 2 s)")
    ap.add_argument("--scenario", default=None,
                    choices=[None, "rush", "ghost", "empty"], help="bis visual demo scenario")
    ap.add_argument("--day", default="Mon")
    ap.add_argument("--camera", default=None,
                    help="webcam index (0), a video file path, or a phone stream URL "
                         "(e.g. http://192.168.1.7:8080/video); omit = simulator only")
    ap.add_argument("--show", action="store_true", help="operator preview window with boxes")
    ap.add_argument("--record", default=None, help="write annotated operator video here")
    ap.add_argument("--stats", default=None,
                    help="write a JSON report of detections and seat state changes here")
    ap.add_argument("--fps", type=float, default=2.0,
                    help="detections per second (a video file is sampled at this rate "
                         "and played back at its own speed)")
    ap.add_argument("--imgsz", type=int, default=640, help="YOLO inference size")
    ap.add_argument("--seconds", type=float, default=None,
                    help="stop on its own after N seconds, for an unattended render")
    ap.add_argument("--warmup", type=float, default=0.0,
                    help="with --record, skip the first N seconds so the video starts "
                         "from real tracked state instead of the simulator stand-in")
    ap.add_argument("--cameras", default=None,
                    help="camera + seat-zone file (default data/cameras.json); use a "
                         "separate file for a demo clip so the webcam zones stay intact")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    print(BANNER)
    manager = CameraManager()
    engine = Engine(speed=args.speed, scenario=args.scenario, day=args.day,
                    cameras=manager, cameras_path=args.cameras)

    if args.camera:
        many = len(engine.camera_specs) > 1
        for cam in engine.camera_specs:
            # one --record path, several cameras: keep the files apart
            record = args.record
            stats = args.stats
            if many and record:
                base, ext = os.path.splitext(record)
                record = f"{base}-{cam['id']}{ext or '.mp4'}"
            if many and stats:
                base, ext = os.path.splitext(stats)
                stats = f"{base}-{cam['id']}{ext or '.json'}"
            print(f"  camera {cam['id']}: opening source {args.camera!r} "
                  f"at {args.fps:g} fps, imgsz {args.imgsz}")
            manager.start(cam, args.camera, engine, show=args.show, record=record,
                          target_fps=args.fps, imgsz=args.imgsz, stats_path=stats,
                          warmup=args.warmup)
    else:
        print("  camera: none (simulator drives every venue)")

    engine.start()
    httpd = serve(engine, host=args.host, port=args.port)
    url = f"http://localhost:{args.port}"
    print(f"  app:    {url}")
    print(f"  clock:  {args.speed:g}x campus time, day {args.day}")
    print("  stop:   Ctrl+C\n")
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    if args.seconds:
        # An unattended render has to end by itself: without this the demo video
        # only stops when someone presses Ctrl+C, and a worker thread never gets
        # the chance to flush its video writer or its stats file.
        def _stop_after(seconds):
            time.sleep(seconds)
            print(f"\n  reached {seconds:g}s, stopping")
            engine.running = False
            manager.stop()
            httpd.shutdown()

        threading.Thread(target=_stop_after, args=(args.seconds,), daemon=True).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  bye")
    finally:
        engine.running = False
        manager.stop()


if __name__ == "__main__":
    main()
