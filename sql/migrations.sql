-- SF Worker Migrations
-- Run these in Supabase SQL Editor (Dashboard > SQL Editor > New Query)

-- 1. Add verification_email column for Microsoft SSO
ALTER TABLE user_sf_profiles ADD COLUMN IF NOT EXISTS verification_email text;

-- 2. Worker heartbeat table for remote monitoring
CREATE TABLE IF NOT EXISTS worker_heartbeats (
    worker_id text PRIMARY KEY,
    last_heartbeat timestamptz NOT NULL DEFAULT now(),
    active_bots integer DEFAULT 0,
    active_entries integer DEFAULT 0,
    active_users jsonb DEFAULT '[]'::jsonb,
    status text DEFAULT 'running'
);

-- Allow service role to read/write heartbeats
ALTER TABLE worker_heartbeats ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Service role can manage heartbeats"
    ON worker_heartbeats
    FOR ALL
    USING (true)
    WITH CHECK (true);
