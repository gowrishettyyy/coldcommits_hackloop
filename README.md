# Smart Campus Seat & Queue Tracker
built by Apeksha, Gowri and Dharini

Cameras + YOLO find free seats, ghost seats (bag, no person), best group seats, and queue wait time.
We store only counts, never faces or footage.

## Run it
1. Install Python 3.11, then in this folder: `pip install -r requirements.txt`
2. Put a short video here named `sample.mp4`
3. `python make_seats.py sample.mp4 0`  (creates seats.json)
4. `streamlit run app.py`

## Files
- detection.py - YOLO finds people, chairs, bags (Person 1)
- make_seats.py - builds seats.json from chairs (Person 1)
- logic.py - seat states, ghost seats, group finder, queue wait (Person 2)
- pipeline.py - joins everything
- app.py - the website (Person 3)
- config.json - settings (ghost_seconds is 5 for demo; 1200 = 20 min in real use)
