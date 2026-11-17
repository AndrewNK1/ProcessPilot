# Prompt for Claude Code (Opus 5.5): finish ProcessPilot and build the evaluation

Paste everything below the line into Claude Code, opened in `C:\Users\user\Desktop\Thesis\Process Pilot project`. Use plan mode. Start a new Claude Code chat for each phase if the context gets long: this file is the shared reference, so tell it "continue with Phase N of EVALUATION_PLAN.md".

---

## Who you are working for and how

You are the implementation engineer for **ProcessPilot**, the artefact of my Business Informatics bachelor thesis (GIU). I am Andrew. The app already works end to end: ingest → validate/clean → segment → mine (PM4Py) → evidence store → LangGraph agent (diagnose → redesign → apply_edits → validate → human_review → finalize) → React UI. What is missing is (a) a set of fixes that the thesis methodology depends on, and (b) the complete evaluation machinery that Chapter 3 of my thesis promises. Your job is to build both, in phases.

**Working rules (apply to every phase):**

1. **First action of the very first session:** copy this whole prompt into the repo as `EVALUATION_PLAN.md` (do not commit secrets) so later sessions can reread it. At the end of every phase, append a short "Phase N done" log to the bottom of that file (what changed, files, test count, open questions).
2. **Plan first, then build.** At the start of each phase, read the relevant code, then show me a short plan (files to touch, design decisions, risks). Wait for my approval. Then implement.
3. **Stop after each phase.** Give me: what you changed, how to verify it myself (exact PowerShell commands), test results, and anything you could not do. Do not start the next phase until I say so.
4. **Verify, never assume.** The facts below come from a repository report dated 2026-11-06. I have used the app since then. Check every fact against the current code before relying on it. If the code disagrees with this prompt, tell me; do not silently follow either.
5. **Do not break what works.** All existing tests must keep passing (511 Python + 7 web on 2026-11-06). Add tests for everything new. Run `.\.venv\python.exe -m pytest` and `cd web; npm run test` at the end of every phase. Rebuild the committed web bundle when you change `web/src`.
6. **Never delete existing run data** in `outputs/` or rows in the SQLite database. Old data is labelled, not removed.
7. **No real LLM calls unless I explicitly approve them in that phase.** My Gemini quota is tiny (see "Gemini limits"). Use the existing scripted/fake LLM test fixtures for development. Every script that can call an LLM must have a `--dry-run` mode that uses a scripted model and makes zero API calls.
8. **Thesis integrity.** The code produces numbers that go into an academic thesis. Never hard-code, round away, or "fix up" a result. Anything that is an assumption must be labelled as one in the output. Anything you could not verify, label `UNVERIFIED`.
9. Commit at the end of each phase with a clear message. Commit as the repo's existing git identity (AndrewNK1).
10. Environment: Windows 11, PowerShell, conda-style env at `.venv\python.exe` (Python 3.11.17), 8 GB RAM laptop (i5-1135G7). Graphviz on PATH. Keep memory use modest; the full BPI 2019 log is 1.6 M events.

---

## Background you need (verify against code)

**Data.** BPI Challenge 2019 (van Dongen 2019, DOI 10.4121/uuid:d06aff4b-79f0-45e6-8ec8-e19730c248f1). Raw: 1,595,923 events, 251,734 cases (PO items), 42 activities. Cleaning applied: drop whole cases with out-of-range timestamps (95 bad timestamps dated 1948–2016, **none after 2020**) → removes 80 cases / 2,494 events → 1,593,429 events, 251,654 cases. Four segments by `case:Item Category`: 2-way match (1,044 cases), 3-way match invoice after GR (15,164 after cleaning), 3-way match invoice before GR (220,948), Consignment (14,498).

**Resources** (`org:resource`): `user_###` = 607 values, 65.2% of events (humans); `batch_##` = 20 values, 9.8% (system/batch jobs); `NONE` = 1 value, 25.0% (no user recorded; mostly vendor actions such as "Vendor creates invoice", "Record Service Entry Sheet"). The code currently treats all of them as people.

**Mining.** IMf (Inductive Miner infrequent, noise 0.2) → process tree → BPMN. Evidence store with typed IDs (`KPI`, `BN`, `AW`, `VA`, `RW`, `RS`, `HO`, `CF`, `CM`), format `<PREFIX>-<rank:02d>-<segment-slug>`, conformance `CF-<rule>-<slug>`. Top-15 bottlenecks, impact = median wait × case share. Conformance rules R-01…R-04 in `configs/bpi2019.yaml`.

