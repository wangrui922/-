ALTER TABLE users ADD COLUMN IF NOT EXISTS access_pin_hash VARCHAR(255);

CREATE TABLE IF NOT EXISTS activation_codes (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code_digest   VARCHAR(64) NOT NULL UNIQUE,
    label         VARCHAR(50),
    user_id       UUID UNIQUE REFERENCES users(id) ON DELETE SET NULL,
    activated_at  TIMESTAMPTZ,
    expires_at    TIMESTAMPTZ,
    revoked_at    TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_activation_codes_active ON activation_codes (revoked_at, expires_at);
