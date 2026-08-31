# scripts/

One-off tooling: loading a semester's data, and the forensic scripts used to
derive it. Not part of the test suite, not imported by the app.

Everything here takes its input as a **path argument**. No data files live in
this repo — see [No data in this repo](#no-data-in-this-repo).

Run from the repo root:

```
uv run python scripts/<name>.py ...
```

---

## Loading a semester

Run in this order. Steps 2–4 all support `--dry-run`, which rolls the
transaction back and writes nothing — use it first, every time.

### 1. Create the semester

```
risk --db <db> semester add FA26 \
    --starts 2026-08-25 --ends 2026-12-05 --pledge-takeover 2026-10-15
risk --db <db> semester set-current FA26
```

The pledge-takeover date is stored as data, never hardcoded — it moves.

### 2. Roster → members (built-in command, not a script)

```
risk --db <db> ingest gform-roster roster.csv --semester FA26 --dry-run
risk --db <db> ingest gform-roster roster.csv --semester FA26
```

**Columns** (matched case-insensitively by substring, so verbose Google Form
phrasing like "What is your full name?" still resolves):

| Column | Required | Notes |
|---|---|---|
| `Full Name` | yes | Slug is derived from this; it is the idempotency key |
| `Rising Class` | yes | `Senior`/`Junior`/`Sophomore`/`Freshman`, optional "Rising " prefix. Combined with the semester's start year to get `class_year` (graduation year) |
| `PC` | yes | Pledge class as a Greek letter |
| `EC` | no | Truthy (`yes`/`y`/`true`/`1`/`x`) grants the `exec` role |

Extra columns are ignored. Re-running inserts nothing new — slugs already
present are skipped.

### 3. Sidecar → aliases, roles, notes, qualifications

```
uv run python scripts/load_sidecar.py sidecar.json --db <db> --semester FA26 --dry-run
uv run python scripts/load_sidecar.py sidecar.json --db <db> --semester FA26
```

Must run **after** step 2 — every key has to resolve to an existing member. If
any key does not resolve, the script refuses to apply *anything* rather than
load a partial roster that looks like it worked.

```json
{
  "aliases":        {"Full Name": ["Other Spelling"]},
  "roles":          {"full name": "risk-chair"},
  "notes":          {"full name": "why they are exempt"},
  "qualifications": {"Full Name": ["dj"]}
}
```

`roles` values are role **automation keys** (`risk-chair`), not slugs
(`risk_chair`); the script maps between them so the two cannot drift apart.

Aliases are not cosmetic. A name spelled two ways across two sources silently
drops that member from anything keyed on name.

### 4. Events

```
uv run python scripts/load_events.py events.csv --db <db> --semester FA26 \
    --house arena=Arena --dry-run
uv run python scripts/load_events.py events.csv --db <db> --semester FA26 \
    --house arena=Arena
```

**Columns:**

| Column | Required | Notes |
|---|---|---|
| `date` | yes | ISO `YYYY-MM-DD` |
| `weekday` | no | Ignored; human convenience only |
| `display_name` | yes | See *duplicate names* below |
| `event_type` | yes | Must be a seeded slug: `mixer`, `krush`, `dage`, `quad`, `open`, `rush`, `other_party`, `philanthropy` |
| `host_house` | no | House slug. **Empty means off-site** → NULL host, which correctly skips the host-house eligibility filter |
| `status` | no | Planning status — see below |
| `note` | no | Free text |

`--house SLUG=NAME` creates a house if absent; repeat it per house.

Creating an event also materialises `event_shift_requirements` from the
three-layer merge (per-event override > per-house preference > event-type
default), so slot counts appear immediately.

**Duplicate names.** `events` carries `UNIQUE (semester_id, display_name)`, but a
real schedule repeats names — "SK Mixer" four times a semester. That constraint
is load-bearing: `events_repo.resolve()` looks events up *by name*, which is what
makes `risk event auto-assign "Halloween Krush @ Arena"` work. So the loader
disambiguates the **data**, not the schema: a repeated name gets a
` (YYYY-MM-DD)` suffix; already-unique names are left alone.

**Status.** `events.status` only permits `created`/`assigned`/`completed`/
`cancelled`, so every row loads as `created`. A planning status in the CSV
(`confirmed`, `potential`, `placeholder`, …) is real information — the difference
between a party that is happening and a slot being held — so it is preserved in
`events.notes` behind a greppable prefix:

```
status=placeholder; Thanksgiving week
```

```sql
SELECT * FROM events WHERE notes LIKE 'status=placeholder%';
```

---

## Forensic scripts

Used to derive the data above from spreadsheets. Kept for provenance — they
document *how* the numbers were reached, which matters when someone later asks
why a member is or is not on a list.

| Script | Usage |
|---|---|
| `check_roster.py` | `check_roster.py <roster.xlsx> <tracker.xlsx>` — names present in one source and not the other. Run this before trusting any name-keyed join |
| `analyze_strict.py` | `analyze_strict.py <tracker.xlsx>` — what a prior-semester tracker can and cannot account for: filled slots vs its own totals, unattributable first-name-only cells, staffing either side of the pledge boundary |
| `reconcile.py` | `reconcile.py <dir containing both schedule workbooks>` — merges two disagreeing schedule views and prints every conflict it had to resolve |

`seed_demo.py` is unrelated to the above: it builds a throwaway demo database
with fake names for frontend smoke-testing.

---

## No data in this repo

This repository is public. It contains **no** CSV, XLSX or JSON holding real
member data, and it must stay that way — a roster is 67 real people's names,
pledge classes, and written exemption reasons.

Keep the data outside the repo and pass its path in.

Two scripts were therefore **not** moved here:

- **`make_roster_csv.py`** — builds the roster CSV and sidecar, but embeds the
  entire reconciliation dataset inline: the nickname map, manual pledge-class
  fixes, the EC list, per-member exemption notes, roles and qualifications, all
  keyed by real name. It is data wearing a script's clothing. To bring it in,
  those six literals need extracting into an input JSON it reads by path — at
  which point it becomes generic and belongs here.
- **`quota4.py`** — computes the quota ratio behind the fairness model. No
  personal names, but it embeds the full party schedule inline (dates and
  partner-organisation names). Same fix: read the events CSV by path instead.

Both still live alongside the data. Neither is required to load a semester.

---

## Commit privacy guard

Install the repository's local hooks explicitly (the project never changes Git
configuration on its own):

```sh
git config core.hooksPath scripts
```

The `pre-commit` hook scans the exact blobs and added lines staged in Git. It
rejects SQLite magic bytes, token-shaped or GroupMe-ID-shaped values, and names
loaded at run time from the local database selected by `RISK_DB_PATH` (or the
normal app database). The companion `commit-msg` hook applies the same textual
checks to the commit message because Git does not make that message available
during `pre-commit`. The roster is queried read-only and is never copied into
the repository or a generated file.
