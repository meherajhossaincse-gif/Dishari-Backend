"""Printable question paper: Word (.docx) and a print-ready HTML page (browser 'Save as PDF').

Why HTML for PDF: Bangla needs proper text shaping (যুক্তবর্ণ). Python PDF libraries break it; browsers and
Word render it correctly. So PDF = open the HTML and print / Save as PDF."""
import base64
import html
import io

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

from app.services.media import image_path

BN_DIGITS = str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯")
OPT_LABEL = {"bn": {"A": "ক", "B": "খ", "C": "গ", "D": "ঘ"}, "en": {"A": "A", "B": "B", "C": "C", "D": "D"}}
TXT = {
    "bn": {"time": "সময়", "min": "মিনিট", "marks": "পূর্ণমান", "key": "উত্তরমালা",
           "note": "[সঠিক উত্তরের বৃত্ত ভরাট করো। প্রতিটি প্রশ্নের মান সমান।]"},
    "en": {"time": "Time", "min": "minutes", "marks": "Full marks", "key": "Answer key",
           "note": "[Choose the correct answer. All questions carry equal marks.]"},
}
BANGLA_FONT = "Nirmala UI"  # ships with Windows 10/11; Hind Siliguri/SolaimanLipi also fine


def num(n, lang):
    s = str(int(n)) if float(n).is_integer() else str(n)
    return s.translate(BN_DIGITS) if lang == "bn" else s


def _font(run, size=None, bold=False):
    run.font.name = BANGLA_FONT
    rpr = run._element.get_or_add_rPr()
    fonts = rpr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = rpr.makeelement(qn("w:rFonts"), {})
        rpr.append(fonts)
    for a in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        fonts.set(qn(a), BANGLA_FONT)
    if size:
        run.font.size = Pt(size)
        szcs = rpr.makeelement(qn("w:szCs"), {qn("w:val"): str(int(size * 2))})
        rpr.append(szcs)
    run.bold = bold
    if bold:
        rpr.append(rpr.makeelement(qn("w:bCs"), {}))


def _p(doc, text="", size=11, bold=False, align=None, space_after=2):
    p = doc.add_paragraph()
    if text:
        _font(p.add_run(text), size, bold)
    if align:
        p.alignment = align
    p.paragraph_format.space_after = Pt(space_after)
    p.paragraph_format.space_before = Pt(0)
    return p


