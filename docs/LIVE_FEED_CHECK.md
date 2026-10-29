# Live camera check: what the detector actually does on a real feed

Tested **29 October 2026** against a public live camera, not a still image and not
the simulator. The same detector the app uses was pointed at the stream.

**Source:** [JazBaz Philippines — Bankerohan public live camera](https://www.youtube.com/watch?v=vp-pIE3FhDw)
(open-air eatery / canteen, tables and chairs in frame). The feed is a public
YouTube live stream owned by the channel that publishes it. It is a good stress
test: wide angle, night lighting, a real crowd, small people in the background.

**Window captured:** 110 seconds of live video, sampled whenever the HLS reader
delivered a frame (this VM's OpenCV HLS pacing gave ~0.2–0.3 fps, so 24 frames).
Nothing was replayed and no frame was staged.

**Model:** `yolo11n.pt` (COCO pre-trained, no fine-tuning) and `yolo11s.pt` for
comparison, confidence 0.30. `yolo11n` is the app default; no custom weights.

## Result: it works, but it badly undercounts a wide crowded room

I hand-counted six frames by eye (counting every visible person, including
staff and small background figures, ±3 because of the crowd). The model counts:

| Frame (live) | Hand count | yolo11n @640 | yolo11n @960 | yolo11s @640 | yolo11s @960 |
|---|---:|---:|---:|---:|---:|
| `raw_0004` | ~30 | 6 | 12 | 13 | 15 |
| `raw_0008` | ~28 | 4 | 9 | 14 | 13 |
| `raw_0012` | ~33 | 5 | 12 | 15 | 19 |
| `raw_0016` | ~31 | 11 | 15 | 15 | 19 |
| `raw_0020` | ~34 | 5 | 10 | 15 | 21 |
| `raw_0024` | ~30 | 5 | 11 | 16 | 21 |
| **mean** | **~31** | **6.0** | **11.5** | **14.7** | **18.0** |
| **mean absolute error** | | **25.0** | **19.5** | **16.3** | **13.0** |
| **approx. recall** | | **19%** | **37%** | **47%** | **58%** |

Boxes that were drawn were almost all real people (precision looked high), so the
failure is **missed people**, not invented people. The detections that exist are
believable; the count is just far too low.

The app's camera worker used to run at `imgsz=480`, which would be worse than the
640 column. It now defaults to 640.

## Why it fails here

* **Distance:** most diners occupy 20–60 pixels. COCO-trained YOLO needs more
  pixels per person than a wide night scene gives it.
* **Night + clutter:** dark clothing against dark tables, overlapping chairs,
  people behind each other and behind signage.
* **The job is different from the demo:** the app's honest demo is one table
  viewed from above. This camera is a whole food street. One camera cannot count
  a hall like this at `yolo11n` resolution.

At table-view distance (the phone-above-the-table setup the app documents),
people are large in frame and detection is much more reliable; the wide-camera
failure above is a camera-placement and model-size problem, not proof the whole
pipeline is broken.

## What was proven end to end

With the live stream attached to the real app:

```
python run.py --camera "<live HLS url>"
```

`/api/snapshot` reported `"mode": "live-cv"`, camera `ready: true`, 12 frames
processed, **3 people in frame**, no errors. The detector is genuinely driving
the front end from the public stream.

The seat map stayed green because `data/cameras.json` marks four zones around a
single demo table, and this wide camera does not point at that table. Seat-level
occupancy accuracy therefore **was not** validated on this stream; only person
counting was.

## What to do about it

1. Put the camera above the seats, not across the room.
2. Run `yolo11s.pt` (`CAMPUSPULSE_MODEL=yolo11s.pt`) and `imgsz=960` for wide
   views; the table above shows the gain.
3. Use tiled inference (split the frame into overlapping crops) for a whole hall.
4. Fine-tune on a few hundred labelled frames from the actual canteen camera; a
   COCO model has never seen this room.
5. For seats specifically, judge **zone occupancy** (is a person box inside this
   chair's zone) rather than counting the whole frame.

## Files

| File | What it is |
|---|---|
| `artifacts/live-canteen-yolo11n.mp4` | 24 held frames with yolo11n boxes and live count |
| `artifacts/live-canteen-yolo11s.mp4` | the same frames with yolo11s boxes |
| `artifacts/live-score.json` | per-frame counts for each model/resolution |
| `artifacts/live-raw-bank/` | the 24 clean frames used for the hand count |
| `artifacts/annotated-sample.jpg` | one frame with every box drawn |

These are test artifacts, not app data. The app itself still never writes or
serves a frame.

## Reproduce

```bash
pip install -r requirements.txt yt-dlp
URL=$(yt-dlp -g -f "bv*[height<=720][protocol^=m3u8]" \
      "https://www.youtube.com/watch?v=vp-pIE3FhDw" | head -1)
python tools/live_feed_check.py --url "$URL" --seconds 110 --fps 1 \
    --sample-every 2 --save-all --raw-dir artifacts/live-raw-bank
python tools/score_live_frames.py --frames artifacts/live-raw-bank \
    --models yolo11n.pt yolo11s.pt --imgsz 640 960
```

`ffmpeg`'s static build crashed inside this particular VM, so the capture tool
reads the HLS with OpenCV. That is why the capture rate is ~0.2–0.3 fps even
though the model itself takes about 130 ms per 640-pixel frame. On a normal
machine with a working ffmpeg, capture runs at full rate.
