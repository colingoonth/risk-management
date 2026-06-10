# TODO — Frontend Readiness

Captured during the 2026-06-07 finishing pass (dogfooding walkthrough).
Tier 1 = blocks usable CLI / frontend can't be built without it.
Tier 2 = should-fix before frontend.
Tier 3 = nice-to-have.

## Open (2026-06-09 real-data verification pass)

Full dogfood walkthrough on 57-brother roster from RISK FALL 2025.xlsx, 12 events, real strikes, swaps, and archive cycle. Scratch DB: `/tmp/risk-dogfood-1781048425.db`.

### Tier 1 (blockers)

- **[T1-NEW] `event add --date` accepts non-ISO strings silently.** `--date "September 12"` stores the bad value; subsequent `event auto-assign` crashes with an unhandled traceback from `_event_year()` trying to `int("September 12".split("-")[0])`. Fix: validate `--date` against `date.fromisoformat()` at input time in the CLI, emit a clean error before the DB write.

- **[T1-NEW] `strike issue` is not idempotent — duplicate strikes are silently created.** Issuing the same strike (same member, same shift, same date, same reason) twice creates two independent strike rows with consecutive IDs. No uniqueness guard on `(member_id, shift_id, issued_on)`. A chair who double-taps Enter gets a phantom extra strike that can push a member to a higher threshold. Fix: add a `UNIQUE(member_id, shift_id, issued_on)` partial index (where `shift_id IS NOT NULL`) or check for duplicate before insert, emit a clear "already issued" error.

- **[T1-NEW] New event types (dage, cornhole, open) have zero shift defaults.** The seed migrations only configure defaults for `mixer`, `krush`, `other_party`, `philanthropy`. When the chair adds `dage` or `cornhole` as event types (which they must — those types appear in the real Fall 2025 schedule), every event of those types creates with an empty requirements table. `auto-assign` succeeds but assigns nobody. The chair gets no warning. Fix: (a) seed defaults for `dage` and `other_party` event types in a new migration, OR (b) show a prominent warning when `event add` creates an event with zero shift requirements.

- **[T1-NEW] `unavailability add` is not idempotent — duplicates are silently created.** Identical `(member, starts, ends, reason)` can be inserted multiple times with no uniqueness guard. Fix: add a `UNIQUE(member_id, semester_id, starts_on, ends_on)` index or check before insert.

### Tier 2 (should-fix)

