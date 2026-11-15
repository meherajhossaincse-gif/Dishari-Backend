import io

from PIL import Image


def _png():
    b = io.BytesIO()
    Image.new("RGB", (120, 80), "white").save(b, "PNG")
    return b.getvalue()


def test_question_image(client, teacher, student):
    qid = client.get("/v1/questions?mine=true", headers=teacher).json()["items"][0]["id"]
    r = client.post(f"/v1/questions/{qid}/image", files={"file": ("f.png", _png(), "image/png")}, headers=teacher)
    assert r.status_code == 200, r.text
    url = r.json()["image_url"]
    assert url.startswith("/media/questions/") and r.json()["figure_dependent"]
    assert client.get(url).status_code == 200  # plain <img src>, no token needed
    bad = client.post(f"/v1/questions/{qid}/image", files={"file": ("x.png", b"not an image")}, headers=teacher)
    assert bad.json()["error"]["code"] == "BAD_IMAGE"
    assert client.post(f"/v1/questions/{qid}/image", files={"file": ("f.png", _png())}, headers=student).status_code == 403


def test_paper_docx_html(client, teacher):
    ids = [q["id"] for q in client.get("/v1/questions?language=bn&page_size=6", headers=teacher).json()["items"]]
    p = client.post("/v1/papers", json={"title": "শ্রেণি পরীক্ষা ১", "language": "bn", "duration_min": 30},
                    headers=teacher).json()
    client.post(f"/v1/papers/{p['id']}/items", json={"question_ids": ids}, headers=teacher)
    d = client.get(f"/v1/papers/{p['id']}/export?format=docx&institution=Demo School", headers=teacher)
    assert d.status_code == 200 and d.content[:2] == b"PK"
    h = client.get(f"/v1/papers/{p['id']}/export?format=html", headers=teacher)
    assert h.status_code == 200 and "উত্তরমালা" in h.text and "পূর্ণমান" in h.text
    assert client.get(f"/v1/papers/{p['id']}/export?format=pdf", headers=teacher).status_code == 422


def test_agreement(client, moderator, teacher, admin):
    q = {"language": "en", "stem": "Which instrument measures electric current?",
         "options": {"A": "Voltmeter", "B": "Ammeter", "C": "Galvanometer", "D": "Ohmmeter"}, "answer": "B"}
    ids = [client.post("/v1/questions", json=dict(q, stem=q["stem"] + f" ({i})"), headers=teacher).json()["id"]
           for i in range(3)]
    client.post(f"/v1/reviews/{ids[0]}", json={"action": "accept"}, headers=moderator)
    client.post(f"/v1/reviews/{ids[1]}", json={"action": "change", "tags": {"cognitive_level": "K2"},
                                                "reason": "x"}, headers=moderator)
    client.post(f"/v1/reviews/{ids[2]}", json={"action": "reject", "reason": "dup"}, headers=moderator)
    rep = client.get("/v1/ml/agreement?model_version=mock-0.1", headers=admin).json()
    assert rep["accepted"] >= 1 and rep["changed"] >= 1 and rep["rejected"] >= 1
    assert "level_confusion" in rep and "mock-0.1" in rep["by_model_version"]
    x = client.get("/v1/ml/agreement?format=xlsx", headers=moderator)
    assert x.status_code == 200 and x.content[:2] == b"PK"
    assert client.get("/v1/ml/agreement", headers=teacher).status_code == 200  # teachers can read it (AI page)
