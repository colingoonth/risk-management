# `com.colin.riskforwarder` — the GroupMe poll trigger

`risk-forwarder` runs one poll-and-forward cycle and exits. This LaunchAgent is
what runs it every two minutes, and once immediately whenever it is loaded.

**Nothing installs it for you.** The template carries `__PLACEHOLDERS__` because
the feed path and the venv path are specific to one machine, and this repo is
public — a finished plist with real values in it is a leak, not a convenience.

**Where the messages go: a file.** `risk-forwarder` appends each new message to
`RISK_FORWARD_FEED`, one line per message, and you read it with `tail -f`. It
does not type them into a terminal, a cmux surface, or an agent. Anybody in the
chapter's GroupMe can write those lines, and a destination that types them
somewhere and presses enter is a remote prompt for a hundred people — see the
module docstring in `src/risk/services/groupme_forward.py`.

## Install

```sh
# 1. Fill in the template. Three substitutions.
mkdir -p ~/Library/LaunchAgents
sed -e "s|__RISK_FORWARDER_BIN__|$PWD/.venv/bin/risk-forwarder|g" \
    -e "s|__HOME__|$HOME|g" \
    -e "s|__FEED_PATH__|$HOME/Library/Application Support/risk-management/groupme-feed.txt|g" \
    packaging/launchd/com.colin.riskforwarder.plist.template \
    > ~/Library/LaunchAgents/com.colin.riskforwarder.plist

# 2. Load it. `bootstrap` also runs it once, because of RunAtLoad.
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.colin.riskforwarder.plist

# 3. Watch it run, and watch the messages arrive.
tail -f ~/Library/Logs/riskforwarder.log
tail -f "$HOME/Library/Application Support/risk-management/groupme-feed.txt"
```

The feed path is yours to choose; it just has to be a file in a directory that
already exists. The forwarder creates the file `0600` and never creates the
directory — a feed that materialised a tree somewhere unexpected would be months
of other people's messages in a place nobody picked.

## Check, stop, reload

```sh
launchctl print gui/$(id -u)/com.colin.riskforwarder     # state + last exit code
launchctl kickstart -k gui/$(id -u)/com.colin.riskforwarder   # run one cycle now
launchctl bootout gui/$(id -u)/com.colin.riskforwarder   # stop and unload
```

After editing the plist you must `bootout` and `bootstrap` again — launchd
caches the loaded copy.

## What to expect in the log

One line per run:

```
2026-08-31T21:04:00+00:00 topics=3 stored=2 dup=0 forwarded=2
2026-08-31T21:06:00+00:00 topics=3 stored=0 dup=0 forwarded=0
2026-08-31T21:08:00+00:00 topics=3 stored=0 dup=0 forwarded=0 failed=risk-friday:network_timeout
2026-08-31T21:10:00+00:00 skipped=lease_held
```

Timestamps, topic slugs, error codes, counts. Message text and senders are never
logged — they go to the database and to the feed file, and nowhere else.

`skipped=lease_held` is normal, not a fault: the previous cycle was still
running (a long catch-up after the machine was asleep), and the lease in
`groupme_poll_lease` stopped this one from moving the same read cursor
underneath it.

## Things that go wrong

| Symptom | Cause |
| --- | --- |
| `feed=feed_not_configured` | `RISK_FORWARD_FEED` is empty. Messages are still being stored — they queue until it is set. |
| `feed=feed_unwritable` | The feed's directory does not exist, the volume is not mounted, the disk is full, or the file is not writable. Harmless; the queue drains once it is fixed. |
| `failed=<slug>:auth_rejected` | The keychain token is missing or revoked. |
| `failed=<slug>:rate_limited` | GroupMe pushed back. The topic sits out until its `retry_after` passes; nothing to do. |
| Nothing in the log at all | The agent is not loaded (`launchctl print`), or the binary path in `ProgramArguments` does not exist. |

The forwarder only ever READS from GroupMe. It cannot post, add anybody to a
group, or remove them — that is a separate feature with its own confirmation.

---

## com.colin.riskrotation — the daily noon rotation

A clock, not a daemon. Once a day it starts one headless Claude, points it at
the `risk-rotation` skill, and exits. Claude decides who enters the chats and
whether the week is safe to post; the plist only decides *when*.

```sh
sed -e "s|__CLAUDE_BIN__|$HOME/.local/bin/claude|" \
    -e "s|__REPO_DIR__|$HOME/code/risk-management|" \
    -e "s|__LOG_DIR__|$HOME/Library/Logs|" \
    packaging/launchd/com.colin.riskrotation.plist.template \
  > ~/Library/LaunchAgents/com.colin.riskrotation.plist

launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.colin.riskrotation.plist
launchctl kickstart -p gui/$(id -u)/com.colin.riskrotation   # run once now, to watch it
```

To stop it: `launchctl bootout gui/$(id -u)/com.colin.riskrotation`.

**It does not RunAtLoad.** Logging in three times before lunch would otherwise
run three rotations. It fires at 12:07 and only then — and if the Mac is asleep
at 12:07, launchd runs it once at wake, not once per missed day.

**What it can send.** The routine weekly announce, which Colin has authorised.
Anything else — a rules change, a correction, a message to one man — still
waits for him. That line is in the skill, not the plist, because it is a
judgement rather than a schedule.
