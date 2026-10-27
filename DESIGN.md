---
name: ProcessPilot
description: Evidence-grounded process redesign for business analysts; a human decides.
colors:
  petrol-teal: "#0B6E77"
  petrol-teal-ink: "#0A5F67"
  petrol-teal-deep-dark: "#1A7F88"
  petrol-teal-bright-dark: "#3CB4BE"
  petrol-teal-link-dark: "#5CC6CF"
  canvas: "#FFFFFF"
  field: "#F3F5F5"
  sidebar-ground: "#F6F7F7"
  ink: "#141A1B"
  control-border: "#D5DBDB"
  table-rule: "#E3E7E7"
  id-tint: "#EDF3F3"
  canvas-dark: "#111415"
  field-dark: "#1B2022"
  sidebar-ground-dark: "#0D1011"
  ink-dark: "#E7ECEC"
  control-border-dark: "#2F3739"
  positive: "#1F7A4D"
  negative: "#B42318"
  positive-dark: "#4CC38A"
  negative-dark: "#F0756A"
typography:
  headline:
    fontFamily: "IBM Plex Sans, sans-serif"
    fontSize: "26px"
    fontWeight: 600
    lineHeight: 1.2
  title:
    fontFamily: "IBM Plex Sans, sans-serif"
    fontSize: "19px"
    fontWeight: 600
  section:
    fontFamily: "IBM Plex Sans, sans-serif"
    fontSize: "16px"
    fontWeight: 600
  figure:
    fontFamily: "IBM Plex Sans, sans-serif"
    fontSize: "24px"
    fontWeight: 500
    lineHeight: 1.25
    letterSpacing: "-0.01em"
    fontFeature: "tnum"
  body:
    fontFamily: "IBM Plex Sans, sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.6
  label:
    fontFamily: "IBM Plex Sans, sans-serif"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.4
  evidence-id:
    fontFamily: "IBM Plex Mono, monospace"
    fontSize: "12px"
    fontWeight: 400
rounded:
  bar: "2px"
  control: "6px"
  panel: "8px"
  pill: "999px"
spacing:
  cell-y: "14px"
  cell-x: "18px"
  section: "1.5rem"
  phone-gutter: "20px"
components:
  button-primary:
    backgroundColor: "{colors.petrol-teal}"
    textColor: "{colors.canvas}"
    rounded: "{rounded.control}"
  button-primary-dark:
    backgroundColor: "{colors.petrol-teal-deep-dark}"
    textColor: "{colors.canvas}"
    rounded: "{rounded.control}"
  kpi-strip:
    backgroundColor: "{colors.canvas}"
    typography: "{typography.figure}"
    rounded: "{rounded.panel}"
    padding: "14px 18px"
  evidence-id:
    backgroundColor: "{colors.id-tint}"
    textColor: "{colors.petrol-teal-ink}"
    typography: "{typography.evidence-id}"
  ladder-rung:
    backgroundColor: "{colors.petrol-teal}"
    rounded: "{rounded.bar}"
    height: "12px"
  ladder-rung-dark:
    backgroundColor: "{colors.petrol-teal-bright-dark}"
    rounded: "{rounded.bar}"
    height: "12px"
---

# Design System: ProcessPilot

## Overview

**Creative North Star: "The Value-Stream Worksheet"**

ProcessPilot reads like a lean value-stream worksheet put on a screen: the analyst's question is
"where do the days go?", and the answer is drawn, not described. The signature is the wait ladder,
one path's steps top to bottom with each step's mean wait as a bar starting where the previous one
ended, so the ladder ends at the path's mean throughput and the longest rung is seen at once.
Everything else is quiet so that the data can speak.

The interface is a working tool for long desktop sessions, not a landing page: noun titles at 26px,
a one-line finding under every title, a divided KPI strip instead of tiles, ruled tables, flat
surfaces. Teal appears on data (ladder rungs, share bars, evidence IDs) and on the one primary
action per form; chrome stays neutral. Light and dark are equals, switched from the sidebar, built
from Streamlit's own theme so every native widget follows.

**Key Characteristics:**
- One hue family, petrol teal, on data and the primary action only.
- IBM Plex Sans for everything; IBM Plex Mono for evidence IDs and nothing else.
- Flat at rest: 1px borders and tone steps, no shadows on resting surfaces.
- Every claim sits beside the evidence it cites; estimates are always labelled ESTIMATE.
- True neutral darks; no navy, no slate.

## Colors

A restrained palette: tinted neutrals plus one petrol teal that deepens on light grounds and
brightens on dark ones.

