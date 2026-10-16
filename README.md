# Contacts API

A FastAPI REST API that stores contacts in PostgreSQL. Interactive API documentation
is available at `/docs`.

## Setup and run

Requires Python 3.10+, Docker Compose, and Bash. Run commands from the project
root. For Docker Compose, start with the containerized setup below; for local API
development, use the separate local section instead.

### 1. Create the Python environment

Run these commands once. If `.env` already exists, keep it and do not overwrite it.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
test -f .env || cp .env.example .env
```

Before starting the API, replace the sample `JWT_SECRET_KEY` in `.env` with a
unique value. Run the command below and paste its output into `.env`. Set the
Cloudinary values only if you want to test avatar uploads. Do not commit `.env`.

```bash
openssl rand -hex 32
```

### 2. Start with Docker Compose

This starts the API, PostgreSQL, Redis, and Mailpit. The API applies Alembic
migrations before it starts.

```bash
docker compose up -d --build
docker compose ps
```

After the API container is healthy, verify that the API responds:

```bash
curl -fsS http://127.0.0.1:8000/openapi.json >/dev/null
```

The interactive API docs are at <http://127.0.0.1:8000/docs> and
<http://127.0.0.1:8000/redoc>. Mailpit captures verification and password-reset
emails at <http://127.0.0.1:8025>.

### 3. Run the API locally

Use this option instead of starting the Compose `api` service. Compose provides
the database, Redis, and Mailpit; the local API reads connection settings from
`.env`. Make sure `DATABASE_URL` uses the host PostgreSQL port (`5432` by
default).

```bash
docker compose stop api
docker compose up -d db redis mailpit
source .venv/bin/activate
alembic upgrade head
uvicorn app.main:app --reload
```

Stop the local server with `Ctrl-C`. To stop the Compose services when finished:

```bash
docker compose down
```

The named database and Redis volumes are preserved. `.env.example` contains the
full list of settings. Compose uses the internal `db` hostname for its API
container; a locally run API must use `localhost` and the published PostgreSQL
port. If you change `POSTGRES_PORT`, update `DATABASE_URL` to match.

JWT configuration is read from these `.env` variables:

- `JWT_SECRET_KEY` — private HMAC signing secret; use a random value of at least
  32 bytes.
- `JWT_ALGORITHM` — one of `HS256`, `HS384`, or `HS512` (default: `HS256`).
- `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` — access-token lifetime in minutes
  (default: `15`).
- `JWT_REFRESH_TOKEN_EXPIRE_DAYS` — refresh-token lifetime in days
  (default: `7`).

`ALLOWED_ORIGINS` is a comma-separated list of browser origins allowed by CORS.
Contact routes are limited to 60 requests per minute per user and route by
default. Contact creation has a stricter per-user limit of 10 requests per
minute. Configure these with `CONTACT_REQUESTS_PER_MINUTE` and
`CONTACT_CREATES_PER_MINUTE`. Limits are process-local and reset when the API
restarts.

Redis caches authenticated user data for 15 minutes by default. Redis connection
settings are `REDIS_HOST`, `REDIS_PORT`, `REDIS_DB`, optional `REDIS_PASSWORD`,
and `REDIS_CACHE_TTL_SECONDS`. A Redis outage is logged and authentication
continues using PostgreSQL.

Email settings are `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`,
`SMTP_FROM_EMAIL`, `SMTP_USE_STARTTLS`, and `APP_BASE_URL`. Compose connects
email delivery to Mailpit. Verification tokens expire after 24 hours and
password reset tokens after one hour; only token hashes are stored in the
database. For Cloudinary avatar uploads, set `CLOUDINARY_CLOUD_NAME`,
`CLOUDINARY_API_KEY`, and `CLOUDINARY_API_SECRET`.

Do not commit `.env` or reuse the example secret outside local development.

## Database maintenance

The database schema is managed with Alembic. For an existing database created
before migrations were added, stop the API and run the following from the
project root with the virtual environment activated and `.env` pointing to that
database. The first command marks the existing contacts schema; the second
applies later migrations without replacing existing contacts.

```bash
alembic stamp 20261012_0001
alembic upgrade head
```

After registering the account that should own legacy contacts, assign all
contacts without an owner to it:

```bash
python -m app.backfill_contacts --email owner@example.com
```

The command reports how many contacts were assigned. Check the address before
running it; assignment applies to every contact without an owner.

## Endpoints

- `POST /auth/register` — register with an email and password (returns `201`;
  an already registered email returns `409`). A verification email is sent.
- `GET /auth/verify-email?token=...` — one-click verification link sent by email.
  `POST /auth/verify-email` also accepts `{"token":"..."}` for API clients.
- `POST /auth/resend-verification-email` — request another verification message.
- `POST /auth/login` — authenticate a verified account and receive an
  access/refresh token pair; an unverified account receives `403`.
- `POST /auth/refresh` — exchange a refresh token from the request body for a
  new access token. Access tokens expire after 15 minutes by default; refresh
  tokens expire after 7 days by default.
- `GET /auth/me` — return the current authenticated user's profile.
- `POST /auth/request-password-reset` — request a password reset email. The
  response does not reveal whether the account exists.
- `POST /auth/reset-password` — set a new password using the emailed token and
  `new_password`.
- `POST /users/avatar` — upload a JPEG, PNG, or WebP image (maximum 5 MB) using
  multipart form field `image`; the returned profile contains the Cloudinary URL.
- `POST /contacts` — create a contact (returns `201`).
- `GET /contacts` — list the current user's contacts. Optional `search` searches
  first name, last name, and email; `first_name`, `last_name`, and `email` can
  also filter individually.
- `GET /contacts/birthdays` — list the current user's contacts whose birthday
  falls today or in the next six days, including across the end of the year.
- `GET /contacts/{contact_id}` — retrieve one of the current user's contacts.
- `PUT /contacts/{contact_id}` or `PATCH /contacts/{contact_id}` — update one of
  the current user's contacts; both accept partial fields.
- `DELETE /contacts/{contact_id}` — delete one of the current user's contacts
  (returns `204`).

All `/contacts` endpoints require an access token in the
`Authorization: Bearer <access_token>` header. New contacts are automatically
owned by the authenticated user; `owner_id` cannot be supplied in the request
body. Contacts belonging to another user return `404` for individual lookups,
updates, and deletes and are excluded from lists and birthday results.

Contact fields are `first_name`, `last_name`, `email`, `phone`, `birthday`
(ISO `YYYY-MM-DD`), and optional `extra_data`. Email addresses must be valid and
unique. Invalid input returns `422`, missing contacts return `404`, and duplicate
emails return `409`.

User registration accepts a valid email and a password between 8 and 128
characters. Passwords are stored as Argon2 hashes; registration responses
contain only the user's `id` and `email`. Unverified users cannot log in.

Login returns `access_token`, `refresh_token`, `token_type`, `expires_in`, and
`refresh_expires_in`. The refresh endpoint accepts
`{"refresh_token":"<refresh_token>"}` and returns a new access token and its
expiration. Use the access token in the `Authorization: Bearer <access_token>`
header for every `/contacts` operation. Missing, invalid, expired,
wrong-purpose tokens, or tokens for deleted users return `401`. Refreshing
returns a new access token; the existing refresh token remains valid until expiry.

## Run the API walkthrough

Start the services as described above, then run these numbered sections in order
in the same Bash terminal from the project root. Run each code block when you
reach it; variables created in earlier sections are reused later. The demo
creates users and contacts in the database, so use the generated email addresses.

### 1. Configure and start services

Set a unique `JWT_SECRET_KEY` in `.env` by generating one with `openssl rand -hex
32` and pasting the output into the file. Set the Cloudinary credentials there
to test avatar uploads. Start the API, PostgreSQL, Redis, and Mailpit:

```bash
docker compose up -d --build
docker compose ps
```

Wait for the API to answer `200` before continuing:

```bash
export BASE_URL=http://127.0.0.1:8000
export MAILPIT_URL=http://127.0.0.1:8025
curl -i "$BASE_URL/openapi.json"
```

### 2. Set demo user values

This generates fresh email addresses so the walkthrough can be run again:

```bash
DEMO_SUFFIX=$(date +%s)
export ADA_EMAIL="ada-${DEMO_SUFFIX}@example.com"
export ADA_PASSWORD=a-secure-password
export GRACE_EMAIL="grace-${DEMO_SUFFIX}@example.com"
export GRACE_PASSWORD=another-secure-password
```

### 3. Register Ada

`201 Created` means registration succeeded. The account is not verified yet:

```bash
curl -i -X POST "$BASE_URL/auth/register" \
  -H 'Content-Type: application/json' \
  -d "{\"email\":\"$ADA_EMAIL\",\"password\":\"$ADA_PASSWORD\"}"
