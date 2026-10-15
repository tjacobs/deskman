#!/usr/bin/env python3
# Benchmark face detectors on a recorded video where a face is always in view.

import argparse
import glob
import os
import time

import cv2

# Where recordings live, and the model files the detectors load
ROBOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECORDINGS_PATTERN = os.path.join(ROBOT_DIR, "recordings", "*.mp4")
YUNET_MODEL = os.path.join(ROBOT_DIR, "models", "face_detection_yunet_2023mar.onnx")
CASCADE_DIR = "/usr/share/opencv4/haarcascades"
DEFAULT_CASCADE = "haarcascade_frontalface_default.xml"
ALT_CASCADE = "haarcascade_frontalface_alt2.xml"

# Frame widths to test, the robot detects on a frame this wide today
DEFAULT_WIDTHS = "640,960,1280,1920"
REFERENCE_WIDTH = 1920

# Test every Nth frame so the run finishes quickly, and print progress this often
DEFAULT_STRIDE = 3
PROGRESS_GAP = 25

# The robot's Haar settings today, sized for a frame this wide
ROBOT_WIDTH = 640
ROBOT_BLUR_SIZE = 5
ROBOT_MIN_FACE = 60
ROBOT_MAX_FACE = 300
HAAR_SCALE_FACTOR = 1.1
HAAR_MIN_NEIGHBORS = 3
PLAIN_MIN_FACE = 20

# Neural detector settings, and the score the reference needs to be sure of a face
YUNET_SCORE = 0.6
YUNET_NMS = 0.3
YUNET_TOP_K = 50
REFERENCE_SCORE = 0.8

# How much a box must overlap the reference to count as the same face
MATCH_IOU = 0.3

# Where annotated sample frames go, and how many
DEFAULT_SAMPLE_DIR = "/tmp/face_bench"
SAMPLE_COUNT = 6
SAMPLE_WIDTH = 960
REFERENCE_COLOR = (0, 255, 0)
BOX_THICKNESS = 3


# Run every detector at every width and print the results
def main():
    args = parse_args()
    widths = [int(width) for width in args.widths.split(",")]
    print("Video: " + args.video, flush=True)

    # Find where the face is, as seen by the best detector at full size
    reference = find_reference(args.video, args.stride, args.save)
    found = sum(1 for boxes in reference if boxes)
    print("Frames tested: %d, reference found a face in %d" % (len(reference), found))

    # Time each detector on each width
    results = []
    for width in widths:
        results += benchmark_width(args.video, args.stride, width, reference)
    print_results(results)


# Read the command line
def parse_args():
    parser = argparse.ArgumentParser(description="Benchmark face detectors on a recorded video")
    parser.add_argument("--video", default=find_latest_video(), help="Video to test, the newest recording by default")
    parser.add_argument("--widths", default=DEFAULT_WIDTHS, help="Comma separated frame widths to test")
    parser.add_argument("--stride", type=int, default=DEFAULT_STRIDE, help="Test every Nth frame")
    parser.add_argument("--save", default=DEFAULT_SAMPLE_DIR, help="Folder for annotated reference frames")
    return parser.parse_args()


# Pick the newest recording
def find_latest_video():
    videos = sorted(glob.glob(RECORDINGS_PATTERN), key=os.path.getmtime)
    return videos[-1] if videos else ""