**Agent.** Prompt version `p4-v1`. LLM sees only the evidence digest (never raw data). 13 heuristics (Reijers & Mansar 2005 via Dumas 2018 Appendix A), 12 editable. 7 typed edit operations on the process tree. Validation with max 2 retries; fake IDs never accepted; persistent `unsupported_number` kept as warnings. Human review = approve/reject whole set + comment, max 2 revision loops. Only protected activity today: `Clear Invoice`. Impact ESTIMATE in `redesign/simulate.py` = trace-based what-if replay; `automate_wait_reduction: 0.5` is an assumption.

**Model.** Gemini via `langchain-google-genai`, `google_genai:gemini-3.5-flash` requested, `gemini-3.6-flash` served (both logged). Settings: temperature 0, seed 42, max_output_tokens 16000. A full real run has worked on 2-way match and (recently) on the largest segment.

---

## Gemini limits (my actual quota, checked 2026-11-13 in AI Studio)

- **RPM 5** (requests per minute), **TPM 250,000** (input tokens per minute), **RPD 20** (requests per day).
- Limits apply **per Google Cloud project, not per API key** (extra keys in the same project do not help; do not try to work around the limits).
- **RPD resets at midnight Pacific time** = **10:00 Cairo time** in November (PST, UTC−8; Cairo UTC+2). Compute the reset time with `zoneinfo` (`America/Los_Angeles`), do not hard-code it.
- RPM is a per-minute window. When I hit 5/5 it was because calls (including validation retries) were fired back-to-back.
- Budget arithmetic: one ProcessPilot run = 2 calls (diagnose + redesign), up to 6 with validation retries. Baseline run = 1 call (up to 3 with retries) if built as a single call (see Phase 6). Campaign = 20 ProcessPilot + 20 baseline runs ≈ 60 calls minimum, realistically 70–100. At ≤ 17 calls/day that is **about 4–6 days**. That is acceptable. Design for it.

---

## Phase 0: Audit and confirm (read only, no code changes)

1. Read `CLAUDE.md`, `README.md`, `docs/thesis_findings.md`, `configs/*.yaml`, `src/processpilot/**`, `web/src/**`, the git log and `git status` (uncommitted changes since 2026-11-06?).
2. Confirm or correct each fact in "Background". List differences.
3. Check the current Google documentation for the Gemini 3.x Flash models on **temperature**: if Google advises against temperature 0 for these models (for example because it causes looping or degraded output), tell me with the link. Do not change the setting; my methodology chapter says temperature 0, so it is my decision.
4. Find out exactly where the agent's LLM calls go through (`agent/llm.py`, `nodes._call_validated`) so a single rate limiter can wrap all of them, including retries.
5. Inspect the latest agent runs I made (including the largest-segment run): did they complete, how many calls, tokens, retries.
6. Output: a short audit report + your plan for Phases 1–9 with any changes you recommend to this plan. Stop.

## Phase 1: Small fixes and housekeeping

- `pyproject.toml` author → `Andrew Nabil`.
- Validation messages and comments that mention "2070" or "1948/2070" junk dates: rewrite generically ("timestamps outside the plausible range"); keep BPI-specific wording out of `src/`.
- `inputs.json` library versions (`nodes._AGENT_PACKAGES`): add `langchain-google-genai`, `langchain-groq`, `langchain-mistralai`, `google-genai` (if installed), and the exact served model name per call (already logged per call; make sure it is also in `summary.json`).
- Add Gemini prices to `configs/agent.yaml` so cost is no longer `null`. Look up the current price for the model in use from Google's pricing page, cite the URL and date in a YAML comment, and label the logged cost "estimated at list price". If you cannot verify the price, leave it null and say so.
- Log hardware in mining manifests and agent `inputs.json`: OS, CPU model, logical cores, RAM (use `platform` + `psutil` if already installed; otherwise ask me before adding a dependency).
- `README.md`: fix setup instructions to match the real conda layout (`.venv\python.exe`, no `Scripts\Activate.ps1`).
- Notebooks: `03_agent_runs.ipynb` refers to stub nodes. Update its markdown to the current flow and make it runnable with the scripted LLM (do not execute live). `04_generalizability.ipynb`: the second dataset was dropped from the thesis; delete the notebook (or ask me if you think it should stay).
- `docs/thesis_findings.md`: update stale numbers (test count, runtimes) from real measurements.
- Tests + commit. Stop.

## Phase 2: Resource types (human / batch / unassigned)

Goal: the evidence must stop presenting system jobs and missing users as people. This changes RQ1, so it must be done before the evaluation.

- Add a configurable resource classifier to the dataset preset, e.g. in `configs/bpi2019.yaml`:
  ```yaml
  resource_types:
    human: '^user_\d+$'
    batch: '^batch_\d+$'
    unassigned: ['NONE']
  default_type: human   # for generic logs without patterns
  ```
  `configs/generic.yaml` must still work for logs without this block.
