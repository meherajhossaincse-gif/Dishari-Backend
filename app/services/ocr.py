"""Read MCQs from a photo or PDF of a question paper (Bangla AND English), free and offline.

Pipeline: image -> upscale + contrast -> split two-column layouts -> Tesseract (ben+eng)
          -> parse stems and options (ক/খ/গ/ঘ or a/b/c/d or A/B/C/D) -> questions for the teacher to check.
PDF: use the text layer when it has one, otherwise render pages (pdftoppm) and OCR them.
OCR is never perfect, so the result is a DRAFT: the teacher edits it before tagging.
"""
import io
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter, ImageOps

from app.core.errors import ApiError, bad_request

BN_RANGE = re.compile(r"[ঀ-৿]")
BN_LETTERS = {"ক": "A", "খ": "B", "গ": "C", "ঘ": "D"}
# common OCR misreads of the Bangla option letters inside brackets
BN_ALIASES = {"কে": "ক", "খে": "খ", "গে": "গ", "ঘে": "ঘ", "পে": "গ", "ছে": "ঘ", "চ": "ঘ", "থ": "খ", "গো": "গ", "=": "ক"}
OPT_RE = re.compile(
    r"(?:^|(?<=\s))[\(\[\{]\s*(কে|খে|গে|ঘে|পে|ছে|গো|ক|খ|গ|ঘ|চ|থ|[a-dA-D])\s*[\)\]\}]"   # (ক) (খ) (a) [B]
    r"|(?:^|(?<=\s))(কে|খে|গে|ঘে|পে|ছে|ক|খ|গ|ঘ|[a-dA-D])\s*[\)\.](?=\s|$)"                # ক) কে) a. B)
    r"|(?:^|(?<=\s))([@©€®])\s*\)?"                                                         # filled bubble (answer mark)
)
NUM_PREFIX = re.compile(r"^\s*[\|।৷/\\]*\s*(?:Q\.?\s*)?[০-৯0-9]{1,3}\s*[\.।৷\):\-]\s*")
STATEMENT = re.compile(r"^\s*(i{1,3}|iv|[১২৩])\s*[\.\)]", re.I)


def _tesseract() -> str:
    cmd = os.environ.get("TESSERACT_CMD") or shutil.which("tesseract")
    if not cmd:
        raise ApiError(503, "OCR_NOT_AVAILABLE", "OCR engine (tesseract) is not installed on the server")
    return cmd


def _prepare(img: Image.Image) -> list[Image.Image]:
    im = ImageOps.exif_transpose(img).convert("L")
    scale = max(1.0, 2400 / max(im.size))
    if scale > 1:
        im = im.resize((int(im.width * scale), int(im.height * scale)), Image.LANCZOS)
    im = ImageOps.autocontrast(im, cutoff=2).filter(ImageFilter.SHARPEN)
    a = np.asarray(im)
    ink = (a < 140).sum(axis=0).astype(float)
    w = a.shape[1]
    lo, hi = int(w * 0.38), int(w * 0.62)
    sm = np.convolve(ink[lo:hi], np.ones(25) / 25, mode="same")
    x = lo + int(sm.argmin())
    if sm.min() < 0.25 * max(1.0, float(np.median(ink))):  # clear gutter -> two columns
        return [im.crop((0, 0, x, im.height)), im.crop((x, 0, w, im.height))]
    return [im]


def _ocr(im: Image.Image, lang: str) -> str:
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "in.png"
        im.save(src)
        r = subprocess.run([_tesseract(), str(src), "stdout", "-l", lang, "--psm", "6"],
                           capture_output=True, timeout=120)
        if r.returncode != 0:
            raise ApiError(500, "OCR_FAILED", r.stderr.decode(errors="ignore")[-300:] or "OCR failed")
        return r.stdout.decode("utf-8", errors="ignore")


def _langs(pref: str) -> str:
    return {"bn": "ben+eng", "en": "eng"}.get(pref, "ben+eng")


def pdf_to_images_or_text(data: bytes) -> tuple[list[Image.Image], str]:
    try:
        from pypdf import PdfReader
        text = "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages[:20])
    except Exception:
        text = ""
    if len(re.sub(r"\s", "", text)) > 80:  # has a real text layer
        return [], text
    if not shutil.which("pdftoppm"):
        raise ApiError(503, "OCR_NOT_AVAILABLE", "PDF rendering (poppler) is not installed on the server")
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "in.pdf").write_bytes(data)
        subprocess.run(["pdftoppm", "-r", "200", "-l", "10", "-png", str(Path(d) / "in.pdf"), str(Path(d) / "p")],
                       check=True, timeout=180)
        return [Image.open(p).copy() for p in sorted(Path(d).glob("p*.png"))], ""


def _clean(s: str) -> str:
    s = re.sub(r"[|¦_~`]+", " ", s)
    return re.sub(r"\s+", " ", s).strip(" .,:;-—")


