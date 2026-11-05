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
                 target_fps=2.0, conf=0.30, size=(1280, 720), imgsz=640,
                 stats_path=None, hud=True, warmup=0.0):
        super().__init__(daemon=True, name=f"camera-{spec['id']}")
        self.spec = spec
        self.source = source
        self.imgsz = imgsz
        self.stats_path = stats_path
        self.hud = hud
        self.warmup = float(warmup)
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
        self.latest = {"ready": False, "people": [], "items": [], "chairs": [],
                       "item_names": [], "fps": 0.0, "frames": 0, "error": None,
                       "state": {}}
        self.lock = threading.Lock()
        self.running = True
        self.started_at = None
        self.every = 1          # process 1 of every N source frames (files only)

    # ------------------------------------------------------------------ #
    def _seat_states(self):
        """Snapshot of this camera's venue seat states, taken from the engine.

        The tracker lives in Engine, on another thread, so this must not touch
        its dicts without its lock. A copy keeps the drawing code safe.
        """
        if not self.engine:
            return {}
        with self.engine.lock:
            raw = self.engine.states.get(self.spec.get("venue_id"), {})
            return {sid: dict(info) for sid, info in raw.items()}

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
        stats = []
        transitions = []
        prev_states = {}
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

        # --- how fast can this source be sampled? ------------------------- #
        # A recorded clip is NOT a live stream: VideoCapture hands frames over as
        # fast as the disk can decode them (about 1000 fps for 720p here). The old
        # loop threw all but one of those frames away on a timer, so a 13 s clip
        # flashed past in under a second and the detector effectively saw one frame
        # per pass. We now step deterministically (every Nth frame) and pace
        # playback to the clip's own clock, so 1 s of video takes 1 s of wall time.
        src_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        if self.loop_video:
            self.every = max(1, int(round(src_fps / max(0.2, self.target_fps))))
            out_fps = max(0.5, src_fps / self.every)
        else:
            self.every = 1
            out_fps = max(0.2, self.target_fps)
        model_name = os.environ.get("CAMPUSPULSE_MODEL", "yolo11n.pt")
        seat_ids = [z["id"] for z in seat_zones]

        self.started_at = time.time()
        seen, read, last = 0, 0, 0.0
        interval = 1.0 / max(0.2, self.target_fps)
        try:
            while self.running:
                ok, frame = cap.read()
                if not ok or frame is None:
                    if self.loop_video:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)   # replay a recorded clip
                        # NOTE: read is deliberately NOT reset. The pacing clock and
                        # the HUD frame counter must keep running across loops, or
                        # the second pass runs at full decode speed.
                        time.sleep(0.02)
                        continue
                    time.sleep(0.2)                            # stream hiccup: retry
                    continue

                read += 1
                if self.every > 1 and (read % self.every):
                    continue                                   # sampled out, by design

                now = time.time()
                if seen == 0 and self.started_at and now - self.started_at > 8:
                    # opened, but nothing is arriving: say so instead of looking dead
                    with self.lock:
                        if not self.latest["ready"]:
                            self.latest["error"] = ("connected but no frames yet - check the "
                                                   "stream URL and that the phone is on the same wifi")
                if not self.loop_video and now - last < interval:
                    continue
                last = now
                seen += 1

                try:
                    result = detect(frame, conf=self.conf, imgsz=self.imgsz)
                except Exception as exc:
                    with self.lock:
                        self.latest["error"] = f"detection failed: {exc}"
                    time.sleep(0.5)
                    continue

                persons = result["people"]
                items = result["items"]
                chairs = result["chairs"]
                queue_count = None
                if queue_zone:
                    from cv.tracker import count_queue
                    queue_count = count_queue(persons, queue_zone)

                # real tracker states for the seat zones, straight from the engine
                states = self._seat_states()

                with self.lock:
                    self.latest.update({
                        "ready": True, "people": persons, "items": items,
                        "chairs": chairs, "item_names": result.get("item_names", []),
                        "states": states,
                        "frames": seen, "error": None, "queue_count": queue_count,
                        "frame_size": [int(cap_w), int(cap_h)],
                        "fps": round(seen / max(0.001, now - self.started_at), 1),
                    })

                # --- operator-only view + proof video (never sent to the app) ---
                if self.show or self.record or self.stats_path:
                    live = sum(1 for sid in seat_ids if states.get(sid, {}).get("live"))
                    hud = None
                    if self.hud:
                        hud = {
                            "left": (f"{model_name}  conf {self.conf:.2f}  "
                                     f"frame {read}  people {len(persons)}  "
                                     f"chairs {len(chairs)}  bags {len(items)}"),
                            "right": "LIVE CV" if live else "SIM",
                            "bottom": (f"{self.spec.get('label', self.spec['id'])} - "
                                       f"{live}/{len(seat_zones)} seats from this camera"),
                        }
                    view = annotate(frame, result, seat_zones, states, hud=hud)
                    if self.record and now - self.started_at >= self.warmup:
                        if writer is None:
                            h, w = view.shape[:2]
                            writer = cv2.VideoWriter(
                                self.record, cv2.VideoWriter_fourcc(*"mp4v"),
                                out_fps, (w, h))
                        writer.write(view)
                    if self.show:
                        cv2.imshow(f"operator view - {self.spec['id']}", view)
                        if cv2.waitKey(1) & 0xFF == 27:
                            self.running = False

                # --- machine-readable proof: counts + every real state change ---
                if self.stats_path:
                    current = {sid: states.get(sid, {}).get("state") for sid in seat_ids}
                    for sid in seat_ids:
                        was, is_now = prev_states.get(sid), current[sid]
                        if was != is_now:
                            transitions.append({
                                "t": round(now - self.started_at, 2),
                                "source_frame": read,
                                "seat": sid,
                                "from": was,
                                "to": is_now,
                            })
                    prev_states = current
                    stats.append({
                        "t": round(now - self.started_at, 2),
                        # position inside the clip, so a player can line boxes up
                        # with the picture even when the file is replayed
                        "video_t": round(cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0, 2),
                        "source_frame": read,
                        "people": len(persons),
                        "chairs": len(chairs),
                        "items": len(items),
                        "item_names": result.get("item_names", []),
                        # the exact boxes YOLO returned for this frame: the same
                        # numbers annotate() draws in the operator window
                        "people_boxes": [[round(float(v), 1) for v in p] for p in persons],
                        "chair_boxes": [[round(float(v), 1) for v in c] for c in chairs],
                        "item_boxes": [[round(float(v), 1) for v in i] for i in items],
                        "seats": current,
                        "live_seats": [sid for sid in seat_ids
                                       if states.get(sid, {}).get("live")],
                    })

                # pace a recorded clip to its own runtime (see the note above)
                if self.loop_video:
                    delay = (self.started_at + read / src_fps) - time.time()
                    if delay > 0:
                        time.sleep(min(delay, 0.5))
        finally:
            cap.release()
            if writer is not None:
                writer.release()
            if self.show:
                cv2.destroyAllWindows()
            if self.stats_path:
                self._write_stats(stats, transitions, out_fps, src_fps,
                                  seat_zones, (cap_w, cap_h))

    # ------------------------------------------------------------------ #
    def _write_stats(self, frames, transitions, out_fps, src_fps,
                     seat_zones=None, frame_size=None):
        """Writes the sidecar JSON that lets someone check the video's claims.

        It records, per processed frame, the boxes YOLO returned, how many
        people/chairs/bags it found and what each seat's state was, plus one row
        for every state change. That is what makes 'the seat changed because the
        detector saw something' verifiable instead of asserted - and it lets the
        admin page redraw the same boxes over the clip.
        """
        import json

        people = [f["people"] for f in frames] or [0]
        seat_ids = sorted({sid for f in frames for sid in f.get("seats", {})})
        payload = {
            "camera": self.spec.get("id"),
            "venue": self.spec.get("venue_id"),
            "source": str(self.source),
            "video": self.record,
            "model": os.environ.get("CAMPUSPULSE_MODEL", "yolo11n.pt"),
            "imgsz": self.imgsz,
            "conf": self.conf,
            "target_fps": self.target_fps,
            "source_fps": round(src_fps, 2),
            "sample_every": self.every,
            "output_fps": round(out_fps, 2),
            "frames_processed": len(frames),
            "seconds_processed": frames[-1]["t"] if frames else 0,
            "people_min": min(people),
            "people_max": max(people),
            "people_mean": round(sum(people) / len(people), 2),
            "source_size": [int(v) for v in (frame_size or (0, 0))],
            "seat_zones": {z["id"]: [round(float(v), 1) for v in z["zone"]]
                           for z in (seat_zones or [])},
            "seats": seat_ids,
            "final_seat_states": frames[-1]["seats"] if frames else {},
            "transitions": transitions,
            "frames": frames,
        }
        try:
            with open(self.stats_path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=1)
            print(f"[camera {self.spec['id']}] stats -> {self.stats_path} "
                  f"({len(transitions)} seat state changes)")
        except OSError as exc:
            print(f"[camera {self.spec['id']}] could not write stats: {exc}")


class CameraManager:
    """Holds the workers and the latest detections for the engine to read."""

    def __init__(self):
        self.workers = {}

    def start(self, spec, source, engine, show=False, record=None, target_fps=2.0,
              imgsz=640, stats_path=None, hud=True, warmup=0.0):
        worker = CameraWorker(spec, source, engine, show=show, record=record,
                              target_fps=target_fps, imgsz=imgsz,
                              stats_path=stats_path, hud=hud, warmup=warmup)
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