- Workload evidence (`RS`): rank human resources only; report batch and unassigned as separate summary items (share of events, top activities) so the information is not lost.
- Handover evidence (`HO`): a handover counts only between two **different human** resources. Transitions involving batch/unassigned are reported separately (e.g. "system/vendor transitions") and never called handovers. `HO-00` medians recomputed on the new definition.
- Evidence summaries (code-written text) must use the right words ("batch job", "no user recorded").
- Data-quality report: add the resource-type breakdown.
- Tests with hand-built logs covering all three types. Show me before/after evidence for one segment from a mining run on the 800-case sample. Commit. Stop.

## Phase 3: Control rules in code

1. **Three-way match.** Make `protected_activities` configurable **per segment** in the domain/preset config. Protect `Record Goods Receipt` in both 3-way segments (keep `Clear Invoice` protected everywhere). Enforcement must happen in code (validation + `apply_operation`), not only in the prompt. Remove the now-redundant free-text constraint or keep it only as context.
2. **Approvals / segregation of duties: "unverifiable" flag.** The published log has approval steps removed, so ProcessPilot cannot check proposals that touch approvals. Add a config list of approval-related activities (match by configurable pattern, e.g. names containing "Approval"/"Approve"; list the matches you find in BPI 2019 and let me confirm). Any proposal whose operations touch such an activity gets a flag `unverifiable_controls: ["approval/segregation of duties cannot be checked against this log"]`. It does **not** block the proposal. Store it in `ProposalValidation`, show it in the review payload, in the UI (clear warning badge on the proposal card) and in the run logs.
3. Bump `PROMPT_VERSION` if prompts or the catalogue text change (e.g. `p5-v1`); record why in a comment.
4. Tests: a proposal removing Record Goods Receipt in a 3-way segment is rejected (`protected_activity`); the same in Consignment/2-way is allowed; a proposal automating "Change Approval for Purchase Order" passes but carries the flag. Commit. Stop.
   **Amended 2026-11-16 (Andrew):** Record Goods Receipt is protected in both 3-way segments **and Consignment**. Test: removing it is rejected in both 3-way segments and in Consignment, allowed in 2-way match.
5. **Amended 2026-11-16 (Andrew): approval list** = activities matching "Approv" (BPI 2019: `Change Approval for Purchase Order`, `SRM: Awaiting Approval`) plus `Release Purchase Order` and `Release Purchase Requisition` (both exist in the log, verified in Phase 0). Show the final list in the Phase 3 report.
6. **Amended 2026-11-16 (Andrew): must-occur rules.** For `activity_must_occur` rules (R-03, R-04), cases ending in `Delete Purchase Order Item` are excluded from the completed-cases share (a deleted item legitimately lacks invoice/GR, domain fact D-14). Must be done before the answer key is built (Phase 4); log the change in the rule metrics/summary and in `docs/thesis_findings.md`.

## Phase 4: Separate old runs from evaluation runs; freeze the answer key

**4a. Labelling old vs new.** All existing runs (mining and agent) stay on disk and in SQLite, but must be clearly marked as development runs.

- Non-destructive SQLite migration (keep the existing `meta` schema-version pattern): add `purpose` (`dev` | `evaluation`) and `campaign_id` (nullable) to `runs` and `agent_runs`; add an `origin` column to `agent_runs` (`app` | `batch`) and a `condition` column (`processpilot` | `baseline`). Backfill every existing row as `purpose='dev'`.
- New tables: `eval_campaigns` (id, created_at, frozen manifest JSON, status) and `eval_runs` (campaign_id, segment, condition, repetition 1–5, status `pending|running|done|failed|quota_stopped`, attempt count, agent_run_id/baseline path, calls used, tokens, started/finished).
- The UI must show the label: existing runs list gets a "dev" / "evaluation" badge and a filter; evaluation runs default to hidden on the normal pages (the dashboard in Phase 8 shows them).

**4b. Fresh evaluation mining run.** After Phases 2–3, run the full BPI 2019 mining once (no LLM; ~2–3 min). This becomes the **frozen mining run** for the whole evaluation.

**4c. Campaign manifest.** Script `scripts/eval/create_campaign.py` creates `outputs/eval/<campaign_id>/manifest.json` (campaign id like `eval-2026-11-v1`) with: frozen mining run id, git commit hash (refuse to run with uncommitted changes in `src/` or `configs/`), git tag `eval-v1`, prompt version, model id (requested), all agent settings, catalogue/domain versions, library versions, hardware, rate-limit settings, list of segments, repetitions = 5, conditions = processpilot + baseline. Everything in the campaign must point to this manifest. If any of these change mid-campaign, the runner must refuse to continue and say why.

