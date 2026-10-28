# Queryly: AI-powered Text-to-SQL Agent

Queryly is a React + TypeScript frontend and FastAPI backend for an AI database assistant. It includes Supabase authentication, protected database connection and schema inspection, SQL generation, validation, and read-only query execution for SQLite, PostgreSQL, and MySQL. Database connection credentials are not persisted.

## Project layout

```text
backend/app/     FastAPI application and modular service boundaries
frontend/src/    React pages, auth UI, and Supabase client
.env.example     Backend environment template
```

## Configure Supabase

1. Create or select a Supabase project. In **Project Settings → API**, copy the Project URL and publishable key (legacy anon key also works).
2. Copy the repository `.env.example` to `.env` and set `SUPABASE_URL` and `SUPABASE_ANON_KEY`. These are used by the backend auth client. The backend only verifies access tokens; it does not store passwords or use a service-role key.
3. Copy `frontend/.env.example` to `frontend/.env.local` and set the same project URL and publishable key, plus the API URL if it differs from `http://localhost:8000`. Vite variables are public by design; never put a service-role or secret key in a `VITE_*` variable.
4. In **Authentication → URL Configuration**, use `http://localhost:5173` as the local Site URL. Add these Redirect URLs:
   - `http://localhost:5173/auth/callback` for email confirmation and OAuth.
   - `http://localhost:5173/reset-password` for password recovery.
   - If you access Vite as `http://127.0.0.1:5173`, add the equivalent callback and reset URLs for that host too.

### Google OAuth

1. In Google Cloud Console, configure the OAuth consent screen and create a **Web application** OAuth client.
2. Add `http://localhost:5173` under **Authorized JavaScript origins**.
3. Copy the exact callback URL displayed in Supabase under **Authentication → Providers → Google** (typically `https://<project-ref>.supabase.co/auth/v1/callback`) into Google's **Authorized redirect URIs**.
4. Enable Google under Supabase's Google provider settings and enter the Google client ID and client secret there. The secret stays in Supabase and is never sent to this frontend or API.

### Email confirmation and password recovery

Email/password signup sends users to `/auth/callback` when confirmation is required. Keep **Confirm email** enabled under **Authentication → Providers → Email** for verified email signups. Configure SMTP under **Project Settings → Authentication → SMTP Settings** for production delivery; the built-in mail service is intended for development and has delivery limits.

The forgot-password flow requests a Supabase recovery email with redirect URL `/reset-password`. The user then chooses a new password on that page. Ensure the exact URL is in Supabase's Redirect URLs list. Supabase's email templates should retain their recovery link and redirect behavior.

## Run locally

Use Python 3.10+ and Node.js 20+.

Backend terminal, from the repository root:

```bash
cd backend
python -m venv .venv
# Windows PowerShell: .\.venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Frontend terminal, from the repository root:

```bash
cd frontend
Copy-Item .env.example .env.local # PowerShell; edit the values before use
npm install
npm run dev
```

Open `http://localhost:5173`. The API health check is `http://localhost:8000/health`; the protected user endpoint is `http://localhost:8000/api/auth/me` and requires `Authorization: Bearer <Supabase access token>`.

## Test a database connection

The protected `POST /api/connections/test` endpoint accepts `{"connection_url":"..."}` and runs only `SELECT 1`. It never stores the URL. PostgreSQL and MySQL must use hosts that resolve only to public IP addresses; the resolved address is pinned for the connection and encrypted transport is required. Private, loopback, link-local, and reserved targets are blocked. MySQL URLs use SQLAlchemy's `mysql+pymysql` dialect; install `backend/requirements.txt` to include the PyMySQL driver.

SQLite accepts in-memory URLs such as `sqlite+pysqlite:///:memory:`. File URLs are read-only and must point to an existing file under `backend/data` by default. To use a different trusted local directory, configure `SQLITE_ALLOWED_DIRECTORY` for the backend. The endpoint will not create a missing SQLite file.

These are local-development safeguards, not a complete production network boundary. A deployed service should also apply outbound firewall rules and an explicit database-host allowlist; public IP checks cannot prevent access to every publicly exposed internal service. Do not persist user credentials until a dedicated secret-storage design is in place.

Run backend tests from `backend/` after installing `requirements-dev.txt`:

```bash
pip install -r requirements-dev.txt
python -m pytest
```

## Security notes

- Passwords are handled by Supabase Auth and are never stored by this application.
- The browser uses only the Supabase publishable/anon key. Never expose a Supabase secret or service-role key in frontend environment variables.
- The API verifies bearer tokens with Supabase's verified JWT claims endpoint before returning user identity fields.
- Keep `.env` and `frontend/.env.local` out of version control.
