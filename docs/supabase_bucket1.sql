-- AFRIBOOK Bucket 1 schema for the Supabase SQL Editor.
-- Equivalent to Alembic migration 0001_initial. Use this OR `flask db upgrade`, never both.
-- The last statements record the migration so later `flask db upgrade` runs start from 0002.

BEGIN;

CREATE TABLE users (
    id SERIAL NOT NULL,
    phone_e164 VARCHAR(16) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(16) NOT NULL,
    is_active BOOLEAN DEFAULT true NOT NULL,
    phone_verified_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT ck_users_role_valid CHECK (role IN ('owner', 'admin')),
    CONSTRAINT pk_users PRIMARY KEY (id),
    CONSTRAINT uq_users_phone_e164 UNIQUE (phone_e164)
);

CREATE TABLE password_reset_otps (
    id SERIAL NOT NULL,
    user_id INTEGER,
    phone_e164 VARCHAR(16) NOT NULL,
    purpose VARCHAR(24) NOT NULL,
    code_hash VARCHAR(64) NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    attempts INTEGER DEFAULT 0 NOT NULL,
    consumed_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT ck_password_reset_otps_purpose_valid CHECK (purpose IN ('signup_verify', 'password_reset')),
    CONSTRAINT ck_password_reset_otps_attempts_nonnegative CHECK (attempts >= 0),
    CONSTRAINT fk_password_reset_otps_user_id_users FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE SET NULL,
    CONSTRAINT pk_password_reset_otps PRIMARY KEY (id)
);

CREATE INDEX ix_password_reset_otps_lookup ON password_reset_otps (phone_e164, purpose, created_at);

-- Block the Supabase Data API from these tables (Flask connects as owner and bypasses RLS).
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE password_reset_otps ENABLE ROW LEVEL SECURITY;

-- Record the migration for Alembic.
CREATE TABLE alembic_version (
    version_num VARCHAR(32) NOT NULL,
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);
INSERT INTO alembic_version (version_num) VALUES ('0001_initial');
ALTER TABLE alembic_version ENABLE ROW LEVEL SECURITY;

COMMIT;