**4d. Answer key (RQ1 reference list), computed by code before any LLM run.** Script `scripts/eval/build_answer_key.py` reads the frozen mining run only and writes `outputs/eval/<campaign_id>/answer_key.json` + `.csv`. Rule (fixed in my Chapter 3, do not change it):
  - per segment, the **top-3 bottlenecks by impact** (BN-01, BN-02, BN-03), with source → target activity, median wait, case share;
  - plus **every conformance rule violated in more than 1% of completed cases** (completed-only share), with its CF ID and share;
  - also store the lenient matching info for each key item: the activities involved, so a diagnosis that names the same problem through a different evidence item (e.g. the matching AW item of the target activity) can be counted under a "lenient" criterion. Strict = cites the exact ID. Report both, strict is primary.
  - Write the key once; it is read-only afterwards (store its SHA-256 in the manifest).
- Tests for the key builder with a synthetic evidence store. Show me the answer key for all four segments. Commit. Stop.

## Phase 5: Rate limiter + batch runner (ProcessPilot condition)

**5a. Rate limiter**, wrapping every LLM call the agent makes (including validation retries), configurable in `configs/eval.yaml`:
```yaml
rate_limit:
  min_seconds_between_calls: 20     # 5 RPM limit -> at most 3 calls/min, safety margin
  seconds_between_runs: 180
  daily_request_budget: 17          # quota is 20; leave headroom for manual app use
  min_budget_to_start_run: 4        # do not start a run that is likely to be cut off
  # Amended 2026-11-16 (Andrew): replaced by worst-case budgets per condition:
  # start a run only if today's remaining budget >= its worst case.
  worst_case_calls: {processpilot: 6, baseline: 3}
  quota_timezone: America/Los_Angeles  # RPD resets at local midnight there
```
- Count calls in a persistent ledger (`outputs/eval/<campaign_id>/quota_ledger.jsonl`, one line per request with timestamp in UTC and Pacific date) so the budget survives restarts. The "day" is the Pacific calendar day.
- On HTTP 429: read the error. If it is a per-minute limit, wait (respect `retryDelay`/`Retry-After` if present, else 65 s) and repeat the same request; log it as a **transport retry**, which must be kept separate from the agent's **validation retries** (validation retries are a measured result; transport retries are not). If it is a per-day limit, stop the run, mark it `quota_stopped`, and stop the runner with a message giving the next reset time in Cairo time.
- The app's normal (non-batch) agent runs should use the same min-gap throttle so manual use also stops hitting 5/5 RPM.

