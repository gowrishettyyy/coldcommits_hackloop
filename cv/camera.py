"""
Camera workers: the only place that ever touches real pixels.

One worker per camera. Each worker reads frames, runs YOLO at a low rate
(1-3 fps is plenty: seats change slowly), converts detections to seat zones and
stores ONLY the boxes + counts. The frame is dropped immediately after that.

Privacy notes that matter for the report:
  * frames never leave this process, and are not written to disk by default;
  * the optional annotated preview window and --record file exist for the
    operator only, and are off unless switched on;
  * the browser API can never reach a frame.
"""
import os
import threading
import time

from cv.detection import annotate, detect

# A phone on flaky wifi can leave VideoCapture blocked forever waiting for bytes.
# These make ffmpeg give up after 5 seconds instead of hanging the whole worker.
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "timeout;5000|rw_timeout;5000000")


class CameraWorker(threading.Thread):
    def __init__(self, spec, source, engine, show=False, record=None,
                 target_fps=2.0, conf=0.30, size=(1280, 720)):
        super().__init__(daemon=True, name=f"camera-{spec['id']}")
        self.spec = spec
        self.source = source
        # A phone streaming MJPEG (IP Webcam / DroidCam) is an http source and must
        # NOT be treated as a video file: rewinding a live stream is meaningless.
        self.is_stream = isinstance(source, str) and source.lower().startswith(
            ("http://", "https://", "rtsp://", "rtmp://"))
        self.loop_video = (isinstance(source, str) and not source.isdigit()
                           and not self.is_stream)
        self.engine = engine
        self.show = show
        self.record = record
        self.target_fps = target_fps
        self.conf = conf
        self.mark_size = tuple(spec.get("frame_size", size))
        self.latest = {"ready": False, "people": [], "items": [], "fps": 0.0,
                       "frames": 0, "error": None, "state": {}}
        self.lock = threading.Lock()
        self.running = True
        self.started_at = None

    # ------------------------------------------------------------------ #
    def _open(self):
        import cv2

        src = self.source
        if isinstance(src, str) and src.isdigit():
            src = int(src)
        if self.is_stream:
            cap = cv2.VideoCapture(src, cv2.CAP_FFMPEG)      # what phones speak
            if not cap.isOpened():
                cap = cv2.VideoCapture(src)                  # try whatever else exists
        else:
            cap = cv2.VideoCapture(src)
        if not cap.isOpened():
            raise RuntimeError(f"could not open camera source {self.source!r}")
        if isinstance(src, int):
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.mark_size[0])
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.mark_size[1])
        return cap

    def run(self):
        import cv2

        writer = None
        try:
            cap = self._open()
        except Exception as exc:
            with self.lock:
                self.latest["error"] = str(exc)
            print(f"[camera {self.spec['id']}] {exc}")
            return

        cap_w = cap.get(cv2.CAP_PROP_FRAME_WIDTH) or self.mark_size[0]
        cap_h = cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or self.mark_size[1]
        # zones were marked on a frame of mark_size; real frames may differ
        sx = cap_w / float(self.mark_size[0] or cap_w)
        sy = cap_h / float(self.mark_size[1] or cap_h)
        seat_zones = [{"id": sid, "zone": [z[0] * sx, z[1] * sy, z[2] * sx, z[3] * sy]}
                      for sid, z in self.spec.get("zones", {}).items()]
        queue_zone = self.spec.get("queue_box")
        if queue_zone:
            queue_zone = [queue_zone[0] * sx, queue_zone[1] * sy,
                          queue_zone[2] * sx, queue_zone[3] * sy]

        self.started_at = time.time()
        seen, last = 0, 0.0
        interval = 1.0 / max(0.2, self.target_fps)
        try:
            while self.running:
                ok, frame = cap.read()
                if not ok or frame is None:
                    if self.loop_video:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)   # replay a recorded clip
                        time.sleep(0.05)
                        continue
                    time.sleep(0.2)                            # stream hiccup: retry
                    continue

                now = time.time()
                if seen == 0 and self.started_at and now - self.started_at > 8:
                    # opened, but nothing is arriving: say so instead of looking dead
                    with self.lock:
                        if not self.latest["ready"]:
                            self.latest["error"] = ("connected but no frames yet - check the "
                                                   "stream URL and that the phone is on the same wifi")
                if now - last < interval:
                    continue
                last = now
                seen += 1

                try:
                    result = detect(frame, conf=self.conf)
                except Exception as exc:
                    with self.lock:
                        self.latest["error"] = f"detection failed: {exc}"
                    time.sleep(0.5)
                    continue

                persons, items = result["people"], result["items"]
                queue_count = None
                if queue_zone:
                    from cv.tracker import count_queue
                    queue_count = count_queue(persons, queue_zone)

                with self.lock:
                    self.latest.update({
                        "ready": True, "people": persons, "items": items,
                        "frames": seen, "error": None, "queue_count": queue_count,
                        "frame_size": [int(cap_w), int(cap_h)],
                        "fps": round(seen / max(0.001, now - self.started_at), 1),
                    })

                # --- operator-only views (never sent to the app) ---
                if self.show or self.record:
                    view = annotate(frame, result, seat_zones)
                    if self.record:
                        if writer is None:
                            h, w = view.shape[:2]
                            writer = cv2.VideoWriter(self.record, cv2.VideoWriter_fourcc(*"mp4v"),
                                                     8.0, (w, h))
                        writer.write(view)
                    if self.show:
                        cv2.imshow(f"operator view - {self.spec['id']}", view)
                        if cv2.waitKey(1) & 0xFF == 27:
                            self.running = False
        finally:
            cap.release()
            if writer is not None:
                writer.release()
            if self.show:
                cv2.destroyAllWindows()


class CameraManager:
    """Holds the workers and the latest detections for the engine to read."""

    def __init__(self):
        self.workers = {}

    def start(self, spec, source, engine, show=False, record=None, target_fps=2.0):
        worker = CameraWorker(spec, source, engine, show=show, record=record,
                              target_fps=target_fps)
        self.workers[spec["id"]] = worker
        worker.start()
        return worker

    def latest(self, camera_id):
        worker = self.workers.get(camera_id)
        if not worker:
            return None
        with worker.lock:
            return dict(worker.latest)

    def status(self):
        out = []
        for cam_id, worker in self.workers.items():
            with worker.lock:
                info = dict(worker.latest)
            out.append({
                "id": cam_id,
                # never echo a signed stream URL (it carries a short-lived token)
                "source": str(worker.source).split("?")[0],
                "ready": bool(info.get("ready")),
                "frames": int(info.get("frames", 0)),
                "fps": info.get("fps", 0.0),
                "people": len(info.get("people") or []),
                "queue": info.get("queue_count"),
                "error": info.get("error"),
                "uptime_s": round(time.time() - worker.started_at, 1) if worker.started_at else 0,
            })
        return out

    def stop(self):
        for worker in self.workers.values():
            worker.running = False
        for worker in self.workers.values():
            if worker.is_alive():
                worker.join(timeout=1.5)   # close the capture cleanly before exit
