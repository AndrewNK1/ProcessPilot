# ProcessPilot: findings for the thesis

Source material for the Methodology, Results and Limitations chapters. Every
item is a measured fact or a recorded decision from the build, with where it
comes from. Numbers are from BPI Challenge 2019 unless stated otherwise.
Last updated: 2026-10-27 (front end replaced: Streamlit -> React web app; see 5c).

**Read first for the Methodology chapter.** The analyst-facing front end is a
React web app served by the FastAPI backend (section 5c). An earlier Streamlit
front end existed during development and was discarded on 2026-10-27; section
5b documents it only as design history. Describe the system as built: data and
evidence pipeline (sections 1-5), API and decision log (5a), web app (5c).

---

## 1. Dataset and preprocessing

**Dataset facts (measured, Phase 1, full parse of the 728.6 MB XES).**
1,595,923 events, 251,734 cases, 42 activities, 21 raw columns (16 case
attributes, 5 event attributes), 628 resources, `org:resource` 0% null.
Four item categories: 3-way match, invoice before GR (221,010 cases);
3-way match, invoice after GR (15,182); Consignment (14,498); 2-way match (1,044).
- `User` is an exact copy of `org:resource` on 100% of rows (redundant column).
- `Cumulative net worth (EUR)` is present but unused (possible future value weighting).

**Junk timestamps.** 95 timestamps fall before 2017, the earliest 1948-01-26
(by year: 1948: 10, 1993: 9, 2001: 22, 2008: 45, 2015: 3, 2016: 6; latest
2016-11-30): sentinel values, not real transactions. None lies after the real
period (an earlier note here mentioned 2070 dates; re-checked 2026-11-13 on the
full log, there are none). The documented upper bound (2020-01-01) was
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

**Resource types (evidence schema 2, 2026-11-17).** The resource column of BPI
2019 is not a list of people. On the full log (1,595,923 events), `user_###`
= 607 values and 65.2% of events (people), `batch_##` = 20 values and 9.8%
(batch jobs), and `NONE` = 1 value and 25.0% (no user recorded, mostly
vendor-side steps). After cleaning, the shares are 65.2 / 9.8 / 25.0% of
1,593,429 events. Resources are therefore classified first, by preset rules
(regular expressions or exact values; empty values are always "unassigned";
a value matched by two types is a configuration error). Workload (RS) ranks
people only. A handover (HO) is a step between two different people, compared
with the same person continuing. Steps with a batch job or no recorded user on
either side are reported separately as system/vendor transitions (ST), with
their direction (e.g. "no user recorded -> person"). Each type gets a summary
item (RT) with its share of events and top activities. The data-quality
report carries the same breakdown, recomputed after cleaning. On the 800-case
sample, segment "3-way match, invoice before GR", the old definition ranked
`NONE` as the busiest "resource" (RS-01, 17.2% of events) and two batch
accounts among the top 15. Its HO-00 compared 4.3 days (resource changes)
with 10.6 h (same resource), and three of its ten handover edges were vendor
steps ("Vendor creates invoice" -> ...). Under the new definition, HO-00
compares 11.9 days (two different people, 397 steps) with 11.2 h (same
person, 54 steps). 473 steps involve a batch job or no user (median
2.4 days). All non-resource evidence is unchanged (171 items identical). The
evidence digest the model reads grows by 17-33% per segment (+2.3k to +5.8k
characters, about +0.7k to +1.8k input tokens per call, estimated at 3.2
characters per token from the logged Gemini calls). Every `evidence.json`
and `manifest.json` now carries `evidence_schema_version` (2). Files without
it are schema 1, and the UI flags such runs.

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

**Cancelled items (2026-11-18, evidence schema 3).** A completed case can end
in "Delete Purchase Order Item", and a deleted item legitimately has no invoice
or goods receipt (domain fact D-14). For the must-occur rules R-03 and R-04
those cases are now left out of the completed-cases figures (configurable per
rule, `exclude_completed_ending_in`; the all-cases figures are unchanged). Full
cleaned log, violations among completed cases before -> after (cases excluded):

