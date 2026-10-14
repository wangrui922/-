ALTER TABLE users ADD COLUMN access_pin_hash VARCHAR(255);

CREATE TABLE activation_codes (
    id            CHAR(36) PRIMARY KEY,
    code_digest   VARCHAR(64) NOT NULL UNIQUE,
    label         VARCHAR(50),
    user_id       CHAR(36) UNIQUE REFERENCES users(id) ON DELETE SET NULL,
    activated_at  TEXT,
    expires_at    TEXT,
    revoked_at    TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX idx_activation_codes_active ON activation_codes (revoked_at, expires_at);
