-- The agent reply rail: durable identity bindings and a reserve-before-send
-- ledger for every message an automated agent may place in a chat.
--
-- An agent has exactly one row in agent_reply_bindings. The service creates the
-- row from trusted coordinator configuration on first use, then refuses any
-- attempt to use that identity with a different named destination. Keeping the
-- binding here as well as in the in-process AgentIdentity means a restart cannot
-- quietly turn the same identity into an agent for another chat.
CREATE TABLE IF NOT EXISTS agent_reply_bindings (
  agent_id TEXT PRIMARY KEY,
  channel_name TEXT NOT NULL,
  channel_kind TEXT NOT NULL,
  destination TEXT NOT NULL,
  bound_at TEXT NOT NULL,
  CHECK (length(trim(agent_id)) > 0),
  CHECK (length(trim(channel_name)) > 0),
  CHECK (length(trim(channel_kind)) > 0),
  CHECK (length(trim(destination)) > 0)
) STRICT;

-- Reserve-before-send is the same protocol as groupme_outbound:
--
--   pending  reserved durably; the transport has not returned
--   sent     the transport acknowledged delivery
--   failed   definitely not delivered; a later attempt is safe
--   unknown  delivery may have happened; never retry blindly
--
-- A process death leaves `pending`, which is deliberately just as blocking as
-- `unknown`: after a restart there is no evidence that the network call did not
-- happen. The partial unique index makes this invariant survive racing writers,
-- while the service gives callers the useful refusal message.
CREATE TABLE IF NOT EXISTS agent_replies (
  id INTEGER PRIMARY KEY,
  agent_id TEXT NOT NULL REFERENCES agent_reply_bindings(agent_id),
  channel_name TEXT NOT NULL,
  channel_kind TEXT NOT NULL,
  destination TEXT NOT NULL,
  body TEXT NOT NULL,
  rendered_text TEXT NOT NULL,
  approved INTEGER NOT NULL CHECK (approved IN (0, 1)),
  source_guid TEXT NOT NULL UNIQUE,
  state TEXT NOT NULL DEFAULT 'pending'
    CHECK (state IN ('pending', 'sent', 'failed', 'unknown')),
  reserved_at TEXT NOT NULL,
  settled_at TEXT,
  message_id TEXT,
  last_error TEXT,
  CHECK (length(body) > 0),
  CHECK (length(rendered_text) > 0)
) STRICT;

-- One unresolved delivery blocks every new send to the same real destination,
-- even if somebody accidentally gives that destination a second display name.
CREATE UNIQUE INDEX IF NOT EXISTS ux_agent_replies_destination_unsettled
  ON agent_replies(channel_kind, destination)
  WHERE state IN ('pending', 'unknown');

-- The rolling rate-limit query. Failed attempts count too: the cap is on
-- transport attempts, and a broken endpoint must not become a tight retry loop.
CREATE INDEX IF NOT EXISTS ix_agent_replies_destination_reserved
  ON agent_replies(channel_kind, destination, reserved_at);

