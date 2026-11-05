# Phase 1 results: real video through the existing YOLO pipeline

Date: 5 Nov 2026. Branch: `freebuff/fd001418ac08670c2b50a878` (cut from `development`).
Nothing here is committed or pushed.

Phase 1 question: **does CampusPulse's detection core actually work on real
restaurant/canteen footage, using the YOLO pipeline that already exists?**

Short answer: **yes for people, chairs, seat mapping and live seat states; no for
crowd counting accuracy, and the seat-*change* case needed a third clip because
neither restaurant clip contains somebody getting up or sitting down.**

Everything below was measured on this machine (CPU only, no GPU).

---

## 1. What runs

```
data/demo/*.mp4                                    (raw stock footage, no annotations)
        |
        v
cv/camera.py     CameraWorker: opens the file with cv2.VideoCapture, paces it to
        |        real time, decodes a frame every 1/2 s, hands it to the engine
        v
cv/engine.py     CameraEngine: runs the model, keeps the seat tracker, marks
        |        which seats this camera actually measures (live=True)
        v
cv/detection.py  detect(): yolo11n.pt -> person / chair / bag boxes
        |        annotate(): draws those boxes, the seat zones, the states, the HUD
        v
cv/tracker.py    SeatTracker: for each seat zone, "is a person box covering this?"
        |        -> free / busy, with a grace count so one bad frame cannot flip it
        v
state.json + /api/snapshot  ->  dashboard (seat map, "seen by a camera" dots)
        |
        v
--record  -> artifacts/*.mp4 with the boxes burned in
--stats   -> artifacts/*.json with per-frame counts + every seat change
```

The model file is the copy already in the repo root: `yolo11n.pt`. No new model, no
replacement detector, no cloud API. Frames never leave the process.

## 2. Demo inputs

| file | what it is | why |
| --- | --- | --- |
| `data/demo/cafe-table.mp4` | Pexels 6024569, 12 s, 1280x720, tripod | fixed camera, 4 chair-sized objects, people large enough to judge by eye |
| `data/demo/canteen-crowd.mp4` | Pexels 6390319, 21 s, 1280x720 | a real canteen with dozens of students |
| `data/demo/seat-event.mp4` | Pexels 7264891, 30 s, 720x1280 | the only clip obtained that contains the actual event: empty chair -> somebody sits in it |

Zone files (all demo-only, all with `ghost_seconds: 25`):

* `data/cameras.demo.json` - four zones on the cafe table (2 occupied chairs, 2 empty)
* `data/cameras.canteen.json` - four zones on four seated students
* `data/cameras.seatevent.json` - one zone on the armchair
* `data/cameras.json` - **untouched**. The real webcam config still says 1200 s.

Reproduce any run with:

```bash
python run.py --camera data/demo/cafe-table.mp4 \
              --cameras data/cameras.demo.json \
              --record artifacts/phase1-cafe-seats.mp4 \
              --stats  artifacts/phase1-cafe-seats.json \
              --fps 2 --speed 1 --seconds 70 --warmup 4
```

## 3. Results

### 3.1 Output files

| artifact | length | what it shows |
| --- | --- | --- |
| `artifacts/phase1-cafe-seats.mp4` | 65.8 s (137 frames @ 2 fps) | 4 seat zones on a real table, 2 red/busy, 2 green/free, the whole way through |
| `artifacts/phase1-canteen-crowd.mp4` | 65.8 s (137 frames) | a crowded canteen, 4 marked students all busy |
| `artifacts/phase1-seat-event.mp4` | 37.9 s (79 frames) | an empty chair reading FREE, then BUSY when a man sits down |
| `artifacts/contact-sheet.jpg` | - | 4 frames from each run, side by side |
| `artifacts/dashboard-live.png` | - | dashboard showing 4 live camera seats |

Recorded with the `mp4v` codec: this build of OpenCV has no H.264 encoder, so files
are bigger than they need to be (9-13 MB per minute at 720p). Not a pipeline
problem, just an encoder missing on this machine.

### 3.2 Is it really YOLO, not pre-annotated footage?

Four independent checks:

1. The source clips were downloaded raw from Pexels and never edited. Compare
   `data/demo/cafe-table.mp4` (no boxes) with `artifacts/phase1-cafe-seats.mp4`.
2. Every rendered frame carries a HUD built from live data:
   `yolo11n.pt  conf 0.30  frame 768  people 5  chairs 7  bags 2` - the frame number
   is the *source* frame the detector just processed, and the counts change frame by
   frame.
3. `--stats` writes one JSON row per processed frame plus every seat transition,
   with the source frame number. Reproducible numbers, not pixels.
4. `/api/snapshot` reports `mode: "live-cv"`, `ready: true`, a rising `frames`
   counter and `live: true` on exactly the four camera seats. If the model were not
   running, `people` would be 0 and no seat would ever leave `free`.

### 3.3 Do detections land on the right seats?

Cafe clip, checked by eye on `artifacts/count-cafe.jpg`:

| seat | what is actually there | reported |
| --- | --- | --- |
| T1S1 | woman on the striped chair | busy |
| T1S2 | woman on the red armchair | busy |
| T1S3 | empty patterned chair | free |
| T1S4 | empty armchair at the right edge | free |

