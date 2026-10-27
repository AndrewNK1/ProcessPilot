---
version: 1
slug: "web-src-app-tsx"
primary_target: "web/src/App.tsx"
related_targets: ["web"]
---

Scope: the ProcessPilot web app (React + TypeScript + Vite in `web/`, built into the Python package and served by FastAPI): Data, Evidence and Redesign screens plus the shell. Mode: Operate.
Audience: SME business analysts in long desktop sessions; the thesis supervisor in demos. Bar: Linear (crisp, dense, fast, strong dark mode).
Task: event log -> mapped and checked data -> evidence per segment -> agent proposals -> analyst approves or rejects.
Constraints: offline at runtime (fonts and bpmn-js bundled); bpmn.io watermark visible; light + dark with a visible switch (OS default); no backend behaviour change beyond serving the app; replaces Streamlit entirely.

## Direction contract

THESIS: The analysis is a ticket wallet: the method is a coupon book whose legs tear off in order, a case path reads as an itinerary whose layovers are the waits, and every proposal is a coupon with fixed fields that the analyst's decision stamps; nothing disappears, it cancels. Refuses the KPI-tiles-over-a-big-table dashboard.

OWN-WORLD: Coupon-white stock on a cool off-white shell; ink navy text and coupon heads (navy band, condensed tab caps); carbon-copy cobalt on every filled figure and on the ESTIMATE copy (a pale cobalt sheet, the projected copy of today); one warm signal red only for stamps (VOID, rejected) and bad news; green only for approved/sound. Archivo in two widths: condensed caps for coupon heads and tabs, normal width for text, tabular figures. Square-cornered coupons, 1px stock edges, no shadows at rest.

STORY: The analyst sees which leg of the wallet is open, confirms mapping, quality and cleaning, reads where cases lay over on the main itinerary with evidence IDs, runs the agent, reads proposals as coupons with their ESTIMATE copy and the as-is/to-be models, and stamps a decision; earlier rounds stay on file, voided.

FIRST VIEWPORT: Left shell 232px (mark, Data / Evidence / Redesign as wallet legs with done/open state, run and segment scope, theme switch, model status). Canvas: noun title with segment, one finding line with its evidence ID, segment tabs, a divided KPI strip, then the itinerary of the chosen path (legs with layover bars in cobalt, the longest layover flagged with its BN ID) beside ranked bottleneck coupons-as-rows. Primary action (navy) bottom of the itinerary: Diagnose and redesign.

FORM: Ticket Wallet (jet-age airline ticket wallet), dealt challenger chosen by the user in re-roll round 1 over the Workshop Wall roll; palette refined at the user's request (no carbon purple: cobalt carbon, ink navy, warm signal red). seed:00380b2c9392fd75

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
