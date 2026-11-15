"""Dishari OMR answer sheet: print it, scan/photograph filled sheets, read the bubbles.

One layout definition (millimetres on A4) is used both to DRAW the sheet and to READ it, so they always match.
Reading: four black corner squares -> perspective correction -> sample every bubble -> darkest filled one wins.
Works with phone photos (tilted, uneven light) and flatbed scans. No paid service, no internet.
"""
import base64
import html
import math
import secrets
from pathlib import Path

import cv2
import numpy as np

from app.core.config import settings
from app.core.errors import bad_request

PX = 10  # canonical resolution: 10 px per mm
W_MM, H_MM = 210, 297
MARK = 8  # corner square size (mm)
MARK_POS = [(10, 10), (192, 10), (10, 279), (192, 279)]  # top-left of each square: TL, TR, BL, BR
MARK_CENTERS = [(x + MARK / 2, y + MARK / 2) for x, y in MARK_POS]

SET_Y, SET_X0, SET_DX = 82, 34, 11       # set bubbles ক খ গ ঘ
ROLL_X0, ROLL_DX, ROLL_Y0, ROLL_DY, ROLL_DIGITS = 122, 9, 37, 5.6, 6
ANS_Y0, ANS_DY, PER_COL = 108, 6.9, 25   # answer grid
ANS_COL_X0, ANS_COL_DX = 20, 45          # column origin and width
ANS_B_DX0, ANS_B_DX = 13, 7.6            # first bubble offset inside a column, spacing
R_BUBBLE = 2.5
MAX_Q = 100
LETTERS = "ABCD"
BN = {"A": "ক", "B": "খ", "C": "গ", "D": "ঘ"}
BN_DIG = "০১২৩৪৫৬৭৮৯"


def bubble_positions(n_questions: int) -> dict:
    """All bubble centres in mm."""
    pos = {"set": [(SET_X0 + i * SET_DX, SET_Y) for i in range(4)],
           "roll": [[(ROLL_X0 + c * ROLL_DX, ROLL_Y0 + r * ROLL_DY) for r in range(10)] for c in range(ROLL_DIGITS)],
           "answers": []}
    for q in range(min(n_questions, MAX_Q)):
        col, row = divmod(q, PER_COL)
        x0 = ANS_COL_X0 + col * ANS_COL_DX
        y = ANS_Y0 + row * ANS_DY
        pos["answers"].append([(x0 + ANS_B_DX0 + j * ANS_B_DX, y) for j in range(4)])
    return pos