```

### 4. Resend and verify Ada's email

First confirm that login is blocked with `403`, then request a fresh message:

```bash
curl -i -X POST "$BASE_URL/auth/login" \
  -H 'Content-Type: application/json' \
  -d "{\"email\":\"$ADA_EMAIL\",\"password\":\"$ADA_PASSWORD\"}"
curl -i -X POST "$BASE_URL/auth/resend-verification-email" \
  -H 'Content-Type: application/json' \
  -d "{\"email\":\"$ADA_EMAIL\"}"
```

The next commands locate Ada's latest Mailpit message, extract its one-time link
token without printing it, and verify the account. Expect `200 OK`. Use this token
only once; resending replaces the previous token.

```bash
ADA_MAIL_ID=$(curl -fsS "$MAILPIT_URL/api/v1/messages" |
  python3 -c 'import json,sys; email=sys.argv[1].lower(); messages=json.load(sys.stdin)["messages"]; print(next(m["ID"] for m in messages if any(r["Address"].lower()==email for r in m["To"])))' "$ADA_EMAIL")
ADA_VERIFY_TOKEN=$(curl -fsS "$MAILPIT_URL/api/v1/message/$ADA_MAIL_ID" |
  python3 -c 'import json,re,sys; text=json.load(sys.stdin)["Text"]; match=re.search(r"[?&]token=([^&\s]+)",text); print(match.group(1) if match else "")')
