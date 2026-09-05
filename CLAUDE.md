# EventTraffic Platform — Claude session brief

> **Read `PROJECT_STATE.md` before doing anything.** It has the full architecture, all calibrations, recent fixes, known issues, and what comes next. Keep it up to date as you work.

## TL;DR
- Predictive event-traffic platform for **6 Mumbai venues** (Wankhede, Dome SVP, DY Patil, NESCO, MMRDA, Mahalaxmi).
- Flask backend (`app.py`) + Leaflet frontend (`index.html`).
- Three map views: 🚗 Vehicular · 🚶 Foot Traffic (metre-anchored density heatmap) · 👷 Staff Ops.
- Start the server: `python app.py` → http://127.0.0.1:5000

## Where things live
- Model: `app.py` (~2,600 lines)
- UI: `index.html`
- Project state / history / next steps: `PROJECT_STATE.md`
- Historical events: `event_history.json` · Disruptions: `disruptions.json`
- OSM road caches: `roads_cache_<venue>.json` (72h TTL)

## Working agreements
1. Don't re-read `app.py` end-to-end. Use **Grep** to locate symbols, **Read with `offset`+`limit`** for the slice you need.
2. When a meaningful change lands, update `PROJECT_STATE.md` so the next session inherits the truth.
3. Server runs in Flask debug mode → auto-reloads on file save. If it dies, just `python app.py` in a separate terminal so it doesn't die when Claude closes.
4. Don't deploy `app.run(debug=True)` to a customer. Swap to `waitress-serve --port=5000 app:app` first.
5. The Waze "live" feed scrapes an unofficial endpoint — fine for demo, **not legal for commercial sale**. Swap to a licensed provider (Mappls, TomTom, HERE) before charging anyone.

## Most recent context (Dec 2025)
- Dome layout corrected from "single dominant entry" to **A+C dual public entrances** + production-north + emergency-south, per the venue's own TWKTK Emergence architectural drawing.
- `CAB_SHARE` recalibrated after Lolla 2025 backtest (central venues are transit + cab dominant, not drive-and-park).
- Crowd size is now a typeable number input with preset suggestions.
- Foot-traffic heatmap replaced leaflet.heat (broke at zoom) with metre-anchored translucent-circle field.

If you came back to this project and don't know what to do, ask: *"What's in `PROJECT_STATE.md` under 'Known stuff to handle next'?"*
