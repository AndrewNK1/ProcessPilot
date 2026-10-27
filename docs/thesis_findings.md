# ProcessPilot: findings for the thesis

Source material for the Methodology, Results and Limitations chapters. Every
item is a measured fact or a recorded decision from the build, with where it
comes from. Numbers are from BPI Challenge 2019 unless stated otherwise.
Last updated: 2026-10-27 (UI design phase: themes, wait ladder).

---

## 1. Dataset and preprocessing

**Dataset facts (measured, Phase 1, full parse of the 728.6 MB XES).**
1,595,923 events, 251,734 cases, 42 activities, 21 raw columns (16 case
attributes, 5 event attributes), 628 resources, `org:resource` 0% null.
Four item categories: 3-way match, invoice before GR (221,010 cases);
3-way match, invoice after GR (15,182); Consignment (14,498); 2-way match (1,044).
- `User` is an exact copy of `org:resource` on 100% of rows (redundant column).
- `Cumulative net worth (EUR)` is present but unused (possible future value weighting).

**Junk timestamps.** Minimum timestamp 1948-01-26 and values in 2070: sentinel
values, not real transactions. The documented upper bound (2020-01-01) was
wrong: the real maximum is 2020-04-09, so a plausible range of
2017-01-01 to 2020-05-01 is used. Cases with out-of-range timestamps are
dropped by an explicit, logged cleaning policy, never silently.
*Methodology point:* validation reports, cleaning is a separate caller-chosen
step, and every removal is logged (`ingest/validate.py`).

**Timezones.** XES timestamps are tz-aware; converted to UTC, then made
tz-naive, so waiting times across a DST switch stay correct. Cost: wall-clock
times shift 1-2 h versus the source ERP (matters only for working-hour analysis).

**Timestamp ties.** About 15% of events tie on timestamp with another event of
the same case. pm4py re-sorts events internally, so its directly-follows
graph could order ties differently from ours. Decision: the DFG, waits and
variants are computed in pandas from one stable canonical order (source order
breaks ties), so every number agrees with every other. A test cross-checks
frequencies against `pm4py.discover_dfg` on a tie-free log.

**Batch postings.** ~181k events are same-case, same-activity, same-instant
repeats (137k "Record Service Entry Sheet", 43k "Record Goods Receipt"): line
items posted together. Collapsed to one event for DFG, variants and rework
(otherwise they appear as zero-wait loops and phantom rework); resource
workload keeps raw counts. (User decision, Phase 2.)

**Single-timestamp log.** Events have one (completion) timestamp, so the wait
on `a -> b` is `ts(b) - ts(a)` and includes b's own processing time. Waiting
and processing time cannot be separated. State this in the methodology.

**Segmenting.** The pipeline runs per item category, because each is a
genuinely different P2P flow. Segmenting must use a case-level attribute; the
validator now flags `segment_varies_within_case` and refuses to split a case
across segments (added in the code audit for generalisability).

**Cleaning for other datasets.** Optional `drop_cases_with_missing_values`:
rows with no case id are dropped alone; cases with a missing or unparseable
activity or timestamp are dropped whole, so no trace gets a silent gap. Off by
default, logged when used.

## 2. Process-mining design

**Discovery.** Inductive miner (sound by construction), noise threshold 0.2 to
avoid a flower model. **Finding:** at 0.2 the miner leaves rare activities
out of the model: 2-way match 8 of 11 activities, 3-way after GR 33 of 38,
3-way before GR 39 of 39, Consignment 15 of 15. Edits may therefore only name
activities that are in the model; the agent is told which observed activities
are outside it.

**Bottlenecks.** Per directly-follows edge: frequency, median/mean/P90 wait,
case share; impact = median wait x case share (median, so a few junk
outliers cannot invent a bottleneck). The top-N cut-off was raised from 10 to
15 (user decision) because with 10 the listed edges covered only 75.1% of
total transition wait in "3-way, invoice after GR". With 15, coverage is
97.7 / 80.8 / 91.2 / 98.2% across the four segments (coverage = sum of mean
wait x frequency over listed edges / all edges).