curl -i "$BASE_URL/auth/verify-email?token=$ADA_VERIFY_TOKEN"
```

### 5. Log in and save tokens

After email verification, login returns an access token and a refresh token:

```bash
ADA_TOKENS=$(curl -fsS -X POST "$BASE_URL/auth/login" \
  -H 'Content-Type: application/json' \
  -d "{\"email\":\"$ADA_EMAIL\",\"password\":\"$ADA_PASSWORD\"}")
ADA_ACCESS_TOKEN=$(printf '%s' "$ADA_TOKENS" |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')
ADA_REFRESH_TOKEN=$(printf '%s' "$ADA_TOKENS" |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["refresh_token"])')
```

### 6. Check the profile and Redis cache

The first `/auth/me` request fills the cache; subsequent requests can use it.
`TTL` prints the remaining cache lifetime in seconds.

```bash
curl -sS "$BASE_URL/auth/me" \
  -H "Authorization: Bearer $ADA_ACCESS_TOKEN"
ADA_USER_ID=$(curl -fsS "$BASE_URL/auth/me" \
  -H "Authorization: Bearer $ADA_ACCESS_TOKEN" |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
docker compose exec redis redis-cli GET "user:$ADA_USER_ID"
docker compose exec redis redis-cli TTL "user:$ADA_USER_ID"
```

### 7. Create a contact

Use a unique contact email. The response contains its ID for the following steps:

```bash
ADA_CONTACT=$(curl -fsS -X POST "$BASE_URL/contacts" \
  -H "Authorization: Bearer $ADA_ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d "{\"first_name\":\"Ada\",\"last_name\":\"Lovelace\",\"email\":\"ada-contact-${DEMO_SUFFIX}@example.com\",\"phone\":\"+441234567890\",\"birthday\":\"1815-12-10\",\"extra_data\":\"Demo contact\"}")
ADA_CONTACT_ID=$(printf '%s' "$ADA_CONTACT" |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
printf '%s\n' "$ADA_CONTACT"
```

### 8. List and search contacts

The new contact should appear in both responses:

```bash
curl -sS "$BASE_URL/contacts" \
  -H "Authorization: Bearer $ADA_ACCESS_TOKEN"
curl -sS "$BASE_URL/contacts?search=Ada" \
  -H "Authorization: Bearer $ADA_ACCESS_TOKEN"
```

### 9. Retrieve and update the contact

These requests should return the contact, first unchanged and then with the
updated first name:

```bash
curl -sS "$BASE_URL/contacts/$ADA_CONTACT_ID" \
  -H "Authorization: Bearer $ADA_ACCESS_TOKEN"
curl -i -X PATCH "$BASE_URL/contacts/$ADA_CONTACT_ID" \
  -H "Authorization: Bearer $ADA_ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"first_name":"Augusta"}'
```

### 10. Check contact ownership

Register and verify a second user. Grace should see an empty list and get `404`
when requesting Ada's contact.

```bash
curl -i -X POST "$BASE_URL/auth/register" \
  -H 'Content-Type: application/json' \
  -d "{\"email\":\"$GRACE_EMAIL\",\"password\":\"$GRACE_PASSWORD\"}"
GRACE_MAIL_ID=$(curl -fsS "$MAILPIT_URL/api/v1/messages" |
  python3 -c 'import json,sys; email=sys.argv[1].lower(); messages=json.load(sys.stdin)["messages"]; print(next(m["ID"] for m in messages if any(r["Address"].lower()==email for r in m["To"])))' "$GRACE_EMAIL")
GRACE_VERIFY_TOKEN=$(curl -fsS "$MAILPIT_URL/api/v1/message/$GRACE_MAIL_ID" |
  python3 -c 'import json,re,sys; text=json.load(sys.stdin)["Text"]; match=re.search(r"[?&]token=([^&\s]+)",text); print(match.group(1) if match else "")')
curl -i -X POST "$BASE_URL/auth/verify-email" \
  -H 'Content-Type: application/json' \
  -d "{\"token\":\"$GRACE_VERIFY_TOKEN\"}"
GRACE_TOKENS=$(curl -fsS -X POST "$BASE_URL/auth/login" \
  -H 'Content-Type: application/json' \
  -d "{\"email\":\"$GRACE_EMAIL\",\"password\":\"$GRACE_PASSWORD\"}")
GRACE_ACCESS_TOKEN=$(printf '%s' "$GRACE_TOKENS" |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')
curl -sS "$BASE_URL/contacts" \
  -H "Authorization: Bearer $GRACE_ACCESS_TOKEN"
curl -i "$BASE_URL/contacts/$ADA_CONTACT_ID" \
  -H "Authorization: Bearer $GRACE_ACCESS_TOKEN"
```

### 11. Delete the contact

Ada can delete her own contact; expect `204 No Content`:

```bash
curl -i -X DELETE "$BASE_URL/contacts/$ADA_CONTACT_ID" \
  -H "Authorization: Bearer $ADA_ACCESS_TOKEN"
```

### 12. Refresh the access token

The refresh token returns a replacement access token:

```bash
ADA_REFRESHED=$(curl -fsS -X POST "$BASE_URL/auth/refresh" \
  -H 'Content-Type: application/json' \
  -d "{\"refresh_token\":\"$ADA_REFRESH_TOKEN\"}")
ADA_ACCESS_TOKEN=$(printf '%s' "$ADA_REFRESHED" |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')
```

### 13. Verify contact rate limits

Rate limits are process-local. To test the general per-route limit quickly,
temporarily set `CONTACT_REQUESTS_PER_MINUTE=1` in `.env`, then recreate the API:

```bash
docker compose up -d --force-recreate api
until curl -fsS "$BASE_URL/openapi.json" >/dev/null; do sleep 1; done
curl -i "$BASE_URL/contacts" -H "Authorization: Bearer $ADA_ACCESS_TOKEN"
curl -i "$BASE_URL/contacts" -H "Authorization: Bearer $ADA_ACCESS_TOKEN"
```

The first list request should return `200`; the second should return `429` and
include `Retry-After`. Restore `CONTACT_REQUESTS_PER_MINUTE=60` before testing
creation. Then set `CONTACT_CREATES_PER_MINUTE=1`, recreate the API, create a
contact once, and repeat the POST with a different contact email. The second
POST should return `429`.

```bash
docker compose up -d --force-recreate api
until curl -fsS "$BASE_URL/openapi.json" >/dev/null; do sleep 1; done
LIMIT_CONTACT=$(curl -fsS -X POST "$BASE_URL/contacts" \
  -H "Authorization: Bearer $ADA_ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d "{\"first_name\":\"Limit\",\"last_name\":\"Demo\",\"email\":\"limit-${DEMO_SUFFIX}@example.com\",\"phone\":\"+10000000001\",\"birthday\":\"1990-01-01\"}")
LIMIT_CONTACT_ID=$(printf '%s' "$LIMIT_CONTACT" |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
curl -i -X POST "$BASE_URL/contacts" \
  -H "Authorization: Bearer $ADA_ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d "{\"first_name\":\"Limit\",\"last_name\":\"Demo\",\"email\":\"limit-second-${DEMO_SUFFIX}@example.com\",\"phone\":\"+10000000002\",\"birthday\":\"1990-01-01\"}"
```

Restore `CONTACT_CREATES_PER_MINUTE=10`, recreate the API, and delete the one
created test contact:

```bash
docker compose up -d --force-recreate api
until curl -fsS "$BASE_URL/openapi.json" >/dev/null; do sleep 1; done
curl -i -X DELETE "$BASE_URL/contacts/$LIMIT_CONTACT_ID" \
  -H "Authorization: Bearer $ADA_ACCESS_TOKEN"
```

### 14. Check CORS

The Origin must be listed in `ALLOWED_ORIGINS` in `.env`. The response should
include `Access-Control-Allow-Origin: http://localhost:3000`.

```bash
curl -i -X OPTIONS "$BASE_URL/auth/login" \
  -H 'Origin: http://localhost:3000' \
  -H 'Access-Control-Request-Method: POST' \
  -H 'Access-Control-Request-Headers: content-type'
```

### 15. Upload an avatar to Cloudinary

Set Cloudinary credentials in `.env` and provide a real JPEG, PNG, or WebP image
under 5 MB. The response should include `avatar_url`.

```bash
curl -i -X POST "$BASE_URL/users/avatar" \
  -H "Authorization: Bearer $ADA_ACCESS_TOKEN" \
  -F 'image=@./avatar.png;type=image/png'
curl -sS "$BASE_URL/auth/me" \
  -H "Authorization: Bearer $ADA_ACCESS_TOKEN"
```

### 16. Request and complete a password reset

Request reset for Ada and for a nonexistent address; both return the same
message. The token expires in one hour and is single-use.

```bash
curl -i -X POST "$BASE_URL/auth/request-password-reset" \
  -H 'Content-Type: application/json' \
  -d "{\"email\":\"$ADA_EMAIL\"}"
curl -i -X POST "$BASE_URL/auth/request-password-reset" \
  -H 'Content-Type: application/json' \
  -d "{\"email\":\"unknown-${DEMO_SUFFIX}@example.com\"}"
```

Extract the token from Ada's newest Mailpit reset email, set a new password, and
submit it. Login with the new password should return `200`; reusing the token
should return `400`.

```bash
RESET_MAIL_ID=$(curl -fsS "$MAILPIT_URL/api/v1/messages" |
  python3 -c 'import json,sys; email=sys.argv[1].lower(); messages=json.load(sys.stdin)["messages"]; print(next(m["ID"] for m in messages if any(r["Address"].lower()==email for r in m["To"])))' "$ADA_EMAIL")
ADA_RESET_TOKEN=$(curl -fsS "$MAILPIT_URL/api/v1/message/$RESET_MAIL_ID" |
  python3 -c 'import json,re,sys; text=json.load(sys.stdin)["Text"]; match=re.search(r"(?m)^([A-Za-z0-9_-]{30,})\\r?$",text); print(match.group(1) if match else "")')
export ADA_NEW_PASSWORD=a-different-secure-password
curl -i -X POST "$BASE_URL/auth/reset-password" \
  -H 'Content-Type: application/json' \
  -d "{\"token\":\"$ADA_RESET_TOKEN\",\"new_password\":\"$ADA_NEW_PASSWORD\"}"
curl -i -X POST "$BASE_URL/auth/login" \
  -H 'Content-Type: application/json' \
  -d "{\"email\":\"$ADA_EMAIL\",\"password\":\"$ADA_NEW_PASSWORD\"}"
curl -i -X POST "$BASE_URL/auth/reset-password" \
  -H 'Content-Type: application/json' \
  -d "{\"token\":\"$ADA_RESET_TOKEN\",\"new_password\":\"$ADA_NEW_PASSWORD\"}"
```

## Run tests

Install the project dependencies first if you have not already done so:

```bash
source .venv/bin/activate
```

Run the complete test suite and require at least 95% application coverage:

```bash
python -m pytest -q tests \
  --cov=app --cov-report=term-missing --cov-fail-under=95
```

To run the `unittest` tests separately:

```bash
python -m unittest discover -s tests -p 'test_*.py' -v
```

## Build and view Sphinx documentation

Build the HTML documentation from the project root:

```bash
python -m sphinx -b html docs docs/_build/html
```

Serve the generated site locally:

```bash
python -m http.server --directory docs/_build/html 8000
```

Then open <http://127.0.0.1:8000> in a browser. Stop the server with `Ctrl-C`.