4 of 4 correct, and correct for the whole 66 s (no flicker: 146 frames, 0
BUSY->FREE->BUSY wobble). Canteen clip: 4 of 4 marked students busy, also stable.

One design note that matters: a seat zone is drawn on the *occupant's body*, not
around the whole chair. A whole-chair rectangle is only about 30% covered by a
person box, which is below the tracker's 0.45 threshold, so it would report FREE
while somebody is sitting there.

### 3.4 Do seat states change from real detections?

`artifacts/phase1-seat-event.mp4`, transitions from the stats file:

```
t= 0.04  T1S1  None -> free     (first real frame arrives)
t=19.7   T1S1  free -> busy     (his person box starts covering the seat)
t=31.7   T1S1  busy -> free     (clip loops back to the empty-chair frame)
```

Frame check at 12 s vs 22 s: green zone + empty chair, then red zone + seated man.

**Limitation, stated plainly:** the BUSY happens at 19.7 s, a few seconds *before*
he actually sits down, because the tracker only knows "a person box overlaps this
rectangle". It has no pose model, so a person standing in front of a chair counts as
occupied. That is the honest behaviour of the current core, and it is also why
leaning over a chair can show it as taken.

Neither restaurant clip contains a person standing up or leaving, so a BUSY -> FREE
transition could not be demonstrated from restaurant footage. The armchair clip is
the substitute, and it is called out as such.

### 3.5 Counting accuracy

Hand count vs model, same frame, from `artifacts/count-cafe.jpg` and
`artifacts/count-canteen.jpg`:

| clip | people actually visible | model says | verdict |
| --- | --- | --- | --- |
| cafe table | 4 | 5 | 1 duplicate box on the centre woman |
| canteen crowd | ~30 | 7 | heavy undercount (~23%) |

The canteen undercount is the big honest number. `yolo11n` at 640 px misses
background students who are small, blurry and half-hidden behind other students.
Chairs: 5-7 detected per canteen frame, 7 in the cafe frame, which roughly matches
what is visible. Bags/laptops: 2 detected in the cafe frame (the laptop on the table
and one handbag) - small items at distance are the least reliable class.

For the product this matters less than it sounds: seat states only depend on the
one person who is *at* a marked seat, and those were all correct. It does mean any
"how full is this room" number taken from raw person counts will be too low.

### 3.6 Speed

* inference: about 250 ms per frame at 640 px on this CPU -> about 4 frames/s
  ceiling; the demo runs at 2 fps to leave room for drawing and encoding.
* decode: 720p H.264 decodes at about 970 frames/s, so decoding is not the limit.
* a 25 fps camera therefore needs a GPU, a smaller `--imgsz`, or sampling at 2 fps
  (which is what we do - seats do not change faster than that).

### 3.7 Dashboard

`/api/snapshot` with the cafe clip running:

```
mode: live-cv
camera: ready=True frames=29 fps=2.2 people=5
venue main-canteen: camera_seats=4 live_seats=4
T1S1 busy (camera, live)   T1S2 busy (camera, live)
T1S3 free (camera, live)   T1S4 free (camera, live)
```

The venue screen renders "4 of 48 seats are measured by a live camera; no image
leaves the device", and the four seats carry the blue "seen by a camera" dot - so
camera-derived and simulated seats are visibly different in the UI
(`artifacts/dashboard-live.png`).

## 4. What is NOT proven

1. **Sit/stand on restaurant footage.** See 3.4.
2. **The 25 s ghost timer was never triggered end to end.** No clip we could get has
   a bag left alone on an empty seat, so no seat reached the abandoned state. The
   timer is configured in the demo files (`ghost_seconds: 25`) and the real config
   still has 1200 s, but the transition itself was not observed on video.
3. **Moving cameras.** Both the canteen and armchair clips drift 25-70 px during the
   clip, so a fixed zone slides across the chair a little. A real pan/tilt camera
   would need zone tracking; a bolted-down canteen camera does not.
4. **24/7 stability.** 66 s runs only. No long-run memory or drift test.
5. **Night/low light.** Untested.

## 5. Phase 1 scope

No admin UI, no upload screen, no frontend redesign. The only user-visible changes
are the annotations drawn into the recorded demo videos and two new demo zone files
that are not used unless you pass `--cameras`.

## 6. Files touched

| file | change |
| --- | --- |
| `cv/detection.py` | `detect()` also returns `item_names`; `annotate()` redrawn: chair boxes, per-class labels, seat-zone rectangles coloured by state, HUD |
| `cv/camera.py` | file source is paced to real time instead of being decoded as fast as the CPU allows; chairs/items/states published in each snapshot; optional `--stats` writer; warmup; HUD strings |
| `cv/engine.py` | accepts `cameras_path`; fixed `live_seats` (it was always 0) |
| `run.py` | `--fps --imgsz --seconds --warmup --cameras --stats`; per-camera output names; `--seconds` stop |
| `data/cameras.demo.json` | new, 4 zones on the cafe clip, ghost 25 s |
| `data/cameras.canteen.json` | new, 4 zones on the canteen clip, ghost 25 s |
| `data/cameras.seatevent.json` | new, 1 zone on the armchair clip, ghost 25 s |
| `tools/fetch_demo_clip.py` | new, reproducible download + re-encode + credits |
| `data/demo/*` | new, 3 clips + `CREDITS.md` |
