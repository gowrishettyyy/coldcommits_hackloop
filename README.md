# CampusPulse — live seats, queues and seating decisions

A camera watches a room and **only counts** travel off the device: free seats, queue length,
wait time, abandoned ("ghost") seats and the best place for a group of N. The student sees a
clean app — nearby venues, a live seat map, a forecast, alerts and an assistant. Nobody sees
footage, because nothing that looks like a frame ever leaves the detection process.

## What is real and what is simulated (say this out loud to judges)

| Part | Reality |
|---|---|
| Detection (YOLO + OpenCV) | Real, and runs on your webcam. Nothing is mocked. |
| Seat state, ghost seats, group finder, queue math | Real logic, unit-tested (`python -m cv.tracker`) |
| The four camera seats in the demo | **Live CV** from your webcam, updated live in the UI |
| Every other venue | Simulated crowd flow, injected at the *same* interface as a camera |
| Best-time forecast, weekly heatmap, staff numbers | Computed from a simulated 14-day history (no canteen permission yet) |
| Live-feed count accuracy | Measured against a public live eatery camera: reliable boxes, but badly undercounts a wide, dim, crowded room (19% recall at `yolo11n`/640, 58% at `yolo11s`/960). Full numbers and method: [docs/LIVE_FEED_CHECK.md](docs/LIVE_FEED_CHECK.md). Seat-zone accuracy is not claimed. |

The simulator deliberately does **not** write app data directly. It emits fake person/item boxes
and the very same tracking logic turns those into seat states, so the pipeline you demo on your
table is the pipeline that would run in a real canteen.

## Quick start

```bash
cd campuspulse
python -m venv .venv
# Windows: .venv\Scripts\activate      mac/linux: source .venv/bin/activate
pip install -r requirements.txt
python run.py                     # then open http://localhost:8000
```

That runs everything with the simulator (no camera needed, works offline, nothing to break).

### Now the computer-vision part

```bash
python run.py --camera 0 --show                               # laptop webcam + operator window
python run.py --camera clip.mp4                               # any video file as the "camera"
python run.py --camera http://192.168.1.7:8080/video --show   # your phone, live
```

**Your phone is the best camera you already own.** Two ways to use it:

1. **Record, then replay** (most reliable, and what a demo video wants). Film the table
   with the phone, copy the file (`.mp4`/`.mov`) to the laptop, then run
   `python run.py --camera table.mp4`. The clip is replayed on a loop as if it were a
   live camera, so you can practise the demo as many times as you like. Add
   `--show --record operator.mp4` to save the annotated operator view as your proof shot.
2. **Live stream** (best for a live demo). Install *IP Webcam* (Android) or *DroidCam*
   (Android/iOS), keep the phone on the same wifi, start the stream in the app, and pass
   the URL it shows you: `http://<phone-ip>:8080/video` for IP Webcam,
   `http://<phone-ip>:4747/video` for DroidCam.

**What is tested and what is not:** route 1 is fully tested, including replaying a file
over http. Route 2 is implemented (it asks for the ffmpeg backend and sets a 5-second
open timeout so a dead stream cannot freeze the app), but the streaming hop could not be
exercised in the environment this was built in, whose OpenCV build cannot decode a
multipart MJPEG stream at all. Try it at home today; if the app has not switched to
`live CV` within ~10 s, open the URL in a browser to confirm it plays and fall back to
route 1. The CV panel (gear button) shows the real error if a source will not open.

Either way, prop the phone *above* the table looking down. A high angle beats a side
angle because people stop hiding each other, and it makes marking seat zones much easier.
If a clip is portrait, that is fine — the zones are scaled to whatever the camera sends.

Point the webcam at your dining table so all four chairs are in view. In the app, open
**Nitte Main Canteen** → those four seats are marked *live CV*. Sit down, stand up, leave a bag,
and watch the seat change colour and the ghost timer start. The operator window (`--show`) is for
you and for the demo video's proof shot; it is never served to the app.

Mark your own seat zones in one minute (this is what makes a new venue a 10-minute job, not a
code change):

```bash
python setup_zones.py --source 0
```

Keyboard: click top-left then bottom-right of each seat, `s` saves to `data/cameras.json`, `q` quits.

## How it works

```
webcam / CCTV ──► YOLO (persons, chairs, bags) ──► seat zones ──► SeatTracker ──► engine ──► app
   frame              boxes in pixels             overlap test    states+timers   JSON     SSE 1 Hz
      │                                                                                        ▲
      └── frame is discarded here; boxes are just 4 numbers ───────────────────────────────────┘
```

* **Seat states**: `free`, `busy` (a person), `held` (bag, owner not seen yet), `probably_free`
  (bag alone 20+ min), `unknown` (no camera).