# Detect at full size with the neural detector, the boxes are fractions of the frame
def find_reference(video, stride, save):
    detector = cv2.FaceDetectorYN.create(YUNET_MODEL, "", (REFERENCE_WIDTH, REFERENCE_WIDTH), REFERENCE_SCORE, YUNET_NMS, YUNET_TOP_K)
    capture = cv2.VideoCapture(video)
    tested = int(capture.get(cv2.CAP_PROP_FRAME_COUNT)) // stride
    capture.release()
    sample_gap = max(1, tested // SAMPLE_COUNT)
    os.makedirs(save, exist_ok=True)

    # Detect on every frame, and save a few with the boxes drawn so the truth can be checked by eye
    reference = []
    for index, frame in enumerate(read_frames(video, stride, REFERENCE_WIDTH)):
        boxes = detect_yunet(detector, frame)
        reference.append(boxes)
        if index % PROGRESS_GAP == 0:
            print("Reference frame %d of %d" % (index, tested), flush=True)
        if index % sample_gap == 0:
            save_reference_sample(frame, boxes, os.path.join(save, "reference_%03d.jpg" % index))
    return reference


# Yield every Nth frame resized to a width
def read_frames(video, stride, width):
    capture = cv2.VideoCapture(video)
    index = 0
    while True:
        success, frame = capture.read()
        if not success:
            break

        # Skip frames between tests, then shrink the rest
        if index % stride == 0:
            height = int(frame.shape[0] * width / frame.shape[1])
            yield cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
        index += 1
    capture.release()


# Run the neural detector, and return boxes as fractions of the frame
def detect_yunet(detector, frame):
    height, width = frame.shape[:2]
    detector.setInputSize((width, height))
    _, faces = detector.detect(frame)
    if faces is None:
        return []
    return [(face[0] / width, face[1] / height, face[2] / width, face[3] / height) for face in faces]


# Save one frame with the reference boxes drawn on it
def save_reference_sample(frame, boxes, path):
    height, width = frame.shape[:2]
    for x, y, box_width, box_height in boxes:
        cv2.rectangle(frame, (int(x * width), int(y * height)), (int((x + box_width) * width), int((y + box_height) * height)), REFERENCE_COLOR, BOX_THICKNESS)
    cv2.imwrite(path, cv2.resize(frame, (SAMPLE_WIDTH, int(height * SAMPLE_WIDTH / width))))


# Run every detector over the frames at one width
def benchmark_width(video, stride, width, reference):
    detectors = build_detectors(width)
    print("Testing width %d" % width, flush=True)
    totals = {name: {"hits": 0, "extra": 0, "frames": 0, "wall": 0.0, "cpu": 0.0} for name, _ in detectors}
    for index, frame in enumerate(read_frames(video, stride, width)):
        if index % PROGRESS_GAP == 0:
            print("  width %d, frame %d of %d" % (width, index, len(reference)), flush=True)
        for name, detect in detectors:
            wall_start = time.perf_counter()
            cpu_start = time.process_time()
            boxes = detect(frame)
            total = totals[name]
            total["wall"] += time.perf_counter() - wall_start
            total["cpu"] += time.process_time() - cpu_start

            # Score the boxes against the reference
            hits, extra = score_boxes(boxes, reference[index])
            total["hits"] += hits
            total["extra"] += extra
            total["frames"] += 1
    return [summarize(name, width, total) for name, total in totals.items()]


# Build each detector for a frame width, each returns boxes as fractions of the frame
def build_detectors(width):
    # Scale the pixel sizes so a face is judged at the same real size on every width
    scale = width / ROBOT_WIDTH
    default_cascade = cv2.CascadeClassifier(os.path.join(CASCADE_DIR, DEFAULT_CASCADE))
    alt_cascade = cv2.CascadeClassifier(os.path.join(CASCADE_DIR, ALT_CASCADE))
    yunet = cv2.FaceDetectorYN.create(YUNET_MODEL, "", (width, width), YUNET_SCORE, YUNET_NMS, YUNET_TOP_K)
    robot_min = int(ROBOT_MIN_FACE * scale)
    robot_max = int(ROBOT_MAX_FACE * scale)
    plain_min = int(PLAIN_MIN_FACE * scale)
    return [
        ("haar_robot", lambda frame: detect_haar(default_cascade, frame, True, robot_min, robot_max)),
        ("haar_plain", lambda frame: detect_haar(default_cascade, frame, False, plain_min, 0)),
        ("haar_alt2", lambda frame: detect_haar(alt_cascade, frame, False, plain_min, 0)),
        ("yunet", lambda frame: detect_yunet(yunet, frame)),
    ]


# Run a Haar cascade, with or without the blur and equalize the robot does first
def detect_haar(cascade, frame, prepare, min_face, max_face):
    height, width = frame.shape[:2]
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    if prepare:
        gray = cv2.GaussianBlur(gray, (ROBOT_BLUR_SIZE, ROBOT_BLUR_SIZE), 0)
        gray = cv2.equalizeHist(gray)

    # A max of zero means no upper limit
    max_size = (max_face, max_face) if max_face else (0, 0)
    faces = cascade.detectMultiScale(gray, HAAR_SCALE_FACTOR, HAAR_MIN_NEIGHBORS, cv2.CASCADE_SCALE_IMAGE, (min_face, min_face), max_size)
    return [(x / width, y / height, box_width / width, box_height / height) for x, y, box_width, box_height in faces]


# Count a frame as a hit when a box matches a reference face, and the other boxes as extra
def score_boxes(boxes, reference_boxes):
    matched = [any(overlap(box, truth) >= MATCH_IOU for truth in reference_boxes) for box in boxes]
    hits = 1 if any(matched) else 0
    extra = len(matched) - sum(matched)
    return hits, extra


# Intersection over union of two boxes
def overlap(first, second):
    left = max(first[0], second[0])
    top = max(first[1], second[1])
    right = min(first[0] + first[2], second[0] + second[2])
    bottom = min(first[1] + first[3], second[1] + second[3])
    inside = max(0, right - left) * max(0, bottom - top)
    union = first[2] * first[3] + second[2] * second[3] - inside
    return inside / union if union else 0


# Turn the totals into one result row
def summarize(name, width, total):
    frames = total["frames"]
    return {
        "name": name,
        "width": width,
        "found": 100.0 * total["hits"] / frames,
        "extra": total["extra"],
        "wall": 1000.0 * total["wall"] / frames,
        "cpu": 1000.0 * total["cpu"] / frames,
    }


# Print one row per detector and width
def print_results(results):
    print("")
    print("%-12s %6s %8s %7s %9s %9s" % ("detector", "width", "found %", "extra", "wall ms", "cpu ms"))
    for row in results:
        print("%-12s %6d %8.1f %7d %9.1f %9.1f" % (row["name"], row["width"], row["found"], row["extra"], row["wall"], row["cpu"]))


if __name__ == "__main__":
    main()