### Primary
- **Petrol Teal** (#0B6E77): light mode's primary buttons, focus, selected segment border, ladder
  rungs and share bars. White labels on it reach 5.98:1.
- **Petrol Teal Ink** (#0A5F67): links and evidence-ID text on light grounds.
- **Deep Petrol** (#1A7F88): dark mode's primary buttons and focus. Deep enough for white labels
  (4.73:1), which is why it is never used as text on the dark ground.
- **Bright Petrol** (#3CB4BE): dark mode's data hue (ladder rungs, the logo mark).
- **Petrol Link** (#5CC6CF): dark mode's links and evidence-ID text.

### Neutral
- **Canvas** (#FFFFFF / dark #111415): the working surface.
- **Field** (#F3F5F5 / dark #1B2022): input and select fills.
- **Sidebar Ground** (#F6F7F7 / dark #0D1011): one tone step off the canvas, never saturated.
- **Ink** (#141A1B / dark #E7ECEC): all running text. Secondary text is ink mixed 70% into the
  ground (about 6:1 light, 8:1 dark), never a lighter grey.
- **Control Border** (#D5DBDB / dark #2F3739) and **Table Rule** (#E3E7E7): 1px structure.
- **ID Tint** (#EDF3F3): the pale fill behind inline evidence IDs.

### Status
- **Positive** (#1F7A4D / dark #4CC38A): an estimate that shortens throughput, sound models,
  approved runs. **Negative** (#B42318 / dark #F0756A): errors and estimates that lengthen it.
  Orange is for "waiting for your decision".

### Named Rules
**The Data-Not-Chrome Rule.** Teal marks data and the one primary action of a form. Navigation,
tabs and selected segments keep ink labels; their selection shows as a tint, a border or an
underline.

**The Two-Teal Rule.** Dark mode uses two teals: a deep one under white labels, a bright one for
data and text. Never set the deep one as text on the dark ground (under 4.5:1).

## Typography

**Body Font:** IBM Plex Sans (with sans-serif), vendored as woff2 400/500/600/700.
**Label/Mono Font:** IBM Plex Mono (with monospace), 400/500, for evidence IDs only.

**Character:** a sober business-technical grotesk with real tabular figures, so waits and shares
line up digit for digit; the mono marks the things an analyst can cite.

### Hierarchy
- **Headline** (600, 26px, 1.2): the page's one h1, a noun plus the segment ("Evidence · 2-way match").
- **Title** (600, 19px): sections ("Where the days go", "Diagnosis").
- **Section** (600, 16px): proposal titles and sub-blocks.
- **Figure** (500, 24px, tabular, -0.01em): KPI values; units follow at 14px in secondary ink.
- **Body** (400, 15px, 1.6): text and table cells.
- **Label** (400, 13px; 12px for meta): KPI labels, captions, axis ticks. Sentence case, never tracked caps.

### Named Rules
**The Cite-In-Mono Rule.** Monospace appears only on evidence IDs (`BN-01-…`, `VA-02-…`) and
run ids; numbers use Plex Sans tabular figures.

## Layout

Streamlit wide layout: a 250px sidebar (mark, three method pages, the current run and segment,
the appearance switch, model status) and a fluid canvas with equal side margins (75px at 1440,
20px below 900px). Each page opens with the h1, one finding line, then the scope row (segment as a
segmented control, wide; run as a select, narrow). Sections are 1.5rem apart, more than any gap
inside them. Below 720px the KPI strip becomes a two-column grid with an odd last cell spanning,
and the ladder folds to name-over-bar rows with the value on the right.

## Elevation & Depth

Flat. Resting surfaces carry no shadow; depth is a 1px border or one tone step. The only lifted
element is the selected option in the appearance switch (a 1px ring), and Streamlit's own menus
and popovers float with their native shadows.

### Named Rules
**The Flat-At-Rest Rule.** A border or a tone step, never both, and never a shadow on a panel.

## Shapes

Tight corners: 6px on controls and buttons, 8px on panels and the KPI strip, 2px on ladder rungs
and comparison bars, full pills only on status badges and ladder tags. Lines are 1px; dashed lines
appear only in chart grids and on ESTIMATE bars.

## Components

### Buttons
- **Shape:** gently squared (6px).
- **Primary:** petrol teal fill, white label, one per form ("Mine this log", "Run the agent",
  "Submit decision"); secondary buttons are outlined.

### Segmented controls and tabs
- **Style:** Streamlit native. Selected segment: teal tint and border with an ink label at 500.
  Active tab: teal underline, ink label at 500.

### KPI strip
- One bordered container (8px) split by 1px rules into equal cells; label above value; values
  bottom-aligned so a wrapped label never lowers its figure. No icons, tiles or sparklines.

### Wait ladder (signature)
- Rows of 40px: activity name (with its BN evidence ID beneath when the transition is a ranked
  bottleneck), a track where each 12px rung starts at the previous rung's end with a 1px riser,
  and the mean wait over its median at the right. Axis ticks are round and end above the total.
  Proposal marks: hatched rung = automated, dashed outline = removed, dashed teal outline = added.

### ESTIMATE bars
- Per metric, "Today" (solid neutral) over "ESTIMATE" (teal tint, dashed teal outline) on that
  pair's own scale, with the change underneath in positive or negative colour by direction.

### Evidence citations
- Beside each claim, in a narrower right column: the ID in mono on the ID tint, then its
  code-written summary in small text.

### Navigation
- Sidebar page links with 16px outline Material icons; the active page is a neutral grey pill.
  The mark (a path stepping down, in teal on a pale teal tile) sits above them.

### BPMN viewer
- bpmn-js on the theme's canvas, strokes and labels in ink; tasks a proposal changes are outlined
  in teal. The bpmn.io watermark takes the ink colour so it stays fully visible in both modes.

## Do's and Don'ts

### Do:
- **Do** put the answer to the page's question in its biggest block (the ladder on Evidence).
- **Do** write one finding line under each title, with its evidence ID.
- **Do** keep every number right-aligned in tabular figures, with its unit in secondary ink.
- **Do** label every projected number ESTIMATE and draw it dashed.
- **Do** leave the decision empty until the analyst chooses Approve or Reject.

### Don't:
- **Don't** put teal on navigation, tab labels or selected segment labels.
- **Don't** add shadows to panels, gradients to surfaces, or icons in tinted tiles.
- **Don't** use a dark or saturated sidebar, or a navy or slate dark theme.
- **Don't** set monospace on anything but evidence and run ids.
- **Don't** hide or dim the bpmn.io watermark.
