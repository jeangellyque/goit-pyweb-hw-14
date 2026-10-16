import asyncio
import json
import os
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.routing import APIRoute
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.auth import get_current_user
from app.main import (
    app,
    create_contact,
    delete_contact,
    get_contact,
    list_contacts,
    update_contact,
)
from app.models import Contact, User
from app.rate_limit import limit_contact_creation, limit_contact_requests
from app.schemas import ContactCreate, ContactUpdate


class ContactCrudTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.user = User(email="owner@example.com", hashed_password="already-hashed")
        self.db.add(self.user)
        self.db.commit()
        self.contact_data = {
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": "ada@example.com",
            "phone": "+441234567890",
            "birthday": "1815-12-10",
        }

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def create_sample_contact(self):
        return create_contact(ContactCreate(**self.contact_data), self.user, self.db)

    def override_database_dependency(self):
        def override_get_db():
            with Session(self.engine) as db:
                yield db

        app.dependency_overrides[get_db] = override_get_db

    def send_asgi_request(self, method, path, payload=None, headers=()):
        body = b"" if payload is None else json.dumps(payload).encode()
        messages = []
        request_sent = False

        async def receive():
            nonlocal request_sent
            if request_sent:
                return {"type": "http.disconnect"}
            request_sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message):
            messages.append(message)

        raw_path = path.encode()
        request_headers = [(b"host", b"testserver")]
        if payload is not None:
            request_headers.append((b"content-type", b"application/json"))
        request_headers.extend(
            (name.encode(), value.encode()) for name, value in headers
        )
        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": raw_path,
            "query_string": b"",
            "root_path": "",
            "headers": request_headers,
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
        }
        asyncio.run(app(scope, receive, send))
        response_status = next(
            message["status"]
            for message in messages
            if message["type"] == "http.response.start"
        )
        response_body = b"".join(
            message.get("body", b"")
            for message in messages
            if message["type"] == "http.response.body"
        )
        return response_status, json.loads(response_body) if response_body else None

    def test_create_list_and_get_contact(self):
        created = self.create_sample_contact()

        self.assertEqual(created.id, 1)
        self.assertEqual(
            list_contacts(
                search=None,
                first_name=None,
                last_name=None,
                email=None,
                current_user=self.user,
                db=self.db,
            ),
            [created],
        )
        self.assertEqual(get_contact(created.id, self.user, self.db), created)

        with self.assertRaises(HTTPException) as error:
            get_contact(999, self.user, self.db)
        self.assertEqual(error.exception.status_code, 404)

    def test_update_contact_changes_only_supplied_fields(self):
        created = self.create_sample_contact()

        updated = update_contact(
            created.id,
            ContactUpdate(first_name="Augusta"),
            self.user,
            self.db,
        )

        self.assertEqual(updated.first_name, "Augusta")
        self.assertEqual(updated.last_name, "Lovelace")
        self.assertEqual(updated.email, "ada@example.com")

    def test_delete_contact_and_missing_contact_responses(self):
        created = self.create_sample_contact()

        response = delete_contact(created.id, self.user, self.db)

        self.assertEqual(response.status_code, 204)
        with self.assertRaises(HTTPException) as error:
            delete_contact(created.id, self.user, self.db)
        self.assertEqual(error.exception.status_code, 404)

    def test_duplicate_email_returns_conflict(self):
        self.create_sample_contact()
        duplicate = ContactCreate(
            **{**self.contact_data, "first_name": "Grace", "phone": "+15555555555"}
        )

        with self.assertRaises(HTTPException) as error:
            create_contact(duplicate, self.user, self.db)
        self.assertEqual(error.exception.status_code, 409)

    def test_user_cannot_read_update_or_delete_another_users_contact(self):
        created = self.create_sample_contact()
        other_user = User(email="other@example.com", hashed_password="already-hashed")
        self.db.add(other_user)
        self.db.commit()

        self.assertEqual(
            list_contacts(
                search=None,
                first_name=None,
                last_name=None,
                email=None,
                current_user=other_user,
                db=self.db,
            ),
            [],
        )
        with self.assertRaises(HTTPException) as get_error:
            get_contact(created.id, other_user, self.db)
        self.assertEqual(get_error.exception.status_code, 404)

        with self.assertRaises(HTTPException) as update_error:
            update_contact(
                created.id,
                ContactUpdate(first_name="Changed"),
                other_user,
                self.db,
            )
        self.assertEqual(update_error.exception.status_code, 404)
        self.assertEqual(self.db.get(Contact, created.id).first_name, "Ada")

        with self.assertRaises(HTTPException) as delete_error:
            delete_contact(created.id, other_user, self.db)
        self.assertEqual(delete_error.exception.status_code, 404)
        self.assertIsNotNone(self.db.get(Contact, created.id))

    def test_create_contact_assigns_authenticated_user_and_does_not_accept_owner_id(self):
        created = self.create_sample_contact()

        self.assertEqual(created.owner_id, self.user.id)
        with self.assertRaises(ValidationError):
            ContactCreate(**{**self.contact_data, "owner_id": 999})

    def test_create_and_delete_routes_use_expected_status_codes(self):
        route_statuses = {
            (method, route.path): route.status_code
            for route in app.routes
            if isinstance(route, APIRoute)
            for method in route.methods
        }

        self.assertEqual(route_statuses[("POST", "/contacts")], 201)
        self.assertEqual(route_statuses[("DELETE", "/contacts/{contact_id}")], 204)
        for route in app.routes:
            if isinstance(route, APIRoute) and route.path.startswith("/contacts"):
                self.assertIn(
                    get_current_user,
                    {dependency.call for dependency in route.dependant.dependencies},
                )
                route_dependencies = {
                    dependency.call for dependency in route.dependant.dependencies
                }
                self.assertIn(limit_contact_requests, route_dependencies)
                if route.path == "/contacts" and "POST" in route.methods:
                    self.assertIn(limit_contact_creation, route_dependencies)

    def test_anonymous_requests_to_every_contact_operation_return_401(self):
        self.override_database_dependency()
        requests = (
            ("POST", "/contacts", self.contact_data),
            ("GET", "/contacts", None),
            ("GET", "/contacts/birthdays", None),
            ("GET", "/contacts/1", None),
            ("PUT", "/contacts/1", {"first_name": "Augusta"}),
            ("PATCH", "/contacts/1", {"first_name": "Augusta"}),
            ("DELETE", "/contacts/1", None),
        )
        try:
            for method, path, payload in requests:
                with self.subTest(method=method, path=path):
                    status_code, _ = self.send_asgi_request(method, path, payload)
                    self.assertEqual(
                        status_code,
                        401,
                    )
        finally:
            app.dependency_overrides.pop(get_db, None)

    def test_full_authenticated_http_flow_and_contact_isolation(self):
        environment = {
            "JWT_SECRET_KEY": "a-random-secret-key-with-at-least-32-bytes",
            "JWT_ALGORITHM": "HS256",
            "JWT_ACCESS_TOKEN_EXPIRE_MINUTES": "15",
            "JWT_REFRESH_TOKEN_EXPIRE_DAYS": "7",
        }
        self.override_database_dependency()

        try:
            with patch.dict(os.environ, environment, clear=True):
                verification_tokens = []
                with patch(
                    "app.main.send_verification_email",
                    side_effect=lambda email, token: verification_tokens.append(token),
                ):
                    status_code, ada = self.send_asgi_request(
                        "POST",
                        "/auth/register",
                        {"email": "ada@example.com", "password": "a-secure-password"},
                    )
                self.assertEqual(status_code, 201)
                self.assertEqual(set(ada), {"id", "email"})

                status_code, _ = self.send_asgi_request(
                    "POST",
                    "/auth/verify-email",
                    {"token": verification_tokens.pop()},
                )
                self.assertEqual(status_code, 200)
                status_code, ada_tokens = self.send_asgi_request(
                    "POST",
                    "/auth/login",
                    {"email": "ada@example.com", "password": "a-secure-password"},
                )
                self.assertEqual(status_code, 200)
                self.assertIn("access_token", ada_tokens)
                self.assertIn("refresh_token", ada_tokens)

                status_code, created = self.send_asgi_request(
                    "POST",
                    "/contacts",
                    self.contact_data,
                    (("authorization", f"Bearer {ada_tokens['access_token']}"),),
                )
                self.assertEqual(status_code, 201)
                contact_id = created["id"]

                with patch(
                    "app.main.send_verification_email",
                    side_effect=lambda email, token: verification_tokens.append(token),
                ):
                    self.send_asgi_request(
                        "POST",
                        "/auth/register",
                        {
                            "email": "grace@example.com",
                            "password": "another-secure-password",
                        },
                    )
                status_code, _ = self.send_asgi_request(
                    "POST",
                    "/auth/verify-email",
                    {"token": verification_tokens.pop()},
                )
                self.assertEqual(status_code, 200)
                status_code, grace_tokens = self.send_asgi_request(
                    "POST",
                    "/auth/login",
                    {
                        "email": "grace@example.com",
                        "password": "another-secure-password",
                    },
                )
                self.assertEqual(status_code, 200)
                grace_authorization = (
                    ("authorization", f"Bearer {grace_tokens['access_token']}"),
                )
                status_code, contacts = self.send_asgi_request(
                    "GET",
                    "/contacts",
                    headers=grace_authorization,
                )
                self.assertEqual(status_code, 200)
                self.assertEqual(contacts, [])

                for method, payload in (
                    ("GET", None),
                    ("PUT", {"first_name": "Changed"}),
                    ("PATCH", {"first_name": "Changed"}),
                    ("DELETE", None),
                ):
                    with self.subTest(method=method, foreign_contact=contact_id):
                        status_code, _ = self.send_asgi_request(
                            method,
                            f"/contacts/{contact_id}",
                            payload,
                            grace_authorization,
                        )
                        self.assertEqual(status_code, 404)

                status_code, refreshed = self.send_asgi_request(
                    "POST",
                    "/auth/refresh",
                    {"refresh_token": ada_tokens["refresh_token"]},
                )
                self.assertEqual(status_code, 200)
                status_code, owned_contact = self.send_asgi_request(
                    "GET",
                    f"/contacts/{contact_id}",
                    headers=(
                        ("authorization", f"Bearer {refreshed['access_token']}"),
                    ),
                )
                self.assertEqual(status_code, 200)
                self.assertEqual(owned_contact["id"], contact_id)
        finally:
            app.dependency_overrides.pop(get_db, None)


if __name__ == "__main__":
    unittest.main()
