import io
import os
import shutil
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image

from tests.conftest import PW, login


def test_portal_login(client):
    r = client.post("/v1/auth/login", json={"email": "student@dishari.com", "password": PW, "portal": "staff"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "WRONG_PORTAL" and r.json()["error"]["fields"]["portal"] == "student"
    assert client.post("/v1/auth/login", json={"email": "student@dishari.com", "password": PW, "portal": "student"}).status_code == 200
    assert client.post("/v1/auth/login", json={"email": "moderator@dishari.com", "password": PW, "portal": "staff"}).status_code == 200


def test_profile_avatar_link_and_parent_register(client, student):
    me = client.get("/v1/auth/me", headers=student).json()
    assert me["link_code"] and len(me["link_code"]) == 6
    r = client.patch("/v1/auth/me", json={"phone": "01700000000", "profile": {"roll": "7", "section": "B", "bio": "hi"}}, headers=student)
    assert r.status_code == 200 and r.json()["profile"]["roll"] == "7"
    bad = client.patch("/v1/auth/me", json={"profile": {"teacher_id": "x"}}, headers=student)
    assert bad.json()["error"]["code"] == "UNKNOWN_PROFILE_FIELD"
    b = io.BytesIO(); Image.new("RGB", (300, 200), "red").save(b, "PNG")
    a = client.post("/v1/auth/me/avatar", files={"file": ("a.png", b.getvalue())}, headers=student).json()
    assert a["avatar_url"].startswith("/media/avatars/") and client.get(a["avatar_url"]).status_code == 200
    p = client.post("/v1/auth/register", json={"name": "Mother", "email": "mom@x.com", "password": "Passw0rd!", "role": "parent",
                                               "child_link_code": me["link_code"]})
    assert p.status_code == 201 and p.json()["user"]["roles"] == ["parent"]
    ph = {"Authorization": f"Bearer {p.json()['access_token']}"}
    kids = client.get("/v1/parents/me/children", headers=ph).json()
    assert kids[0]["id"] == me["id"]
    assert client.get("/v1/auth/me/stats", headers=student).json()["student"]["parents_linked"] >= 2


def test_online_test_flow(client, teacher, student, parent):
    qs = client.get("/v1/questions?language=bn&page_size=5", headers=teacher).json()["items"]
    ids = [q["id"] for q in qs if q["answer"]][:4]
    p = client.post("/v1/papers", json={"title": "Online test 1"}, headers=teacher).json()
    client.post(f"/v1/papers/{p['id']}/items", json={"question_ids": ids}, headers=teacher)
    now = datetime.now(timezone.utc)
    t = client.post("/v1/tests", json={"paper_id": p["id"], "start_at": (now - timedelta(minutes=1)).isoformat(),
                                        "end_at": (now + timedelta(hours=1)).isoformat(), "duration_min": 20,
                                        "show_answers": "after_submit"}, headers=teacher)
    assert t.status_code == 201, t.text
    tid = t.json()["id"]
    lst = client.get("/v1/tests", headers=student).json()
    assert any(x["id"] == tid and x["state"] == "live" for x in lst)
    s = client.post(f"/v1/tests/{tid}/start", headers=student).json()
    assert len(s["questions"]) == len(ids) and "answer" not in s["questions"][0]
    first = s["questions"][0]["id"]
    assert client.put(f"/v1/tests/{tid}/answers", json={"answers": {first: "A"}}, headers=student).json()["saved"] == 1
    r = client.post(f"/v1/tests/{tid}/submit", json={"answers": {q["id"]: "A" for q in s["questions"]}}, headers=student).json()
    assert r["state"] == "submitted" and r["answers_visible"] and len(r["review"]) == len(ids)
    assert client.post(f"/v1/tests/{tid}/start", headers=student).json()["error"]["code"] == "ALREADY_SUBMITTED"
    res = client.get(f"/v1/tests/{tid}/results", headers=teacher).json()
    assert res["submitted"] == 1 and res["students"][0]["score"] is not None
    me = client.get("/v1/auth/me", headers=student).json()["id"]
    w = client.get(f"/v1/students/{me}/weekly-report", headers=parent).json()
    assert w["answered"] >= len(ids) and len(w["days"]) == 7 and w["tests"]
    assert "সাপ্তাহিক" in client.get(f"/v1/students/{me}/weekly-report?format=html&lang=bn", headers=student).text


def test_exam_mode_practice(client, student):
    s = client.post("/v1/practice/sessions", json={"language": "en", "size": 3, "mode": "exam", "time_limit_min": 5}, headers=student).json()
    assert s["mode"] == "exam" and s["time_limit_min"] == 5


@pytest.mark.skipif(not shutil.which("tesseract"), reason="tesseract not installed")
def test_ocr_parse_english():
    from app.services.ocr import parse_mcqs
    qs = parse_mcqs("1. Which of the following is a vector quantity?\n(a) Mass (b) Speed\n(c) Displacement (d) Work\n"
                    "২। বলের একক কোনটি?\n(ক) জুল (খ) নিউটন\n(গ) ওয়াট (ঘ) প্যাসকেল")
    assert len(qs) == 2 and qs[0]["options"]["C"] == "Displacement" and qs[1]["language"] == "bn" and qs[1]["options"]["B"] == "নিউটন"


def test_ml_models_for_teacher(client, teacher):
    assert client.get("/v1/ml/models", headers=teacher).status_code == 200
    assert client.get("/v1/ml/agreement", headers=teacher).status_code == 200


def test_board_paper_sets_and_section(client, teacher, student):
    from datetime import datetime, timedelta, timezone
    qs = [q["id"] for q in client.get("/v1/questions?language=bn&page_size=8", headers=teacher).json()["items"] if q["answer"]]
    p = client.post("/v1/papers", json={"title": "অর্ধবার্ষিক পরীক্ষা", "meta": {"subject_name": "পদার্থবিজ্ঞান", "subject_code": "136", "class_level": "10"}}, headers=teacher).json()
    assert p["meta"]["subject_code"] == "136"
    client.post(f"/v1/papers/{p['id']}/items", json={"question_ids": qs}, headers=teacher)
    a = client.get(f"/v1/papers/{p['id']}/export?format=html&set=ক&embed=true", headers=teacher).text
    b = client.get(f"/v1/papers/{p['id']}/export?format=html&set=খ", headers=teacher).text
    assert "বহুনির্বাচনি অভীক্ষা" in a and "বিষয় কোড" in a and "Print / Save" not in a and "Print / Save" in b
    assert a.split('<main class="cols">')[1][:300] != b.split('<main class="cols">')[1][:300]  # set খ has another order
    now = datetime.now(timezone.utc)
    t = client.post("/v1/tests", json={"paper_id": p["id"], "class_level": "10", "section": "Z", "start_at": (now - timedelta(minutes=1)).isoformat(), "end_at": (now + timedelta(hours=1)).isoformat(), "duration_min": 10}, headers=teacher).json()
    assert t["section"] == "Z"
    assert not any(x["id"] == t["id"] for x in client.get("/v1/tests", headers=student).json())  # student is section B/A, not Z


def test_omr_end_to_end(client, teacher, student, parent):
    import cv2
    from app.services.omr import render_filled_sheet_for_test
    client.patch("/v1/auth/me", json={"class_level": "10", "profile": {"roll": "4321"}}, headers=student)
    qs = [q for q in client.get("/v1/questions?language=bn&page_size=12", headers=teacher).json()["items"] if q["answer"]][:6]
    p = client.post("/v1/papers", json={"title": "OMR test", "meta": {"class_level": "10"}}, headers=teacher).json()
    assert p["meta"]["institution"] == "Dishari Demo School" and p["meta"]["subject_code"] == "136"  # from teacher profile
    client.post(f"/v1/papers/{p['id']}/items", json={"question_ids": [q["id"] for q in qs]}, headers=teacher)
    sheet = client.get(f"/v1/papers/{p['id']}/omr-sheet?set=খ", headers=teacher)
    assert sheet.status_code == 200 and "রোল" in sheet.text
    # the key for set খ (B) is the paper's order shuffled; read it from the board html answer key
    from app.services.paper_export import set_order
    from types import SimpleNamespace as N
    order = set_order([{"question": N(id=q["id"], answer=q["answer"]), "marks": 1} for q in qs], p["id"], "খ")
    key = {i + 1: it["question"].answer for i, it in enumerate(order)}
    answers = dict(key); answers[1] = "D" if key[1] != "D" else "A"   # one wrong
    img = render_filled_sheet_for_test(len(qs), 1, "4321", answers)
    ok, buf = cv2.imencode(".jpg", img)
    r = client.post(f"/v1/papers/{p['id']}/omr", files=[("files", ("s1.jpg", buf.tobytes(), "image/jpeg")), ("files", ("bad.jpg", b"xx", "image/jpeg"))], headers=teacher).json()
    assert len(r["results"]) == 1 and r["errors"][0]["code"] == "BAD_IMAGE"
    res = r["results"][0]
    assert res["set"] == "B" and res["roll"] == "4321" and res["score"] == len(qs) - 1 and res["student_name"] == "Student Demo"
    fixed = client.patch(f"/v1/omr-results/{res['id']}", json={"answers": {"1": key[1]}}, headers=teacher).json()
    assert fixed["score"] == len(qs) and fixed["edited"]
    assert client.get(f"/v1/papers/{p['id']}/omr/export", headers=teacher).content[:2] == b"PK"
    me = client.get("/v1/auth/me", headers=student).json()["id"]
    w = client.get(f"/v1/students/{me}/weekly-report", headers=parent).json()
    assert any(t.get("kind") == "omr" for t in w["tests"])
