---
name: ProcessPilot
description: Evidence-grounded process redesign for business analysts; a human decides.
colors:
  ground: "#FFFFFF"
  shell: "#F5F6F9"
  raised: "#F8F9FB"
  active: "#E7EAF2"
  ink: "#101B3A"
  muted: "#4E5873"
  faint: "#5A6378"
  edge: "#DDE2EB"
  rule: "#ECEFF4"
  control: "#C4CBD8"
  head: "#13234D"
  head-muted: "#B9C3DC"
  primary: "#13234D"
  carbon: "#2F4FB5"
  carbon-soft: "#9FB2EA"
  carbon-sheet: "#EEF2FD"
  carbon-edge: "#C9D4F4"
  signal: "#C63A2A"
  signal-sheet: "#FDEFEC"
  go: "#16784F"
  go-sheet: "#E6F4ED"
  notice: "#8A5300"
  notice-sheet: "#FFF4E0"
  ground-dark: "#0E1015"
  shell-dark: "#12151B"
  ink-dark: "#E8ECF4"
  muted-dark: "#A6AFC4"
  edge-dark: "#262C38"
  head-dark: "#1C2D5E"
  primary-dark: "#E8ECF4"
  carbon-dark: "#93ABF6"
  carbon-sheet-dark: "#161E36"
  signal-dark: "#FF7B69"
  go-dark: "#52CC99"
  notice-dark: "#F2B556"
typography:
  title:
    fontFamily: "Archivo Variable, Archivo, sans-serif"
    fontSize: "26px"
    fontWeight: 680
    lineHeight: "32px"
    letterSpacing: "-0.015em"
  section:
    fontFamily: "Archivo Variable, sans-serif"
    fontSize: "16px"
    fontWeight: 650
    lineHeight: "22px"
  coupon-head:
    fontFamily: "Archivo Variable, sans-serif"
    fontSize: "14px"
    fontWeight: 680
    fontVariation: "wdth 80"
  figure:
    fontFamily: "Archivo Variable, sans-serif"
    fontSize: "24px"
    fontWeight: 560
    lineHeight: "30px"
    fontFeature: "tnum"
  body:
    fontFamily: "Archivo Variable, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: "20px"
  small:
    fontFamily: "Archivo Variable, sans-serif"
    fontSize: "13px"
    lineHeight: "19px"
  meta:
    fontFamily: "Archivo Variable, sans-serif"
    fontSize: "12px"
  evidence-id:
    fontFamily: "Archivo Variable, sans-serif"
    fontSize: "12px"
    fontWeight: 650
    fontVariation: "wdth 82"
  stamp:
    fontFamily: "Archivo Variable, sans-serif"
    fontSize: "12.5px"
    fontWeight: 780
    fontVariation: "wdth 76"
    letterSpacing: "0.08em"
    textTransform: "uppercase"
rounded:
  coupon: "3px"
  control: "5px"
  tag: "2px"
  pill: "999px"
spacing:
  gap: "16px"
  card-pad: "20px"
  canvas-x: "40px"
  canvas-x-phone: "16px"
  control-height: "34px"
  shell-width: "240px"
components:
  coupon-head:
    backgroundColor: "{colors.head}"
    textColor: "#FFFFFF"
    typography: "{typography.coupon-head}"
  coupon-head-quiet:
    backgroundColor: "{colors.shell}"
    textColor: "{colors.ink}"
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "#FFFFFF"
    rounded: "{rounded.control}"
  button-primary-dark:
    backgroundColor: "{colors.primary-dark}"
    textColor: "{colors.ground-dark}"
  evidence-id:
    backgroundColor: "{colors.carbon-sheet}"
    textColor: "{colors.carbon}"
    typography: "{typography.evidence-id}"
    rounded: "{rounded.tag}"
  estimate-stub:
    backgroundColor: "{colors.carbon-sheet}"
    borderTop: "2px dotted {colors.carbon-edge}"
  kpi-figure:
    textColor: "{colors.carbon}"
    typography: "{typography.figure}"
---

# Design System: ProcessPilot

The front end is a React app (`web/`) served by the FastAPI backend. This file
records the visual system as built on 2026-10-27; it replaces the earlier
Streamlit design ("The Value-Stream Worksheet"), which the user discarded.

## Overview

**Creative North Star: "The Ticket Wallet"**

The analysis is a ticket wallet. The method is a coupon book whose legs tear
off in order (Data: event log, column mapping, data quality, clean and mine).
A case path reads as an itinerary whose layovers are the waits. Every proposal
is a coupon with fixed fields, and the analyst's decision stamps it. Nothing
disappears, it cancels: rejected rounds stay in the decision log, voided.

The world lends four things only, as an Operate surface should: type (Archivo
in two widths), palette (ink navy, carbon cobalt, one signal red), density
(Linear-like: tight rows, small type, flat surfaces) and one signature move
(the coupon with its torn-off carbon-copy ESTIMATE stub). Navigation, tabs,
selects, radios and tables are standard web controls.

**Key characteristics**
- Coupon heads are navy bands with the condensed face; the open leg of the wallet is navy, done and unreached legs are quiet.
- Carbon-copy cobalt is the data hue: KPI figures, layover bars, evidence IDs, the ESTIMATE copy.
- The ESTIMATE is printed on a pale cobalt "carbon sheet" below a perforation: a projected copy of today, never the original.
- Signal red only for stamps (VOID, rejected, failed) and for bad news (an estimate that lengthens throughput, an error).
- Flat at rest; a 1px stock edge separates surfaces. Only the phone menu sheet floats.
- Light and dark are equals, switched in the shell (System / Light / Dark); dark grounds are true near-black, never navy.

## Colors

