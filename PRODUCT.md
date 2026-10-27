# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Business analysts at small and medium-sized enterprises, not data scientists. They work at a
desktop in long working sessions: load an event log, check its quality, read the mined evidence,
and decide on redesign proposals. The thesis supervisor and examiners see the same app in a demo;
the design serves the analyst first and the demo benefits from that.

## Product Purpose

ProcessPilot is an agentic AI consultant for the Analysis and Redesign phases of the BPM
lifecycle. It ingests event data (file upload or preset), mines the as-is process, identifies
bottlenecks with evidence, and proposes redesigned to-be processes (BPMN) with a rationale and an
estimated impact. It never executes changes: a human analyst approves or rejects every proposal.
Success means an analyst without data-science training can go from an event log to a reviewed,
evidence-backed redesign decision and trust why each proposal was made.

## Positioning

Deterministic computation, LLM reasoning: every number is computed by code (PM4Py/pandas), and the
LLM only interprets precomputed evidence. Every claim cites a stable evidence ID (e.g.
`BN-01-2-way-match`); impact figures are labelled ESTIMATE; the analyst has the last word.

## Operating Context

- Flow: Data (source, column mapping, data-quality report, cleaning choice, mine) -> Evidence (per
  segment: KPIs, bottlenecks, variants, rework, resources, conformance, as-is BPMN) -> Redesign
  (agent run, diagnosis with cited evidence, 2-3 proposals, as-is vs to-be BPMN, approve or reject
  with a comment, which resumes the paused agent).
- Evaluation dataset: BPI Challenge 2019 (purchase-to-pay), segmented by item category; a committed
  800-case sample for demos and tests.
- Runs locally on Windows; everything offline except the LLM call (a rule-based mock exists).

## Capabilities and Constraints

- Web front end (React + TypeScript + Vite in `web/`), built into `src/processpilot/api/static`
  and served by the FastAPI backend from the same origin; it calls the HTTP API only, with no
  direct data access. It replaced the Streamlit front end on 2026-10-27 (user decision).
- Must work offline: fonts, scripts and the bpmn-js viewer are bundled into the build; no CDN at
  runtime. Node is needed only to change the UI, never to run it.
- The bpmn.io watermark in the BPMN viewer must stay visible (bpmn-js licence).
- Light and dark mode, switchable from a visible control; defaults to the OS setting.
- Dataset-agnostic: nothing BPI-specific in the UI outside data it receives.
- Approve/reject applies to the whole set of proposals of a round (graph design).

## Brand Commitments

- Name: ProcessPilot. No existing logo; a simple mark is drawn for it (replaceable).
- Visual world: "Ticket Wallet" (user choice in the 2026-10-27 direction round): ink navy coupon
  heads, carbon-copy cobalt for data and the ESTIMATE, one warm signal red for stamps; palette
  refined from the dealt card at the user's request (no purple). The earlier petrol teal + IBM
  Plex direction was discarded with Streamlit.
- Typeface: Archivo (variable, two widths); no monospace.
- Voice: plain, factual, evidence-first; never hype. Estimates are always called estimates.

## Evidence on Hand

Real mined runs under `outputs/runs/` (BPI 2019 full and sample) and the mock model provide real
evidence, proposals and BPMN for every screen. No customers, testimonials or usage statistics
exist; none may be invented.

## Product Principles

1. The evidence leads: every finding shows its ID, its numbers and where they come from.
2. The human decides: the decision control is always clear, never pre-filled, never hidden.
3. The next step is obvious: each page shows where the analyst is in the method and what to do next.
4. Honest numbers: estimates labelled, missing values shown as missing, zeros as zeros.

## Accessibility & Inclusion

WCAG 2.1 AA contrast in both themes; keyboard-operable controls with visible focus.