# ------------------------------------------------------------------ drawing (printable HTML, exact mm)
def sheet_html(n_questions: int, lang: str, header: dict, set_code: str | None = None, embed: bool = False) -> str:
    if n_questions < 1:
        raise bad_request("EMPTY_PAPER", "Add questions to the paper first")
    if n_questions > MAX_Q:
        raise bad_request("TOO_MANY_QUESTIONS", f"OMR sheet supports up to {MAX_Q} questions")
    e = html.escape
    bn = lang == "bn"
    d = (lambda v: "".join(BN_DIG[int(c)] if c.isdigit() else c for c in str(v))) if bn else str
    lab = (lambda k: BN[k]) if bn else (lambda k: k)
    pos = bubble_positions(n_questions)
    el = []

    def bubble(x, y, text, filled=False):
        el.append(f'<div class="b{" f" if filled else ""}" style="left:{x - R_BUBBLE}mm;top:{y - R_BUBBLE}mm">{text}</div>')

    for x, y in MARK_POS:
        el.append(f'<div class="m" style="left:{x}mm;top:{y}mm"></div>')
    # header
    T = (lambda en, b: b if bn else en)
    el.append(f'''<div class="hd" style="left:22mm;top:20mm;width:92mm">
      <div class="t1">{e(header.get("institution") or "")}</div>
      <div class="t2">{T("OMR answer sheet", "ওএমআর উত্তরপত্র")} · {e(header.get("exam_name") or "")}</div>
      <div>{T("Subject", "বিষয়")}: <b>{e(header.get("subject_name") or "")}</b> &nbsp; {T("Code", "কোড")}: <b>{e(d(header.get("subject_code") or ""))}</b></div>
      <div class="line">{T("Name", "নাম")}:</div>
      <div class="line">{T("Class", "শ্রেণি")}: {e(d(header.get("class_level") or ""))} &nbsp; {T("Section", "শাখা")}:</div>
      <div class="line">{T("Signature", "স্বাক্ষর")}:</div>
    </div>''')
    el.append(f'<div class="lbl" style="left:22mm;top:{SET_Y - 8}mm">{T("Set (fill one)", "সেট কোড (একটি ভরাট কর)")}</div>')
    for i, (x, y) in enumerate(pos["set"]):
        bubble(x, y, lab(LETTERS[i]), filled=False)
    if set_code:
        el.append(f'<div class="lbl" style="left:80mm;top:{SET_Y - 2.5}mm">{T("This paper", "এই প্রশ্নপত্র")}: <b>{e(set_code)}</b></div>')
    # roll grid
    el.append(f'<div class="lbl" style="left:{ROLL_X0 - 4}mm;top:20mm">{T("Roll number (write, then fill)", "রোল নম্বর (লিখে বৃত্ত ভরাট কর)")}</div>')
    for c in range(ROLL_DIGITS):
        el.append(f'<div class="wbox" style="left:{ROLL_X0 + c * ROLL_DX - 3.5}mm;top:26mm"></div>')
        for r in range(10):
            x, y = pos["roll"][c][r]
            bubble(x, y, d(r))
    el.append(f'<div class="frame" style="left:{ROLL_X0 - 6}mm;top:18mm;width:{ROLL_DIGITS * ROLL_DX + 3}mm;height:{ROLL_Y0 + 9 * ROLL_DY + 5 - 18}mm"></div>')
    # instructions
    el.append(f'<div class="ins" style="left:22mm;top:{ANS_Y0 - 13}mm;width:166mm">{T("Fill one circle completely with a black ball-point pen. Do not fold or mark the four black corner squares.", "কালো বল পয়েন্ট কলম দিয়ে একটি বৃত্ত সম্পূর্ণ ভরাট কর। কাগজ ভাঁজ করবে না, চার কোণের কালো বর্গে দাগ দেবে না।")}</div>')
    # answers
    for q, row in enumerate(pos["answers"]):
        x0, y = row[0][0] - ANS_B_DX0, row[0][1]
        el.append(f'<div class="qn" style="left:{x0}mm;top:{y - 2.2}mm">{d(q + 1)}</div>')
        for j, (x, yy) in enumerate(row):
            bubble(x, yy, lab(LETTERS[j]))
    cols = math.ceil(len(pos["answers"]) / PER_COL)
    for c in range(1, cols):
        el.append(f'<div class="vr" style="left:{ANS_COL_X0 + c * ANS_COL_DX - 3}mm;top:{ANS_Y0 - 4}mm;height:{PER_COL * ANS_DY + 1}mm"></div>')
    bar = "" if embed else '<div class="bar"><button onclick="print()">Print</button></div>'
    return f"""<!doctype html><html lang="{lang}"><head><meta charset="utf-8"><title>OMR</title>
<link href="https://fonts.googleapis.com/css2?family=Hind+Siliguri:wght@400;600;700&display=swap" rel="stylesheet">
<style>
@page {{ size: A4; margin: 0; }}
* {{ box-sizing: border-box; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
html {{ background: {'#fff' if embed else '#8a8a8a'}; {'overflow:hidden;' if embed else ''} }}
body {{ margin: 0; font-family: 'Hind Siliguri','Nirmala UI',sans-serif; color: #000; }}
.page {{ position: relative; width: 210mm; height: 297mm; margin: {'0' if embed else '10mm'} auto; background: #fff; overflow: hidden; box-shadow: {'none' if embed else '0 4px 24px rgba(0,0,0,.35)'}; }}
.page > div {{ position: absolute; }}
.m {{ width: {MARK}mm; height: {MARK}mm; background: #000; }}
.b {{ width: {2 * R_BUBBLE}mm; height: {2 * R_BUBBLE}mm; border: .35mm solid #7a0f1e; border-radius: 50%; font-size: 2.6mm; line-height: {2 * R_BUBBLE - .7}mm; text-align: center; color: #c0838c; font-weight: 600; }}
.b.f {{ background: #000; }}
.qn {{ width: 9mm; text-align: right; font-weight: 700; font-size: 3.2mm; }}
.hd {{ font-size: 3.3mm; line-height: 1.55; }}
.hd .t1 {{ font-size: 4.6mm; font-weight: 700; }} .hd .t2 {{ font-size: 3.8mm; font-weight: 600; }}
.hd .line {{ border-bottom: .2mm solid #555; margin-top: 1.2mm; }}
.lbl {{ font-size: 3mm; font-weight: 600; }}
.wbox {{ width: 7mm; height: 7mm; border: .3mm solid #000; }}
.frame {{ border: .3mm solid #7a0f1e; border-radius: 2mm; }}
.ins {{ font-size: 2.9mm; border: .3mm solid #000; padding: 1.2mm 2mm; }}
.vr {{ width: 0; border-left: .25mm dashed #999; }}
.bar {{ position: fixed; top: 10px; right: 14px; }} .bar button {{ font: 600 14px sans-serif; padding: 8px 16px; border-radius: 8px; border: 0; background: #7A0F1E; color: #fff; cursor: pointer; }}
@media print {{ html {{ background: #fff; }} .bar {{ display: none; }} .page {{ margin: 0; box-shadow: none; }} }}
</style></head><body>{bar}<div class="page">{''.join(el)}</div></body></html>"""


