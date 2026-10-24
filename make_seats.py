"""PERSON 1 owns this file.
Makes seats.json by treating every chair YOLO finds in ONE frame as a seat.
Run:  python make_seats.py sample.mp4 0
(the 0 is the frame number - pick a frame where chairs are easy to see)
Then open seats_preview.jpg and check the boxes. You can edit seats.json by hand."""
import json
import sys

import cv2

from detection import detect

video = sys.argv[1]
frame_no = int(sys.argv[2]) if len(sys.argv) > 2 else 0

cap = cv2.VideoCapture(video)
cap.set(cv2.CAP_PROP_POS_FRAMES, frame_no)
ok, frame = cap.read()
if not ok:
    print("Could not read that video/frame. Check the file name.")
    sys.exit(1)

chairs = detect(frame, conf=0.15)["chairs"]
chairs.sort(key=lambda b: (b[0] + b[2]) / 2)  # left to right

seats = [{"id": f"S{i}", "box": [int(v) for v in b]} for i, b in enumerate(chairs, 1)]
with open("seats.json", "w") as f:
    json.dump(seats, f, indent=2)

for s in seats:
    x1, y1, x2, y2 = s["box"]
    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
    cv2.putText(frame, s["id"], (x1, max(15, y1 - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
cv2.imwrite("seats_preview.jpg", frame)
print(f"Found {len(seats)} seats. Saved seats.json and seats_preview.jpg")