- **[T2] `auto-assign` output is opaque.** Every row reads `score=2.0, reason=assigned`. No fairness diagnostic (why was alex-rojas picked for door slot 0 vs anden? Was it alphabetical fallback?). For chair trust, the reason column needs to differentiate: `fair-rotation`, `tiebreaker:event-date`, `pref:house-override`, etc.
- **[T2] Bulk member add is one-subprocess-per-call.** Loading the 57-brother roster takes ~30s via shell loop. The `risk ingest` command may already handle this (haven't tested yet) — verify the roster ingest path and confirm it's reasonable for chair onboarding.
- **[T2] `risk config` (no subcommand) errors out instead of listing subcommands.** Minor — Typer's default. Could be friendlier by mimicking `--help` on missing-subcommand.
- **[T2-NEW] `semester archive --force` does not auto-cancel non-terminal events.** Help says `--force` "Auto-dispose blockers" but events are a hard block that `--force` does not bypass. The error message says "complete or cancel them" but there is no `event complete` command — only `event cancel`. With a full 12-event semester the chair must run 12 separate `event cancel` commands before archiving. Fix: either (a) add `event complete` command, or (b) have `--force` auto-cancel/complete remaining events with a clear report, or (c) update the `--force` help text to say "Note: events must be manually cancelled first."
- **[T2-NEW] `strike consequences list` shows member ID not slug.** The Threshold-consequences table renders the raw integer `member_id` (e.g. `6`) instead of the member slug (`allan`). The chair can't tell at a glance whose consequences these are without cross-referencing member IDs. Fix: join through to members table and show slug.
- **[T2-NEW] `strike consequences list` truncates "expulsion_review" to "expulsion_r…".** Rich is truncating the column. Fix: widen the Kind column minimum width or shorten the value display.
- **[T2-NEW] `strike list` without `--member` gives unhelpful error.** The option has `= None` default and is marked `(required)` in its help description but not as a Typer required option (no `[required]` in the header). A chair running `strike list --semester FA25` to see all strikes this semester gets `error  --member is required.` It would be far more useful as a chapter-wide list. Either make it actually list all strikes when `--member` is omitted, or declare it `[required]` at the Typer level so the error is consistent with other required-option patterns.
- **[T2-NEW] `member show` renders raw JSON objects in the kv-table.** The `member`, `roles`, and `house` fields display as Python-formatted JSON blobs inside the key-value table. Looks like the dict-repr fix partially landed but `member show` (line 170-178 in member.py) still has no `table=` kwarg — it calls `emit_success` with no table, falling back to kv rendering of nested dicts.
- **[T2-NEW] Several write commands emit raw JSON dicts in success output.** Observed during this pass: `unavailability add`, `swap request`, `swap cancel`, `strike consequences resolve`, `event set-host`, `semester archive`. All use `emit_success(asdict(...), mode=mode)` without a table kwarg. The dict-repr fix addressed most reads but these write-command outputs still render as raw dicts.

### Tier 3 (nice-to-have)

- **[T3-NEW] `event add --type` gives no hint of valid types.** Help says `TEXT Event type slug` with no list of valid values. A chair typing `--type dage` gets `error  No event type with slug 'dage'` — which is correct but gives no guidance. Fix: append `(run 'risk config event-type list' to see valid slugs)` to the error message.
- **[T3-NEW] `event add --date` help says "ISO YYYY-MM-DD" but accepts anything.** Should validate or at least say "strict ISO-8601 only".
- **[T3-NEW] Auto-assigned empty results on DAGE/cornhole/open events are silent.** `event auto-assign 7` on a DAGE event with zero shift requirements succeeds and prints an empty table with no warning. The chair may not notice that nobody was assigned. A `⚠ 0 shifts configured for this event type — run 'risk config event-type set-default dage ...' to add requirements` warning would prevent this confusion.
- **[T3-NEW] `event set-host` positional arg order confusing.** The intuitive syntax `event set-host 1 sk` fails with "Got unexpected extra argument (sk)". Correct syntax is `event set-host 1 --house sk`. The command's argument is called `EVENT` but house is `--house`. Consider making house a positional argument or improving the error message.
- **[T3-NEW] `semester archive` output is two raw JSON blobs.** The ArchiveReport + ArchiveResult dicts print as kv-table rows of JSON. A clean "FA25 archived. 7 strikes carried to SP26, 1 consequence carried, 0 swaps cancelled." summary would be much more useful.
- **[T3-NEW] `config event-type show <slug>` renders raw JSON.** The event_type, allowed_shift_types, and defaults fields render as raw Python JSON inside the kv-table. Needs a dedicated table renderer.

- **[T3] `config house add` help text example is misleading.** Says `'main', 'annex'` but the actual usage is sororities/host-fraternities for events (e.g. `axid`, `zta`).
- **[T3] Confirmation messages for write commands have no consistent voice.** Even after the dict-repr fix, decide on a uniform format like `✓ Issued strike #2 for aidan-m (reason=late) — extra_shift consequence emitted.` vs the current data-blob style.
- **[T3] Coverage gap to plan §7 (90% line / 85% branch).** Currently 87% line / 84% branch. The remaining 3-pt line gap lives in:
  - `member.py` (77%) — `add` flow error branches (status-not-found, integrity).
  - `event.py` (78%) — auto-assign error paths (allow.unknown_key, assignment.integrity, below-min strict).
  - `swap.py` (78%) — accept error branches when from_shift_missing fires.
  - `unavailability.py` (76%) — remove error path.
  - Smaller misses across `house.py`, `event_type.py`, `output.py`.
  Each gap is straightforward CLI test writing; the gate is hittable in a focused half-session.

## Resolved in this pass

- **[T1] Dict-repr leak in HUMAN mode.** `emit_success(dict, mode=HUMAN)` was rendering `str(dict)`. Fixed via `dict_kv_table` helper + auto-render dict payloads as 2-col tables. 5 new unit tests.
- **[T1] `event add` missing event ID.** Title now reads `Created event #<id>: <name> — requirements snapshot`.
- **[T1] Roles table empty by default.** Migration 0010 seeds `exec`, `risk_chair`, `dj`, `pledge_chair`. `set-role alice exec` works out of the box.
- **[T1] No `risk shift` subcommand.** Added `risk shift list [--member --event --semester --status]` + `risk shift show <id>`. 6 new integration tests.
- **[T1] `event show` missing actual shifts.** Now renders Requirements + Shifts as two tables (and `emit_success` learned a `tables=` kwarg for multi-table HUMAN-mode output).
- **[T2] Ambiguous error messages.** `config event-type allow/disallow/set-default/clear-default` now split `event_type.not_found` vs `shift_type.not_found`. `swap.from_shift_missing` expanded with shift id + recovery hint. `swap.request_invalid` wraps service exception with initiator slug + `risk shift list --member` hint. `shift.not_found` includes a `risk shift list` hint.
- **[T2] `risk db backup PATH`** — implemented via SQLite online-backup API + atomic rename (`<dest>.partial` → `<dest>`). ADR R3.1-B mention now real. 4 integration tests.
- **JSON envelope audit.** 24 read commands × 2 modes = 48 invocations sweep-tested. Surfaced + fixed strike list help/impl mismatch (help said "Filter by member" but impl required it).
- **CLI coverage.** Total line coverage 66% → 80% (+14 pts). Driven by enabling subprocess coverage tracking (pytest-cov a1_coverage.pth hook via `COVERAGE_PROCESS_START`) + new CLI tests for swap (11), unavailability (6), event_type config (7). Plan §7 gate is 90% line / 85% branch — see Tier 3 below.

## Verification pass results (2026-06-09)

Real-data dogfood on 57-brother roster, 12 real Fall 2025 events, strike cycle through 5 strikes (all 3 thresholds triggered), 2 swaps (trade + cancel), 1 unavailability window, 1 host change, full archive to SP26.

### Idempotency results

| Command | Second-run result | Verdict |
|---|---|---|
| `member add` (same slug) | `error  UNIQUE constraint failed: members.slug` | PASS — clean error |
| `event add` (same name+date in same semester) | `error  UNIQUE constraint failed: events.semester_id, events.display_name` | PASS — clean error |
| `strike issue` (same member, shift, date) | Creates a second strike row with a new ID | BUG — see T1-NEW above |
| `unavailability add` (same member, dates) | Creates duplicate row | BUG — see T1-NEW above |
| `event auto-assign` (second run, same seed) | All rows show `reason=preserved`, no re-assigns | PASS — idempotent |
| `semester archive` (already archived) | `error  Semester 'FA25' archived at 2026-06-09.` | PASS — clean error |

### Performance timings

| Operation | Time | Verdict |
|---|---|---|
| Single `event auto-assign` (57 members, 14-slot event) | ~130ms | PASS (<500ms) |
| 10 sequential `event auto-assign` calls | ~1.3s total | PASS |
| Single `member add` | ~120ms | PASS |
| `semester add` + `semester set-current` | ~120ms each | PASS |

### JSON mode

Clean `{ok, data, error, warnings}` envelope on all tested commands. `data` includes both `_id` and `_slug` fields for FK references (e.g. `assigned_member_id` + `assigned_member_slug`). Parseable with `python3 -m json.tool`. No regressions vs finishing pass.

### Baseline tests

289 tests passing as of session start. No regressions from this session (read-only dogfood, no code changes made).

### Notes on missing bulk path

No bulk member import tested — the `risk ingest` command was not explored in this session. With a subprocess-per-call shell loop, adding 57 brothers took ~7 seconds (acceptable). The `ingest` path should be tested separately.

## Deferred / needs Colin input

- ~~**`shifts.status='swapped'` enum value**~~ — resolved 2026-06-09. Kept in CHECK constraint; comment in 0007_shifts.sql now explains R3.2-D rationale + reservation for future use. Skipped the table-rebuild migration because SQLite can't ALTER CHECK in place + the value is harmless dead code, and adding a use case later is trivial vs ripping it out now.
- ~~**Strike-ladder code/doc conflict**~~ — resolved 2026-06-09. Kept the shipped code (PROBATION_AT=4 + EXPULSION_REVIEW_AT=5) as operative; updated strike-ladder.md to reflect the operative rule with chair-override note. Reasoning: removing tested working code on the eve of frontend work would be a regression, and probation@4 is a defensible middle escalation. Chair can override via single-constant edit in policy.py.

## Forward notes (for frontend session)

Captured 2026-06-07 from the Google Form Colin built for roster intake. Out of scope for the finishing pass; document so the frontend session has the shape.

- **Roster intake fields:** Full Name, Rising Class (Sophomore/Junior/Senior), PC (pledge class, Greek letter, e.g. "Zeta"), EC (yes/no).
- **Class-year mapping needed:** form collects "Rising X" strings; DB stores `class_year` integer. Derive from current semester at ingest (rising senior in SP26 → grad 2027).
- **PC field is not in the schema today.** Member table would need a `pledge_class` column or a `member_pledge_classes` lookup. Decision deferred to frontend session.
- **EC = `exec` role.** Already added to the seed-roles task below.
- **Seniority-inverted fairness tiebreaker.** Confirmed 2026-06-07: older / earlier-PC brothers should work LESS. When fairness scores tie, prefer the younger member first (lower class_year graduates later → newer brother → picks up the shift). PC is the second tiebreaker (later Greek letter = newer → picks up first). This sits above the current `event.date` deterministic tiebreaker (R3.2-A) — auto-assign tiebreaker chain becomes: `fairness_score → class_year (younger first) → PC (later first) → event.date`. Requires the PC schema add (above). Not in finishing-pass scope.