**Wait before each activity** (AW evidence) is kept separately from
per-transition waits, because an analyst looking at one task asks "how long
do cases wait before this task, whatever came before?". Examples: median 35.0
days before "Clear Invoice" (3-way before GR, 78.6% of cases), 16.8 days
(3-way after GR), 22.6 days before "Vendor creates invoice" (2-way match).
Long waits before clearing may reflect payment terms rather than
inefficiency (domain fact D-03), which the agent must consider.

**Rework.** Extra time = median throughput of cases repeating an activity
minus that of cases doing it once. This is association, not cause, and the
evidence text says so.

**Handovers.** Waits are split by resource change. In P2P a handover usually
also crosses a department, so handover and bottleneck findings overlap; they
cross-reference each other.

**Variants.** Top-N with case share and throughput; the "happy path" is the
most frequent variant, not necessarily the best. An optional variant filter
(top-k / coverage / minimum share) exists; by default it affects only the
drawn model, never the numbers. It is off for BPI 2019.

**Conformance rules.** Declarative rules in YAML, so the engine stays
dataset-agnostic. Two lessons:
- Rule R-02 originally required "Clear Invoice" before "Vendor creates debit
  memo"; 96% of cases (5,441 of 5,676) "violated" it because a debit memo
  normally precedes clearing. The rule was wrong, not the process; it was
  replaced. *Rules must be sanity-checked against the data before reporting.*
- R-03 ("invoice must occur") would flag 100% of Consignment as violations:
  Consignment is invoiced outside this log (0 of 14,498 cases). Restricted to
  the other segments.

**Open cases.** The log is a time window, so "activity never happened" may
mean "still running". Conformance is reported for all cases and for completed
cases (last activity in a configured end set). Measured end shares: "Clear
Invoice" ends 77.8% (3-way before GR), 61.0% (after GR), 15.9% (2-way);
Consignment ends in "Record Goods Receipt" for 90.6%. Caveat: in 2-way
match "completed" nearly implies an invoice, so R-03 on completed cases is
close to tautological there.

**Reference-model conformance.** Supported (token-based replay) but BPI 2019
has no reference model, so it is reported as "not performed". A model
discovered from the same log is deliberately NOT used as reference: that would
be circular (it would always "conform"). **pm4py finding:** token-based replay
silently skips activities that are not in the model, so a case containing one
counts as fitting; ProcessPilot counts such cases as non-conforming itself.

## 3. Evidence and grounding (core of the architecture)

- Deterministic computation, LLM reasoning: pandas/pm4py compute every
  number; the LLM sees only evidence items, never raw event data.
- Every finding has a deterministic ID (`<PREFIX>-<rank>-<segment>`, e.g.
  `BN-01-2-way-match`), so reruns are comparable. Reruns were verified
  byte-identical on BPI; the run after the code audit and Phase 4 reproduced
  all 294 items exactly.
- Each item's one-sentence summary is generated by code, not by the LLM.
- Absence is made explicit: when an analysis is skipped (no resource column,
  no reference model), an item says so, so the agent cannot read silence as
  "no problem".
- Evidence count: 250 items (Phase 3) -> 294 items (Phase 4, adding activity
  waits and model-conformance notices).

## 4. Agent design (LangGraph)

- Fixed pipeline, not a free-roaming agent: load_evidence -> diagnose ->
  redesign -> apply_edits -> validate -> human_review (interrupt) -> finalize;
  a rejection with a comment loops back to redesign (max 2 loops).
- The diagnosis gets a pre-built evidence digest instead of tool calls
  (~60 items per segment fits one prompt): the prompt is identical on rerun,
  it costs one model call, and there are no tool-loop failure modes.
- Validators in code, not in the prompt. An output is rejected and retried
  (max 2 retries, with the errors fed back) for: unknown evidence IDs, unknown
  heuristics, non-editable heuristics, unknown activities, edits to protected
  activities, edits that cannot be applied to the model, and numbers not
  present in the cited evidence.
- Number check: a number must match (at the precision written) a number in
  the cited evidence item. If only number issues remain after the retries,
  the output is kept with visible warnings (user decision), because the check
  has false positives.
- Redesign is restricted to a catalogue of Reijers & Mansar (2005) heuristics
  (v2, 13 heuristics, one not BPMN-editable), so the LLM cannot invent
  interventions.
- Human in the loop: the system never executes changes; an analyst approves
  or rejects every proposal.
