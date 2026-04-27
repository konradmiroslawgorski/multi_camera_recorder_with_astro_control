import cv2
import os
import sys
import subprocess

if len(sys.argv) < 2:
    print("Podaj plik video")
    sys.exit(1)

VIDEO = sys.argv[1]

cap = cv2.VideoCapture(VIDEO)

if not cap.isOpened():
    print("Nie można otworzyć pliku")
    sys.exit(1)

fps = cap.get(cv2.CAP_PROP_FPS)
total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
duration = total_frames / fps

START_PERCENT = float(sys.argv[2]) if len(sys.argv) > 2 else 0
DURATION_PERCENT = float(sys.argv[3]) if len(sys.argv) > 3 else 100

start_time = duration * START_PERCENT / 100
end_time = start_time + duration * DURATION_PERCENT / 100

print(f"FPS: {fps}")
print(f"Czas trwania: {duration:.1f}s")

cap.set(cv2.CAP_PROP_POS_MSEC, start_time * 1000)
frame_id = int(start_time * fps)


# -----------------------------
# Parametry do strojenia
# -----------------------------

AREA_THRESHOLD = 1500
FRAME_SKIP = 1
DIFF_THRESHOLD = 100
GROUP_GAP = 1
MIN_DURATION = 0

# ---- FILTR WIATRU ----
WIND_FILTER = True
MAX_GLOBAL_AREA = 5000     # jeśli suma ruchu > to uznaj za wiatr
MAX_CONTOURS = 500            # jeśli za dużo obiektów jednocześnie → wiatr
# -----------------------------

ret, prev = cap.read()
if not ret:
    print("Błąd pierwszej klatki")
    sys.exit(1)




prev_gray = cv2.cvtColor(prev, cv2.COLOR_BGR2GRAY)
#mamy tak niska jakość, że blurr kamery wystarcza
#prev_gray = cv2.GaussianBlur(prev_gray, (11, 11), 0)

motion_events = []
frame_id = 1
current_motion_start = None

while True:
    ret, frame = cap.read()
    if not ret:
        break

    current_time = frame_id / fps
    if current_time > end_time:
        break


    if frame_id % FRAME_SKIP != 0:
        frame_id += 1
        continue

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
 #   gray = cv2.GaussianBlur(gray, (11, 11), 0)

    frame_diff = cv2.absdiff(prev_gray, gray)
    #print("max diff:", frame_diff.max())
    thresh = cv2.threshold(frame_diff, DIFF_THRESHOLD, 255, cv2.THRESH_BINARY)[1]
    thresh = cv2.dilate(thresh, None, iterations=2)

    contours, _ = cv2.findContours(
        thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )

    total_area = 0
    large_contours = 0

    for c in contours:
        area = cv2.contourArea(c)
        total_area += area
        if area > AREA_THRESHOLD:
            large_contours += 1
    #print(f"large_contours = {large_contours}")
    #print(f"total_area = {total_area}")
     # ---- LOGIKA DETEKCJI ----
    if WIND_FILTER:
        if total_area < DIFF_THRESHOLD:
            motion_detected = False
            #print(f"motion_detected = False 0")
        elif total_area > MAX_GLOBAL_AREA:
            motion_detected = False
            #print(f"motion_detected = False 1")
        elif large_contours > MAX_CONTOURS:
            motion_detected = False
            #print(f"motion_detected = False 2")
        else:
            motion_detected = large_contours > 0
    else:
        motion_detected = large_contours > 0

    timestamp = frame_id / fps
    if current_motion_start:
    	z =  timestamp - current_motion_start
    else:
        z = 99999
    #print(f"motion_detected = {motion_detected}")
    if motion_detected:
        #if large_contours > 0:
            # print(f"md        large_contours = {large_contours}")
            # print(f"md        total_area = {total_area}")
        print(f"Motion detected at {timestamp}\t:\t{z}\t{large_contours}\t:{total_area}")
        if current_motion_start is None:
            current_motion_start = timestamp
    else:
        if current_motion_start is not None:
           #print(f"timestamp - current_motion_start: {z}")
           print(f"{timestamp}\t:\t{z}\t{large_contours}\t:{total_area}")
        if current_motion_start is not None:
            print(f"{timestamp}\t:\t{z}\t{large_contours}\t:{total_area}")
            if timestamp - current_motion_start >= MIN_DURATION:
                percent = (current_motion_start / duration) * 100
                motion_events.append(current_motion_start)
                print(f"Ruch przy {percent:.2f}% ({current_motion_start:.1f}s)")
                print(f"	large_contours = {large_contours}")
                print(f"	total_area = {total_area}")
            current_motion_start = None
        #else:
            #if large_contours > 0:
                #print(f"        large_contours = {large_contours}")
                #print(f"        total_area = {total_area}")


    prev_gray = gray
    frame_id += 1

cap.release()

# domknięcie segmentu jeśli trwał do końca pliku
if current_motion_start is not None:
    if duration - current_motion_start >= MIN_DURATION:
        percent = (current_motion_start / duration) * 100
        motion_events.append(current_motion_start)
        print(f"Ruch przy {percent:.2f}% ({current_motion_start:.1f}s)")

# -----------------------------
# Grupowanie zdarzeń
# -----------------------------

segments = []

if motion_events:
    current_start = motion_events[0]

    for t in motion_events[1:]:
        if t - current_start > GROUP_GAP:
            segments.append(current_start)
            current_start = t

    segments.append(current_start)

print("\nSegmenty końcowe:")
for s in segments:
    print(f"{s:.1f}s")

# -----------------------------
# Wycinanie klipów
# -----------------------------

BASE_DIR = os.path.dirname(VIDEO)
FOUND_DIR = os.path.join(BASE_DIR, "Znaleziono")

os.makedirs(FOUND_DIR, exist_ok=True)

for i, s in enumerate(segments):
    start = max(s - 5, 0)

    clip_name = os.path.join(
        FOUND_DIR,
        f"clip_{i+1:02d}_{int(s)}s.mp4"
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-ss", str(start),
        "-i", VIDEO,
        "-t", "10",
        "-c", "copy",
        clip_name
    ]

    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

print("Gotowe.")