| Segment | Rule | Before | After | Excluded |
|---|---|---|---|---|
| 2-way match | R-03 | 1 / 169 (0.59%) | 0 / 166 (0.00%) | 3 |
| 3-way, invoice after GR | R-03 | 303 / 9,544 (3.17%) | 2 / 9,242 (0.02%) | 302 |
| 3-way, invoice after GR | R-04 | 292 / 9,544 (3.06%) | 1 / 9,242 (0.01%) | 302 |
| 3-way, invoice before GR | R-03 | 7,760 / 179,259 (4.33%) | 375 / 171,845 (0.22%) | 7,414 |
| 3-way, invoice before GR | R-04 | 7,866 / 179,259 (4.39%) | 516 / 171,845 (0.30%) | 7,414 |

Almost every "completed case without invoice / goods receipt" was a deleted
item. Consequence for the evaluation: with the answer-key rule ">1% of
completed cases", R-03 and R-04 no longer enter the answer key in any segment
(before the change they would have in both 3-way segments).

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
- Reproducibility: the model id requested and the model names the provider
  served, prompt version (`p5-v1` since 2026-11-18; `p4-v1` before), full prompts, raw outputs, stop/finish
  reason, token usage (reasoning tokens separately), cost (estimated at list
  price), library versions and hardware are logged per run; a snapshot
  (`review_r<N>.json`) is written when a round reaches review, because
  `summary.json` only exists once a run is finalised. Hosted models are not
  bit-reproducible, so logging is the substitute: Claude Opus 5.5 rejects
  sampling parameters, and Google documents temperature/top_p/top_k as
  "deprecated and ignored" for Gemini 3.6 Flash (checked 2026-11-13), so they
  are no longer sent (user decision 2026-11-16). The seed (42) is still sent;
  its effect is UNVERIFIED and every run log says so.
- Evaluation model: `google_genai:gemini-3.6-flash`, requested directly (user
  decision 2026-11-16). Earlier runs requested `gemini-3.5-flash` and were
  served `gemini-3.6-flash`; both names are in their logs.
- Real runs before the evaluation (development, Gemini, prompt `p4-v1`):
  2-way match reached review with 2 calls and no retries (15,110 input /
  6,058 output tokens); 3-way match, invoice before GR (the largest segment)
  reached review with 4 calls, one retry each in diagnose (3 unsupported
  numbers) and redesign (1 edit not applicable to the model), 39,920 input /
  15,623 output tokens, 2 min 46 s. Agent tests use scripted fake models;
  notebooks and dry runs use the scripted model in `agent/dry_run.py`
  (`scripted:dry-run`, zero API calls, never offered in the app).

## 5. Redesign and validation (Phase 4)

**Edits act on a process tree, not on BPMN.** The inductive miner produces a
block-structured process tree; every process tree maps to a sound workflow
net, so editing the tree keeps every to-be model sound by construction.
Seven typed operations: remove, add, add check, reorder, parallelize, merge,
automate. Parallelize and merge need adjacent steps of one sequence; anything
else is refused with a reason, and the agent is asked to revise.

**Protected activities** (e.g. "Clear Invoice", a financial control) may not
be removed, moved, merged or parallelised, but may be automated (user decision).
Since evaluation Phase 3 (2026-11-18) they are configured **per segment** in the
dataset preset: in BPI 2019 "Clear Invoice" is protected everywhere and "Record
Goods Receipt" in both 3-way segments and in Consignment (not in 2-way match,
which has no goods-receipt control). The preset's list is a floor: the analyst
can protect more activities for a run but cannot unprotect a preset one. It is
enforced twice in code, by the validator (the model must retry) and by
`apply_operation` (an edit that reaches it is refused), not only in the prompt.
The free-text constraint "Goods receipt recording cannot be removed for
physical-goods categories" was removed, because the per-segment list now says
exactly where it applies.

**Unverifiable controls.** BPI 2019 was published with its approval steps
removed, so ProcessPilot cannot check what a proposal does to approvals or to
segregation of duties. A proposal is flagged, not blocked, when one of its
operations names an approval activity in any role, including the `after`
anchor of an added step, or introduces a new activity whose name matches. In
BPI 2019 an approval activity is any name containing "Approv"
(case-insensitive), plus "Release Purchase Order" and "Release Purchase
Requisition". On the full raw log that is four activities: Change Approval for
Purchase Order (7,541 events, 4,377 cases), SRM: Awaiting Approval
(1,628 / 1,440), Release Purchase Order (1,610 / 961) and Release Purchase
Requisition (467 / 467). The flag is stored with the proposal's validation
result, appears in the review payload, `review_r<N>.json`, the run log and
`summary.json`, and is shown as a warning on the proposal in the UI. It is
not sent to the model.

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

