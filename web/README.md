# ProcessPilot web app

React 19 + TypeScript, built with Vite. It talks only to the ProcessPilot HTTP
API (`src/processpilot/api/`); it never reads event data or computes a metric.

You need Node only to change the UI. Running the app needs Python alone: the
production build in `src/processpilot/api/static` is committed and served by
the API (`.\scripts\run_app.ps1`).

## Commands (PowerShell, in this folder)

```powershell
npm ci            # install the exact versions in package-lock.json
npm run dev       # hot reload on http://localhost:5173; API calls go to a running
                  # backend (http://127.0.0.1:8000, or PP_API_URL)
npm run check     # TypeScript type check
npm test          # unit tests (vitest): itinerary logic and number formatting
npm run build     # type check + production build into ../src/processpilot/api/static
```

After a UI change, run `npm run build` and commit the rebuilt
`src/processpilot/api/static` together with the source.

## Layout

| Path | Role |
| --- | --- |
| `src/api.ts` | typed client for every API route the app uses |
| `src/format.ts` | durations, shares and IDs, formatted like the evidence summaries |
| `src/journey.ts` | the itinerary logic: default path, legs, proposal marks, axis ticks |
| `src/Itinerary.tsx` | a path drawn as an itinerary (the signature component) |
| `src/BpmnView.tsx` | read-only bpmn-js viewer (keeps the bpmn.io watermark visible) |
| `src/ui.tsx` | coupon, evidence tag, stamp, KPI strip, notices, job progress |
| `src/pages/` | Data, Evidence, Redesign |
| `src/styles/tokens.css` | colours and type tokens, light and dark |

Every screen state lives in the URL (`/evidence/<run>/<segment>?path=3`,
`/redesign/<run>/<segment>?agent=<id>`). The browser's local storage holds only
conveniences: the appearance, the last run and segment, the analyst's name.

The visual system is documented in `../DESIGN.md`.
