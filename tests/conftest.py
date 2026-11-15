import os
import tempfile

_tmp = tempfile.mkdtemp()
os.environ.update({
    "ENV": "test", "DATABASE_URL": os.environ.get("TEST_DATABASE_URL", f"sqlite:///{_tmp}/test.db"), "AUTO_CREATE_TABLES": "true",
    "SEED_ON_STARTUP": "true", "SEED_DEMO_USERS": "true", "TAGGER_IMPL": "mock", "STORAGE_DIR": _tmp,
    "DEMO_PASSWORD": "Test@12345",
})

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

PW = "Test@12345"


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


def login(client, email):
    r = client.post("/v1/auth/login", json={"email": email, "password": PW})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture(scope="session")
def admin(client):
    return login(client, "admin@dishari.com")


@pytest.fixture(scope="session")
def teacher(client):
    return login(client, "teacher@dishari.com")


@pytest.fixture(scope="session")
def moderator(client):
    return login(client, "moderator@dishari.com")


@pytest.fixture(scope="session")
def student(client):
    return login(client, "student@dishari.com")


@pytest.fixture(scope="session")
def parent(client):
    return login(client, "parent@dishari.com")
