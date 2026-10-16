import os
import smtplib
from datetime import date
from io import BytesIO
from unittest.mock import patch

import pytest

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app import main  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.models import User  # noqa: E402
from app.rate_limit import contact_limiter, create_contact_limiter  # noqa: E402

PASSWORD = "a-secure-password"
CONTACT = {
    "first_name": "Ada",
    "last_name": "Lovelace",
    "email": "contact@example.com",
    "phone": "+441234567890",
    "birthday": "1815-12-10",
}


@pytest.fixture
def engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def mails():
    sent = {"verification": [], "reset": []}
    with (
        patch.object(
            main,
            "send_verification_email",
            side_effect=lambda email, token: sent["verification"].append(
                (email, token)
            ),
        ),
        patch.object(
            main,
            "send_password_reset_email",
            side_effect=lambda email, token: sent["reset"].append((email, token)),
        ),
    ):
        yield sent


@pytest.fixture
def client(engine, mails, monkeypatch):
    monkeypatch.setattr(os, "environ", dict(os.environ))
    os.environ["JWT_SECRET_KEY"] = "a-random-secret-key-with-at-least-32-bytes"

    def override_get_db():
        with Session(engine) as db:
            yield db

    main.app.dependency_overrides[get_db] = override_get_db
    contact_limiter._requests.clear()
    create_contact_limiter._requests.clear()
    with (
        patch("app.auth.get_cached_user", return_value=None),
        patch("app.auth.cache_user"),
        patch.object(main, "invalidate_user_cache"),
        TestClient(main.app) as test_client,
    ):
        yield test_client
    main.app.dependency_overrides.clear()