The palette was refined at the user's request from the dealt card: the card's
carbon purple was replaced by real carbon-copy cobalt, and the flag-like
navy/red pairing was softened to an ink navy and a warm signal red.

- **Ink navy** (#101B3A text, #13234D heads and primary): all running text, coupon heads, the primary action. On dark, text is #E8ECF4 and the primary inverts to a light fill with a near-black label.
- **Carbon cobalt** (#2F4FB5; dark #93ABF6): every filled figure and the main data element. Soft cobalt (#9FB2EA) for the other layover bars, so the longest layover is the one in full cobalt.
- **Carbon sheet** (#EEF2FD; dark #161E36): evidence ID tags and the ESTIMATE stub.
- **Signal red** (#C63A2A; dark #FF7B69): VOID and rejected stamps, failures, a worse estimate.
- **Go green** (#16784F; dark #52CC99): approved stamps, sound models, a better estimate.
- **Notice amber** (#8A5300 on #FFF4E0): "awaiting decision" and warnings; it is never a ground.
- **Neutrals**: ground #FFFFFF, shell #F5F6F9, stock edge #DDE2EB, row rule #ECEFF4, muted text #4E5873 (all text tiers reach 4.5:1).

**The Carbon-Is-Data Rule.** Cobalt marks data and the ESTIMATE copy. Navigation, tabs and selected segments keep ink labels; selection shows as an underline or a neutral pill.

## Typography

**Archivo Variable** (OFL, bundled through `@fontsource-variable/archivo`), one family in two widths:
- **Title** 26/32, 680, -0.015em: the page's one h1, a noun (the segment's name, or "Data").
- **Coupon head** 14px, 680, width 80: sentence case on the navy band. Capitals were tried and dropped: the band and the narrow width carry the ticket, and tiny tracked caps read as a template tell.
- **Section** 16/22, 650. **Body** 14/20. **Small** 13/19. **Meta** 12.
- **Figure** 24/30, 560, tabular, in cobalt; its unit follows at 14px in muted ink.
- **Evidence ID** 12px, 650, width 82, on the carbon sheet; shortened to type and rank (BN-01) with the full ID and its code-written summary as the tooltip.
- **Stamp** 12.5px, 780, width 76, uppercase, tracked 0.08em, inside a 2px border: the one place capitals stay.

## Layout

A 240px shell on the left (mark, the three method legs with their state, the
current run and segment, the appearance switch, model and job status) and a
fluid canvas with 40px gutters. Each screen opens with a crumb, the h1 and one
finding line (with its evidence ID), then the scope bar (segment tabs and the
run picker). Below 1180px two-column blocks stack; below 900px the shell
becomes a 52px top bar with a menu sheet, the KPI strip folds to two columns
(an odd last cell spans), and below 640px each itinerary leg puts its bar
under its name.

## Components

- **Coupon**: 1px stock edge, 3px corners, a 34px head band (navy when open, quiet shell when done or not reached), an optional leg number, meta text on the right, and an optional stub.
- **Stub**: a 2px dotted perforation with half-circle notches, then the carbon sheet. Used for the ESTIMATE of each proposal.
- **Itinerary**: departure, arrival and the path's mean throughput on top; one strip with every layover end to end (the longest in full cobalt); then one row per leg with its layover on a shared axis in the axis's own unit, its BN evidence ID when the transition is a ranked bottleneck, and mean over median on the right. Proposal marks: hatched = automated, red dashed outline = removed, cobalt dashed outline = new step.
- **ESTIMATE bars**: per metric, "today" (solid muted) over ESTIMATE (cobalt tint, dashed cobalt edge), on one scale shared by every proposal of the round, with the change in green or red by direction.
- **Stamps**: Awaiting decision (amber), Approved (green), Void / Rejected / Failed (red), tilted -3deg in the page head only.
- **Decision**: two radio rows with no default, a comment, the analyst's name (remembered in the browser), one primary button; keyed by round so every round starts empty.
- **BPMN viewer**: bpmn-js navigated viewer on the theme's ground and ink; changed tasks outlined in cobalt; the bpmn.io watermark takes the ink colour and is never hidden.

## Motion

CSS for control feedback (120ms), the phone sheet (220ms) and disclosures (160ms). GSAP 3.15 (`web/src/motion.ts`, `useGSAP`) only where movement carries meaning, all on `expo.out` (the `--ease-out` curve):

- **Screen enter**: the canvas's sections settle in (8px rise, 0.45s, 0.2s total stagger) when the path changes; not on query-string changes.
- **Itinerary draw**: the strip, then each layover, revealed left to right in travel order (clip-path, 0.6-0.8s).
- **ESTIMATE departs from today**: today's bars draw, then each ESTIMATE bar starts at today's length and moves to its own (0.9s), on every proposal switch.
- **Stamp landing**: a stamp lands (scale 1.8 to 1, `back.out`) only when a status or decision changes for the same agent run while the screen is open; never on first view.

Every tween clears its inline styles at the end, so the resting layout is always the CSS one. Under `prefers-reduced-motion` nothing moves. No smooth-scroll library: native scrolling stays exact for dense tables, the sticky shell and the BPMN viewer's scroll-to-zoom.

## Do's and Don'ts

- **Do** put the answer first: the finding line, then the itinerary of the most frequent multi-step path.
- **Do** label every projected number ESTIMATE and print it on the carbon sheet.
- **Do** keep earlier decision rounds on screen, voided.
- **Don't** put cobalt on navigation, tabs or chrome.
- **Don't** add resting shadows, gradients (the automated hatch is data encoding, the one exception), glass, or icons in tinted tiles.
- **Don't** use capitals outside stamps, or monospace anywhere.
- **Don't** hide or dim the bpmn.io watermark.
