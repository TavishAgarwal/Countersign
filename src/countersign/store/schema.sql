-- Countersign schema (architecture.md §7). Money is integer cents. Times are server ms.

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS processed_events (
    message_id      TEXT PRIMARY KEY,
    room_id         TEXT NOT NULL,
    ts_ms           INTEGER NOT NULL,
    processed_at_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS identities (
    username      TEXT PRIMARY KEY,
    sender_id     TEXT NOT NULL,
    pinned_ts_ms  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS proposals (
    action_hash     TEXT PRIMARY KEY,
    short_id        TEXT NOT NULL UNIQUE,
    kind            TEXT NOT NULL CHECK (kind IN ('payment', 'autopilot_grant', 'policy_ratify')),
    vendor_id       TEXT,
    invoice_number  TEXT,
    amount_cents    INTEGER,
    canonical_json  TEXT NOT NULL,
    initiator       TEXT NOT NULL,
    k_required      INTEGER NOT NULL,
    needs_callback  INTEGER NOT NULL CHECK (needs_callback IN (0, 1)),
    flags_json      TEXT NOT NULL,
    policy_hash     TEXT NOT NULL,
    state           TEXT NOT NULL CHECK (
        state IN ('OPEN', 'PAID', 'REFUSED', 'REJECTED', 'EXPIRED', 'VOID', 'GRANTED')
    ),
    opened_at_ms    INTEGER NOT NULL,
    expires_at_ms   INTEGER NOT NULL,
    card_message_id TEXT,
    intake_room_id TEXT,
    proposal_message_id TEXT,
    superseded_by   TEXT
);
CREATE INDEX IF NOT EXISTS idx_proposals_state ON proposals (state);
CREATE INDEX IF NOT EXISTS idx_proposals_vendor_invoice ON proposals (vendor_id, invoice_number);

CREATE TABLE IF NOT EXISTS approvals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    action_hash TEXT NOT NULL REFERENCES proposals (action_hash),
    message_id  TEXT NOT NULL UNIQUE,
    username    TEXT NOT NULL,
    sender_id   TEXT NOT NULL,
    decision    TEXT NOT NULL CHECK (decision IN ('approve', 'reject', 'callback')),
    reason      TEXT,
    ts_ms       INTEGER NOT NULL,
    counted     INTEGER NOT NULL CHECK (counted IN (0, 1)),
    code        TEXT
);
CREATE INDEX IF NOT EXISTS idx_approvals_hash ON approvals (action_hash);

CREATE TABLE IF NOT EXISTS ledger (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    action_hash    TEXT NOT NULL UNIQUE REFERENCES proposals (action_hash),
    vendor_id      TEXT NOT NULL,
    account_last4  TEXT NOT NULL,
    amount_cents   INTEGER NOT NULL CHECK (amount_cents > 0),
    memo           TEXT NOT NULL,
    ts_ms          INTEGER NOT NULL,
    via            TEXT NOT NULL CHECK (via IN ('quorum', 'autopilot'))
);
CREATE TRIGGER IF NOT EXISTS ledger_no_update BEFORE UPDATE ON ledger
BEGIN SELECT RAISE(ABORT, 'ledger is insert-only'); END;
CREATE TRIGGER IF NOT EXISTS ledger_no_delete BEFORE DELETE ON ledger
BEGIN SELECT RAISE(ABORT, 'ledger is insert-only'); END;

CREATE TABLE IF NOT EXISTS receipts (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    type        TEXT NOT NULL,
    action_hash TEXT,
    body_json   TEXT NOT NULL,
    prev_digest TEXT NOT NULL,
    digest      TEXT NOT NULL,
    ts_ms       INTEGER NOT NULL
);
CREATE TRIGGER IF NOT EXISTS receipts_no_update BEFORE UPDATE ON receipts
BEGIN SELECT RAISE(ABORT, 'receipts are append-only'); END;
CREATE TRIGGER IF NOT EXISTS receipts_no_delete BEFORE DELETE ON receipts
BEGIN SELECT RAISE(ABORT, 'receipts are append-only'); END;

CREATE TABLE IF NOT EXISTS autopilot_rules (
    rule_id        TEXT PRIMARY KEY,
    vendor_id      TEXT NOT NULL,
    account_hash   TEXT NOT NULL,
    max_cents      INTEGER NOT NULL,
    cap_cents      INTEGER NOT NULL,
    spent_cents    INTEGER NOT NULL DEFAULT 0,
    granted_at_ms  INTEGER,
    expires_at_ms  INTEGER NOT NULL,
    state          TEXT NOT NULL CHECK (state IN ('PROPOSED', 'ACTIVE', 'REVOKED', 'EXPIRED')),
    grant_hash     TEXT,
    revoke_reason  TEXT
);
