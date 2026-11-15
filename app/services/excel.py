"""Excel/CSV templates and parsing. Column names here are the agreed upload format
(also documented in docs/INTEGRATION.md). Headers are matched case-insensitively and both
English and Bangla header spellings are accepted."""
import csv
import io

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

from app.core.errors import bad_request

QUESTION_COLUMNS = ["external_id", "language", "question", "option_a", "option_b", "option_c", "option_d",
                    "answer", "source", "figure_dependent"]
BOARD_COLUMNS = ["board", "year", "language", "question", "option_a", "option_b", "option_c", "option_d",
                 "answer", "chapter_id"]

ALIASES = {
    "id": "external_id", "item id": "external_id", "question id": "external_id",
    "lang": "language", "ভাষা": "language",
    "stem": "question", "প্রশ্ন": "question",
    "a": "option_a", "option a": "option_a", "ক": "option_a",
    "b": "option_b", "option b": "option_b", "খ": "option_b",
    "c": "option_c", "option c": "option_c", "গ": "option_c",
    "d": "option_d", "option d": "option_d", "ঘ": "option_d",
    "correct answer": "answer", "উত্তর": "answer",
    "figure dependent": "figure_dependent", "chapter id": "chapter_id", "বোর্ড": "board", "সাল": "year",
}
BN_LETTER = {"ক": "A", "খ": "B", "গ": "C", "ঘ": "D"}


def _key(h) -> str:
    h = str(h or "").strip().lower().replace("-", " ").replace("_", " ")
    return ALIASES.get(h, h.replace(" ", "_"))


def make_template(kind: str) -> bytes:
    cols = QUESTION_COLUMNS if kind == "questions" else BOARD_COLUMNS
    wb = Workbook()
    ws = wb.active
    ws.title = "Questions" if kind == "questions" else "BoardBank"
    ws.append(cols)
    for c in ws[1]:
        c.font = Font(bold=True)
        c.fill = PatternFill("solid", fgColor="DDDDDD")
    if kind == "questions":
        ws.append(["Q-001", "bn", "নিচের কোনটি ভেক্টর রাশি?", "ভর", "দ্রুতি", "সরণ", "কাজ", "C", "Teacher made", "no"])
        ws.append(["Q-002", "en", "Which of the following is a vector quantity?", "Mass", "Speed", "Displacement",
                   "Work", "C", "", "no"])
    else:
        ws.append(["Dhaka", 2024, "bn", "[board question text]", "[A]", "[B]", "[C]", "[D]", "A", "1"])
    for i, w in enumerate([12, 10, 60, 18, 18, 18, 18, 9, 18, 14][: len(cols)]):
        ws.column_dimensions[chr(65 + i)].width = w
    lang_col = "B" if kind == "questions" else "C"
    dv = DataValidation(type="list", formula1='"bn,en"', allow_blank=False)
    ws.add_data_validation(dv)
    dv.add(f"{lang_col}2:{lang_col}5001")
    help_ws = wb.create_sheet("Help")
    for line in [
        ["Column", "Rule"],
        ["language", "bn or en"],
        ["question", "required, the MCQ stem"],
        ["option_a..option_d", "all four required"],
        ["answer", "A/B/C/D (or ক/খ/গ/ঘ); optional for tagging"],
        ["figure_dependent", "yes/no"],
        ["Max rows", "5000 per file"],
    ]:
        help_ws.append(line)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _norm_answer(v):
    if v is None:
        return None
    s = str(v).strip()
    s = BN_LETTER.get(s, s.upper())
    return s or None


def read_rows(filename: str, data: bytes, max_rows: int) -> list[dict]:
    """Returns list of dicts with normalised keys + '_row' (1-based Excel row number)."""
    name = filename.lower()
    rows: list[list] = []
    if name.endswith(".csv"):
        text = data.decode("utf-8-sig", errors="replace")
        rows = list(csv.reader(io.StringIO(text)))
    elif name.endswith((".xlsx", ".xlsm")):
        try:
            wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        except Exception:
            raise bad_request("BAD_FILE", "Could not read the Excel file")
        ws = wb.worksheets[0]
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
    else:
        raise bad_request("BAD_FILE_TYPE", "Upload a .xlsx or .csv file")
    if not rows:
        raise bad_request("EMPTY_FILE", "The file has no rows")
    header = [_key(h) for h in rows[0]]
    if "question" not in header:
        raise bad_request("BAD_HEADER", "Header row must contain a 'question' column (download the template)")
    out = []
    for i, r in enumerate(rows[1:], start=2):
        if not r or all(v in (None, "") for v in r):
            continue
        d = {header[j]: (r[j] if j < len(r) else None) for j in range(len(header))}
        d["_row"] = i
        out.append(d)
    if len(out) > max_rows:
        raise bad_request("TOO_MANY_ROWS", f"Max {max_rows} rows per file, got {len(out)}")
    return out


def row_to_item(d: dict) -> dict:
    def s(k):
        v = d.get(k)
        return None if v is None else str(v).strip()
    return {
        "external_id": s("external_id"), "language": (s("language") or "").lower(), "stem": s("question") or "",
        "options": {"A": s("option_a") or "", "B": s("option_b") or "", "C": s("option_c") or "",
                    "D": s("option_d") or ""},
        "answer": _norm_answer(d.get("answer")), "source": s("source"),
        "figure_dependent": str(d.get("figure_dependent") or "").strip().lower() in ("yes", "y", "true", "1", "হ্যাঁ"),
    }


def results_workbook(rows: list[dict]) -> bytes:
    cols = ["row", "external_id", "question_id", "status", "chapter_id", "concept_id", "topic_id",
            "cognitive_level", "difficulty", "min_confidence", "board_match", "error_code", "error_message"]
    wb = Workbook()
    ws = wb.active
    ws.title = "Results"
    ws.append(cols)
    for c in ws[1]:
        c.font = Font(bold=True)
    for r in rows:
        ws.append([r.get(c) for c in cols])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