def to_docx(paper, items, lang, institution, include_key) -> bytes:
    t = TXT[lang]
    doc = Document()
    sec = doc.sections[0]
    sec.page_height, sec.page_width = Cm(29.7), Cm(21.0)
    for side in ("left_margin", "right_margin"):
        setattr(sec, side, Cm(1.8))
    sec.top_margin = sec.bottom_margin = Cm(1.5)
    full = sum(i["marks"] for i in items)
    if institution:
        _p(doc, institution, 14, True, WD_ALIGN_PARAGRAPH.CENTER)
    _p(doc, paper.title, 13, True, WD_ALIGN_PARAGRAPH.CENTER)
    info = []
    if paper.duration_min:
        info.append(f'{t["time"]}: {num(paper.duration_min, lang)} {t["min"]}')
    info.append(f'{t["marks"]}: {num(full, lang)}')
    _p(doc, "    ·    ".join(info), 11, False, WD_ALIGN_PARAGRAPH.CENTER)
    _p(doc, t["note"], 10, False, WD_ALIGN_PARAGRAPH.CENTER, 8)

    lab = OPT_LABEL[lang]
    for n, it in enumerate(items, 1):
        q = it["question"]
        p = _p(doc, "", 11, space_after=2)
        _font(p.add_run(f"{num(n, lang)}. "), 11, True)
        _font(p.add_run(q.stem), 11)
        p.paragraph_format.keep_with_next = True
        img = image_path(q.image_path)
        if img:
            doc.add_picture(str(img), width=Cm(6))
        tbl = doc.add_table(rows=2, cols=2)
        for k, (r, c) in zip("ABCD", [(0, 0), (0, 1), (1, 0), (1, 1)]):
            cell = tbl.cell(r, c)
            cp = cell.paragraphs[0]
            cp.paragraph_format.space_after = Pt(0)
            _font(cp.add_run(f'({lab[k]}) {q.options.get(k, "")}'), 10.5)
        _p(doc, "", 4, space_after=4)

    if include_key:
        doc.add_section(WD_SECTION.NEW_PAGE)
        _p(doc, f'{t["key"]} — {paper.title}', 12, True, WD_ALIGN_PARAGRAPH.CENTER, 6)
        keys = [f'{num(n, lang)}. {lab.get(it["question"].answer or "", "–")}' for n, it in enumerate(items, 1)]
        for row in range(0, len(keys), 10):
            _p(doc, "     ".join(keys[row:row + 10]), 11)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def to_html(paper, items, lang, institution, include_key) -> str:
    t = TXT[lang]
    lab = OPT_LABEL[lang]
    e = html.escape
    full = sum(i["marks"] for i in items)
    info = []
    if paper.duration_min:
        info.append(f'{t["time"]}: {num(paper.duration_min, lang)} {t["min"]}')
    info.append(f'{t["marks"]}: {num(full, lang)}')
    qs = []
    for n, it in enumerate(items, 1):
        q = it["question"]
        img = ""
        p = image_path(q.image_path)
        if p:
            mime = "image/png" if p.suffix == ".png" else "image/jpeg"
            img = f'<img src="data:{mime};base64,{base64.b64encode(p.read_bytes()).decode()}" alt="">'
        opts = "".join(f'<div class="o">({lab[k]}) {e(q.options.get(k, ""))}</div>' for k in "ABCD")
        qs.append(f'<div class="q"><div class="s"><b>{num(n, lang)}.</b> {e(q.stem)}</div>{img}'
                  f'<div class="opts">{opts}</div></div>')
    key = ""
    if include_key:
        cells = "".join(f'<span>{num(n, lang)}. {lab.get(it["question"].answer or "", "–")}</span>'
                        for n, it in enumerate(items, 1))
        key = f'<section class="key"><h2>{t["key"]}</h2><div class="kg">{cells}</div></section>'
    head = f'<div class="inst">{e(institution)}</div>' if institution else ""
    return f"""<!doctype html><html lang="{lang}"><head><meta charset="utf-8"><title>{e(paper.title)}</title>
<link href="https://fonts.googleapis.com/css2?family=Hind+Siliguri:wght@400;600&display=swap" rel="stylesheet">
<style>
@page {{ size: A4; margin: 15mm 16mm; }}
body {{ font-family: 'Hind Siliguri', 'Nirmala UI', 'SolaimanLipi', sans-serif; color:#000; background:#fff;
       font-size: 11.5pt; line-height: 1.45; max-width: 180mm; margin: 0 auto; padding: 10mm 0; }}
header {{ text-align:center; border-bottom: 1.5px solid #000; padding-bottom: 6px; margin-bottom: 10px; }}
.inst {{ font-size: 15pt; font-weight: 600; }} h1 {{ font-size: 13.5pt; margin: 2px 0; }}
.info {{ font-size: 11pt; }} .note {{ font-size: 10pt; margin-top: 2px; }}
.paper {{ columns: 2; column-gap: 9mm; column-rule: 1px solid #999; }}
.q {{ break-inside: avoid; margin-bottom: 9px; }} .q img {{ max-width: 100%; max-height: 45mm; display:block; margin:4px 0; }}
.opts {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1px 10px; padding-left: 14px; }}
.key {{ break-before: page; }} .key h2 {{ text-align:center; font-size: 13pt; }}
.kg {{ display:grid; grid-template-columns: repeat(10, 1fr); gap: 4px; }}
.bar {{ position: fixed; top: 10px; right: 10px; }} .bar button {{ font: inherit; padding: 6px 14px; cursor: pointer; }}
@media print {{ .bar {{ display:none; }} body {{ padding: 0; }} }}
</style></head><body>
<div class="bar"><button onclick="window.print()">Print / Save as PDF</button></div>
<header>{head}<h1>{e(paper.title)}</h1><div class="info">{"  ·  ".join(info)}</div><div class="note">{t["note"]}</div></header>
<main class="paper">{"".join(qs)}</main>{key}
</body></html>"""


# ---------------------------------------------------------------- board-style one-page paper (SSC MCQ layout)
SET_CODES = ["ক", "খ", "গ", "ঘ"]
SET_ALIAS = {"A": "ক", "B": "খ", "C": "গ", "D": "ঘ"}


def set_order(items: list, paper_id: int, set_code: str) -> list:
    """Set ক keeps the builder order; খ/গ/ঘ get their own fixed shuffle (same every time)."""
    import random
    code = SET_ALIAS.get(set_code.upper(), set_code) if set_code else "ক"
    idx = SET_CODES.index(code) if code in SET_CODES else 0
    if idx == 0:
        return items
    out = list(items)
    random.Random(paper_id * 101 + idx).shuffle(out)
    return out