- Reproducibility: the model id, prompt version (`p4-v1`), full prompts, raw
  outputs, token usage, cost and library versions are logged per run. Claude
  Opus 5.5 rejects sampling parameters, so its output is not bit-reproducible;
  logging is the substitute.
- No API key yet: no real LLM run has been made. All agent tests use a
  scripted fake model.

## 5. Redesign and validation (Phase 4)

**Edits act on a process tree, not on BPMN.** The inductive miner produces a
block-structured process tree; every process tree maps to a sound workflow
net, so editing the tree keeps every to-be model sound by construction.
Seven typed operations: remove, add, add check, reorder, parallelize, merge,
automate. Parallelize and merge need adjacent steps of one sequence; anything
else is refused with a reason, and the agent is asked to revise.

**Protected activities** (e.g. "Clear Invoice", a financial control) may not
be removed, moved, merged or parallelised, but may be automated (user decision).

**Stable element IDs.** pm4py's tree-to-BPMN converter writes new random
UUIDs on every export, which breaks diffs and overlays. ProcessPilot renders
BPMN itself: task ids come from activity names and stay the same in the as-is
and every to-be model.
**pm4py finding (2026-10-26):** even with fixed element ids, pm4py's BPMN
exporter still made every file different. It writes elements in Python set
order, which changes between runs; it gives the diagram and plane new random
ids on each write; and it ignores the process id it is given, using a random
one per Python session instead. ProcessPilot puts the exported XML into a
fixed form (elements sorted by tag and id, ids derived from the process id),
so the same tree now gives a byte-identical BPMN file across runs. This was
verified on the 129-shape "3-way, invoice after GR" model in two separate
processes, and the Graphviz layout coordinates were unchanged. The order of
elements carries no meaning in BPMN.

**Soundness in three layers:** (1) by construction (process tree);
(2) a structural check of the rendered BPMN (reachability, one in/one out per
task, gateways actually split or join); (3) Woflan as an independent check,
only up to 40 Petri-net transitions. **Finding:** Woflan did not finish within
120 s on BPI's two large models (91 and 126 transitions, many loops and
parallel blocks), while 22- and 33-transition models took under 0.2 s. The
gate is a count, not a time limit, so the verdict does not depend on machine
speed. `pm4py.check_soundness` is deprecated since pm4py 2.3.0 and was not
used. Woflan warned that its default scipy solver "may be unstable", so PuLP
was added as the solver.

**Impact ESTIMATE, not simulation.** Historical cases are replayed with the
edit applied to their observed waits: removing a step drops its wait;
automating it reduces the wait by an assumed 50% (configurable); an added
step costs the segment's median wait (configurable); parallel steps cost the
longest wait instead of the sum; reorder and merge are explicitly "not
estimated". A discrete-event simulation was rejected because it needs arrival
rates and capacities that the log does not contain.
**Limitation:** the whole gap before an event is attributed to that event, so
removal savings are upper bounds. Demo on 2-way match: removing "Change
Approval for Purchase Order" gives an estimated median of 23.7 -> 9.0 days
(-61.8%); automating invoice recording and clearing gives 23.7 -> 23.3 days.
A parallelisation proposal was correctly refused (the invoice step sits
inside a choice).

## 5a. Application architecture (app phase)

**Three layers, one direction.** Streamlit (what the analyst sees) talks only
to a FastAPI backend over HTTP; the backend calls the same pipeline functions
the notebooks use (`pipeline.py`, `AgentRunner`). Why a backend at all: the
review step is a paused LangGraph run that may be resumed hours later, and
mining and agent runs take minutes, so long-lived state (background jobs,
checkpoints, decisions) needs an owner that survives a browser refresh.

**SQLite as an index plus the decision log, not a copy** (user decision). The
run folders stay the single source of truth for evidence, models, agent logs
and checkpoints; SQLite records which datasets and runs exist, the state of
background jobs, and every analyst decision (round, approve/reject, comment,
analyst name, proposal titles, UTC time). The decision is written *before* the
graph resumes. This table is the audit trail behind "a human approves or
rejects every proposal". Because it is only an index, it can be rebuilt; runs
mined in notebooks are picked up from disk.

**Analyst in control of the data.** The Data page enforces the method's order:
choose a preset or upload a file -> confirm the column mapping (suggestions
ranked with confidence, never picked silently) -> read the data-quality
report (which changes nothing) -> tick the cleaning steps (the preset's
suggestion is only the default) -> mine. Every removal is written to the run's
`data_quality.json`.

