-- ADR 0031: job-site logins the product creates or the owner imports, stored in PostgreSQL. The password is
-- AES-256-GCM ciphertext (hashing cannot work: the product must replay the password); the key lives in
-- data/.vault-key, outside the database and outside backups. One row per (origin, username). A generated password is
-- written as `pending` BEFORE it is submitted to the site, so a flaky browser "Save password" bubble can never lose an
-- account. The legacy `accounts` / `account_approvals` tables stay untouched.
CREATE TABLE site_credentials (
    id               bigserial PRIMARY KEY,
    origin           text NOT NULL CHECK (origin ~ '^https?://[^/@\s]+$'),
    username         text NOT NULL CHECK (length(username) BETWEEN 1 AND 320),
    ciphertext       bytea NOT NULL CHECK (length(ciphertext) BETWEEN 17 AND 1024),
    nonce            bytea NOT NULL CHECK (length(nonce) = 12),
    key_id           smallint NOT NULL DEFAULT 1,
    state            text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending', 'active', 'failed', 'retired')),
    source           text NOT NULL CHECK (source IN ('generated', 'imported', 'manual')),
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    last_used_at     timestamptz,
    last_verified_at timestamptz,
    UNIQUE (origin, username)
);
CREATE INDEX site_credentials_origin_idx ON site_credentials (origin) WHERE state IN ('pending', 'active');
