"""PERSON 3 owns this file.
The website. Run:  streamlit run app.py"""
import streamlit as st

from pipeline import load_all, run

st.set_page_config(page_title="Smart Seat Tracker", layout="wide")
st.title("Smart Campus Seat & Queue Tracker")
st.caption("Runs on the device. We store only counts - never faces or footage.")

config, seats = load_all()
video = st.sidebar.text_input("Video file", config["video"])
group_size = st.sidebar.slider("We need seats together for:", 1, 8, 4)
st.sidebar.write("Colours: green = free, red = busy, orange = held (bag, nobody), yellow = probably free, blue outline = best group")

start = st.button("▶ Start live demo")

c1, c2, c3, c4 = st.columns(4)
p_free, p_busy, p_total, p_wait = c1.empty(), c2.empty(), c3.empty(), c4.empty()
msg = st.empty()
left, right = st.columns(2)
cam_box, plan_box = left.empty(), right.empty()

if start:
    bar = st.progress(0, text="Starting... the first frame takes a few seconds")
    for r in run(video, config, seats, group_size):
        counts = r["counts"]
        p_free.metric("Free seats", counts["free"] + counts["probably_free"])
        p_busy.metric("Busy seats", counts["busy"])
        p_total.metric("Total seats", r["total"])
        p_wait.metric("Queue wait (min)", r["wait"], help=f'{r["queue"]} people in queue')
        if r["group"]:
            msg.success(f'Best {group_size} seats together: {", ".join(r["group"])}')
        else:
            msg.warning(f"No {group_size} free seats together right now")
        cam_box.image(r["frame"][:, :, ::-1], caption="Camera view (detections)")
        plan_box.image(r["plan"][:, :, ::-1], caption="Live seat map")
        bar.progress(0, text=f'Video time: {r["time"]:.0f}s')
    bar.empty()
    st.success("Video finished.")