**Cleaning recommendation.** For each cleaning option ProcessPilot gives a
recommendation computed by code from the dataset itself: each option is
dry-run alone, and fixed rules turn the exact counts into a recommendation and
a written reason. Position encoded: remove data only when keeping it would
distort waits or throughput; keep real behaviour; refuse removals above 5% of
events, because they point to a wrong mapping, timestamp format or date range
rather than bad data; remove single-event cases only when they exceed 5% of
cases (each counts as zero throughput). The analyst starts from it and can
override every option. On full BPI 2019 the recommendation equals the
preset's suggestion: remove the 80 cases (2,494 events, 0.2%) with dates
outside 2017-01-01 to 2020-05-01; keep the 2,835 single-event cases (1.1%)
and the 180,755 exact duplicate events (11.3%, batch postings already
collapsed for flow analysis).

**Mock model as a baseline.** `PP_MODEL=mock` is a rule-based stand-in behind
the same interface as the LLM: it reads the same evidence, its output passes
the same validators, and every text is marked `[mock]`. It lets the whole
pipeline run without an API key, and it is the natural baseline for the
evaluation: "what would simple rules have proposed from the same evidence?"
Its rules: diagnose the top bottlenecks, the top activity wait and the top
rework; propose automation of the activity with the leading wait, elimination
of the most repeated non-protected activity, and automation of the target of
the top bottleneck, each trial-applied to the model.

**BPMN display offline.** bpmn-js 18.31.0 (navigated viewer) is vendored, so
the app needs no network except for the LLM call. As-is and to-be models are
shown side by side; because element ids are stable, the same task has the
same id in both.

**Findings while building it.**
- pm4py 2.7 looks up its parent process name (psutil) when first imported and
  raises if that process has exited. A server outlives its launcher, so the
  API imports pm4py at start-up; otherwise every mining job failed with
  `NoSuchProcess`.
- pm4py's BPMN exporter made every file different even with fixed element ids
  (set order, random diagram/plane ids, ignored process id); fixed by writing
  the XML in a canonical form (see section 5).
- The committed sample (800 cases, 200 per item category, chosen by SHA-256 of
  the case id after cleaning) mines in about 7 s into 275 evidence items, so
  the full app flow can be tested end to end in the automated suite.

## 5b. User interface design (UI design phase)

**Users first, demo second** (user decision). The UI is designed for an SME
business analyst in long desktop sessions; the thesis demo benefits from that.
Streamlit was kept (user decision) so the API client and tests stay valid; the
look comes from Streamlit's own theme config (light and dark), vendored IBM Plex
fonts, and custom components v2 that render in the page and read the theme's
CSS variables, so light and dark need no second set of styles.

**The wait ladder** (user-chosen direction, from lean value-stream maps). Each
page answers one question first; on Evidence it is "where do the days go?". The
answer is drawn: one variant's steps top to bottom, each step's *mean* wait as a
bar starting where the previous one ended. WHY per path and not per edge: edge
waits are medians over every case on that transition, so laid end to end along
one path they describe nothing a case experienced (the 2-way-match happy path
takes 40 min; its one edge has a segment-wide median of days). WHY the mean
positions the bars: means are additive, so the rungs sum exactly to the path's
mean throughput (checked in `tests/test_mining_paths.py`); medians and P90 are
shown per step but do not add up. Computed in `mining/paths.py` from the
`case_waits.parquet` mining already writes; served read-only by
`GET /runs/{id}/segments/{slug}/variants/{rank}/waits`. The default path is the
most frequent one with more than one wait (choosing the path through the worst
bottleneck was tried and opened on a path of 4 cases in the sample).

**Proposals are shown where they act.** Each proposal draws the same ladder for
the most frequent path it touches, with automated / removed / added steps
marked, and its ESTIMATE as today-vs-estimate bars on the pair's own scale;
cited evidence sits beside every claim rather than behind an expander. As-is and
to-be BPMN are stacked full width (side by side, the wide models shrank below
reading size) with the changed tasks outlined.

**Human in the loop, enforced in the form.** The approve/reject control has no
default; submitting without a choice keeps the typed comment and asks again
(tested in `tests/test_app_ui.py`).

