from datetime import datetime, timedelta, timezone

from tests.conftest import PW, login


def test_teacher_signup_needs_approval(client, admin):
    body = {"name": "New Teacher", "email": "newt@x.com", "password": "Passw0rd!", "role": "teacher",
            "institution": "Rajshahi Govt. School", "subjects": "Physics", "classes_taught": "9, 10", "designation": "Lecturer"}
    assert client.post("/v1/auth/register", json={**body, "institution": ""}).json()["error"]["fields"]["institution"] == "required"
    r = client.post("/v1/auth/register", json=body)
    assert r.status_code == 201 and r.json()["pending"] is True and "access_token" not in r.json()
    lg = client.post("/v1/auth/login", json={"email": "newt@x.com", "password": "Passw0rd!", "portal": "staff"})
    assert lg.status_code == 403 and lg.json()["error"]["code"] == "ACCOUNT_PENDING" and lg.json()["error"]["fields"]["message_bn"]
    pend = client.get("/v1/admin/pending-teachers", headers=admin).json()
    me = next(u for u in pend if u["email"] == "newt@x.com")
    assert me["profile"]["subjects"] == "Physics" and me["institution"] == "Rajshahi Govt. School"
    assert client.get("/v1/dashboard", headers=admin).json()["admin"]["pending_teachers"] >= 1
    # reject with a reason, then approve
    rj = client.post(f"/v1/users/{me['id']}/reject", json={"reason": "Unknown school"}, headers=admin).json()
    assert rj["status"] == "rejected"
    e = client.post("/v1/auth/login", json={"email": "newt@x.com", "password": "Passw0rd!"}).json()["error"]
    assert e["code"] == "ACCOUNT_REJECTED" and e["fields"]["reason"] == "Unknown school"
    assert client.post(f"/v1/users/{me['id']}/approve", headers=admin).json()["status"] == "active"
    ok = client.post("/v1/auth/login", json={"email": "newt@x.com", "password": "Passw0rd!", "portal": "staff"})
    assert ok.status_code == 200
    th = {"Authorization": f"Bearer {ok.json()['access_token']}"}
    # header defaults come from the sign-up profile
    p = client.post("/v1/papers", json={"title": "T"}, headers=th).json()
    assert p["meta"]["institution"] == "Rajshahi Govt. School" and p["meta"]["class_level"] == "9"


def test_admin_portal_and_lockout(client, admin):
    r = client.post("/v1/auth/login", json={"email": "teacher@dishari.com", "password": PW, "portal": "admin"})
    assert r.json()["error"]["code"] == "WRONG_PORTAL" and r.json()["error"]["fields"]["portal"] == "staff"
    assert client.post("/v1/auth/login", json={"email": "admin@dishari.com", "password": PW, "portal": "admin"}).status_code == 200
    client.post("/v1/auth/register", json={"name": "Lock Me", "email": "lock@x.com", "password": "Passw0rd!", "role": "student"})
    s = client.put("/v1/admin/settings", json={"lock_after_failed": 3, "board_match_threshold": 0.9}, headers=admin)
    assert s.status_code == 200 and s.json()["board_match_threshold"] == 0.9
    assert client.put("/v1/admin/settings", json={"review_threshold": 5}, headers=admin).status_code == 422
    for _ in range(3):
        client.post("/v1/auth/login", json={"email": "lock@x.com", "password": "wrong-pass"})
    lk = client.post("/v1/auth/login", json={"email": "lock@x.com", "password": "Passw0rd!"})
    assert lk.status_code == 423 and lk.json()["error"]["code"] == "ACCOUNT_LOCKED"
    uid = client.get("/v1/users?q=lock@x.com", headers=admin).json()["items"][0]["id"]
    client.post(f"/v1/users/{uid}/unlock", headers=admin)
    assert client.post("/v1/auth/login", json={"email": "lock@x.com", "password": "Passw0rd!"}).status_code == 200
    client.put("/v1/admin/settings", json={"lock_after_failed": 5, "board_match_threshold": 0.85}, headers=admin)