BOARD_TXT = {
    "bn": {"mcq": "বহুনির্বাচনি অভীক্ষা", "syl": "[{y} সালের সিলেবাস অনুযায়ী]", "time": "সময়—{d} মিনিট", "marks": "পূর্ণমান—{m}",
           "code": "বিষয় কোড :", "set": "সেট :", "class": "শ্রেণি", "no_mark": "প্রশ্নপত্রে কোনো প্রকার দাগ/চিহ্ন দেয়া যাবে না।",
           "note": "[বিশেষ দ্রষ্টব্য : সরবরাহকৃত বহুনির্বাচনি অভীক্ষার উত্তরপত্রে প্রশ্নের ক্রমিক নম্বরের বিপরীতে প্রদত্ত বর্ণসংবলিত বৃত্তসমূহ হতে সঠিক/সর্বোৎকৃষ্ট উত্তরের বৃত্তটি বল পয়েন্ট কলম দ্বারা সম্পূর্ণ ভরাট কর। প্রতিটি প্রশ্নের মান ১।]",
           "key": "উত্তরমালা", "subject": "পদার্থবিজ্ঞান"},
    "en": {"mcq": "Multiple Choice Test", "syl": "[According to the {y} syllabus]", "time": "Time—{d} minutes", "marks": "Full marks—{m}",
           "code": "Subject code :", "set": "Set :", "class": "Class", "no_mark": "Do not write or mark anything on the question paper.",
           "note": "[N.B. On the supplied MCQ answer sheet, fill in completely with a ball-point pen the circle of the correct/best answer against the question number. Each question carries 1 mark.]",
           "key": "Answer key", "subject": "Physics"},
}


