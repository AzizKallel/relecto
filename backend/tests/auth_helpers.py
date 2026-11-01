"""Test accounts: one per role, created once per test run."""
import uuid

from fastapi.testclient import TestClient

from relecto.api.app import app
from relecto.auth import create_user

PASSWORD = "Test-password-123"


def client(role="radiologist", name=None):
    email = f"{role}-{uuid.uuid4().hex[:8]}@test.tn"
    create_user(email, name or f"{role.title()} Test", role, PASSWORD, "Dr" if role == "radiologist" else None)
    c = TestClient(app)
    c.headers.update({"X-Relecto": "1"})
    r = c.post("/api/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return c
