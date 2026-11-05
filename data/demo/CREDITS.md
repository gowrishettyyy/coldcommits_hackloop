# Demo clip credits

These files are re-encodes of Pexels stock clips, used only as camera input for
the CampusPulse demo. They are RAW FOOTAGE: nothing is pre-annotated, and no
detection result is stored in the video. Every box you see in a rendered demo
video was drawn live by yolo11n.pt while that video was playing.

## `cafe-table.mp4`

- Title: Young women with face masks using tablet in coffee shop
- Author: Pavel Danilyuk (Pexels)
- Source: https://www.pexels.com/video/young-women-with-face-masks-using-tablet-in-coffee-shop-6024569/
- Used for: fixed tripod camera on a table with four chairs; people are large in frame, so seat zones can be checked against what is visibly there

## `canteen-crowd.mp4`

- Title: Students eating at a cafeteria
- Author: Phillips Vinegar (Pexels)
- Source: https://www.pexels.com/video/students-eating-at-a-cafeteria-6390319/
- Used for: a real canteen with dozens of students: people stand up, walk and sit down, so seat states genuinely change

## `seat-event.mp4`

- Title: Blind guy taking a braille book and sitting in a armchair
- Author: Thirdman (Pexels)
- Source: https://www.pexels.com/video/blind-guy-taking-a-braille-book-and-sitting-in-a-armchair-7264891/
- Used for: the only clip we could get that contains the actual event: an empty chair, then a person sits down in it. Used to prove a seat goes FREE -> BUSY from real detections. Shot vertically on a phone and the camera pans, which is called out in the report


## Licence

Pexels Licence (https://www.pexels.com/license/): free to use, attribution not
required, no redistribution as stock footage.

## Why 720p

CPU inference costs about 250 ms per frame at 640 px, and the file in the repo
should stay a few MB. 1280x720 is the same size `data/cameras.json` was marked
at, so zones line up one-to-one.

Regenerate with `python tools/fetch_demo_clip.py`.
