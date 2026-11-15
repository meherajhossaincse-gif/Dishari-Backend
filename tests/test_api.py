import io

from openpyxl import Workbook, load_workbook

Q_BN = {"language": "bn", "stem": "নিচের কোনটি স্কেলার রাশি?", "options": {"A": "বেগ", "B": "সরণ", "C": "ভর", "D": "বল"},
        "answer": "C"}


def test_health_and_taxonomy(client, teacher):
    assert client.get("/health").json()["database"] is True
    t = client.get("/v1/taxonomy", headers=teacher).json()
    assert t["counts"] == {"chapters": 13, "concepts": 91, "topics": 193}
    assert t["chapters"][0]["name_bn"] and t["chapters"][0]["concepts"][0]["topics"]
    assert [l["code"] for l in t["cognitive_levels"]] == ["K1", "K2", "K3", "K4"]


def test_auth_errors(client):
    r = client.post("/v1/auth/login", json={"email": "admin@dishari.com", "password": "wrong"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "INVALID_CREDENTIALS"
    r = client.get("/v1/questions")
    assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHORIZED"


def test_register_refresh_and_roles(client):
    r = client.post("/v1/auth/register", json={"name": "New Student", "email": "s2@x.com", "password": "Passw0rd!"})
    assert r.status_code == 201 and r.json()["user"]["roles"] == ["student"]
    rt = r.json()["refresh_token"]
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert client.get("/v1/questions", headers=h).status_code == 403
    r2 = client.post("/v1/auth/refresh", json={"refresh_token": rt})
    assert r2.status_code == 200
    assert client.post("/v1/auth/refresh", json={"refresh_token": "junk"}).json()["error"]["code"] == "TOKEN_INVALID"


def test_forgot_reset(client):
    r = client.post("/v1/auth/forgot-password", json={"email": "s2@x.com"}).json()
    tok = r["debug_reset_token"]
    assert client.post("/v1/auth/reset-password", json={"token": tok, "new_password": "NewPass123"}).status_code == 200
    assert client.post("/v1/auth/login", json={"email": "s2@x.com", "password": "NewPass123"}).status_code == 200
    assert client.post("/v1/auth/reset-password", json={"token": tok, "new_password": "x" * 9}).status_code == 400


def test_tag_contract(client, teacher):
    bad = client.post("/v1/questions/tag", json={"items": [dict(Q_BN, stem="   ")]}, headers=teacher).json()
    assert bad[0]["ok"] is False and bad[0]["error"]["code"] == "EMPTY_STEM"
    r = client.post("/v1/questions/tag", json={"items": [Q_BN]}, headers=teacher)
    assert r.status_code == 200
    res = r.json()[0]
    assert res["ok"]
    q = res["question"]
    assert q["chapter"]["id"] and q["concept"]["name_bn"] and q["cognitive_level"]["id"] in ("K1", "K2", "K3", "K4")
    assert q["status"] in ("tagged", "needs_review")
    assert q["model_version"] == "mock-0.1"


def test_tag_row_errors(client, teacher):
    r = client.post("/v1/questions/tag", json={"items": [
        Q_BN, dict(Q_BN, options={"A": "x", "B": "", "C": "y", "D": "z"})]}, headers=teacher).json()
    assert r[0]["ok"] and not r[1]["ok"] and r[1]["error"]["code"] == "TOO_FEW_OPTIONS"
    prev = client.post("/v1/questions/tag", json={"items": [Q_BN], "save": False}, headers=teacher).json()
    assert prev[0]["prediction"]["chapter"]["id"]


def test_list_filter_and_review(client, teacher, moderator):
    r = client.get("/v1/questions?status=needs_review,tagged&page_size=5", headers=teacher).json()
    assert r["total"] >= 1 and len(r["items"]) <= 5
    queue = client.get("/v1/reviews/queue", headers=moderator).json()
    if not queue["items"]:
        return
    qid = queue["items"][0]["id"]
    r = client.post(f"/v1/reviews/{qid}", json={"action": "change", "tags": {"cognitive_level": "K2"}},
                    headers=moderator)
    assert r.json()["error"]["code"] == "REASON_REQUIRED"
    r = client.post(f"/v1/reviews/{qid}", json={"action": "change", "tags": {"cognitive_level": "K2"},
                                                "reason": "asks for understanding"}, headers=moderator).json()
    assert r["status"] == "verified" and r["cognitive_level"]["id"] == "K2" and r["tag_source"] == "moderator"
    assert client.post(f"/v1/reviews/{qid}", json={"action": "accept"}, headers=teacher).status_code == 403


def test_bad_taxonomy_edit(client, teacher):
    qid = client.get("/v1/questions?mine=true", headers=teacher).json()["items"][0]["id"]
    r = client.patch(f"/v1/questions/{qid}", json={"chapter_id": "1", "concept_id": "C05.1"}, headers=teacher)
    assert r.json()["error"]["code"] == "TAXONOMY_MISMATCH"
    r = client.patch(f"/v1/questions/{qid}", json={"cognitive_level": "K3"}, headers=teacher).json()
    assert r["status"] == "needs_review" and r["tag_source"] == "teacher"


def test_bulk_upload(client, teacher):
    t = client.get("/v1/bulk/template", headers=teacher)
    assert t.status_code == 200
    wb = load_workbook(io.BytesIO(t.content))
    ws = wb.active
    ws.append(["Q-003", "en", "", "a", "b", "c", "d", "A", "", "no"])  # empty stem -> error row
    buf = io.BytesIO()
    wb.save(buf)
    r = client.post("/v1/bulk/upload", files={"file": ("q.xlsx", buf.getvalue())}, headers=teacher)
    assert r.status_code == 202
    job = client.get(f"/v1/bulk/jobs/{r.json()['id']}", headers=teacher).json()
    assert job["status"] == "done" and job["total"] == 3 and job["succeeded"] == 2 and job["failed"] == 1
    assert job["errors"][0]["code"] == "EMPTY_STEM"
    res = client.get(job["result_url"], headers=teacher)
    assert res.status_code == 200


def test_board_bank_and_match(client, admin, teacher):
    wb = Workbook()
    ws = wb.active
    ws.append(["board", "year", "language", "question", "option_a", "option_b", "option_c", "option_d", "answer"])
    ws.append(["Dhaka", 2023, "en", "What is the SI unit of force?", "Joule", "Newton", "Watt", "Pascal", "B"])
    ws.append(["Dhaka", 2023, "en", "What is the SI unit of force?", "Joule", "Newton", "Watt", "Pascal", "B"])
    buf = io.BytesIO()
    wb.save(buf)
    r = client.post("/v1/board-questions/import", files={"file": ("b.xlsx", buf.getvalue())}, headers=admin).json()
    assert r["added"] == 1 and r["duplicates_skipped"] == 1
    q = {"language": "en", "stem": "What is the SI unit of force ?", "options": {"A": "J", "B": "N", "C": "W", "D": "Pa"}}
    out = client.post("/v1/questions/tag", json={"items": [q]}, headers=teacher).json()[0]["question"]
    assert out["board_match"]["matched"] and out["board_match"]["board"] == "Dhaka"


def test_paper_flow(client, teacher):
    ids = [q["id"] for q in client.get("/v1/questions?page_size=8", headers=teacher).json()["items"]]
    p = client.post("/v1/papers", json={"title": "Class test 1"}, headers=teacher).json()
    p = client.post(f"/v1/papers/{p['id']}/items", json={"question_ids": ids}, headers=teacher).json()
    assert p["item_count"] == len(ids)
    a = client.get(f"/v1/papers/{p['id']}/analysis", headers=teacher).json()
    assert a["question_count"] == len(ids) and len(a["levels"]) == 4
    assert client.get(f"/v1/papers/{p['id']}/export", headers=teacher).status_code == 200


def test_practice_and_progress(client, student, parent, admin):
    s = client.post("/v1/practice/sessions", json={"language": "bn", "size": 5}, headers=student)
    assert s.status_code == 201, s.text
    s = s.json()
    assert "answer" not in s["questions"][0]
    ans = {q["id"]: "A" for q in s["questions"]}
    r = client.post(f"/v1/practice/sessions/{s['id']}/submit", json={"answers": ans}, headers=student).json()
    assert r["status"] == "submitted" and r["score"] is not None and r["review"][0]["correct_answer"]
    me = client.get("/v1/auth/me", headers=student).json()["id"]
    assert client.get(f"/v1/students/{me}/progress", headers=parent).status_code == 200
    m = client.get(f"/v1/students/{me}/mastery", headers=student).json()
    assert "concepts" in m
    assert client.get("/v1/dashboard", headers=student).json()["student"]["sessions_done"] == 1


def test_mastery_formula():
    from app.services.analysis import mastery
    rows = [{"concept_id": "C01.1", "difficulty": "Easy", "is_correct": True}] * 3
    m = mastery(rows)[0]
    assert m["mastery"] == round((3 + 1) / (3 + 2), 3) and m["label"] == "strong"
    assert mastery(rows[:2])[0]["label"] == "insufficient_evidence"


def test_model_not_ready_goes_pending(client, teacher, admin):
    from app.ml.tagger import tagger
    tagger.load("nonexistent_pkg:Tagger")
    assert client.get("/v1/ml/status", headers=teacher).json()["status"] == "failed"
    q = client.post("/v1/questions", json=dict(Q_BN, stem="পরীক্ষামূলক প্রশ্ন?"), headers=teacher).json()
    assert q["status"] == "pending"
    r = client.post("/v1/admin/models", json={"version": "mock-0.1", "impl": "mock"}, headers=admin)
    assert r.status_code == 200
    client.post("/v1/admin/retag-pending", headers=admin)
    assert client.get(f"/v1/questions/{q['id']}", headers=teacher).json()["status"] in ("tagged", "needs_review")


def test_admin_users(client, admin, teacher):
    r = client.post("/v1/users", json={"name": "T2", "email": "t2@x.com", "password": "Passw0rd!",
                                       "roles": ["teacher", "moderator"]}, headers=admin)
    assert r.status_code == 201
    assert client.get("/v1/users?role=moderator", headers=admin).json()["total"] >= 2
    assert client.get("/v1/users", headers=teacher).status_code == 403
    assert client.get("/v1/admin/audit-logs", headers=admin).json()["total"] > 0