**Accessibility.** WCAG AA contrast checked for every token in both modes. One
trade-off found: Streamlit always puts white labels on primary buttons, so dark
mode's primary teal must stay deep (#1A7F88, 4.73:1 with white), which is under
4.5:1 as text on the dark ground; selected segment and tab labels therefore use
the ink colour, and a brighter teal (#3CB4BE) carries data in dark mode.

## 6. Limitations to state

- Single-timestamp log: waiting and processing time cannot be separated.
- The noise threshold removes rare activities from the model; they cannot be
  redesigned.
- The estimate ignores queueing, capacity and resource contention;
  removal effects are upper bounds; the automation % is an assumption.
- Rework extra time is association, not causation.
- Conformance rules are hand-written domain rules; a wrong rule produces false
  findings (R-02 lesson).
- Open cases at extraction bias "missing activity" counts; partly handled by
  completed-case reporting.
- No reference model for BPI 2019, so there are no model-deviation findings.
- Woflan does not scale to large models; soundness there rests on construction
  plus the structural check.
- LLM outputs are not bit-reproducible (Opus 5.5 rejects sampling parameters).
- pm4py is AGPL v3: ProcessPilot cannot be distributed as closed source.
- The app is a single-user local tool: no authentication, the analyst name in
  the decision log is self-reported, and background jobs run one at a time in
  the API process.
- The analyst approves or rejects the set of 2-3 proposals as a whole; there
  is no per-proposal choice in the graph.
- Streamlit has no API to switch the theme: the Light/Dark switch stores the
  choice where Streamlit's settings menu keeps it and reloads the page (the
  current run, segment and path are carried over in the URL).
- The wait ladder shows one variant at a time; cases on rare paths (outside the
  top-10 variants) are only in the tables.

## 7. Tools and environment

Python 3.11.17; pm4py 2.7.23.11 (AGPL v3); pandas 3.0.6; pydantic 2.14.1;
LangGraph 1.2.15; langchain 1.4.6; langchain-anthropic 1.7.6; PuLP 3.3.2
(capped below 4.0: pm4py uses APIs that PuLP 4.0 removes); Graphviz 16.1.0
(needed for BPMN layout and pictures). Exact set in `requirements.lock`.
FastAPI 0.162.0, uvicorn 0.54.0, Streamlit 1.66.0, bpmn-js 18.31.0 (vendored).
Runtime: mining all four BPI segments takes about 1.5 min (largest segment
about 59 s); the 800-case sample about 7 s. Tests: 295 automated tests on
hand-built logs with known answers and on the committed sample (including the
API end to end: preset -> mine -> agent -> reject -> revise -> approve); BPI
2019 tests are opt-in.

## 8. User decisions log (cite as design choices)

| When | Decision |
|---|---|
| Phase 0 | Timestamps: convert to UTC, then tz-naive |
| Phase 2 | Collapse same-instant batch postings |
| Phase 2 | Report conformance for all and for completed cases |
| Phase 3 | Bottleneck top-N raised 10 -> 15 (coverage) |
| Phase 3 | Model: Claude Opus 5.5 via `PP_MODEL`, swappable |
| Phase 3 | Number-check issues downgrade to warnings after retries |
| Phase 3 | "Extra resources" heuristic is not BPMN-editable |
| Re-plan | Keep existing architecture; add mock LLM, FastAPI and SQLite later |
| Re-plan | Raw BPI zip stays in the repo for the supervisor |
| Phase 4 | Protected activities may be automated |
| Phase 4 | Automation assumes 50% wait reduction; an added step costs the median wait |
| App phase | SQLite holds an index and the decision log, not a copy of runs |
| App phase | Data enters via a configured preset or an uploaded file with analyst-confirmed mapping |
| App phase | A rule-based mock model is built (no-key runs, evaluation baseline) |
| App phase | bpmn-js is vendored so the app works offline |
| UI phase | Analysts are the primary user; the demo is secondary |
| UI phase | Stay on Streamlit; light and dark mode with a visible sidebar switch |
| UI phase | Petrol teal accent, IBM Plex Sans (vendored, offline) |
| UI phase | Signature element: value-stream wait ladder (per-path mean waits) |
| UI phase | BPMN diagrams follow the dark theme; the bpmn.io watermark stays visible |
