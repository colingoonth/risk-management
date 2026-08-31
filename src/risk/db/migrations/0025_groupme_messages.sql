-- Everything that came IN from GroupMe, where each topic's read cursor sat when
-- we last looked, and the lease that stops two pollers doing it at once.
--
-- The chapter's risk traffic lives in three day topics under one parent group.
-- Nobody is going to sit in GroupMe all night, and the messages that matter
-- ("we're out of water", "somebody needs a ride") are the ones that need an
-- answer in minutes. So a poller reads each topic and pushes what is new into
-- the terminal Colin already has open. THE POLLER ONLY READS. Nothing in this
-- migration records anything the chapter could see; posting, adding and
-- removing people are a different feature with a different confirmation gate.
--
-- WHY A TABLE AND NOT JUST A PIPE. Two reasons, and both are about the machine
-- being asleep. First, DEDUPE: the poll runs every 120s from a LaunchAgent, it
-- can be run by hand, and the API can force a cycle — the same message will be
-- offered for storage several times over its life and must land exactly once.
-- `groupme_message_id UNIQUE` is the only thing standing between "he read it
-- once" and "he read it four times", so the uniqueness lives in the schema
-- rather than in a service that could be bypassed. Second, CATCH-UP: a laptop
-- that slept from 01:00 to 09:00 missed four hours of a party. The cursor in
-- `groupme_poll_state` is what lets the first poll after waking walk forward
-- from where it stopped instead of quietly resuming at "now" and losing the
-- night.
--
-- `forwarded_at` is deliberately separate from `received_at`. Storing a message
-- and appending it to the feed file are different acts that fail independently:
-- the feed's volume can be unmounted or its directory gone, and when it is, the
-- honest state is "we have it, he has not seen it" — which is exactly a row with
-- `received_at` set and `forwarded_at` NULL. That NULL is the delivery queue.
--
-- `triage` is the human's side of the loop: NULL means untouched, and the three
-- values mean he has looked. The vocabulary is enforced in the repo, which is
-- the only writer, so a bad value comes back as a 400 naming the alternatives
-- rather than as a raw constraint error.
--
-- IF NOT EXISTS on everything because `ensure_schema` replays every migration
-- file on EVERY connect (see db/schema.py) — a bare CREATE would make the
-- second connect fail, with the chapter's data locked inside.
CREATE TABLE IF NOT EXISTS groupme_inbound (
  id INTEGER PRIMARY KEY,
  groupme_message_id TEXT NOT NULL UNIQUE,
  group_slug TEXT NOT NULL,           -- FK-ish to groupme_groups.slug
  sender_user_id TEXT,
  sender_name TEXT NOT NULL,
  text TEXT NOT NULL,
  created_at TEXT NOT NULL,           -- ISO, from GroupMe created_at epoch
  received_at TEXT NOT NULL,
  forwarded_at TEXT,                  -- when appended to the feed; NULL = not yet
  triage TEXT,                        -- NULL | 'urgent' | 'noted' | 'handled'
  triage_note TEXT
);

-- `last_error` HOLDS A CODE, NEVER A MESSAGE. The vocabulary is fixed in
-- services/groupme_poll.ERROR_CODES ('network_timeout', 'auth_rejected',
-- 'rate_limited', ...). This is not tidiness: an exception string from an HTTP
-- client carries the URL it was calling, which is a real group id, and this
-- column is rendered by a health endpoint and printed into a log file in a
-- PUBLIC repo's working tree. A code is everything a human needs to know what
-- to do and nothing they need to redact.
--
-- `retry_after` is an ISO instant, not a duration: it is when the topic may be
-- polled again, computed once from the server's Retry-After header. Storing the
-- header's number instead would mean re-deriving "how long is left" against a
-- `last_polled_at` that the very next failed poll overwrites. NULL means now.
CREATE TABLE IF NOT EXISTS groupme_poll_state (
  group_slug TEXT PRIMARY KEY,
  last_message_id TEXT,
  last_polled_at TEXT,
  last_ok_at TEXT,
  consecutive_failures INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  retry_after TEXT
);

-- ONE POLLER AT A TIME. The LaunchAgent fires every 120 seconds whether or not
-- the previous cycle finished, Colin runs `risk-forwarder` by hand, and the
-- dashboard has a button that forces a cycle — so three processes can be inside
-- the catch-up loop simultaneously, and the read cursor is a single value they
-- would each be moving. Two pollers reading the same page and both advancing
-- past it is harmless (the unique index absorbs it); a slow one finishing after
-- a fast one and writing back an OLDER cursor is not, and would replay hours of
-- messages into the terminal.
--
-- A row rather than a lock file because the cursor it protects is a row: taking
-- the lease inside the same `BEGIN IMMEDIATE` that SQLite already serialises
-- writers with means there is no second thing to keep in sync, and a lease
-- cannot be held by a process that a crash has already taken away — it simply
-- expires. `holder` is an opaque pid+nonce token, deliberately carrying no user,
-- host, or path information, because this row is as public as the log file.
--
-- Renewed at every page boundary rather than taken once for the whole cycle: a
-- genuine eight-hour catch-up can outlast any TTL short enough to be useful
-- after a crash, and a holder that fails to renew must stop writing the cursor
-- rather than fight the process that took over.
CREATE TABLE IF NOT EXISTS groupme_poll_lease (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  holder TEXT NOT NULL,
  acquired_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);

-- The delivery queue, and it is read once every 120 seconds forever. Partial,
-- so it stays the size of the backlog (usually zero rows) rather than the size
-- of the term's entire message history.
CREATE INDEX IF NOT EXISTS groupme_inbound_unforwarded
  ON groupme_inbound(created_at, id)
  WHERE forwarded_at IS NULL;

-- The two reads the inbound list does: newest-first overall, and newest-first
-- within one topic.
CREATE INDEX IF NOT EXISTS groupme_inbound_recent
  ON groupme_inbound(created_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS groupme_inbound_by_group
  ON groupme_inbound(group_slug, created_at DESC);
