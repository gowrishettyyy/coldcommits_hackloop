"""
Fetch the Phase 1 demonstration clips and turn them into small local files.

The repo ships no video, and a demo that streams from the internet is not
reproducible on a hackathon laptop with bad wifi. So this script downloads two
Pexels clips ONCE and re-encodes them to 720p with ffmpeg (or OpenCV if ffmpeg
is missing). The results are ordinary files that cv/camera.py can read exactly
like a webcam.

    python tools/fetch_demo_clip.py                # both clips
    python tools/fetch_demo_clip.py --only cafe    # just one

Writes:
  data/demo/cafe-table.mp4      static camera on one table + chairs (12 s, 1280x720)
  data/demo/canteen-crowd.mp4   busy school canteen (21 s, 1280x720)
  data/demo/seat-event.mp4      one empty chair, then somebody sits in it (30 s, 720x1280)
  data/demo/CREDITS.md          sources and licences

Three clips, three jobs:
  * cafe-table has a genuinely fixed camera, so seat zones marked on its chairs
    stay on those chairs for the whole clip. It is the clip for verifying that a
    person is mapped to the right seat.
  * canteen-crowd is a real canteen with dozens of students. Everyone stays in
    their seat for the whole clip, so it turned out to be the crowd/accuracy
    case, not the transition case.
  * seat-event is the only clip we could get that contains the actual event: an
    empty chair, then a person sits down in it. It is shot vertically and the
    camera pans, which the report calls out.

Nothing here touches the detection code: this only produces input files.
"""
import argparse
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO = os.path.join(ROOT, "data", "demo")

CLIPS = {
    "cafe": {
        "out": "cafe-table.mp4",
        "url": "https://videos.pexels.com/video-files/6024569/6024569-hd_1920_1080_25fps.mp4",
        "title": "Young women with face masks using tablet in coffee shop",
        "page": "https://www.pexels.com/video/young-women-with-face-masks-using-tablet-in-coffee-shop-6024569/",
        "author": "Pavel Danilyuk (Pexels)",
        "why": "fixed tripod camera on a table with four chairs; people are large in "
               "frame, so seat zones can be checked against what is visibly there",
    },
    "seat": {
        "out": "seat-event.mp4",
        "url": "https://videos.pexels.com/video-files/7264891/7264891-hd_1080_1920_25fps.mp4",
        "title": "Blind guy taking a braille book and sitting in a armchair",
        "page": "https://www.pexels.com/video/blind-guy-taking-a-braille-book-and-sitting-in-a-armchair-7264891/",
        "author": "Thirdman (Pexels)",
        "why": "the only clip we could get that contains the actual event: an empty "
               "chair, then a person sits down in it. Used to prove a seat goes "
               "FREE -> BUSY from real detections. Shot vertically on a phone and the "
               "camera pans, which is called out in the report",
    },
    "canteen": {
        "out": "canteen-crowd.mp4",
        "url": "https://videos.pexels.com/video-files/6390319/6390319-hd_1920_1080_25fps.mp4",
        "title": "Students eating at a cafeteria",
        "page": "https://www.pexels.com/video/students-eating-at-a-cafeteria-6390319/",
        "author": "Phillips Vinegar (Pexels)",
        "why": "a real canteen with dozens of students: people stand up, walk and sit "
               "down, so seat states genuinely change",
    },
}

CREDITS = """# Demo clip credits

These files are re-encodes of Pexels stock clips, used only as camera input for
the CampusPulse demo. They are RAW FOOTAGE: nothing is pre-annotated, and no
detection result is stored in the video. Every box you see in a rendered demo
video was drawn live by yolo11n.pt while that video was playing.

{entries}
## Licence

Pexels Licence (https://www.pexels.com/license/): free to use, attribution not
required, no redistribution as stock footage.

## Why 720p

CPU inference costs about 250 ms per frame at 640 px, and the file in the repo
should stay a few MB. 1280x720 is the same size `data/cameras.json` was marked
at, so zones line up one-to-one.

Regenerate with `python tools/fetch_demo_clip.py`.
"""


def download(url, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    })
    with urllib.request.urlopen(req, timeout=120) as resp, open(path, "wb") as fh:
        total = 0
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            fh.write(chunk)
            total += len(chunk)
    return total


def ffmpeg_exe():
    """H.264 keeps the clip a few MB instead of the ~38 MB OpenCV writes.
    Falls back to None when no ffmpeg binary is available."""
    import shutil

    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def transcode_ffmpeg(exe, src, dst, width=1280):
    import subprocess

    # fit the clip inside width x width so a VERTICAL phone clip becomes
    # 720x1280 instead of being upscaled to a 2276 px tall monster
    subprocess.run([exe, "-y", "-loglevel", "error", "-i", src,
                    "-vf", f"scale={width}:{width}:force_original_aspect_ratio=decrease",
                    "-c:v", "libx264", "-crf", "26",
                    "-preset", "veryfast", "-pix_fmt", "yuv420p",
                    "-movflags", "+faststart", "-an", dst], check=True)


def transcode(src, dst, width=1280):
    exe = ffmpeg_exe()
    if exe:
        transcode_ffmpeg(exe, src, dst, width)
    else:
        print("[clip] no ffmpeg found, falling back to OpenCV (bigger file)")
        import cv2

        cap = cv2.VideoCapture(src)
        if not cap.isOpened():
            raise SystemExit(f"could not open {src}")
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        in_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        in_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        scale = min(width / float(in_w), width / float(in_h))
        width = int(round(in_w * scale / 2.0)) * 2
        height = int(round(in_h * scale / 2.0)) * 2
        writer = cv2.VideoWriter(dst, cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
        if not writer.isOpened():
            raise SystemExit("could not open the output writer (mp4v)")
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            writer.write(cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA))
        cap.release()
        writer.release()

    import cv2

    cap = cv2.VideoCapture(dst)
    frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or width)
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    cap.release()
    return frames, fps, w, h


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=sorted(CLIPS), default=None)
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--keep-source", action="store_true")
    args = ap.parse_args()

    wanted = [args.only] if args.only else sorted(CLIPS)
    for key in wanted:
        clip = CLIPS[key]
        dst = os.path.join(DEMO, clip["out"])
        raw = os.path.join(DEMO, f"_{key}-source.mp4")

        print(f"\n[clip] {key}: {clip['title']}")
        print(f"[clip]   {clip['url']}")
        got = download(clip["url"], raw)
        print(f"[clip]   downloaded {got / 1e6:.1f} MB")

        frames, fps, w, h = transcode(raw, dst, args.width)
        if not args.keep_source:
            os.remove(raw)
        print(f"[clip]   wrote {os.path.relpath(dst, ROOT)}  {frames} frames  "
              f"{w}x{h} @ {fps:.2f} fps  {os.path.getsize(dst) / 1e6:.2f} MB  "
              f"({frames / max(1.0, fps):.1f}s)")

    entries = "\n".join(
        f"## `{CLIPS[k]['out']}`\n\n"
        f"- Title: {CLIPS[k]['title']}\n"
        f"- Author: {CLIPS[k]['author']}\n"
        f"- Source: {CLIPS[k]['page']}\n"
        f"- Used for: {CLIPS[k]['why']}\n"
        for k in sorted(CLIPS)
    )
    with open(os.path.join(DEMO, "CREDITS.md"), "w", encoding="utf-8") as fh:
        fh.write(CREDITS.format(entries=entries))
    print("\n[clip] credits -> data/demo/CREDITS.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