def test_groups_feedback_class_report_summary(client, teacher, student, parent):
    sid = client.get("/v1/auth/me", headers=student).json()["id"]
    client.patch("/v1/auth/me", json={"profile": {"section": "A"}}, headers=student)
    found = client.get("/v1/students/search?class_level=10&section=A", headers=teacher).json()
    assert any(s["id"] == sid for s in found)
    g = client.post("/v1/groups", json={"name": "Class 10 · A", "class_level": "10", "section": "A", "add_matching": True},
                    headers=teacher).json()
    assert g["members_count"] >= 1 and any(m["id"] == sid for m in g["members"])
    assert client.put(f"/v1/groups/{g['id']}/members", json={"student_ids": [sid]}, headers=teacher).json()["members_count"] == 1
    # an online test so the report has data
    qs = client.get("/v1/questions?page_size=6", headers=teacher).json()["items"]
    ids = [q["id"] for q in qs if q["answer"]][:5]
    p = client.post("/v1/papers", json={"title": "Model Test 3"}, headers=teacher).json()
    client.post(f"/v1/papers/{p['id']}/items", json={"question_ids": ids}, headers=teacher)
    now = datetime.now(timezone.utc)
    t = client.post("/v1/tests", json={"paper_id": p["id"], "start_at": (now - timedelta(minutes=1)).isoformat(),
                                        "end_at": (now + timedelta(hours=1)).isoformat(), "duration_min": 20,
                                        "show_answers": "after_submit"}, headers=teacher).json()
    s = client.post(f"/v1/tests/{t['id']}/start", headers=student).json()
    client.post(f"/v1/tests/{t['id']}/submit", json={"answers": {q["id"]: "B" for q in s["questions"]}}, headers=student)
    rep = client.get(f"/v1/class-report?test_id={t['id']}", headers=teacher).json()
    assert rep["students_count"] == 1 and rep["average"] is not None and rep["students"][0]["student_id"] == sid
    assert set(rep["level_accuracy"]) == {"K1", "K2", "K3", "K4"}
    assert client.get(f"/v1/class-report?group_id={g['id']}", headers=teacher).json()["students_count"] == 1
    assert "ক্লাস রিপোর্ট" in client.get(f"/v1/class-report?test_id={t['id']}&format=html&lang=bn", headers=teacher).text
    src = client.get("/v1/class-report/sources", headers=teacher).json()
    assert any(x["id"] == t["id"] for x in src["tests"]) and src["groups"]
    # feedback to the group -> student + parent see it
    assert client.post("/v1/feedback", json={"group_id": g["id"], "message": "Revise Newton's laws", "context": "Model Test 3"},
                       headers=teacher).json()["sent"] == 1
    assert client.get(f"/v1/students/{sid}/feedback", headers=parent).json()[0]["message"] == "Revise Newton's laws"
    assert client.get("/v1/feedback/sent", headers=teacher).json()[0]["student_id"] == sid
    sm = client.get(f"/v1/students/{sid}/summary", headers=parent).json()
    assert sm["latest"]["total"] and sm["feedback"] and sm["student"]["id"] == sid and sm["progress"]
    assert client.get(f"/v1/students/{sid}/summary", headers=login(client, "moderator@dishari.com")).status_code == 200
    d = client.get("/v1/dashboard", headers=teacher).json()["teacher"]
    assert d["exams"] >= 1 and d["class_average"] is not None and "recent_uploads" in d
    assert isinstance(client.get("/v1/omr/summary", headers=teacher).json(), list)
    # students cannot use teacher tools
    assert client.get("/v1/groups", headers=student).status_code == 403


def test_settings_prefs(client, teacher, parent):
    r = client.patch("/v1/auth/me", json={"profile": {"pref_digits": "bn", "notify_omr_done": True, "exam_marks_per_q": 1}},
                     headers=teacher)
    assert r.status_code == 200 and r.json()["profile"]["notify_omr_done"] is True
    assert client.patch("/v1/auth/me", json={"profile": {"notify_frequency": "weekly"}}, headers=parent).status_code == 200
    assert client.patch("/v1/auth/me", json={"profile": {"exam_negative": True}}, headers=parent).status_code == 400


def test_tag_review_confirm(client, teacher, moderator):
    items = client.get("/v1/questions?mine=true&status=tagged,needs_review&page_size=3", headers=teacher).json()["items"]
    ids = [q["id"] for q in items if q["cognitive_level"]][:2]
    r = client.post("/v1/questions/confirm", json={"ids": ids}, headers=teacher).json()
    assert r["status"] == "tagged" and set(r["confirmed"]) == set(ids)
    m = client.post("/v1/questions/confirm", json={"ids": ids}, headers=moderator).json()
    assert m["status"] == "verified" and client.get(f"/v1/questions/{ids[0]}", headers=teacher).json()["status"] == "verified"


def test_board_bank_crud(client, admin, teacher):
    b = client.post("/v1/board-questions", json={"board": "Dhaka", "year": 2023, "q_no": 12, "language": "en",
                                                 "stem": "Which law explains why a rocket moves forward in space?",
                                                 "options": {"A": "First", "B": "Second", "C": "Third", "D": "Gravity"}, "answer": "C"},
                    headers=admin)
    assert b.status_code == 201 and b.json()["q_no"] == 12
    assert client.post("/v1/board-questions", json={"board": "Dhaka", "year": 2023, "language": "en",
                                                    "stem": "Which law explains why a rocket moves forward in space?"}, headers=admin).status_code == 409
    assert client.patch(f"/v1/board-questions/{b.json()['id']}", json={"year": 2022}, headers=admin).json()["year"] == 2022
    assert client.post("/v1/board-questions", json={"board": "X1", "year": 2023, "language": "en", "stem": "abc def"}, headers=teacher).status_code == 403
    assert client.delete(f"/v1/board-questions/{b.json()['id']}", headers=admin).status_code == 204


def test_parent_unlink_child(client, student):
    code = client.get("/v1/auth/me", headers=student).json()["link_code"]
    r = client.post("/v1/auth/register", json={"name": "Dad", "email": "dad2@x.com", "password": "Passw0rd!", "role": "parent", "child_link_code": code})
    ph = {"Authorization": f"Bearer {r.json()['access_token']}"}
    kid = client.get("/v1/parents/me/children", headers=ph).json()[0]["id"]
    assert client.delete(f"/v1/parents/me/children/{kid}", headers=ph).status_code == 204
    assert client.get("/v1/parents/me/children", headers=ph).json() == []


def test_question_export(client, teacher):
    r = client.get("/v1/questions/export?mine=true", headers=teacher)
    assert r.status_code == 200 and r.content[:2] == b"PK"
