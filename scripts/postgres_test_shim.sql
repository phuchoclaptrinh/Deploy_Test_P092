-- Supabase-compatible surface for a disposable PostgreSQL test database.
--
-- The Alembic chain references `auth.users`, `auth.uid()` and the Supabase
-- roles, because that is what the production database is. A plain PostgreSQL
-- instance has none of them, so `alembic upgrade head` fails on the first
-- migration that adds a foreign key onto `auth.users`.
--
-- This is the smallest surface that lets the chain run and lets the v4
-- end-to-end suite insert the `auth.users` rows that `user_profiles.user_id`
-- points at. It is a *test* shim: no password hashing, no sessions, no RLS
-- policies, and it is never applied to a real Supabase project, which already
-- has all of this.
--
-- It also plants the sentinel that `tests/e2e_postgres/` requires before it
-- truncates anything. That is the point of putting it here: applying this file
-- is a deliberate act against a database you chose, so the sentinel can only
-- exist somewhere a person decided was disposable. URL checks cannot establish
-- that - a migrated Railway, Neon or RDS database has a perfectly ordinary
-- PostgreSQL URL.
--
-- Apply once, before `alembic upgrade head`:
--     psql -v ON_ERROR_STOP=1 -f scripts/postgres_test_shim.sql -d "$V4_E2E_DATABASE_URL"
--
-- Everything here is idempotent and safe to re-apply.

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
    CREATE ROLE anon NOLOGIN NOINHERIT;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
    CREATE ROLE authenticated NOLOGIN NOINHERIT;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
    CREATE ROLE service_role NOLOGIN NOINHERIT BYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'supabase_auth_admin') THEN
    CREATE ROLE supabase_auth_admin NOLOGIN NOINHERIT;
  END IF;
END $$;

CREATE SCHEMA IF NOT EXISTS auth;
CREATE SCHEMA IF NOT EXISTS extensions;

CREATE TABLE IF NOT EXISTS auth.users (
    id uuid PRIMARY KEY,
    email varchar(255),
    phone varchar(32),
    raw_user_meta_data jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- The three functions the migrations and RLS definitions call. They read the
-- same `request.jwt.*` settings PostgREST sets, so a policy written against
-- Supabase behaves the same way here when the setting is present, and falls
-- back to anon when it is not.
CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid
LANGUAGE sql STABLE AS $$
  SELECT NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid
$$;

CREATE OR REPLACE FUNCTION auth.role() RETURNS text
LANGUAGE sql STABLE AS $$
  SELECT COALESCE(NULLIF(current_setting('request.jwt.claim.role', true), ''), 'anon')
$$;

CREATE OR REPLACE FUNCTION auth.jwt() RETURNS jsonb
LANGUAGE sql STABLE AS $$
  SELECT COALESCE(NULLIF(current_setting('request.jwt.claims', true), ''), '{}')::jsonb
$$;

GRANT USAGE ON SCHEMA auth TO anon, authenticated, service_role;
GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role;
GRANT SELECT ON auth.users TO authenticated, service_role;

-- ---------------------------------------------------------------------------
-- The disposable-database sentinel.
--
-- Deliberately a real table in `public`, not a temporary one: a temporary table
-- disappears with the session that made it, so it would prove nothing about the
-- database a later pytest run connects to. It is never listed in E2E_TABLES and
-- has no foreign keys, so no `TRUNCATE ... CASCADE` in the suite can reach it.
--
-- The marker is a fixed, project-specific string, not a secret. Its only job is
-- to be absent from every database nobody deliberately marked.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.v4_e2e_disposable_guard (
    guard_key text PRIMARY KEY,
    guard_value text NOT NULL
);

INSERT INTO public.v4_e2e_disposable_guard (guard_key, guard_value)
VALUES ('suite', 'fixit-v4-e2e-disposable-database')
ON CONFLICT (guard_key) DO UPDATE SET guard_value = EXCLUDED.guard_value;
