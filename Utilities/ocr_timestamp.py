# Reads the date/time burned into the top-right corner of a pond CCTV frame.
# Needs: pip install opencv-python pytesseract   (and the Tesseract program installed)
# Model weights: Models/Tesseract OCR/eng.traineddata
#
# Usage:
#   python ocr_timestamp.py frame.jpg
#   python ocr_timestamp.py clip.mp4            # reads frame 1 and the last frame
# In code:
#   from ocr_timestamp import read_timestamp
#   read_timestamp(image)  ->  {"date": "2026-09-16", "time": "11:07:55", "raw": "..."}

import os
import re
import sys
from collections import Counter
import cv2
import numpy as np
import pytesseract

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.path.normpath(os.path.join(HERE, "..", "Models", "Tesseract OCR"))
TESSERACT_EXE = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
if os.path.exists(TESSERACT_EXE):
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_EXE

# Where the stamp sits, as fractions of the frame (x0, y0, x1, y1). Same spot for both cameras.
# The date and the time are read separately: each is a short, simple line.
ROI_DATE = (0.752, 0.032, 0.884, 0.098)
ROI_TIME = (0.874, 0.032, 0.975, 0.098)


def crop_stamp(img, roi):
    h, w = img.shape[:2]
    x0, y0, x1, y1 = roi
    return img[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)]


def _prepare(crop, thresh=215, outline=True):
    """Binary image, dark text on white. The stamp is white with a thin dark outline, so with
    outline=True only white pixels that touch dark ones are kept (drops bright sky / water)."""
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    if thresh == 0:                                  # plain grayscale, no binarising
        big = cv2.resize(gray, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        return cv2.copyMakeBorder(cv2.bitwise_not(big), 12, 12, 12, 12, cv2.BORDER_CONSTANT, value=255)
    mask = (gray > thresh).astype(np.uint8)
    if outline:
        near_dark = cv2.dilate((gray < 80).astype(np.uint8), np.ones((5, 5), np.uint8))
        mask = cv2.morphologyEx(mask & near_dark, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    mask = cv2.resize(mask * 255, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    return cv2.copyMakeBorder(cv2.bitwise_not(mask), 12, 12, 12, 12, cv2.BORDER_CONSTANT, value=255)


def _digits(text):
    return re.sub(r"\D", "", text.replace("O", "0").replace("o", "0").replace("l", "1").replace("|", "1"))


def parse(text):
    """Pull the date and time out of OCR text as 14 digits YYYYMMDDHHMMSS (the gap between
    them is often lost). Returns {"date": "YYYY-MM-DD", "time": "HH:MM:SS"}, or '' if invalid."""
    m = re.search(r"(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])([01]\d|2[0-3])([0-5]\d)([0-5]\d)", _digits(text))
    if not m:
        return {"date": "", "time": ""}
    y, mo, d, h, mi, sec = m.groups()
    return {"date": f"{y}-{mo}-{d}", "time": f"{h}:{mi}:{sec}"}


VARIANTS = ((True, 215), (True, 235), (False, 245), (False, 225), (True, 200), (False, 0), (False, 180), (True, 170))   # (outline filter, threshold)
DATE_RE = re.compile(r"^(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])$")
TIME_RE = re.compile(r"^([01]\d|2[0-3])([0-5]\d)([0-5]\d)$")


def _read_part(img, roi, length, valid):
    """All digit strings of the right length read from one region of one frame."""
    os.environ["TESSDATA_PREFIX"] = MODEL_DIR
    config = "--oem 1 --psm 7 -c tessedit_char_whitelist=0123456789-:"
    crop = crop_stamp(img, roi)
    raws, ok = [], []
    for outline, thresh in VARIANTS:
        raw = pytesseract.image_to_string(_prepare(crop, thresh, outline), lang="eng", config=config).strip()
        raws.append(raw)
        d = _digits(raw)
        # a stray leading/trailing digit from the neighbouring field or the background: try every window
        for i in range(0, len(d) - length + 1):
            if valid.match(d[i:i + length]):
                ok.append(d[i:i + length])
                break
    return ok, raws


def read_timestamp(img_or_imgs):
    """One BGR frame, or a list of near-identical frames (their readings are voted together).
    Returns {"date", "time", "raw"}; date/time are '' when nothing valid was read."""
    imgs = img_or_imgs if isinstance(img_or_imgs, (list, tuple)) else [img_or_imgs]
    dates, times, raws = [], [], []
    for img in imgs:
        d, r1 = _read_part(img, ROI_DATE, 8, DATE_RE)
        t, r2 = _read_part(img, ROI_TIME, 6, TIME_RE)
        dates += d
        times += t
        raws.append(" | ".join(r1[:1] + r2[:1]))
    date = Counter(dates).most_common(1)[0][0] if dates else ""
    time = Counter(times).most_common(1)[0][0] if times else ""
    return {"date": f"{date[:4]}-{date[4:6]}-{date[6:]}" if date else "",
            "time": f"{time[:2]}:{time[2:4]}:{time[4:]}" if time else "",
            "raw": raws[0] if raws else ""}


def read_clip(path, samples=8):
    """Stamp of the first and the last frame of a video. Several frames next to each end are read
    and voted, because the stamp's seconds change only about once per 24 frames."""
    cap = cv2.VideoCapture(path)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    out = []
    for idxs in (range(0, samples * 2, 2), range(n - 1, n - 1 - samples * 2, -2)):
        frames = []
        for idx in idxs:
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, idx))
            ok, frame = cap.read()
            if ok:
                frames.append(frame)
        out.append(read_timestamp(frames))
    cap.release()
    # A clip is a few seconds long, so both ends share one date.
    date = out[0]["date"] or out[1]["date"]
    for o in out:
        o["date"] = o["date"] or date
    return {"start": out[0], "end": out[1]}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    p = sys.argv[1]
    if p.lower().endswith((".mp4", ".avi", ".mov", ".mkv")):
        print(read_clip(p))
    else:
        print(read_timestamp(cv2.imread(p)))