def _letter(tok: str, seq: int) -> str:
    tok = BN_ALIASES.get(tok, tok)
    if tok in BN_LETTERS:
        return BN_LETTERS[tok]
    if tok.upper() in "ABCD" and len(tok) == 1:
        return tok.upper()
    return "ABCD"[seq] if seq < 4 else "?"  # bubble symbol: take the next free letter


def parse_mcqs(text: str) -> list[dict]:
    """Turn OCR text into [{stem, options{A-D}, answer, language, warnings}]."""
    qs: list[dict] = []
    cur: dict | None = None
    stem_buf: list[str] = []

    def finish():
        nonlocal cur
        if cur:
            stem = _clean(" ".join(cur.pop("_stem")))
            if len(stem) > 70 and "?" in stem:  # drop header/instruction text OCR glued in front
                head, q = stem.rsplit("?", 1)[0], stem.rsplit("?", 1)[1]
                parts = re.split(r"[।\]\|]|(?<=[a-z])\.\s", head)
                stem = _clean(parts[-1] + "?" + q) if len(parts[-1]) > 8 else stem
            cur["stem"] = stem
            opts = {k: _clean(v) for k, v in cur["options"].items()}
            cur["options"] = {k: opts.get(k, "") for k in "ABCD"}
            w = []
            if sum(1 for v in cur["options"].values() if v) < 4:
                w.append("missing_options")
            if len(cur["stem"]) < 6:
                w.append("short_stem")
            cur["warnings"] = w
            cur["language"] = "bn" if len(BN_RANGE.findall(cur["stem"] + " ".join(opts.values()))) > 3 else "en"
            if cur["stem"] or any(cur["options"].values()):
                qs.append(cur)
        cur = None

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        matches = list(OPT_RE.finditer(line))
        if not matches:
            # a new stem starts when we already have options, or a numbered/question line appears
            if cur and len(cur["options"]) >= 2:
                finish()
            line = NUM_PREFIX.sub("", line)
            if cur:
                if STATEMENT.match(line) or not cur["options"]:
                    cur["_stem"].append(line)
                elif cur["_last"]:
                    cur["options"][cur["_last"]] += " " + line
            else:
                stem_buf.append(line)
                if line.endswith(("?", "—", "-", ":", "?।")) or re.search(r"(কোনটি|কত|কী|কি|কাকে|কেন|which|what|how|why|who)\b", line, re.I):
                    cur = {"_stem": stem_buf[-3:] if len(stem_buf) > 1 and not stem_buf[-2].endswith(("?", "।")) else stem_buf[-1:],
                           "options": {}, "answer": None, "_last": None, "_seq": 0}
                    stem_buf = []
            continue
        if cur is None:
            cur = {"_stem": stem_buf[-2:], "options": {}, "answer": None, "_last": None, "_seq": 0}
            stem_buf = []
        before = line[: matches[0].start()].strip()
        if before and not cur["options"]:
            cur["_stem"].append(NUM_PREFIX.sub("", before))
        for i, m in enumerate(matches):
            tok = next(g for g in m.groups() if g)
            seq = cur["_seq"]
            letter = _letter(tok, seq)
            if letter in cur["options"] and letter != "?":  # same letter again -> next question started
                finish()
                cur = {"_stem": [], "options": {}, "answer": None, "_last": None, "_seq": 0}
                seq, letter = 0, _letter(tok, 0)
            if tok in "@©€®":
                cur["answer"] = letter
            end = matches[i + 1].start() if i + 1 < len(matches) else len(line)
            cur["options"][letter] = line[m.end():end]
            cur["_last"] = letter
            cur["_seq"] = "ABCD".index(letter) + 1 if letter in "ABCD" else seq + 1
            if len(cur["options"]) == 4 and i == len(matches) - 1:
                finish()
    finish()
    for q in qs:
        q.pop("_last", None)
        q.pop("_seq", None)
    return [q for q in qs if q["stem"] and sum(1 for v in q["options"].values() if v) >= 2]


def extract(filename: str, data: bytes, lang: str = "auto") -> dict:
    name = filename.lower()
    pages: list[Image.Image] = []
    text = ""
    if name.endswith(".pdf"):
        pages, text = pdf_to_images_or_text(data)
    else:
        try:
            pages = [Image.open(io.BytesIO(data))]
            pages[0].load()
        except Exception:
            raise bad_request("BAD_FILE", "Upload a photo (JPG/PNG) or a PDF of the question paper")
    if pages:
        parts = []
        for pg in pages[:10]:
            for col in _prepare(pg):
                parts.append(_ocr(col, _langs(lang)))
        text = "\n".join(parts)
    questions = parse_mcqs(text)
    return {"engine": "tesseract", "pages": max(1, len(pages)), "text": text, "questions": questions,
            "count": len(questions), "needs_check": sum(1 for q in questions if q["warnings"])}
