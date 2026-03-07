import urllib.request
import os

MODEL_URL  = "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task"
MODEL_PATH = "pose_landmarker.task"

if os.path.exists(MODEL_PATH):
    print(f"Model already exists at '{MODEL_PATH}' ✓")
else:
    print("Downloading pose model (~5MB)...")
    urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)
    print(f"Downloaded to '{MODEL_PATH}' ✓")

print("\nYou can now run:  py goal3_camera.py")