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
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    print(BANNER)
    manager = CameraManager()
    engine = Engine(speed=args.speed, scenario=args.scenario, day=args.day, cameras=manager)

    if args.camera:
        for cam in engine.camera_specs:
            print(f"  camera {cam['id']}: opening source {args.camera!r}")
            manager.start(cam, args.camera, engine, show=args.show, record=args.record)
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
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  bye")
    finally:
        engine.running = False
        manager.stop()


if __name__ == "__main__":
    main()
