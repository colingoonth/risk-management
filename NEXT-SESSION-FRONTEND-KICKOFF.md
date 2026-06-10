I'm starting the frontend phase of the risk-management app at `~/code/risk-management/`. The backend CLI is feature-complete and just finished a comprehensive verification pass (2026-06-09). State at start of this session:

- 19 commits ahead of origin (verification pass) — already pushed
- 413 tests, all green
- 91% line / 91% branch coverage (plan §7 gate cleared)
- ruff, mypy --strict (60 source files), import-linter all clean
- Zero known bugs

The CLI is the v1 release candidate. Your job is to design and build the v1 frontend. **This is not a small change — it's a real project with multiple architectural questions Colin can't pre-pick.** Treat the first half of this session as architecture decisions, the second half as build.

## Orientation pass (do these first, in parallel where possible)

1. Read `~/code/risk-management/TODO-frontend-readiness.md` — the canonical state doc. The "Forward notes" section lists what the frontend session is *expected* to address (Google Form intake, PC schema add, seniority-inverted fairness tiebreaker).
2. Read the canonical plan: `/Users/colin/Documents/Documents - Colins Laptop/ObsidianVault/Projects/risk-management/plan-r3-canonical.md` — focus on amendments R3.1-A through R3.2-D for spec deviations, and §3.3 for the dependency graph (`policy ← eligibility/fairness ← ... ← cli/*`).
3. Read the v1 post-mortem: `/Users/colin/Documents/Documents - Colins Laptop/ObsidianVault/Projects/risk-management/kappa-sigma-risk-management-app.md` — locked UX decisions and the 3 real workflows the chair runs.
4. Read the most recent session log: `/Users/colin/Documents/Documents - Colins Laptop/ObsidianVault/Projects/risk-management/session-2026-06-09-verification-pass.md` for what just shipped.
5. Read the council session-01 decision: `~/.claude/council/risk-management/sessions/session-01/decision.md` — ratified design posture.
6. `git log --oneline -25` in `~/code/risk-management/` for commit context.
7. `uv run pytest -q` to confirm baseline is still green.

Real-world data lives at `/Users/colin/Downloads/RISK FALL 2025.xlsx` — 57-brother roster, 6 sorority partners + Phi Tau, 15 real Fall 2025 events. Use as the seed dataset for any UI testing.

## Architectural decisions to make BEFORE writing any code

These are genuine council-warranted questions. Each has 2+ defensible approaches and the wrong choice has multi-week downstream cost. **Run `/council` on whichever feels most contested.** Colin will gate-keep the triage.

### Decision 1 — Backend integration approach

**A) CLI-as-backend.** Frontend shells out to `risk` CLI subprocess for every action. Parses `--json` envelopes. Zero new Python code. Pros: backend layer is already done + tested + audited. Cons: subprocess startup ~100ms per call, no streaming, no shared connection.

**B) FastAPI thin layer.** New `src/risk/api/` package exposes the same service-layer functions over HTTP. Frontend speaks REST/JSON. Pros: single open DB connection (faster), proper streaming, easier to add auth later for the v2 exec-board exposure. Cons: new code surface, must re-test the API layer against the service layer, must keep two adapter layers (CLI + API) in sync.

**C) Hybrid.** FastAPI for read-heavy paths (dashboard rendering), CLI subprocess for one-shot mutations. Pros: minimizes new code while solving the latency problem on the page that matters. Cons: split-brain ergonomics.

ADR-014 (zero paid runtime) says any choice must run locally. Plan §4 mentions FastAPI v2 explicitly as "post-verification" scope.

### Decision 2 — Frontend stack

The constraint set: single-user, local-first, single-machine, no cloud, no auth required for v1, but should be exposable to a small exec board v2.

Options to weigh:
- **SvelteKit + Tailwind** — fast, small bundle, SSR-ready, good DX
- **Next.js + Tailwind + shadcn** — most ergonomic, biggest ecosystem, heavier
- **Vite + React + Tailwind + shadcn** — lighter than Next, no SSR opinions
- **Plain HTML + HTMX + Tailwind served from FastAPI** — radically simpler, no JS build, fits "local-first single-chair tool"

Per Colin's UI feedback memories (`feedback_ui_bold_first`, `feedback_serif_display_rejected`, `feedback_obsidian_visual_standards`): **lead bold, no AI-default palettes (cream / generic green / generic navy), no editorial-serif display fonts.** The first design pass needs an identity test before any pixels are committed.

### Decision 3 — Schema additions for Google Form intake

Captured during finishing pass, deferred to this session:

- **PC (pledge class) column** on `members`. Greek letter (Zeta, Eta, Theta, etc.). Either a column on `members` OR a `member_pledge_classes` lookup if PC ever needs metadata. Decide.
- **Rising-class → class_year mapping at ingest.** Form collects "Rising Senior" / "Rising Junior" / "Rising Sophomore" / "Rising Freshman". Need to derive `class_year` integer from current semester at ingest time (e.g. Rising Senior in SP26 → grad 2027).
- **EC (exec) → `exec` role.** Already seeded in migration 0010. Just need ingest wiring.

After PC lands, the **seniority-inverted fairness tiebreaker** (`fairness_score → class_year younger-first → PC later-first → event.date`) becomes implementable. Plan §3.3 mentions it; the order is locked. Older brothers work LESS, younger pledges pick up the shift first.

These are mutually independent decisions but they sequence: PC schema → tiebreaker → frontend can ingest the form.

## The 3 workflows the UI must support (from v1 post-mortem)

