---
version: 1
slug: "src-processpilot-app-streamlit-app-py"
primary_target: "src/processpilot/app/streamlit_app.py"
related_targets: ["src/processpilot/app"]
---

Scope: the ProcessPilot Streamlit app (Data, Evidence, Redesign pages). Mode: Operate.
Audience: SME business analysts in long desktop sessions. Task: event log -> checked data -> evidence -> reviewed redesign decision.
Constraints: Streamlit 1.66 controls; offline; light + dark via sidebar toggle; bpmn.io watermark visible; no backend behaviour change.

## Direction contract

THESIS: The analyst sees where the days go. Waiting is drawn as a value-stream wait ladder per path, and each proposal shows which rungs it acts on and its ESTIMATE against today. Refuses the stock KPI-tiles-over-a-big-table dashboard.

OWN-WORLD: A lean value-stream worksheet on screen: tinted neutral ground, full-ink text, petrol teal only on waiting (the data) and the one primary action, IBM Plex Sans with Plex Mono for evidence IDs, 1px rules, 6px radius, no shadows, pale status pills.

STORY: Load a log and see the step you are on; confirm mapping, quality and cleaning; read where cases wait with evidence IDs; run the agent; compare as-is and to-be; approve or reject with a comment.

FIRST VIEWPORT: Sidebar: mark, method steps, run and segment scope, theme toggle. Canvas: noun title with segment, one finding line, divided KPI strip, the wait ladder full width (~220px) with its path selector, then ranked bottlenecks.

FORM: Value-stream wait ladder, candidate 1 of my ordered list (Impeccable's pick, chosen by the user). seed:25efd530dff59120

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