* **Ghost seats**: we remember the last time a person was seen on a seat. An item with no person
  past a grace window starts a timer; after `ghost_seconds` (20 min real, ~40 s at 30× demo speed)
  the seat becomes *probably free* — a bag is not a person, so a student should be allowed to take it.
* **Group finder**: one whole table first, then the tightest cluster. Never four seats scattered
  across the room.
* **Queue wait**: people in the queue area × average service seconds per person. It is an
  estimate — we say so in the UI.
* **Honest provenance**: every seat in the JSON says whether it is camera-backed (`"live": true`)
  or simulated, and the header pill says `live CV` or `simulated campus`.

## What the app looks like now

Open the site and the first screen is the brand: a giant, soft 3D **Campus Pulse**
wordmark that assembles letter by letter, then two questions — how many of you
(1–8) and what matters (closest / quietest / no queue, pick any combination).
The answer screen gives you one place, its name in huge type, one line of facts,
the numbers, a "See the seat map" button, and a small arrow that unfolds the rest
of campus. There is no top navigation bar: the **hammer** in the top right opens
Home, Map, Alerts, Staff view, demo controls and the student/staff switch. The
Nunito font is bundled in `web/fonts`, so the type survives a bad wifi day.

## Live camera accuracy check

We pointed the same model at a **public live camera of a busy open-air eatery**
(110 seconds, 24 sampled frames, night, wide shot) and hand-counted six frames.
Mean absolute error was 25 people for `yolo11n` at 640, 16 for `yolo11s` at 640
and 13 for `yolo11s` at 960; recall went from 19% to 58%. The boxes it draws are
real people — it simply misses the small, dark, overlapping ones in a crowd. On a
phone held above one table (the setup the demo uses) people are large in frame,
which is the case this app is actually designed for. The app also confirmed the
live hop: `mode: live-cv`, frames arriving, counts updating. Read the honest
write-up, videos and reproduce commands in
[docs/LIVE_FEED_CHECK.md](docs/LIVE_FEED_CHECK.md).

## Controls for the demo (hammer menu, top right)

* campus clock speed: 1× → 120× (at 30× a whole campus day plays in ~30 minutes, lunch peak in ~5)
* jump the clock: 08:00 / 11:30 / 13:00 / 17:30 / 19:30 — go straight to the lunch crush
* scenario: normal day, lunch rush, ghost seats, near empty
* CV status: which cameras are attached, frames processed, real FPS, errors

## Layout

```
run.py                  start everything
server.py               HTTP API + SSE (standard library only)
setup_zones.py          click-to-mark seat zones on any camera
check_cv.py             prove the AI works on your own machine in ~60 seconds
docs/GUIDE.md           handbook: setup, judge script, viva Q&A, limits, 2-day plan
docs/DEMO_SCRIPT.md     shot-by-shot script for the 3-minute demo video
docs/CampusPulse_Handbook.pdf   the same two documents as a printable PDF
cv/detection.py         YOLO wrapper: one frame in, boxes out
cv/tracker.py           seat states, ghost timer, group finder, queue math (self-tested)
cv/simulator.py         simulated campus crowd flow
cv/camera.py            camera worker threads (the only code that touches pixels)
cv/engine.py            clock, merging, forecast, alerts, staff stats, snapshot
cv/insights.py          the assistant, answered from live data
data/venues.json        the venues + floor plans (rename these for your campus)
data/cameras.json       which camera watches which seats, and the zone boxes
web/                    the app (HTML + CSS + JS, no build step)
web/fonts/              Nunito, bundled so the big type works offline
tools/live_feed_check.py  run the detector on a public live camera, save proof video + counts
tools/score_live_frames.py compare models on saved frames, render an annotated clip
docs/LIVE_FEED_CHECK.md   the live-feed accuracy test, with numbers and limits
```

## Documents

[docs/GUIDE.md](docs/GUIDE.md) is the full handbook (setup, how it works, what was
verified, honest limits, judge script, 24 viva questions with answers, a two-day plan,
troubleshooting). [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md) is the demo-video shot
list, including how to film the table with your phone. Both are also shipped as
[docs/CampusPulse_Handbook.pdf](docs/CampusPulse_Handbook.pdf); rebuild it with
`pip install fpdf2 && python docs/build_pdf.py`.

## Limits we state up front

* Accuracy drops when people block each other, in dim light, and at shallow camera angles.
  One camera sees one area; a big hall needs several.
* The queue estimate assumes a steady service speed; it is not measured per person.
* The forecast and heatmap come from simulated history until there are real cameras and days of data.
* Camera permission for the canteen is still pending, so the demo runs on a laptop webcam.

## Roadmap (future work, not claimed as working)

Order-ahead synced with the queue, leave-now push notifications, per-seat reservations, emergency
headcount (safety-critical, needs certification), food-waste prediction from weeks of real data,
multi-venue rollout on Raspberry Pi / Jetson at 1–2 fps per camera.
