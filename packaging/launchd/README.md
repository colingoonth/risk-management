# `com.colin.riskforwarder` — the GroupMe poll trigger

`risk-forwarder` runs one poll-and-forward cycle and exits. This LaunchAgent is
what runs it every two minutes, and once immediately whenever it is loaded.

**Nothing installs it for you.** The template carries `__PLACEHOLDERS__` because
the cmux surface id and the venv path are specific to one machine, and this repo
is public — a finished plist with real values in it is a leak, not a
convenience.

## Install

```sh
# 1. Fill in the template. Three substitutions.
mkdir -p ~/Library/LaunchAgents
sed -e "s|__RISK_FORWARDER_BIN__|$PWD/.venv/bin/risk-forwarder|g" \
    -e "s|__HOME__|$HOME|g" \
    -e "s|__CMUX_SURFACE_ID__|surface:N|g" \
    packaging/launchd/com.colin.riskforwarder.plist.template \
    > ~/Library/LaunchAgents/com.colin.riskforwarder.plist

# 2. Load it. `bootstrap` also runs it once, because of RunAtLoad.
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.colin.riskforwarder.plist

# 3. Watch it.
tail -f ~/Library/Logs/riskforwarder.log
```

Replace `surface:N` with the cmux surface you want the messages typed into.

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
logged — they go to the database and to cmux, and nowhere else.

`skipped=lease_held` is normal, not a fault: the previous cycle was still
running (a long catch-up after the machine was asleep), and the lease in
`groupme_poll_lease` stopped this one from moving the same read cursor
underneath it.

## Things that go wrong

| Symptom | Cause |
| --- | --- |
| `cmux=cmux_binary_missing` | `cmux-say` is not at `~/.local/bin/cmux-say`, or `PATH` in the plist is wrong. |
| `cmux=cmux_not_configured` | `RISK_CMUX_SURFACE` is empty. Messages are still being stored — they queue until it is set. |
| `cmux=cmux_send_failed` | cmux is not running, or that surface is gone. Harmless; the queue drains when it comes back. |
| `failed=<slug>:auth_rejected` | The keychain token is missing or revoked. |
| `failed=<slug>:rate_limited` | GroupMe pushed back. The topic sits out until its `retry_after` passes; nothing to do. |
| Nothing in the log at all | The agent is not loaded (`launchctl print`), or the binary path in `ProgramArguments` does not exist. |

The forwarder only ever READS from GroupMe. It cannot post, add anybody to a
group, or remove them — that is a separate feature with its own confirmation.
