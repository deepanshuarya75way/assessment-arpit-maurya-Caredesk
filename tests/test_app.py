"""Run with:  pytest -q"""
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import database  # noqa: E402

ADMIN_PW = "Test-Admin-1"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "ADMIN_PASSWORD", ADMIN_PW)
    import main
    with TestClient(main.app) as c:
        yield c


def login(c, username, password):
    r = c.post("/auth/login", json={"username": username, "password": password})
    return r, ({"Authorization": "Bearer " + r.json()["token"]} if r.status_code == 200 else None)


PATIENT = {"name": "Asha Verma", "age": 30, "gender": "Female", "email": "asha@example.com",
           "phone": "9876543210", "department": "Cardiology", "symptoms": "chest pain"}


def test_signup_and_login(client):
    assert client.post("/auth/signup", json={"username": "rahul", "password": "secret1"}).status_code == 201
    assert login(client, "rahul", "secret1")[0].status_code == 200


def test_only_registered_users_can_login(client):
    assert login(client, "ghost", "secret1")[0].status_code == 401
    client.post("/auth/signup", json={"username": "rahul", "password": "secret1"})
    assert login(client, "rahul", "wrong-pass")[0].status_code == 401


def test_duplicate_username_rejected(client):
    client.post("/auth/signup", json={"username": "rahul", "password": "secret1"})
    assert client.post("/auth/signup", json={"username": "rahul", "password": "other12"}).status_code == 409


def test_password_is_never_stored_in_plain_text(client):
    client.post("/auth/signup", json={"username": "rahul", "password": "secret1"})
    with sqlite3.connect(database.DB_PATH) as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(users)")]
        dump = str(conn.execute("SELECT * FROM users").fetchall())
    assert "password_plain" not in cols
    assert "secret1" not in dump and ADMIN_PW not in dump


def test_admin_login_uses_env_password_and_lists_users(client):
    client.post("/auth/signup", json={"username": "rahul", "password": "secret1"})
    _, admin = login(client, config.ADMIN_USERNAME, ADMIN_PW)
    users = client.get("/admin/users", headers=admin).json()
    assert {u["username"] for u in users} >= {config.ADMIN_USERNAME, "rahul"}
    assert all("password" not in u for u in users)


def test_normal_user_cannot_see_users(client):
    client.post("/auth/signup", json={"username": "rahul", "password": "secret1"})
    _, user = login(client, "rahul", "secret1")
    assert client.get("/admin/users", headers=user).status_code == 403
    assert client.get("/admin/users").status_code == 401


def test_patient_saved_in_database(client):
    client.post("/auth/signup", json={"username": "rahul", "password": "secret1"})
    _, h = login(client, "rahul", "secret1")
    pid = client.post("/patients", json=PATIENT, headers=h).json()["id"]
    with sqlite3.connect(database.DB_PATH) as conn:   # read straight from the database file
        row = conn.execute("SELECT name, email, department FROM patients WHERE id=?", (pid,)).fetchone()
    assert row == ("Asha Verma", "asha@example.com", "Cardiology")
    assert client.get("/patients", headers=h).json()[0]["name"] == "Asha Verma"


def test_patient_edit_delete_and_appointment(client):
    _, h = login(client, config.ADMIN_USERNAME, ADMIN_PW)
    pid = client.post("/patients", json=PATIENT, headers=h).json()["id"]
    assert client.put(f"/patients/{pid}", json={**PATIENT, "age": 31}, headers=h).status_code == 200
    assert client.get("/patients", headers=h).json()[0]["age"] == 31

    day = (date.today() + timedelta(days=2)).isoformat()
    appt = {"patient_id": pid, "department": "Cardiology", "doctor": "Dr. Mehta",
            "appointment_date": day, "appointment_time": "10:00"}
    assert client.post("/appointments", json=appt, headers=h).status_code == 201
    assert client.post("/appointments", json=appt, headers=h).status_code == 409  # same slot

    assert client.delete(f"/patients/{pid}", headers=h).status_code == 200
    assert client.get("/patients", headers=h).json() == []
    assert client.get("/appointments", headers=h).json() == []


def test_data_requires_login(client):
    assert client.get("/patients").status_code == 401
    assert client.post("/patients", json=PATIENT).status_code == 401


def test_change_password(client):
    client.post("/auth/signup", json={"username": "rahul", "password": "secret1"})
    _, h = login(client, "rahul", "secret1")
    wrong = client.post("/auth/password", json={"old_password": "nope", "new_password": "newpass1"}, headers=h)
    assert wrong.status_code == 400
    ok = client.post("/auth/password", json={"old_password": "secret1", "new_password": "newpass1"}, headers=h)
    assert ok.status_code == 200
    assert login(client, "rahul", "secret1")[0].status_code == 401
    assert login(client, "rahul", "newpass1")[0].status_code == 200


def test_logout_stops_the_token(client):
    client.post("/auth/signup", json={"username": "rahul", "password": "secret1"})
    _, h = login(client, "rahul", "secret1")
    assert client.get("/patients", headers=h).status_code == 200
    client.post("/auth/logout", headers=h)
    assert client.get("/patients", headers=h).status_code == 401


def test_password_spaces_are_kept(client):
    client.post("/auth/signup", json={"username": "  rahul  ", "password": " pass 12 "})
    assert login(client, "rahul", " pass 12 ")[0].status_code == 200


def test_ai_error_and_success(client, monkeypatch):
    import ai_service
    _, h = login(client, config.ADMIN_USERNAME, ADMIN_PW)

    def ollama_down(*args, **kwargs):
        raise ai_service.AIServiceError("Cannot connect to Ollama.")

    monkeypatch.setattr(ai_service, "ask_ollama", ollama_down)
    assert client.post("/ai/assistant", json={"question": "what is fever?"}, headers=h).status_code == 503

    monkeypatch.setattr(ai_service, "ask_ollama", lambda *a, **k: "Drink water and rest.")
    r = client.post("/ai/assistant", json={"question": "what is fever?"}, headers=h)
    assert r.status_code == 200 and "rest" in r.json()["answer"]
    assert client.get("/stats", headers=h).json()["ai_queries"] == 1
