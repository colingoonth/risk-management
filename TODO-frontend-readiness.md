# TODO — Frontend Readiness

Captured during the 2026-06-07 finishing pass (dogfooding walkthrough).
Tier 1 = blocks usable CLI / frontend can't be built without it.
Tier 2 = should-fix before frontend.
Tier 3 = nice-to-have.

## Open

### Tier 1 (blockers)

- **[T1] No `risk shift` subcommand.** No way to list/discover shift IDs from the CLI. `strike issue --shift N` and `swap request --from-shift N --to-shift N` both require shift IDs but there's no enumeration path. Even `event show <id>` returns only the requirements snapshot, not the actual assigned shifts. Need at minimum `risk shift list --event ID` and `risk shift list --member SLUG`.
- **[T1] `event show` omits assigned shifts.** Same root cause as above. The `shifts` table is populated after `auto-assign` but `event show` only renders the `event_shift_requirements` snapshot. Add an "Assignments" section showing each shift_id + slot + assignee + status.
- **[T1] `event add` doesn't surface the new event ID.** Next command (auto-assign, set-shift-req, etc.) needs that ID. Currently you have to `event list` to find it. Return ID in the success output.
- **[T1] Roles table empty by default.** Kickoff's `member set-role alice exec` fails out of the box because no roles are seeded. Either seed `exec`, `risk_chair`, `dj`, `pledge_chair` in a Phase-1 migration, or have `set-role` print "no roles configured — try `risk config role add`" when it fails.
- **[T1] Dict-repr leak: 14 CLI files use `emit_success(dict, mode=HUMAN)`.** Falls through to `stdout_console.print(data)` which renders the dict's repr (single-quoted Python literal). User-facing examples seen during dogfood: `member set-role`, `strike issue`, `strike standing`, `unavailability add`, `swap request`, `event-type show`. Single fix in `output.py` — when `table is None and data` is a dict, render it as a 2-column Rich table or a one-line success summary instead of `print(data)`.

### Tier 2 (should-fix)

- **[T2] `auto-assign` output is opaque.** Every row reads `score=2.0, reason=assigned`. No fairness diagnostic (why was alex-rojas picked for door slot 0 vs anden? Was it alphabetical fallback?). For chair trust, the reason column needs to differentiate: `fair-rotation`, `tiebreaker:event-date`, `pref:house-override`, etc.
- **[T2] Bulk member add is one-subprocess-per-call.** Loading the 57-brother roster takes ~30s via shell loop. The `risk ingest` command may already handle this (haven't tested yet) — verify the roster ingest path and confirm it's reasonable for chair onboarding.
- **[T2] `risk config` (no subcommand) errors out instead of listing subcommands.** Minor — Typer's default. Could be friendlier by mimicking `--help` on missing-subcommand.

### Tier 3 (nice-to-have)

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
