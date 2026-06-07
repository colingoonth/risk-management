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

- **[T2] Strike-ladder code/doc conflict.** `services/policy.py:20` hard-codes `PROBATION_AT = 4` and `EXPULSION_REVIEW_AT = 5`. But `strikes/strike-ladder.md` explicitly says "Do not build Strike 4 enforcement logic until this is confirmed with the chair." Either the doc is stale (code shipped the rule) or the code shipped a fabricated rule. Either way: reconcile before real-data ingest.
- **[T2] `auto-assign` output is opaque.** Every row reads `score=2.0, reason=assigned`. No fairness diagnostic (why was alex-rojas picked for door slot 0 vs anden? Was it alphabetical fallback?). For chair trust, the reason column needs to differentiate: `fair-rotation`, `tiebreaker:event-date`, `pref:house-override`, etc.
- **[T2] Bulk member add is one-subprocess-per-call.** Loading the 57-brother roster takes ~30s via shell loop. The `risk ingest` command may already handle this (haven't tested yet) — verify the roster ingest path and confirm it's reasonable for chair onboarding.
- **[T2] `risk config` (no subcommand) errors out instead of listing subcommands.** Minor — Typer's default. Could be friendlier by mimicking `--help` on missing-subcommand.

### Tier 3 (nice-to-have)

- **[T3] `config house add` help text example is misleading.** Says `'main', 'annex'` but the actual usage is sororities/host-fraternities for events (e.g. `axid`, `zta`).
- **[T3] Confirmation messages for write commands have no consistent voice.** Even after the dict-repr fix, decide on a uniform format like `✓ Issued strike #2 for aidan-m (reason=late) — extra_shift consequence emitted.` vs the current data-blob style.

## Resolved in this pass

_(populated as fixes land)_

## Deferred / needs Colin input

- **`shifts.status='swapped'` enum value** — currently unused after R3.2-D. Either drop from CHECK constraint via migration 0010, or add a use (swap_history view). Discuss before committing.
- **Strike-ladder code/doc conflict** — see Tier 2. `policy.py` ships `PROBATION_AT=4` + `EXPULSION_REVIEW_AT=5` but ladder doc forbids strike-4 logic until chair confirmation. Need ruling: keep code (doc is stale) or pull thresholds (code shipped unauthorized rule).

## Forward notes (for frontend session)

Captured 2026-06-07 from the Google Form Colin built for roster intake. Out of scope for the finishing pass; document so the frontend session has the shape.

- **Roster intake fields:** Full Name, Rising Class (Sophomore/Junior/Senior), PC (pledge class, Greek letter, e.g. "Zeta"), EC (yes/no).
- **Class-year mapping needed:** form collects "Rising X" strings; DB stores `class_year` integer. Derive from current semester at ingest (rising senior in SP26 → grad 2027).
- **PC field is not in the schema today.** Member table would need a `pledge_class` column or a `member_pledge_classes` lookup. Decision deferred to frontend session.
- **EC = `exec` role.** Already added to the seed-roles task below.
- **Seniority-inverted fairness tiebreaker.** Confirmed 2026-06-07: older / earlier-PC brothers should work LESS. When fairness scores tie, prefer the younger member first (lower class_year graduates later → newer brother → picks up the shift). PC is the second tiebreaker (later Greek letter = newer → picks up first). This sits above the current `event.date` deterministic tiebreaker (R3.2-A) — auto-assign tiebreaker chain becomes: `fairness_score → class_year (younger first) → PC (later first) → event.date`. Requires the PC schema add (above). Not in finishing-pass scope.