# ------------------------------------------------------------------ reading
def _find_marks(gray: np.ndarray) -> np.ndarray:
    h, w = gray.shape
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    th = cv2.adaptiveThreshold(blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 51, 15)
    cnts, _ = cv2.findContours(th, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)  # LIST: a dark table around the page must not hide the squares
    cands = []
    page_area = h * w
    for c in cnts:
        a = cv2.contourArea(c)
        if a < page_area * 0.00015 or a > page_area * 0.01:
            continue
        x, y, bw, bh = cv2.boundingRect(c)
        if not (0.7 < bw / bh < 1.4):
            continue
        hull = cv2.contourArea(cv2.convexHull(c))
        (rw, rh) = cv2.minAreaRect(c)[1]  # rotated box: a tilted photo still looks square
        if hull == 0 or a / hull < 0.9 or not rw or a / (rw * rh) < 0.85:
            continue
        m = np.zeros_like(gray)
        cv2.drawContours(m, [c], -1, 255, -1)
        if cv2.mean(blur, mask=cv2.erode(m, np.ones((3, 3), np.uint8)))[0] > 110:  # must be solid black, not an outline box
            continue
        cands.append((x + bw / 2, y + bh / 2, a))
    if len(cands) < 4:
        raise bad_request("OMR_MARKS_NOT_FOUND", "Could not find the 4 black corner squares. Photograph the whole sheet, flat, with all corners visible.")
    # corner squares (8 mm) are the biggest solid squares; bubbles (5 mm) are far smaller
    amax = max(p[2] for p in cands)
    cands = [p for p in cands if p[2] >= amax * 0.45]
    if len(cands) < 4:
        raise bad_request("OMR_MARKS_NOT_FOUND", "Could not find the 4 black corner squares. Photograph the whole sheet, flat, with all corners visible.")
    # extremes work for a rotated / tilted photo too
    tl = min(cands, key=lambda p: p[0] + p[1]); br = max(cands, key=lambda p: p[0] + p[1])
    tr = max(cands, key=lambda p: p[0] - p[1]); bl = min(cands, key=lambda p: p[0] - p[1])
    pick = [tl[:2], tr[:2], bl[:2], br[:2]]
    if len({tuple(map(int, p)) for p in pick}) < 4:
        raise bad_request("OMR_MARKS_NOT_FOUND", "Corner squares are unclear. Retake the photo with the full sheet visible.")
    return np.array(pick, dtype=np.float32)