def register_and_login(client, mails, email="ada@example.com"):
    response = client.post(
        "/auth/register", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 201
    token = mails["verification"][-1][1]
    assert client.post("/auth/verify-email", json={"token": token}).status_code == 200
    tokens = client.post(
        "/auth/login", json={"email": email, "password": PASSWORD}
    ).json()
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def test_register_verify_login_and_read_profile(client, mails):
    response = client.post(
        "/auth/register", json={"email": "Ada@Example.com", "password": PASSWORD}
    )
    assert response.status_code == 201
    assert response.json()["email"] == "ada@example.com"
    assert "hashed_password" not in response.json()

    login = client.post(
        "/auth/login", json={"email": "ada@example.com", "password": PASSWORD}
    )
    assert login.status_code == 403

    headers = register_and_login(client, mails, "bob@example.com")
    me = client.get("/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["email"] == "bob@example.com"
    assert "hashed_password" not in me.json()


def test_duplicate_registration_conflicts(client, mails):
    body = {"email": "ada@example.com", "password": PASSWORD}
    assert client.post("/auth/register", json=body).status_code == 201
    assert client.post("/auth/register", json=body).status_code == 409


def test_protected_routes_require_valid_bearer_token(client):
    assert client.get("/auth/me").status_code == 401
    response = client.get("/auth/me", headers={"Authorization": "Bearer bad"})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_refresh_returns_new_access_token(client, mails):
    register_and_login(client, mails)
    tokens = client.post(
        "/auth/login", json={"email": "ada@example.com", "password": PASSWORD}
    ).json()

    response = client.post(
        "/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert response.status_code == 200
    assert response.json()["token_type"] == "bearer"
    rejected = client.post(
        "/auth/refresh", json={"refresh_token": tokens["access_token"]}
    )
    assert rejected.status_code == 401


def test_contact_crud_flow(client, mails):
    headers = register_and_login(client, mails)

    created = client.post("/contacts", json=CONTACT, headers=headers)
    assert created.status_code == 201
    contact_id = created.json()["id"]

    assert client.post("/contacts", json=CONTACT, headers=headers).status_code == 409

    listed = client.get("/contacts", params={"search": "ada"}, headers=headers)
    assert [item["id"] for item in listed.json()] == [contact_id]
    filtered = client.get(
        "/contacts",
        params={"first_name": "ad", "last_name": "love", "email": "contact"},
        headers=headers,
    )
    assert len(filtered.json()) == 1
    assert client.get(
        "/contacts", params={"first_name": "zzz"}, headers=headers
    ).json() == []

    assert client.get(f"/contacts/{contact_id}", headers=headers).status_code == 200
    updated = client.patch(
        f"/contacts/{contact_id}", json={"phone": "+1000"}, headers=headers
    )
    assert updated.json()["phone"] == "+1000"

    deleted = client.delete(f"/contacts/{contact_id}", headers=headers)
    assert deleted.status_code == 204
    assert client.get(f"/contacts/{contact_id}", headers=headers).status_code == 404
    assert client.delete(f"/contacts/{contact_id}", headers=headers).status_code == 404
    assert (
        client.patch(f"/contacts/{contact_id}", json={}, headers=headers).status_code
        == 404
    )


def test_contacts_are_isolated_between_users(client, mails):
    alice = register_and_login(client, mails, "alice@example.com")
    bob = register_and_login(client, mails, "bob@example.com")
    contact_id = client.post("/contacts", json=CONTACT, headers=alice).json()["id"]

    assert client.get("/contacts", headers=bob).json() == []
    assert client.get(f"/contacts/{contact_id}", headers=bob).status_code == 404


def test_upcoming_birthdays_route(client, mails):
    headers = register_and_login(client, mails)
    today = date.today()
    client.post(
        "/contacts", json={**CONTACT, "birthday": f"1990-{today:%m-%d}"}, headers=headers
    )
    client.post(
        "/contacts",
        json={**CONTACT, "email": "far@example.com", "birthday": "1990-01-01"}
        if today.month != 1 or today.day > 1
        else {**CONTACT, "email": "far@example.com", "birthday": "1990-07-01"},
        headers=headers,
    )

    response = client.get("/contacts/birthdays", headers=headers)
    assert response.status_code == 200
    assert [item["email"] for item in response.json()] == ["contact@example.com"]


def test_contact_creation_rate_limit_returns_429(client, mails):
    headers = register_and_login(client, mails)
    os.environ["CONTACT_CREATES_PER_MINUTE"] = "1"

    assert client.post("/contacts", json=CONTACT, headers=headers).status_code == 201
    response = client.post(
        "/contacts", json={**CONTACT, "email": "other@example.com"}, headers=headers
    )
    assert response.status_code == 429
    assert int(response.headers["retry-after"]) >= 1


def test_registration_reports_email_outage(client):
    with patch.object(main, "send_verification_email", side_effect=OSError("down")):
        response = client.post(
            "/auth/register", json={"email": "a@example.com", "password": PASSWORD}
        )
    assert response.status_code == 503


def test_resend_verification_branches(client, mails):
    client.post("/auth/register", json={"email": "a@example.com", "password": PASSWORD})
    body = {"email": "a@example.com"}
    assert client.post("/auth/resend-verification-email", json=body).status_code == 200
    assert len(mails["verification"]) == 2

    unknown = client.post(
        "/auth/resend-verification-email", json={"email": "nobody@example.com"}
    )
    assert unknown.status_code == 200
    assert len(mails["verification"]) == 2

    with patch.object(
        main, "send_verification_email", side_effect=smtplib.SMTPException("down")
    ):
        failed = client.post("/auth/resend-verification-email", json=body)
    assert failed.status_code == 503


def test_password_reset_flow_and_email_outage(client, mails):
    register_and_login(client, mails)
    body = {"email": "ada@example.com"}
    assert client.post("/auth/request-password-reset", json=body).status_code == 200
    token = mails["reset"][-1][1]

    reset = client.post(
        "/auth/reset-password", json={"token": token, "new_password": "new-secure-pass"}
    )
    assert reset.status_code == 200
    assert (
        client.post(
            "/auth/login", json={"email": "ada@example.com", "password": "new-secure-pass"}
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/auth/reset-password",
            json={"token": token, "new_password": "another-secure-pass"},
        ).status_code
        == 400
    )

    unknown = client.post(
        "/auth/request-password-reset", json={"email": "nobody@example.com"}
    )
    assert unknown.status_code == 200
    assert len(mails["reset"]) == 1

    with patch.object(main, "send_password_reset_email", side_effect=OSError("down")):
        failed = client.post("/auth/request-password-reset", json=body)
    assert failed.status_code == 503


def test_verify_email_link_route(client, mails):
    client.post("/auth/register", json={"email": "a@example.com", "password": PASSWORD})
    token = mails["verification"][-1][1]

    assert client.get("/auth/verify-email", params={"token": "short"}).status_code == 422
    assert client.get("/auth/verify-email", params={"token": token}).status_code == 200
    assert client.get("/auth/verify-email", params={"token": token}).status_code == 400


def test_avatar_upload_success_and_error_branches(client, mails, engine):
    headers = register_and_login(client, mails)
    files = {"image": ("a.png", BytesIO(b"png"), "image/png")}

    with patch.object(main, "upload_avatar", return_value="https://cdn/a.png"):
        response = client.post("/users/avatar", files=files, headers=headers)
    assert response.status_code == 200
    assert response.json()["avatar_url"] == "https://cdn/a.png"

    bad = {"image": ("a.gif", BytesIO(b"gif"), "image/gif")}
    assert client.post("/users/avatar", files=bad, headers=headers).status_code == 422

    with Session(engine) as db:
        user_id = db.scalar(select(User.id))
        db.delete(db.get(User, user_id))
        db.commit()
    stale = User(id=user_id, email="ada@example.com", is_email_verified=True)
    with (
        patch("app.auth.get_cached_user", return_value=stale),
        patch.object(main, "upload_avatar", return_value="https://cdn/a.png"),
    ):
        files = {"image": ("a.png", BytesIO(b"png"), "image/png")}
        gone = client.post("/users/avatar", files=files, headers=headers)
    assert gone.status_code == 401