**Three layers, one direction.** The web app (what the analyst sees; React
since 2026-10-27, Streamlit before, see 5c) talks only to a FastAPI backend over
HTTP; the backend calls the same pipeline functions
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

**BPMN display offline.** bpmn-js (navigated viewer; 18.31.1 since the React
app, bundled into the build) needs no network, so the app needs none except
for the LLM call. As-is and to-be models are shown stacked full width; because
element ids are stable, the same task has the same id in both.

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

## 5b. User interface design, first iteration (Streamlit; superseded by 5c)

> Superseded on 2026-10-27: this Streamlit front end and its visual design
> (petrol teal, IBM Plex, wait ladder widgets) were removed. Kept as design
> history. What carried over: the analyst-first framing, the per-path wait
> computation (`mining/paths.py` and its API route, now drawn as the
> itinerary), the no-default decision control, and stacked BPMN models.

**Users first, demo second** (user decision). The UI is designed for an SME
business analyst in long desktop sessions; the thesis demo benefits from that.
Streamlit was kept at that point (user decision) so the API client and tests stayed valid; the
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
(then tested in `tests/test_app_ui.py`, removed with Streamlit; the React
form keeps the behaviour, see 5c).

**Accessibility.** WCAG AA contrast checked for every token in both modes. One
trade-off found: Streamlit always puts white labels on primary buttons, so dark
mode's primary teal must stay deep (#1A7F88, 4.73:1 with white), which is under
4.5:1 as text on the dark ground; selected segment and tab labels therefore use
the ink colour, and a brighter teal (#3CB4BE) carries data in dark mode.

## 5c. Web front end (replaces Streamlit, 2026-10-27)

**Why Streamlit was replaced** (user decision). The Streamlit UI looked dated
to the user, and its limits were structural rather than cosmetic: every
interaction re-runs the whole page script; layout is limited to Streamlit's
own containers; the theme could not be switched from code (the Light/Dark
switch had to write Streamlit's private browser-storage key and reload);
primary buttons always carry white labels, which forced a too-dark accent in
dark mode; and custom visuals needed a component protocol per widget. The
backend already exposed everything over HTTP (5a), so only the presentation
layer changed: the pipeline, the API contract, the agent and the decision log
are untouched, and all API tests still pass unchanged.

**Architecture.** React 19 + TypeScript, built with Vite (`web/`). The build is
written into the Python package (`src/processpilot/api/static`) and committed,
and the FastAPI process serves it from the same origin as the API: one
process, one port, no CORS, and the app runs with Python alone (Node is needed
only to change the UI). Client routes (`/data`, `/evidence/<run>/<segment>`,
`/redesign/<run>/<segment>?agent=<id>`) are answered with the app shell, so
every view has an address that can be bookmarked or shared. Server state is
fetched with TanStack Query; background jobs (profile, validate, mine, agent
start/resume) are polled at `poll_seconds` from `configs/app.yaml` (served by
`GET /settings/ui`). The browser stores only conveniences (appearance, last
run and segment, the analyst's name); nothing the method depends on.

**Same method, same guarantees.** The UI computes no metric and never sees
raw events: every number comes from the API (evidence, path waits, ESTIMATE),
and the itinerary only arranges the API's per-step waits (`web/src/journey.ts`).
The Data screen keeps the method's order as four coupons that open one after
the other (event log, column mapping, data quality, clean and mine); nothing
is removed unless the analyst ticks it. Each evidence ID is shown wherever it
is cited, with its code-written summary. The decision control has no default,
submitting without a choice keeps the comment, each review round starts with
an empty form, and the decision is logged before the agent resumes.

**Design process.** The visual direction was chosen in a structured design
round (Impeccable skill): candidate visual worlds were derived from the
audience's own world, a random roll assigned one, catalogue challengers were
weighed against it, and the user chose from first-screen sketches. The user
re-rolled once and then picked "Ticket Wallet", with the palette refined at
the user's request. Result: the method is a coupon book whose legs open in
order; a case path is an itinerary whose layovers are the waits; each proposal
is a coupon whose ESTIMATE is torn off on a pale "carbon copy" stub, labelled
ESTIMATE; decisions stamp the run, and rejected rounds stay in the log, voided.
Ink navy, carbon-copy cobalt on data, one signal red for stamps and bad news;
Archivo in two widths; light and dark modes. Recorded in `DESIGN.md`.

**Verification.** Python: `tests/test_web_app.py` (shell on every client
route, assets, API routes unaffected, settings endpoint, an unbuilt checkout
explains how to build) plus the unchanged API suite. TypeScript: type check
and vitest unit tests for the itinerary logic and number formatting. In a real
browser (Playwright, Chromium): upload of the sample CSV -> profiling ->
mapping -> quality report -> mining -> evidence; and the agent flow:
started from the UI -> submit without a choice (comment kept) -> reject
with comment -> revised round with an empty form -> approve -> two log entries
(one voided, one approved). Screens checked at 1440, 768 and 390 px in light
and dark; automated design checks (taste inspector, Impeccable detector) pass
with 0 failures. The browser walk-throughs were run by hand with Playwright;
they are not part of the automated test suite.

## 5d. BPMN drawing (2026-11-02)

**Problem.** Rendering each process-tree operator as its own split/join pair is
correct but wasteful. In the largest BPI 2019 model, 33 tasks sat among 94
gateways, and 115 of 180 flows ran from one gateway straight to another. Most
of those gateways decided nothing, which makes the model harder for an analyst
to read even though it is formally right.

**Method.** Three changes to the rendering only; the process tree, evidence and
estimates are unchanged.

1. *Gateway merging.* A flow between two gateways of the same type is removed
   when nothing else can happen at that point: the source has one exit, the
   target has one entrance, or two exclusive gateways lead straight back to each
   other. In Petri-net terms an exclusive gateway is a place and a parallel
   gateway a transition, so such a flow is a silent step. A skip from a real
   split to a real join is a routing decision and stays, as do boundaries
   between exclusive and parallel gateways.
2. *Loop marker.* A single task repeated on its own is drawn with BPMN's standard
   loop marker instead of two gateways and a back-arrow.
3. *Layout.* Graphviz is called directly with BPMN's shapes and fixed sizes,
   replacing pm4py's layouter, whose flows stopped short of diamond outlines.

**Result** (as-is models, run `20261102T160209Z`):

| Segment | Tasks | Gateways | Flows | Drawing width |
| --- | --- | --- | --- | --- |
| 2-way match | 8 | 16 -> 11 (-31%) | 34 -> 27 | 2305 -> 1888 |
| 3-way, invoice after GR | 33 | 94 -> 47 (-50%) | 180 -> 117 | 9591 -> 6104 |
| 3-way, invoice before GR | 39 | 58 -> 31 (-47%) | 149 -> 114 | 5705 -> 4174 |
| Consignment | 15 | 16 -> 7 (-56%) | 47 -> 36 | 2815 -> 1872 |

Edges cross nowhere in these four drawings and none passes through a task.
Polyline routing was chosen over orthogonal after measuring both on these models:
orthogonal routing looks tidier but added up to 14 crossings at the same spacing
and stacked several flows on one track, so a reader could not follow them.

**Verification.** A shorter drawing is only acceptable if the process is the
same, so the written BPMN file is executed as a token game and its traces are
compared with the traces the process tree allows by definition. They were equal
on hand-picked shapes and 150 random trees, and on the four real models up to
5, 3, 2 and 5 activities (2-way, after GR, before GR, consignment). Deliberately
breaking the merge rule makes these tests fail. Woflan confirms soundness on the
two models within its size gate (21 and 30 Petri-net transitions); the two larger
ones (117 and 85) are skipped, so they rest on construction plus the structural
check, as before.

## 5e. Evaluation set-up (Phase 4, 2026-11-18)

**Frozen mining run.** All evaluation runs use one mining run, `20261118T011500Z`
(preset `bpi2019`, mined at commit `24303fd` with the preset's suggested cleaning:
1,593,429 events, 251,654 cases, evidence schema 3, 344 evidence items, 82 s). Its 28
files that the agent and the scorers read are hashed in the campaign manifest, together
with the source XES (728,558,522 bytes, SHA-256 recorded) and the preset's content hash.
The app refuses agent runs on it, so only the campaign runner writes to it.

**Draft and seal.** The campaign (`eval-2026-11-v1`) was drafted before any LLM run,
which fixes the answer key. It is sealed only after the runner and the baseline are
committed. Sealing re-checks every hash and records the code commit, prompt version,
model, settings, library versions and hardware, and the runner refuses to continue if
any of them changes.

**Answer key (RQ1 reference list).** By the fixed rule (top-3 bottlenecks by impact,
plus rules violated in more than 1% of completed cases), the key holds **only
bottlenecks in all four segments**:

| Segment | BN-01 | BN-02 | BN-03 |
|---|---|---|---|
| 2-way match | Change Approval for PO -> Vendor creates invoice (29.3 d, 19.9%) | Vendor creates invoice -> Create PO Item (11.5 d, 40.8%) | Clear Invoice -> Change Approval for PO (35.4 d, 12.1%) |
| 3-way, invoice after GR | Record Invoice Receipt -> Clear Invoice (21.1 d, 51.7%) | Record Goods Receipt -> Record Invoice Receipt (19.0 d, 35.5%) | Vendor creates invoice -> Record Invoice Receipt (16.4 d, 32.7%) |
| 3-way, invoice before GR | Record Invoice Receipt -> Clear Invoice (37.2 d, 56.4%) | Record Goods Receipt -> Record Invoice Receipt (12.2 d, 48.0%) | Remove Payment Block -> Clear Invoice (27.5 d, 21.4%) |
| Consignment | Create PO Item -> Record Goods Receipt (19.9 d, 82.3%) | Receive Order Confirmation -> Record Goods Receipt (16.5 d, 4.4%) | Create PO Item -> Change Quantity (7.9 d, 5.9%) |

(median wait, share of cases.) Completed-case violation rates of every rule (none above
1%): 2-way R-02 0/168, R-03 0/166; after GR R-01 0/9,241, R-02 4/9,241 (0.04%), R-03
2/9,242 (0.02%), R-04 1/9,242 (0.01%); before GR R-02 6/171,498 (0.00%), R-03
375/171,845 (0.22%), R-04 516/171,845 (0.30%); Consignment has no applicable rule. On
all cases, R-03 and R-04 are much higher (e.g. R-03 after GR 26.7%), almost entirely open
or deleted items. A diagnosis that reports these as compliance problems is therefore not
supported by the completed-case evidence; Phase 7 measures this ("unsupported compliance
claims", user decision 2026-11-18).

**Rate limiting and quota accounting (Phase 5, 2026-11-18).** The Gemini quota (5
requests per minute, 20 per day per Google Cloud project) is enforced in code, not by
hand. Every model request, including validation retries, passes one limiter: at least
20 s between requests, at most 17 requests per quota day (the calendar day in Los Angeles,
whose midnight is when Google resets the quota), and a run starts only if the day's
remaining budget covers its worst case (6 requests for ProcessPilot, 3 for the
baseline). Every request is written to one shared ledger and counts whatever its
outcome, including requests refused with a 429 (whether Google counts those is
UNVERIFIED, so counting them is the conservative choice). The Gemini SDK's own retries
are off (its default would send up to 6 hidden attempts). A per-minute 429, a server
error or a timeout is waited out and repeated; these **transport retries** are logged
apart from **validation retries**, which are a result of the model's output. A per-day
429 stops the run; the run is later repeated as a new attempt and the cut-short attempt
is kept and labelled. Runs are executed strictly one after another in a fixed,
interleaved order (repetition 1 of every segment and condition first), so a problem on
a late day affects all segments and conditions alike. A run whose output still fails
validation after the retries is a result and is never repeated. Runs stop at the human
review step; no decision is taken in the evaluation.

## 6. Limitations to state

- Single-timestamp log: waiting and processing time cannot be separated.
- The noise threshold removes rare activities from the model; they cannot be
  redesigned.
- The estimate ignores queueing, capacity and resource contention;
  removal effects are upper bounds; the automation % is an assumption.
- Rework extra time is association, not causation.
- Resource types come from naming patterns in the log (`user_`, `batch_`,
  `NONE`), not from an HR or system register. A batch account run on a
  person's behalf, or a shared login, would be misclassified. "No user
  recorded" also hides who did vendor-side steps, so their waits cannot be
  attributed to anyone.
- Development runs mined before 2026-11-17 (evidence schema 1) count batch
  jobs and `NONE` as people in RS/HO; figures from them must not be quoted for
  resources.
- Conformance rules are hand-written domain rules; a wrong rule produces false
  findings (R-02 lesson).
- Approval and segregation-of-duties effects of a proposal cannot be checked:
  the published log has no approval steps. Proposals touching the four
  approval/release activities are only flagged, and the flag depends on a
  name pattern, so an approval step with an unusual name would be missed.
- Open cases at extraction bias "missing activity" counts; partly handled by
  completed-case reporting.
- No reference model for BPI 2019, so there are no model-deviation findings.
- Woflan does not scale to large models; soundness there rests on construction
  plus the structural check.
- LLM outputs are not bit-reproducible (Opus 5.5 rejects sampling parameters;
  Gemini 3.6 Flash ignores temperature, and whether it honours the seed is
  UNVERIFIED).
- pm4py is AGPL v3: ProcessPilot cannot be distributed as closed source.
- The app is a single-user local tool: no authentication, the analyst name in
  the decision log is self-reported, and background jobs run one at a time in
  the API process.
- The analyst approves or rejects the set of 2-3 proposals as a whole; there
  is no per-proposal choice in the graph.
- The itinerary shows one variant at a time; cases on rare paths (outside the
  top-10 variants) are only in the tables.
- The web app's end-to-end flows were verified in a browser by hand
  (Playwright); the automated suite covers the API, the serving of the app and
  the UI's logic, not clicks through the screens.
- The built front end is committed next to its source; a UI change that is not
  rebuilt leaves the served app behind the source.
- Behaviour-preservation of the compact BPMN drawing is shown by bounded trace
  comparison (2 to 5 activities on the real models) and by tests on small trees,
  not by a formal proof. The two large models are still dense: their remaining
  gateways are real skips and exclusive/parallel boundaries.
- A compact drawing merges a join directly followed by a split into one gateway.
  This is valid BPMN, but some modelling style guides prefer them separate.

## 7. Tools and environment

Python 3.11.17; pm4py 2.7.23.11 (AGPL v3); pandas 3.0.6; pydantic 2.14.1;
LangGraph 1.2.15; langchain 1.4.6; langchain-anthropic 1.7.6;
langchain-google-genai 4.4.2 (google-genai 2.34.0); psutil 7.2.2;
matplotlib 3.11.2; openpyxl 3.1.5; PuLP 3.3.2
(capped below 4.0: pm4py uses APIs that PuLP 4.0 removes); Graphviz 16.1.0
(needed for BPMN layout and pictures). Exact set in `requirements.lock`.
FastAPI 0.162.0, uvicorn 0.54.0. Web app: React 19.3.1, TypeScript 7.0.2,
Vite 8.3.5, React Router 7.18.4, TanStack Query 5.105.0, bpmn-js 18.31.1,
lucide-react 1.62.0, Archivo (OFL, via @fontsource-variable/archivo 5.3.0),
vitest 5.0.5; built with Node 24.11.0 (exact set in `web/package-lock.json`).
Streamlit 1.66.0 was used until 2026-10-27 and has been uninstalled.
Hardware: Windows 11 (build 26200), 11th Gen Intel Core i5-1135G7 @ 2.40 GHz
(4 cores, 8 logical), 7.7 GB RAM; since 2026-11-16 every mining manifest and
agent run log records the hardware it ran on.
Runtime (measured by the app's job log on that laptop): mining all four BPI
segments took 92 to 184 s over 9 runs between 2026-10-26 and 2026-11-11
(92, 94, 167, 169, 166, 172, 93, 95, 184 s in date order; median 166 s). The
spread has no recorded cause (other load on the laptop is not logged); the
800-case sample took 5 s. A full agent run to review took 75 s (2-way match, 2
calls) and 173 s (invoice before GR, 4 calls), almost all of it model latency.
Tests: 542 automated Python tests (2026-11-17; plus 9 TypeScript unit tests for
the web app's logic) on
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
| Re-plan | Keep existing architecture; add FastAPI and SQLite later |
| Re-plan | Raw BPI zip stays in the repo for the supervisor |
| Phase 4 | Protected activities may be automated |
| Phase 4 | Automation assumes 50% wait reduction; an added step costs the median wait |
| App phase | SQLite holds an index and the decision log, not a copy of runs |
| App phase | Data enters via a configured preset or an uploaded file with analyst-confirmed mapping |
| App phase | bpmn-js is vendored so the app works offline |
| UI phase | Analysts are the primary user; the demo is secondary |
| UI phase | Stay on Streamlit; light and dark mode with a visible sidebar switch (superseded by "Front end" rows) |
| UI phase | Petrol teal accent, IBM Plex Sans (vendored, offline) (superseded by "Front end" rows) |
| UI phase | Signature element: value-stream wait ladder (per-path mean waits) (superseded by "Front end" rows) |
| UI phase | BPMN diagrams follow the dark theme; the bpmn.io watermark stays visible |
| Front end | Replace Streamlit entirely (its limits made the design look dated) |
| Front end | React + TypeScript + Vite; build committed and served by FastAPI (no Node to run) |
| Front end | New visual identity; nothing kept from the Streamlit look; Linear as quality bar |
| Front end | Visual world "Ticket Wallet" chosen after one re-roll; palette refined (no purple) |
| 2026-11-02 | The agent runs only on a real LLM; there is no rule-based stand-in or baseline model |
| 2026-11-02 | BPMN drawings must be compact: no gateways that decide nothing |
| 2026-11-16 | Gemini: no temperature/top_p/top_k sent (ignored by the model); seed 42 kept, effect labelled UNVERIFIED |
| 2026-11-16 | Evaluation model requested directly as `google_genai:gemini-3.6-flash` |
| 2026-11-16 | A scripted dry-run model exists for notebooks and evaluation dry runs only (zero API calls); the app still runs only on a real LLM |
| 2026-11-16 | Second-dataset notebook (04) dropped from the thesis and deleted |
| 2026-11-17 | Gemini `thinking_level` pinned to "medium" (Google's current default), logged per run |
| 2026-11-17 | Resource types: workload ranks people only; a handover is between two different people; batch / no-user steps are system/vendor transitions (ST); empty values count as unassigned |
| 2026-11-17 | HO shares stay over all transitions of the edge, with the ST share beside them |
| 2026-11-17 | `evidence_schema_version` in every evidence.json and manifest.json (2 = resource types), shown in the UI |
| 2026-11-16 | Record Goods Receipt protected in both 3-way segments and Consignment; Clear Invoice everywhere |
| 2026-11-18 | Preset-protected activities are a floor the analyst cannot remove; shown as locked chips |
| 2026-11-18 | Unverifiable-controls flag: approval activities = "Approv" pattern + Release PO / Release PR; an operation touches an activity it names in any role (incl. `after` anchors) or a new name that matches; flag never blocks |
| 2026-11-18 | Prompt `p5-v1`: digest heading names RS / HO / RT / ST |
| 2026-11-18 | Must-occur rules (R-03, R-04): completed cases ending in Delete Purchase Order Item excluded from the completed share; evidence schema 3 |
| 2026-11-18 | Answer-key rule kept as fixed; if no rule passes 1%, the key holds bottlenecks only, stated explicitly with every rule's completed-case rate |
| 2026-11-18 | Campaign drafted before the LLM runs, sealed after Phases 5-6; sealing re-checks the frozen files and the answer key |
| 2026-11-18 | The app refuses agent runs on the evaluation mining run |
| 2026-11-18 | Phase 7 measures compliance problems claimed by a diagnosis but not supported by the evidence |
| 2026-11-18 | Gemini limits enforced in code: 20 s gap, 17 requests per Pacific day, worst-case check before a run (6 / 3), one shared ledger for app and runner |
| 2026-11-18 | Every request sent counts against the budget, including 429s; SDK retries off; 5xx/timeouts handled like per-minute 429s (max 3 retries) |
| 2026-11-18 | Campaign runs, logs and checkpoints live in the campaign folder; a stopped run is repeated as a new attempt and the cut-short attempt is kept |