def _warp(img: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    scale = 2000 / max(gray.shape)
    if scale < 1:
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    src = _find_marks(gray)
    dst = np.array([(x * PX, y * PX) for x, y in MARK_CENTERS], dtype=np.float32)
    M = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(gray, M, (W_MM * PX, H_MM * PX), flags=cv2.INTER_LINEAR, borderValue=255)


def _fill(binv: np.ndarray, x_mm: float, y_mm: float) -> float:
    cx, cy, r = int(x_mm * PX), int(y_mm * PX), int(R_BUBBLE * PX * 0.72)
    y0, y1, x0, x1 = max(cy - r, 0), cy + r + 1, max(cx - r, 0), cx + r + 1
    patch = binv[y0:y1, x0:x1]
    yy, xx = np.ogrid[y0:y1, x0:x1]
    mask = (yy - cy) ** 2 + (xx - cx) ** 2 <= r * r
    return float(patch[mask].mean() / 255) if mask.any() else 0.0


def _choose(vals: list[float], hi=0.42, gap=0.18):
    """Index of the one filled bubble, or ('blank'|'multiple')."""
    order = sorted(range(len(vals)), key=lambda i: -vals[i])
    top, second = vals[order[0]], vals[order[1]] if len(vals) > 1 else 0
    if top < hi:
        return "blank"
    if second >= hi and second > top - gap:
        return "multiple"
    return order[0]


def read_sheet(data: bytes, n_questions: int) -> dict:
    arr = np.frombuffer(data, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise bad_request("BAD_IMAGE", "Upload a JPG or PNG photo/scan of the OMR sheet")
    warped = _warp(img)
    # local contrast so pencil-grey / shadowed marks still count
    binv = cv2.adaptiveThreshold(warped, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 61, 18)
    pos = bubble_positions(n_questions)
    flags = []
    sv = [_fill(binv, x, y) for x, y in pos["set"]]
    s = _choose(sv)
    set_letter = LETTERS[s] if isinstance(s, int) else None
    if not isinstance(s, int):
        flags.append(f"set_{s}")
    roll = ""
    for c, col in enumerate(pos["roll"]):
        v = _choose([_fill(binv, x, y) for x, y in col])
        if isinstance(v, int):
            roll += str(v)
        elif v == "multiple":
            roll += "?"
            flags.append(f"roll_digit_{c + 1}_multiple")
    roll = roll.lstrip("0") or ("0" if roll else "")
    answers, conf = {}, []
    for q, row in enumerate(pos["answers"], 1):
        vals = [_fill(binv, x, y) for x, y in row]
        v = _choose(vals)
        answers[q] = LETTERS[v] if isinstance(v, int) else None
        if v == "multiple":
            flags.append(f"q{q}_multiple")
        conf.append(max(vals))
    return {"set": set_letter, "roll": roll or None, "answers": answers, "flags": flags, "warped": warped}


def overlay_png(warped: np.ndarray, n_questions: int, answers: dict, key: dict, set_letter, roll) -> bytes:
    """Annotated sheet: green ring = correct mark, red = wrong mark, blue ring = right answer the student missed."""
    vis = cv2.cvtColor(warped, cv2.COLOR_GRAY2BGR)
    pos = bubble_positions(n_questions)
    r = int(R_BUBBLE * PX * 1.25)
    for q, row in enumerate(pos["answers"], 1):
        a, k = answers.get(q), key.get(q)
        for j, (x, y) in enumerate(row):
            L = LETTERS[j]
            c = (int(x * PX), int(y * PX))
            if a == L:
                cv2.circle(vis, c, r, (40, 160, 40) if a == k else (40, 40, 220), 5)
            elif k == L and a != k:
                cv2.circle(vis, c, r, (220, 120, 30), 3)
    if set_letter:
        x, y = pos["set"][LETTERS.index(set_letter)]
        cv2.circle(vis, (int(x * PX), int(y * PX)), r, (30, 140, 230), 5)
    small = cv2.resize(vis, (1050, 1485), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 82])
    return buf.tobytes()


def save_overlay(jpg: bytes) -> str:
    d = Path(settings.STORAGE_DIR) / "media" / "omr"
    d.mkdir(parents=True, exist_ok=True)
    name = secrets.token_hex(12) + ".jpg"
    (d / name).write_bytes(jpg)
    return f"/media/omr/{name}"


def render_filled_sheet_for_test(n_questions: int, set_idx: int, roll: str, answers: dict) -> np.ndarray:
    """Synthetic filled sheet (used by tests to check the reader end-to-end)."""
    img = np.full((H_MM * PX, W_MM * PX), 255, np.uint8)
    for x, y in MARK_POS:
        cv2.rectangle(img, (x * PX, y * PX), ((x + MARK) * PX, (y + MARK) * PX), 0, -1)
    pos = bubble_positions(n_questions)
    rr = int(R_BUBBLE * PX)

    def ring(x, y):
        cv2.circle(img, (int(x * PX), int(y * PX)), rr, 120, 3)

    def fill(x, y):
        cv2.circle(img, (int(x * PX), int(y * PX)), rr - 2, 15, -1)
    for p in pos["set"]:
        ring(*p)
    for col in pos["roll"]:
        for p in col:
            ring(*p)
    for row in pos["answers"]:
        for p in row:
            ring(*p)
    fill(*pos["set"][set_idx])
    for c, ch in enumerate(roll.rjust(ROLL_DIGITS, "0")):
        fill(*pos["roll"][c][int(ch)])
    for q, a in answers.items():
        if a:
            fill(*pos["answers"][q - 1][LETTERS.index(a)])
    return img


def data_url(jpg: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(jpg).decode()
