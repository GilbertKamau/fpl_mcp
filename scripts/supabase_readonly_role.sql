-- SPDX-License-Identifier: MIT
-- Copyright (c) 2026 Gilbert Kamau
-- Read-only user for the hosted server; set a password, run in Supabase's SQL editor.

create role fpl_reader with login password 'CHANGE-ME-to-a-long-random-password';
grant usage on schema public to fpl_reader;
grant select on all tables in schema public to fpl_reader;
alter default privileges in schema public grant select on tables to fpl_reader;

-- Optional: close Supabase Data API access (skip if other apps use it).
revoke all on all tables in schema public from anon, authenticated;
alter default privileges in schema public revoke all on tables from anon, authenticated;