def to_board_html(paper, items, lang, institution, include_key, set_code="ক", embed=False) -> str:
    T = BOARD_TXT[lang]
    lab = OPT_LABEL[lang]
    e = html.escape
    meta = getattr(paper, "meta", None) or {}
    code = SET_ALIAS.get((set_code or "ক").upper(), set_code or "ক")
    set_label = code if lang == "bn" else {"ক": "A", "খ": "B", "গ": "C", "ঘ": "D"}.get(code, code)
    subject = meta.get("subject_name") or T["subject"]
    scode = (meta.get("subject_code") or "136")[:3]
    year = meta.get("syllabus_year") or "2026"
    inst = meta.get("institution") or institution or ""
    exam = meta.get("exam_name") or paper.title
    full = sum(i["marks"] for i in items)
    dur = paper.duration_min or max(10, len(items))
    nn = (lambda v: num(v, lang))
    qs = []
    for n_, it in enumerate(items, 1):
        q = it["question"]
        img = ""
        p = image_path(q.image_path)
        if p:
            mime = "image/png" if p.suffix == ".png" else "image/jpeg"
            img = f'<img src="data:{mime};base64,{base64.b64encode(p.read_bytes()).decode()}" alt="">'
        opts = "".join(f'<div class="o"><span class="b">({lab[k]})</span> {e(q.options.get(k, ""))}</div>' for k in "ABCD")
        qs.append(f'<div class="q"><div class="s"><span class="n">{nn(n_)}।</span> {e(q.stem)}</div>{img}<div class="opts">{opts}</div></div>')
    key = ""
    if include_key:
        cells = "".join(f'<span>{nn(n_)}. <b>{lab.get(it["question"].answer or "", "–")}</b></span>' for n_, it in enumerate(items, 1))
        key = f'<section class="key"><h2>{T["key"]} — {e(exam)} · {T["set"]} {set_label}</h2><div class="kg">{cells}</div></section>'
    boxes = "".join(f"<i>{nn(int(c)) if c.isdigit() else e(c)}</i>" for c in scode.ljust(3)[:3])
    bar = "" if embed else '<div class="bar"><button onclick="window.print()">Print / Save as PDF</button></div>'
    return f"""<!doctype html><html lang="{lang}"><head><meta charset="utf-8"><title>{e(exam)} — {T['set']} {set_label}</title>
<link href="https://fonts.googleapis.com/css2?family=Hind+Siliguri:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
@page {{ size: A4; margin: 10mm 11mm; }}
* {{ box-sizing: border-box; }}
html {{ background: {'#fff' if embed else '#8a8a8a'}; {'overflow: hidden;' if embed else ''} }}
body {{ margin: 0; font-family: 'Hind Siliguri','Nirmala UI','SolaimanLipi',sans-serif; color: #000; }}
.page {{ width: {'100%' if embed else '210mm'}; min-height: 297mm; margin: {'0' if embed else '10mm'} auto; background: #fff; padding: 10mm 11mm; box-shadow: {'none' if embed else '0 4px 24px rgba(0,0,0,.35)'}; font-size: var(--fs, 10.5pt); line-height: 1.38; }}
.head {{ display: grid; grid-template-columns: 1fr auto; align-items: start; gap: 6mm; }}
.center {{ text-align: center; line-height: 1.45; }}
.center .inst {{ font-size: 1.25em; font-weight: 700; }}
.center .mcq {{ font-weight: 700; font-size: 1.12em; }}
.center .subj {{ font-weight: 700; font-size: 1.2em; }}
.codes {{ border: 1.5px solid #000; padding: 2.5mm 3mm; display: grid; gap: 2.5mm; font-weight: 600; }}
.codes div {{ display: flex; align-items: center; gap: 2mm; white-space: nowrap; }}
.codes i {{ font-style: normal; display: inline-grid; place-items: center; width: 7mm; height: 7mm; border: 1.3px solid #000; font-weight: 700; }}
.note {{ margin-top: 3mm; border: 1.3px solid #000; padding: 1.6mm 2.5mm; font-size: .9em; text-align: justify; }}
.nomark {{ text-align: center; font-weight: 700; margin: 2mm 0 2.5mm; font-size: .95em; }}
.cols {{ columns: 2; column-gap: 7mm; column-rule: 1px solid #000; border-top: 1.5px solid #000; padding-top: 2.5mm; }}
.q {{ break-inside: avoid; margin-bottom: 2.4mm; }}
.n {{ font-weight: 700; }}
.q img {{ max-width: 100%; max-height: 32mm; display: block; margin: 1mm auto; }}
.opts {{ display: grid; grid-template-columns: 1fr 1fr; gap: .3mm 4mm; padding-left: 5mm; }}
.b {{ font-weight: 600; }}
.key {{ break-before: page; padding-top: 4mm; }}
.key h2 {{ font-size: 1.1em; text-align: center; }}
.kg {{ display: grid; grid-template-columns: repeat(10, 1fr); gap: 2mm; }}
.bar {{ position: fixed; top: 10px; right: 14px; z-index: 9; }}
.bar button {{ font: 600 14px sans-serif; padding: 8px 16px; border-radius: 8px; border: 0; background: #7A0F1E; color: #fff; cursor: pointer; }}
@media print {{ html {{ background: #fff; }} .bar {{ display: none; }} .page {{ margin: 0; box-shadow: none; width: auto; min-height: 0; padding: 0; }} }}
</style></head><body>{bar}
<div class="page" id="pg">
  <div class="head">
    <div class="center">
      {f'<div class="inst">{e(inst)}</div>' if inst else ''}
      <div>{e(exam)}{f' · {T["class"]} {e(nn(meta["class_level"]) if str(meta.get("class_level","")).isdigit() else meta.get("class_level"))}' if meta.get("class_level") else ''}</div>
      <div class="mcq">{T['mcq']}</div>
      <div class="subj">{e(subject)}</div>
      <div>{T['syl'].format(y=nn(int(year)) if str(year).isdigit() else e(year))}</div>
      <div>{T['time'].format(d=nn(dur))} &nbsp;·&nbsp; {T['marks'].format(m=nn(full))}</div>
    </div>
    <div class="codes"><div>{T['code']} {boxes}</div><div>{T['set']} <i>{set_label}</i></div></div>
  </div>
  <div class="note">{e(meta.get('instructions') or T['note'])}</div>
  <div class="nomark">{T['no_mark']}</div>
  <main class="cols">{''.join(qs)}</main>
  {key}
</div>
<script>
// shrink the font a little (down to 8.5pt) so the questions fit on one A4 page when possible
(function(){{ var pg=document.getElementById('pg'), key=pg.querySelector('.key'); var limit=297*3.78-40; var fs=10.5;
  function h(){{ return pg.scrollHeight - (key? key.offsetHeight:0); }}
  while(h()>limit && fs>8.5){{ fs-=0.25; pg.style.setProperty('--fs', fs+'pt'); }} }})();
</script>
</body></html>"""
