-- Run this in Supabase SQL Editor
-- Creates user_sf_profiles table and adds columns to crm_entries

-- New table: user_sf_profiles
CREATE TABLE IF NOT EXISTS user_sf_profiles (
    id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    user_id uuid NOT NULL UNIQUE REFERENCES auth.users(id) ON DELETE CASCADE,
    sf_instance_url text NOT NULL,
    sf_username text NOT NULL,
    sf_password text NOT NULL,
    profile_path text,
    session_valid boolean DEFAULT true,
    needs_mfa boolean DEFAULT false,
    novnc_port integer,
    org_layout jsonb,
    last_used_at timestamptz,
    created_at timestamptz DEFAULT now()
);

-- RLS: users can only read/write their own row
ALTER TABLE user_sf_profiles ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can read own SF profile"
    ON user_sf_profiles FOR SELECT
    USING (auth.uid() = user_id);

CREATE POLICY "Users can insert own SF profile"
    ON user_sf_profiles FOR INSERT
    WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update own SF profile"
    ON user_sf_profiles FOR UPDATE
    USING (auth.uid() = user_id);

CREATE POLICY "Users can delete own SF profile"
    ON user_sf_profiles FOR DELETE
    USING (auth.uid() = user_id);

-- Add columns to crm_entries
ALTER TABLE crm_entries ADD COLUMN IF NOT EXISTS sf_pushed_at timestamptz;
ALTER TABLE crm_entries ADD COLUMN IF NOT EXISTS retry_count integer DEFAULT 0;
ALTER TABLE crm_entries ADD COLUMN IF NOT EXISTS processing_started_at timestamptz;
ALTER TABLE crm_entries ADD COLUMN IF NOT EXISTS send_method text;

-- Index for worker polling
CREATE INDEX IF NOT EXISTS idx_crm_entries_sending
    ON crm_entries(status, retry_count)
    WHERE status = 'sending';