1. **Bulk pre-semester fill.** Social chair drops the full semester event schedule. UI auto-assigns every shift before the semester starts. Primary workflow — one bulk run, not one-event-at-a-time.

2. **Pledge takeover.** Mid-semester, pledges take over all remaining events; brothers are relieved. Pledge pool may be thin (rolling rush, low PC numbers) — UI must show the pledge-mode degradation transparently (`pledges_only → mixed → brothers_only`).

3. **Ad-hoc additions.** Events added mid-semester outside the original schedule. Single-event flow.

**Locked UX principle from v1:** Dashboard leads with **actions**, not stats. Unfilled events, pending swaps, members needing shifts — with dates always visible. "Who hasn't worked?" is the #1 chair question.

## What "v1 frontend done" looks like

- Chair can run all 3 workflows end-to-end without touching the CLI
- Dashboard answers "what needs my attention?" in <2 seconds of looking at the screen
- Google Form CSV ingest works for the SP26 roster (when Colin gets it)
- Auto-assign feedback is intelligible — reasons must explain WHY a member got a shift, not just "assigned"
- Strike system is visible and resolvable from the UI (issue / remove / mark consequence served)
- Swap requests can be browsed, accepted, rejected from the UI
- Locally runnable: `npm run dev` or equivalent + the backend → chair opens browser → works
- No paid services on any code path (ADR-014)

## What's deferred to v2

- Multi-user / auth / exec-board access
- Cloud sync or hosted deployment
- SMS / push notifications
- Read-only mobile view for brothers to check their schedule
- Reporting / analytics dashboards beyond the "chair-action" dashboard

## Concrete work, prioritized

### Tier 1 — Architecture decisions (council mode)

Run `/council` on Decision 1 (CLI-as-backend vs FastAPI) and Decision 2 (stack). These have real multi-week cost if wrong. Decision 3 (schema) is council-eligible but probably SOLO once you've read the Google Form structure.

### Tier 2 — Schema + ingest (do BEFORE the UI build)

- Migration 0012: add `pledge_class` to `members` (TEXT, nullable) OR create `member_pledge_classes` table — decide via /council if needed
- Service: `ingest.gform_roster()` reads a Google-Form-shaped CSV and inserts members with PC + class_year mapping
- CLI: `risk ingest gform-roster PATH --semester SLUG` for headless ingest
- Seniority-inverted tiebreaker: extend `services/fairness.py` to break ties on (class_year ASC, pledge_class DESC, event.date ASC) per R3.2-A chain
- Tests: full ingest path against a sample form CSV + tiebreaker reproducibility

### Tier 3 — Design system + first screen

- **NO bold-first violation.** Identity test before any pixels: 3-5 distinct visual directions before picking one. Avoid AI-default palettes (cream, generic green, generic navy) and editorial-serif display fonts (Fraunces, DM Serif Display, etc.).
- Tailwind config with a chosen palette + type system
- First screen is the dashboard. Build it first. If the dashboard works, the rest follows.

### Tier 4 — Workflow flows

In order:
1. Roster import (Google Form CSV → members)
2. Semester + event setup
3. Auto-assign one event + show the result
4. Bulk auto-assign (the primary workflow)
5. Strike issue / remove / resolve consequence
6. Swap request / accept / cancel
7. Mid-cycle host change + resync visibility
8. Semester archive

### Tier 5 — Polish + dogfooding

Same approach as the verification session: real Fall 2025 data, real cycle, surface every "I had to do this twice" moment as a bug.

## Operating rules (carry forward)

- `feedback_plan_before_work` — propose a plan before any work-doing task. Quick outlines, not heavy docs.
- `feedback_dont_halt_progress` — once execution starts, don't offer stop-points mid-build.
- `feedback_fix_permanently` — if a bug traces to a skill/config/instruction root cause, fix THERE, not just in code.
- `feedback_no_validation` — present findings honestly. Bad designs get called out.
- `feedback_local_first_ml` — applies to the whole project. Zero paid runtime on any path.
- `feedback_ui_bold_first` — never start with safe/minimal UI. Lead bold. Run identity test. Avoid AI-default palettes.
- `feedback_serif_display_rejected` — no Fraunces, DM Serif Display, or "editorial magazine" display fonts.
- `feedback_no_simplifying_output` — don't compress/merge/reduce Colin's requested scope. Build what he asks for.
- `feedback_personal_project_ml_lead` — Colin owns architecture decisions. Agents pair, don't own.
- Repo: `~/code/risk-management/`
- Auto mode is fine for local work. Confirm before destructive git operations (force push, reset --hard, branch deletion). Commits are not destructive.
- **Commits:** small focused commits per change, descriptive messages. The verification session had 19 well-scoped commits — keep that bar.

## What I expect at the end of this session

- A green test suite (no backend regressions)
- Migration 0012 + ingest path landed for the Google Form
- Seniority-inverted fairness tiebreaker landed + tests
- An architectural choice committed for backend integration (CLI subprocess vs FastAPI vs hybrid)
- An architectural choice committed for frontend stack
- At minimum: dashboard + roster import screen functional in a browser
- Identity-tested design system applied
- Commits pushed
- Updated `TODO-frontend-readiness.md` with a `## Frontend session — 2026-06-XX` section listing what shipped, what's still open, what was deferred

## Session-end

Spawn `/librarian-documentation` to capture this frontend pass to the vault. Tell it the timestamp of the last librarian call was 2026-06-09 (the verification pass).

If this session touched architecture (council convened), surface the decision docs to Colin before closing.

The v2 phase (FastAPI, exec-board exposure, mobile read-only) starts in a separate fresh session.
