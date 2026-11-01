# Stira Forest UI

React/Vite App Platform page that renders one SVG tree per crew:

- trunk width = `stability`
- branch count and spread = `growth`
- leaves = knowledge entries projected by `effectiveScore`
- trunk tilt = purpose drift
- unnatural branch bends = means drift

The page reads `GET /api/apps/stira/crews` and then `GET /api/apps/stira/crews/{crew_id}/knowledge` for each crew. The build externalizes React, ReactDOM, `@kirocrew/app-sdk`, `@kirocrew/app-sdk/ui`, and `lucide-react`, which are provided by the KiroCrew host.

```powershell
cd ui
npm install
npm run build
```

The bundle is emitted to `ui/dist/index.mjs`, matching `ui.entry` in `app.json`.
