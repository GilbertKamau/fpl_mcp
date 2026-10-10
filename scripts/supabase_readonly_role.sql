-- SPDX-License-Identifier: MIT
-- Copyright (c) 2026 Gilbert Kamau
-- A read-only database user for the public MCP server.
-- Run in Supabase: SQL Editor > New query. Replace the password first.
-- Then use this user in the hosted server's DATABASE_URL (Session pooler format):
--   postgresql://fpl_reader.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres
-- Your own machine keeps using the main user, since fpl-sync and fpl-model write data.

create role fpl_reader with login password 'CHANGE-ME-to-a-long-random-password';
grant usage on schema public to fpl_reader;
grant select on all tables in schema public to fpl_reader;
alter default privileges in schema public grant select on tables to fpl_reader;

-- Optional hardening: Supabase's Data API lets the `anon` and `authenticated` roles reach tables in
-- `public`. This project doesn't use the Data API, so remove that access (skip this if other apps in
-- the same Supabase project rely on it).
revoke all on all tables in schema public from anon, authenticated;
alter default privileges in schema public revoke all on tables from anon, authenticated;