**5b. Batch runner** `scripts/eval/run_campaign.py --campaign <id> [--max-runs N] [--dry-run] [--only-condition processpilot|baseline] [--status]`:
- Strictly sequential: one run at a time, never parallel.
- **Order: round-robin, interleaved**: repetition 1 of every segment × condition, then repetition 2, and so on. If something goes wrong on day 4 (quota, a served-model change), all segments and both conditions are affected equally. Fixed, logged order.
- Resumable: skips `done`, retries `pending`/`quota_stopped`/`failed` (failed runs caused by transport errors are retried; a run that fails because the model's output failed validation after all retries is a **result**: mark it `done` with outcome `failed_validation`, never re-run it to get a nicer result).
- Uses the frozen mining run and the manifest settings only.
- At `human_review`, it does **not** approve or reject: it stores the payload, records the run as `done` (outcome `reached_review`), and closes the graph cleanly (or leaves it interrupted, whichever keeps the checkpoint DB clean; tell me which). No revision loops in the evaluation.
- Checks the **served model** after each call; if it differs from the first served model of the campaign, stop and tell me (this must be decided by me, not silently continued).
- Before starting a run, checks `min_budget_to_start_run` against today's remaining budget. Prints a clear status line after every run: done/total per segment and condition, calls used today, remaining, next reset time (Cairo).
- `--status` prints progress without calling anything.
- Writes per run: the agent run folder as today + an `eval_run.json` (campaign, segment, condition, repetition, calls, validation retries per node, transport retries, tokens, durations, outcome).
- Windows-friendly: I will start it by hand each day (`.\.venv\python.exe scripts\eval\run_campaign.py --campaign eval-2026-11-v1`). Optionally provide a `scripts/eval/run_daily.ps1` and instructions for Windows Task Scheduler to start it at 10:15 Cairo time, but do not register the task yourself.

**5c. Verification.** Full dry-run of the whole campaign with the scripted model (zero API calls, the limiter's waits shortened via a test flag). Then ask me for permission for **one** live smoke run on the 2-way segment, flagged as `purpose='dev'` (not part of the campaign). Commit. Stop.

## Phase 6: Baseline condition

Purpose (thesis 2.6.1 / 3.6): show whether the evidence store and typed edits help, compared with the same LLM given only PM4Py's own text abstractions of the log (the approach of Berti et al., PM4Py.LLM).

- Inputs per segment: the **same cleaned, segmented mining view** of the frozen run, turned into text with PM4Py's LLM abstraction functions (`pm4py.llm`, e.g. DFG abstraction with performance, variants abstraction, and any others available in pm4py 2.7.23.11; check what exists). Use their `max_len` options so the text stays well under the TPM limit and comparable in size to the ProcessPilot digest; record the exact functions, parameters and character/token length in the run log.
- Same model and settings, same heuristic catalogue (editable heuristics with descriptions), same domain facts, same constraints text and protected activities stated in text. Differences are only: (1) evidence = PM4Py text instead of the evidence store, (2) changes described in free text instead of typed edits.
- **One LLM call per baseline run** returning a Pydantic schema: diagnosis (1–5 problems, each with a description and the activities involved) + 2–3 proposals (title, heuristic_id, change described in text, rationale, expected effect, risks, assumptions). No evidence IDs. Schema/heuristic validation with up to 2 retries (same policy as the agent, fake heuristics rejected).
- Store outputs in the campaign folder under `baseline/<segment>/rep<k>/`. Registered in `eval_runs` with condition `baseline`. Runs through the same rate limiter and runner.
- Dry-run + tests. Ask me before one live smoke baseline run (dev). Commit. Stop.

## Phase 7: Scoring scripts (the "marking")

All in `scripts/eval/`, no LLM calls, rerunnable at any time (also on a partial campaign). Outputs go to `outputs/eval/<campaign_id>/results/` as JSON + CSV, plus PNG/SVG figures suitable for the thesis (plain, readable, labelled axes, no decoration). Every output records the manifest hash and answer-key hash.

**RQ1: diagnosis correctness** (`score_rq1.py`)
- For each ProcessPilot run: which answer-key items its diagnosis cites (strict = exact ID; lenient = activity match). Per segment: found in k of 5 runs for each key item.
- Success criterion (fixed): **in every segment, at least 2 of the top-3 bottlenecks covered in at least 4 of 5 runs, and coverage no lower than the baseline.** Output pass/fail per segment and overall.
- Internal faithfulness: first-attempt fake-ID rate (from the validation issues of attempt 1), unsupported-number rate (first attempt and final), validation retries per node, failed-validation runs.
- Baseline: it has no IDs, so generate a **hand-coding sheet** (CSV/XLSX: one row per baseline run × key item, with the baseline's diagnosis text, and an empty "covered (0/1)" column) and an importer that reads my completed sheet back. Same k-of-5 table for the baseline once coded.
- Consistency: across the 5 repetitions, Jaccard similarity of cited evidence IDs and of chosen heuristics per segment.
- **External check vs Gutermuth et al. (2019, BPIC 2019 report)**, problem level only (units differ; never compare their numbers with ours directly). Produce a table with one row per reference finding below, the evidence items in the frozen run that relate to it (by activities), whether any ProcessPilot diagnosis mentioned it (k of 5), and an empty "Andrew's judgement" column. Reference findings (page numbers are theirs):
  1. Invoice-after-GR flow has longer processing times than invoice-before-GR (p. 18).
  2. 2-way match: recording the invoice and the goods receipt are ~3 days apart on average, possibly batch-triggered (p. 18).
  3. Possible compliance issue: invoices recorded and cleared before the goods receipt in the invoice-after-GR flow (1,704 sequences; they count sequences, our R-01 counts first receipts per case, so state the unit difference) (p. 18). (Phase 0: R-01 has 0 violations in that segment by construction of the first-occurrence check; report it as is, amended 2026-11-16.)
  4. Anomaly: cases with only Create PO Item → Delete PO Item, often the same user within a minute (p. 19).
  5. Anomaly: Create PO Item followed by Change Price, also in consignment (p. 19).
  6. The process is better described by more models than the four categories (service vs non-service, single vs multiple invoices/receipts, SRM subprocess) (p. 13): report as context, not as a detection target.

**RQ2: soundness, controls, estimated effect** (`score_rq2.py`)
- Per proposal: applied or `edit_not_applicable`; soundness result and **which method** confirmed it (Woflan vs structural only for models > 40 transitions); protected-activity rejections during validation; `unverifiable_controls` flags; whether any control-violating proposal reached review undetected (must be zero).
- **Sensitivity test:** recompute the ESTIMATE of every applied proposal with `automate_wait_reduction` = 0.25, 0.50, 0.75 (no LLM calls; reuse `redesign/simulate.py` with an override parameter, do not edit the config). Record median/mean/P90 change for each.
- Success criterion (fixed): **all applied proposals sound; no control violation reaches review undetected; per segment at least one proposal whose estimated median reduction keeps its sign at 25/50/75%.** Pass/fail.
  **Amended 2026-11-16 (Andrew):** keep the criterion, but report per segment which edit type(s) made it pass, and mark proposals whose ESTIMATE does not depend on `automate_wait_reduction` as `assumption-independent`.
- **Devil's Quadrangle profile per proposal**, each value labelled `measured` / `proxy` / `qualitative`:
  - Time (measured): median/mean/P90 throughput change and share of cases affected (from the ESTIMATE).
  - Quality (proxy): rework rate and compliance-rule violation rate of the cases the proposal affects (as-is), and the direction implied by the operation (e.g. removing a repeated activity removes its rework). Explain the rule in the output.
  - Cost (proxy): manual work = events by **human** resources (Phase 2 classification) per affected case, as-is vs after the operation (removed/automated activities' human events removed).
  - Flexibility (qualitative): from a config table `configs/eval_quadrangle.yaml` mapping each heuristic to the quadrangle dimensions it primarily affects according to Dumas et al. 2018, Appendix A. **I will fill this table from the book**; create it with the heuristic IDs and empty values, and make the script report "not filled" rather than inventing values.
- Heuristic usage frequency per segment.
- Baseline: free-text proposals cannot be applied or simulated. Generate a hand-coding sheet (does the proposal break a P2P control: yes/no/unclear; which heuristic it really applies) and an importer.

**RQ3: rationale quality** (`score_rq3.py`, `make_scoring_sheet.py`, `import_scores.py`)
- Automatic faithfulness checks per ProcessPilot proposal: all cited evidence IDs exist; every number in the rationale/effect text matches a cited evidence item (reuse the validator's number logic); heuristic matches the edit operations (e.g. H-AUTOMATION ↔ automate_activity).
- **Planted flawed proposals:** create a small fixed set (around 5) of hand-crafted flawed proposals in a versioned file `configs/eval_planted_flaws.yaml`, run them through the same validators, and record whether code catches each. Suggested flaws: removes Record Goods Receipt in a 3-way segment; cites a non-existent evidence ID; states a number that is not in the evidence; heuristic that does not match its edit; automates an approval activity (should be flagged unverifiable). Flaws not caught by code stay in the scoring sample so I can catch them by hand.
- **Blind scoring sheet:** sample = all proposals from **2 repetitions per segment for each condition** (fixed, seeded random choice of which 2, recorded) + the planted flaws. Shuffle with a fixed seed, replace origin with an opaque code, strip anything that reveals origin (evidence IDs are part of the rationale and stay; that limit of blinding is acknowledged in my thesis). Export XLSX with columns C1–C5, each scored 0/1/2: C1 evidence cited correctly (**amended 2026-11-16, same wording for both conditions, printed in the scoring sheet:** "claims traceable to the input the model was given (evidence ID, or a concrete figure/activity from that input)"), C2 heuristic fits the problem, C3 effect stated, C4 uncertainty stated (assumptions/risks), C5 readable. The key mapping codes → origin is written to a separate file that the dashboard and scripts must not open until scores are imported.
- **Second scorer:** export a random third of the sample, stratified by origin (fixed seed), as a separate sheet for my classmate. After both are imported: weighted Cohen's kappa per criterion (quadratic or linear weights; state which).
- After import: mean scores per criterion and origin, with the success criterion (fixed): **ProcessPilot above the baseline on C1 and C4; every planted flaw caught by code or by scoring.** Pass/fail.

**Efficiency (for Chapter 5):** per condition and segment: calls, validation vs transport retries, input/output tokens, latency per node, estimated cost.

**`summarize.py`:** one `summary.json` + a readable `summary.md` with all success criteria, tables and figure paths, clearly labelled as generated from campaign `<id>`. No interpretation text; that is my job in the thesis.

Tests with synthetic campaign data. Run everything on the dry-run campaign from Phase 5. Commit. Stop.

## Phase 8: Evaluation dashboard in the app

A new read-only page **Evaluation** in the React app, route `/evaluation/:campaignId` (plus a campaign picker), backed by new FastAPI endpoints (`GET /eval/campaigns`, `GET /eval/campaigns/{id}`, `/progress`, `/results`, `/answer-key`) that read the campaign folder and SQLite. Follow the existing design system (`DESIGN.md`, `PRODUCT.md`, `.impeccable/`): same typography, colours and components; no new UI library. Panels:

1. **Campaign header:** model (requested/served), prompt version, frozen mining run, git tag, dates, manifest hash.
2. **Progress:** grid segment × condition × repetition (pending / running / done / failed_validation / quota_stopped); calls used per Pacific day vs the 17/20 budget; next reset time in Cairo time.
3. **Success criteria:** RQ1, RQ2, RQ3 cards with pass / fail / not yet available, each linking to its panel.
4. **RQ1:** heatmap of answer-key items × segment showing "found in k of 5" (ProcessPilot vs baseline side by side, baseline only after hand-coding is imported); first-attempt fake-ID rate; unsupported numbers; retries; consistency (Jaccard).
5. **RQ2:** soundness (with method), control rejections and flags, distribution of estimated median throughput change per segment, sensitivity chart (25/50/75%) per proposal, heuristic usage, quadrangle profile table with measured/proxy/qualitative labels.
6. **RQ3:** automatic faithfulness pass rates and planted-flaw detection. **Score panels stay locked** (show "blind scoring in progress") until both scoring sheets are imported, so the dashboard can never unblind me during scoring.
7. **Efficiency:** tokens, latency, cost per condition.
8. Every chart has a "download CSV" of its data and a small "how this is computed" note.
9. **Run explorer (added 2026-11-16, Andrew)**, for reviewing runs with my supervisor:
   - A browsable list of every campaign run, grouped by segment, then condition, then repetition ("Segment 1 · ProcessPilot · Run 3").
   - Opening a run shows on one screen: run metadata (served model, date, calls, retries, tokens, outcome); the AI's diagnosis (hypotheses with confidence, alternative explanations and cited evidence; clicking an evidence ID shows its summary); each redesign proposal (title, heuristic, edit operations, rationale, expected effect, risks, assumptions, any unverifiable-controls flag); as-is vs to-be BPMN side by side with the changed tasks highlighted (reuse `BpmnView`); validation results (soundness and method, the ESTIMATE at 25/50/75%, the quadrangle profile); the RQ1 marking for that run (which answer-key items it covered).
   - Baseline runs show their diagnosis and free-text proposals, plus Andrew's hand-coded results once imported.
   - A print-friendly view per run.
   - A review-notes field per run and per proposal (author, text, timestamp; stored in SQLite, kept separate from the scoring data) for comments during a supervisor meeting.
   - Dev runs in a separate, clearly labelled tab.
   - Blinding: the explorer reveals which condition wrote each proposal, so it is locked until both scoring sheets are imported. An explicit "unlock before scoring" button with an in-page confirmation (no browser alert/confirm) unlocks it early and records the unlock date and time in the campaign folder.

Show me a screenshot of each panel populated with the dry-run campaign. Web tests for the new page. Rebuild the bundle. Commit. Stop.

## Phase 9: Running the campaign (me) and wrap-up (you)

1. Give me a one-page **runbook** (`docs/EVALUATION_RUNBOOK.md`): how to create the campaign, start/resume each day, read `--status`, what to do on quota stop, on served-model change, on a crash; how to do the hand-coding sheets and the blind scoring; how to import scores; how to regenerate results.
2. I create the real campaign and run it over several days (you are not needed while it runs).
3. When I come back with a finished campaign: run all scoring scripts, check results for anomalies (missing runs, impossible values, mismatched hashes), report them, and produce the final `summary.md` and thesis figures. Also list for me every number from the campaign that Chapter 5 should report, with the file it comes from.

---

## Things you must not do

- Do not change the answer-key rule, the success criteria, the scoring checklist, the number of repetitions or the baseline design. They are fixed in my methodology chapter. If you think one is flawed, tell me; do not change it.
- Do not approve/reject proposals automatically or run revision loops in the evaluation.
- Do not re-run an evaluation run because its result looks bad. Only transport/quota failures are retried.
- Do not send raw log data to the LLM in the ProcessPilot condition.
- Do not delete old runs, uncommitted work, or database rows.
- Do not put API keys in code, logs, commits or output files.
- Do not make real LLM calls without my explicit go-ahead in that phase.

---

# Decisions after Phase 0 (Andrew, 2026-11-16)

- Gemini: temperature/top_p/top_k are no longer sent (Google: deprecated and ignored on
  Gemini 3.6 Flash). Seed 42 is kept and logged; its effect is labelled UNVERIFIED in the run logs.
- Model requested directly as `google_genai:gemini-3.6-flash`.
- Record Goods Receipt protected in both 3-way segments and Consignment (Phase 3).
- Approval list adds Release Purchase Order / Release Purchase Requisition (Phase 3).
- Must-occur rules exclude Delete-ending cases from the completed share, before the answer key (Phase 3).
- openpyxl added; psutil and matplotlib declared in pyproject.toml.
- Transcript `.txt` gitignored; notebook 04 deleted.
- Sensitivity reporting, C1 wording, Gutermuth finding 3, worst-case run budgets: see the
  amended lines in Phases 5 and 7. Phase 1/4/5/7 additions from the Phase 0 report approved:
  Phase 1 finish_reason + reasoning tokens + served model snapshot at review; Phase 4
  `agent_runs.launched_by` (not `origin`) and per-campaign checkpoints; Phase 5 eval runs left
  paused at review (app refuses decisions, 409), SDK retries off for Gemini
  (langchain-google-genai: `max_retries=1` means no retries; default is 6), cut-short runs
  labelled, never deleted; Phase 7 XLSX via openpyxl.
- Run explorer added to Phase 8 (item 9).

# Decisions after Phase 1 (Andrew, 2026-11-17)

- Gemini `thinking_level: medium` pinned in `configs/agent.yaml` (Google's current
  default), logged in `inputs.json` (`model_kwargs`, `model_notes`), tested on the
  request the client builds. **Phase 4c: the campaign manifest must include it**
  (it is part of "all agent settings"; check it explicitly).
- Hidden SDK retries (langchain-google-genai default 6): fixed in Phase 5 as planned;
  no agent runs from the app until then.
- BPI design comments in src, the Anthropic-only live test and the local CLAUDE.md
  stay as they are.

# Phase log

## Phase 0 done (2026-11-13, read-only audit)

- Created this file (uncommitted). No code changed, no LLM calls.
- Background facts verified against code, the cached parquet and run `20261111T140530Z`;
  all data/resource/mining/agent facts hold. 511 Python tests collected.
- Findings that change later phases:
  - Gemini 3.6 Flash (the served model) ignores `temperature` (Google: deprecated and
    ignored; https://ai.google.dev/gemini-api/docs/generate-content/whats-new-gemini-3.6).
    Seed handling UNVERIFIED. Default thinking level `medium`, not set or logged by us.
  - "2070" junk dates do not exist (bad timestamps are 1948–2016); wrong text in
    `ingest/validate.py:10,180` and `docs/thesis_findings.md:26`.
  - Gemini `finish_reason` is not captured (`stop_reason` is null); reasoning tokens are
    folded into `output_tokens` and not logged separately.
  - `summary.json` is only written by `finalize`, so runs paused at review have none.
  - All LLM calls pass through `ctx.llm.invoke` in `nodes._call_validated`; a
    `StructuredLLM` wrapper is the single chokepoint. Provider SDK retries must be
    disabled or counted, or the quota ledger undercounts.
  - `runs.origin` (app|disk) already exists; a new `agent_runs.origin` would clash in meaning.
  - psutil 7.2.2 and matplotlib 3.11.2 installed (pm4py deps, undeclared); openpyxl absent.
- Open questions for Andrew: listed in the Phase 0 report (temperature, requested model id,
  Consignment GR protection, approval activity list, openpyxl, untracked transcript file).

## Phase 1 done (2026-11-16)

- Gemini: `configs/agent.yaml` sends no temperature/top_p/top_k (langchain-google-genai
  then sends none for Gemini 3+, tested); seed 42 kept; new `model.notes` caveats (seed
  effect UNVERIFIED, thinking_level default) copied into `inputs.json`, `review_r<N>.json`
  and `summary.json`. `.env` / `.env.example`: `PP_MODEL=google_genai:gemini-3.6-flash`.
- Price `gemini-3.6-flash`: 0.75 / 3.75 / cache 0.075 USD per M tokens (paid Standard tier,
  valid through 2026-12-31, source + date in the YAML); every cost logged with
  `cost_basis: "estimated at list price ..."`.
- `agent/llm.py`: stop reason also from `finish_reason`; `TokenUsage.reasoning_tokens`.
  `runlog.py`: `served_models()`, reasoning tokens in totals. `nodes.py`: `_AGENT_PACKAGES`
  + langchain-google-genai, google-genai, langchain-groq, groq, langchain-mistralai;
  hardware in `inputs.json`; `review_r<N>.json` snapshot written by `validate`;
  `summary.json` has `served_models`. Served model cannot be in `inputs.json` (written before
  the first call).
- `mining/run.py`: `hardware_info()` (OS incl. Windows 11 fix, CPU name, cores, RAM) in every
  `manifest.json`. `ingest/validate.py`: no BPI/2070 wording.
- `agent/dry_run.py`: scripted dry-run model (`scripted:dry-run`, zero API calls, valid
  canned outputs from the segment's evidence/model); notebook 03 rewritten to the current
  flow, dry run by default on the sample mined into a temp folder (executed once in dry-run
  to a scratch copy: reject -> revise -> approve OK; committed notebook has no outputs).
- Deleted notebook 04; transcript pattern in `.gitignore`; README setup = conda `.venv\python.exe`;
  pyproject: psutil, matplotlib, openpyxl (3.1.5 installed); `requirements.lock` refreshed;
  `docs/thesis_findings.md`: junk dates corrected (none after 2020), measured runtimes,
  real Gemini runs, hardware, test count, decisions.
- Tests: 520 Python passed (511 + 9 new: `test_llm_logging.py`, `test_dry_run.py`, one in
  `test_agent_graph.py`, manifest hardware in `test_evidence.py`); 7 web passed; ruff clean.
- Open: thinking_level is not pinned (provider default); decide before Phase 4c freezes the
  manifest. langchain-google-genai retries 429s itself (default 6): fix in Phase 5.
