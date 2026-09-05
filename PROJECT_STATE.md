# EventTraffic Platform — Project State

Handoff document so a fresh Claude session can pick up immediately. Read this top-to-bottom in the new chat.

## What this is
A predictive event-traffic platform for Mumbai venues. Flask backend (`app.py`) + Leaflet frontend (`index.html`). Models vehicular congestion (ECI/LOS), pedestrian foot traffic (PCI density heatmap), parking + Uber/Ola pickup zone load, event-specific gate layouts, and a Staff Ops planner (ticket office / helpdesk / cleaning / medical / water). 6 venues.

## How to run
```
cd C:\Users\ADMIN\Desktop\traffic-platform
python app.py     # serves http://127.0.0.1:5000
```
Flask debug mode auto-reloads on file changes. If "site can't be reached", the process died — just relaunch in its own terminal window (NOT inside Claude's bash, so closing Claude doesn't kill it).

## Map accuracy fixes (Jul 2026)
- **DY Patil centre was ~200 m off.** Stored 19.0433/73.0278; OSM way "Dr. D.Y. Patil Cricket Stadium" centres at **19.04176/73.02674**. The map centred on empty ground NE of the stadium, so the venue looked absent. Corrected — map centre now falls inside the footprint polygon. ⚠ Its gate/parking/pickup coords are absolute and were authored against the old centre; re-verify them against OSM when convenient.
- **Oberoi Mall was ~1.2 km west.** Stored lng 72.8490 in BOTH `PARKING_AREAS["nesco"]` and `PARKING_FACILITIES["nesco"]["ns_oberoi"]`; OSM puts it at **72.8606** (its own listed access road is the WEH, which is at ~72.86). Both corrected.
- **Road clutter → `event_affected` filter.** Roads carrying only ordinary background traffic were being outlined, implying event congestion that doesn't exist and burying the venue. Each OSM road now carries `event_weight`, `event_delta`, `km_from_venue`, `km_from_edge`, `event_affected`; the frontend filters on `event_affected !== false`.
  - **Distance is measured from the venue EDGE, not the centre** (`venue_radius_km` = max distance from centre to any `VENUE_FOOTPRINTS` point, default 120 m). This was a real bug: an early centre-based version hid the entire perimeter ring of large venues — Mahalaxmi (radius **805 m**), JLN (**530 m**), NESCO (**365 m**) — because their surrounding roads are 0.4–0.9 km from the *pin* while sitting right at the venue. Symptom was exactly "missing roads inside the radius, roads outlined outside".
  - Allowance from the edge: **arterial 1.20 km · sub-arterial 0.50 km · local 0.20 km**. Named corridors and disrupted roads always render regardless of distance.
  - **`_UNDRAWN_HIGHWAYS` class filter** (Jul 2026): `service`, `residential`, `living_street`, `construction`, `track`, `path`, `footway`, `pedestrian` are never drawn. Rationale: 417 of 1,403 drawn roads were `service` (parking aisles/driveways) and 263 `residential` — 48% overall, 68% at Mahalaxmi — none of them traffic-management targets, and all scored only by a background heuristic, so colouring them implied precision the model doesn't have. They remain in the API response (`event_affected: false`); only the map layer skips them. Named corridors and disrupted roads override, so a venue's own named access road can never vanish. Result: Mahalaxmi 82% → **25%** drawn; all venues now 11–30%, showing only motorway/trunk/primary/secondary/tertiary + links.
  - Verified: 100% of roads within 150 m of the edge render at every venue; nothing renders beyond 1.2 km from the edge. Drawn share — DY Patil 18%, MMRDA 20%, Wankhede 39%, NESCO 39%, Dome 41%, JLN 43%, **Mahalaxmi 82%** (expected: its 805 m radius means almost the whole 1 km OSM fetch genuinely is perimeter).

## Venue data audit (Jul 2026) — bugs found and fixed
Ran an internal-consistency sweep over all 6 Mumbai venues (`_audit_venues.py` does the OSM half; the local half checks share sums, capacity coherence, walk-time plausibility, corridor geometry).
- **DY Patil `out_share` summed to 1.10** → every DY Patil event over-allocated egress demand by 10%. Re-normalised to 1.00 keeping relative weights (0.23/0.18/0.18/0.41).
- **Wankhede `concert` layout `in_share` summed to 1.05** → 5% ingress over-allocation at every Wankhede concert. gate_east 0.30 → 0.25.
- Now enforced across **all 12 venue×layout combinations — every in_share and out_share totals exactly 1.000**. Worth re-running this check whenever gates are edited.
- **`VENUES[...]["parking_capacity"]` had drifted from `PARKING_FACILITIES`** (dome declared 800 vs 550 mapped; nesco 2500 vs 3200; mmrda 2000 vs 2500; wankhede 500 vs 1150). It gates when overflow lots open, so the drift changed behaviour. Now **derived** from PARKING_FACILITIES at import — single source of truth, cannot diverge.
- **DY Patil footprint was a self-intersecting donut.** The outer bowl ring AND an inner ring were concatenated into one point sequence, so it rendered as a seamed ring in Leaflet and the shoelace area returned outer-minus-inner (**15,935 m² instead of 34,953 m²**). Reduced to the outer ring: bearing sweep 582° → **360°**. A bearing-sweep test (>520° = self-intersecting) confirms all 7 footprints are now simple rings — re-run it whenever a footprint is edited.
- **Mahalaxmi centre was 157 m off** OSM way "Mahalaxmi Racecourse" (its footprint polygon was accurate to 11 m, so only the pin was wrong). Corrected to 18.98589/72.81850; venue radius 806 → 815 m.
- OSM cross-check results (14 of 30 lookups were SKIPped — Overpass unreachable, so this needs a re-run): NESCO footprint matches OSM to **x1.00** (200,034 vs 200,193 m²), Mahalaxmi **x0.99**, BKC metro station **5 m**, Churchgate **177 m**, NESCO car park **127 m**. Outstanding WARNs to investigate: Mahalaxmi MAIN ENTRY gate 144 m off the outline; several parking lots with no OSM parking nearby (RWITC apron 861 m, Jacob Circle 976 m, BKC G-Block 422 m — may be genuinely unmapped informal parking); corridor names not found in OSM near DY Patil/NESCO (likely OSM naming variants, e.g. "Thane Belapur Road" vs "Thane-Belapur Road").
- Open//known, not bugs: `walk_min` is overloaded for shuttle-served lots (Sector 15 CBD Belapur is 2.1 km with `walk_min=10`, meaning 10 min by shuttle; Oberoi Mall 2.8 km with 12) — worth a separate `shuttle_min` field. Wankhede's 'Grant Road' (3.7 km) and DY Patil's 'Vashi Bridge approach' (4.8 km) corridors legitimately extend far.

## Venue auto-discovery (Jul 2026) — any coordinates, any city
The platform no longer needs hand-authored venue data. `discover_venue(lat,lng)` in app.py builds a full profile from OpenStreetMap:
- **venue outline + footprint area** (leisure=stadium/sports_centre/pitch, building=stadium, amenity=events_venue within 450 m; largest polygon = outline; shoelace area with local metric projection).
- **performance floor area** — prefers the actual `leisure=pitch` polygon (confidence high). Fallback is footprint × 0.22, flagged **low** confidence with a warning, because a stadium outline includes stands/concourses/complex grounds: the first cut used 45% and gave JLN Delhi a 171,808 m² "floor" → a nonsense "comfortable 0.59 p/m²" density for a 45k crowd.
- **approach corridors** — named motorway/trunk/primary/secondary/tertiary within 1.3 km, real `out geom` geometry, grouped by name, classified via `OSM_ROAD_TYPE`, sorted far→venue, with a compass `approach_from` label.
- **nearest station** — broad query (railway=station/halt, public_transport=station+train, station=subway, way railway=station) within 3 km.
- **parking** — amenity=parking within 1.5 km; capacity tag, else area ÷ 28 m²/space; ≥25 spaces; nearest 6.
- **gates** — OSM entrance nodes if ≥2, else synthesised at the outline point nearest each top corridor (flagged low confidence; a floor-plan upload corrects them).
- **feeder highways** — named motorway/trunk/primary within 6 km reaching ≥1.8 km out; top 3; pre-seeded into `feeders_cache_<slug>.json` so no later Overpass call is needed.

**API:** `GET /api/venue/discover?lat=&lng=&name=` (preview, saves nothing) · `POST /api/venue/save` (persists `venue_<slug>.json`) · `GET /api/venues` (built-in + discovered) · `DELETE /api/venue/<slug>`. `register_venue_profile()` injects the profile into the SAME module dicts the model already reads (VENUES, ROAD_CORRIDORS, BASE_TRANSPORT_SPLIT, CAB_SHARE, HOURLY_BG_ECI, PARKING_FACILITIES, PICKUP_ZONES, VENUE_GATES, VENUE_FOOTPRINTS, VENUE_FLOOR_AREAS, NEAREST_STATIONS, …) so every existing code path works unchanged. `load_discovered_venues()` runs on import.

**Per-field `confidence` map + `warnings` list** on every profile. Critically, `_overpass()` returns `None` on request failure vs `[]` for genuinely-nothing-there, and callers distinguish them — a failed lookup reports **"unknown / re-run discovery"**, never "none found". (This bug bit once during the Delhi test: a timed-out road query rendered as "no roads near JLN Stadium".)

**UI:** "➕ Add venue by coords" beside the venue dropdown → paste Google Maps coordinates → preview with per-field confidence chips and warnings → "Add this venue". Dropdown is populated from `/api/venues` (discovered venues prefixed 📍).

## City calibration profiles — the part a map CANNOT provide
`CITY_PROFILES` holds what OSM can never supply: hourly background ECI curve, transport mode split, cab share, parking occupancy, DOW multipliers, historical prior. **This is city-level, not venue-level** — calibrate a city once and every venue in it works. Commercially this is the unit of sale: adding a venue is free, adding a city is an engagement.
- **mumbai** — calibrated against the 10-event backtest. Rail-dominant (0.45 train/metro), cab_share 0.60.
- **delhi** — seeded from Delhi travel-demand studies (~50% public-transport mode share, 11M daily transit trips, private modes +21% 2005-19, two-wheelers ~26% of trips): 0.35 train/metro, 0.50 car_cab, cab_share 0.45 (more self-driving), parking occupancy 2.8, flatter/broader peak curve than Mumbai's twin spikes. **NOT yet event-backtested** — flagged in the profile's `notes`.
- Venues outside every profile's radius fall back to Mumbai defaults and the UI says so explicitly.

## Venues (6 built-in)
| key | name | center (lat, lng) |
|---|---|---|
| `wankhede` | Wankhede Stadium | 18.9388, 72.8251 |
| `dome` | Dome @ NSCI SVP Stadium, Worli | 18.9865, 72.8155 |
| `dypatil` | DY Patil Stadium, Nerul | 19.0433, 73.0278 |
| `nesco` | NESCO / Bombay Exhibition Centre | 19.1493, 72.8542 |
| `mmrda` | MMRDA Ground, BKC | 19.0674, 72.8613 |
| `mahalaxmi` | Mahalaxmi Racecourse (RWITC) | 18.9852, 72.8198 |

Footprints are stored in `VENUE_FOOTPRINTS` (real OSM polygons, pulled via OSM API `/api/0.6/way/{id}/full.json`). Outlines render on the map.

## Three map views
- **🚗 Vehicular** — OSM roads colored by ECI/LOS, parking lots (P marker, fill %), Uber/Ola zones (🚕, load %), entrance/exit markers (IN/OUT/IN-OUT).
- **🚶 Foot Traffic** — geographic **density field** built from translucent metre-circles (NOT leaflet.heat — that pixel-based approach broke at zoom; current circles scale correctly). Gate bubbles with utilisation %.
- **👷 Staff Ops** — staffing plan + station markers (ticket lanes 🎟, info 🛈, cleaning 🧹, first-aid ➕, water 💧).

## Event-specific gate layouts (`EVENT_LAYOUTS`)
Each venue has a default + one alternate setup, real-event-grounded:
- **Wankhede**: Cricket stand-wise / Concert GA-funnel.
- **Dome**: A+C dual-entry standing concert (per TWKTK Emergence architectural drawing) / Seated show (tier opens for platinum-table guests).
- **DY Patil**: Coldplay-style wristband gates / Cricket-football stand-wise.
- **Mahalaxmi**: Single south main entry (Lollapalooza/NH7) / Dual-entry for large festivals.
- **MMRDA**: Concert single main / Expo multi-gate.
- **NESCO**: Concert 2-entry / Exhibition multi-gate (Halls 1–5).

## Foot-traffic model (key calibrations)
- `PEAK_HOUR_FRACTION`: western_superstar 0.64, indian 0.58, edm 0.60, sports 0.50 — fraction of crowd through gates in the busy hour.
- Phase-aware gate distribution via `in_share`/`out_share` per gate (entries idle on egress, exits idle on entry).
- Utilisation uncapped (can read >100% with "over capacity" tag); LOS still capped at F.
- Density heatmap (foot view): metre-anchored circles around gates + along corridors. Reliable at every zoom.

## Parking + Pickup model
- `PARKING_FACILITIES` (venue lots, paid structures, overflow grounds) + `PICKUP_ZONES` (Uber/Ola/taxi).
- `CAB_SHARE` (fraction of car_cab arrivals who cab vs drive-and-park) — **recalibrated after Lolla backtest**: wankhede 0.70, dome 0.72, dypatil 0.40, nesco 0.48, mmrda 0.58, mahalaxmi 0.80. Central venues are transit + cab dominant; DY Patil is the only car-oriented venue with a real big lot.
- Hotspots feed road ECI: roads near saturated lots/pickup zones get an ECI bump (proximity-weighted).

## Staff Ops
`compute_staff_ops()` produces role cards (headcount, deploy/peak/stand-down timeline, tasks) and positioned map stations. Scales with crowd, gate congestion, ingress vs egress. Five roles: Ticket office, Helpdesk/wayfinding, Cleaning, Medical, Water/welfare.

## Calibration v2 (Jul 2026) — applied from the 10-event backtest
Iterated 4 rounds against the same 10 events. **Severity 6/10 → 8/10, egress timing 1/10 → 6/10, hotspots steady ~9/10.** Changes (all in app.py):
1. **Special-train mode shift capped by rail capacity** — `min(0.30, car×0.5, 3500/crowd)` instead of flat 30%.
2. **Transit ECI relief scaled by carrying capacity** (`apply_transit_augmentation`) — special train relief = `min(0.35, 2×3500/crowd)`, metro `min(0.30, 2×6000/crowd)`, shuttle `min(0.15, 2×1500/crowd)`. This (not the mode shift) was the main Coldplay underprediction: a flat ×0.65 corridor cut for 2 rakes carrying ~7% of a 50k crowd. Displayed reduction % now matches.
3. **Weekend factor interpolates by venue district** — `base = 0.85 + (EVENT_DOW − 0.85) × sensitivity × min(1, crowd/40k)`; `WEEKEND_SENSITIVITY`: dypatil 1.0 (leisure highway), nesco 0.5, mahalaxmi/dome 0.3, wankhede 0.15, mmrda 0.0 (CBD empties on weekends). Replaces background DOW_TRAFFIC for the event blend (kept as legacy fallback in `_named_corridor_eci_map`/`compute_all_roads_eci`, which now accept `dow_factor`/`crowd_hist_ratio` params from predict — they were silently recomputing the old inverted DOW).
4. **Corridor history crowd-scaled** — `0.25 × ch_mean × crowd_hist_ratio` where ratio = clamp(crowd/TYPICAL_EVENT_CROWD[venue], 0.55, 1.15).
5. **Staggered egress** — `EGRESS_BULK_FRACTION` (edm 0.62 … sports 0.90) applied to gate/parking/pickup egress pulses + tails; tail padding 1.3×+15 → 1.15×+10. Coldplay egress now 119 min (real ~120); Klang 30 (real ~30).
6. **VENUE_CAPACITY_FACTOR dypatil 0.78** (3 real events all saturated harder than formula).
7. **`egress_advisory`** in predict response + PDF advisory box — "heaviest traffic in first N min after the event; clears in ~M min; X% exits in one pulse."
**Residual known misses (documented, conservative direction):** Ed Sheeran/Diljit @ Mahalaxmi predict E vs reported moderate (soft ground truth); Mahalaxmi egress tails still ~145-165 min vs ~60-90 reported (exit capacity data likely understated — verify on site survey). ⚠ Caveat for pitch: this is *calibration on* the 10 events, not out-of-sample validation — the next live event is the true test. Also: density module assumes all-standing floors; stadium shows with seated stands (Coldplay) over-read density.

## 10-event backtest (Jul 2026) — accuracy scorecard
Ran the 10 biggest 2023–26 Mumbai events through the live model vs press-documented reality (script: scratchpad/backtest_top10.py).
- **Hotspot identification: ~85% (9/10-ish)** — model named the exact roads/facilities that failed: Thane-Belapur for Coldplay (press: jams on Sion-Panvel AND Thane-Belapur), Keshavrao Khadye Marg = the "KK Road" in Lolla press reports, Nerul station 72% (special trains were in fact needed), Lolla cab waits predicted 120 min vs ~90 reported, Klangkuenstler density DANGER 4.99/m² at a show where someone died on a packed floor while gates stayed clear.
- **Severity band: 6/10** with a clear bias signature: UNDERpredicts suburban mega-events (Coldplay N1 predicted LOS C 0.51 vs real gridlock — special_train −30% car shift overcredited; DOW factor made Sat *lighter* than Tue, reality was opposite) and OVERpredicts mid-size Mahalaxmi shows (Ed Sheeran E vs moderate; Diljit F vs moderate — corridor history is Lolla-dominated and not crowd-weighted).
- **Egress duration: weak (~1/10 strict)** — egress_tail systematically too long for festivals (predicts 180 min vs ~90 real): the single-pulse assumption is wrong for all-day festivals where 30–40% trickle out before the finale. OK for single-set concerts (Coldplay 159 vs ~120 real).
- **Calibration TODOs from this study:** (1) cap special_train car-shift by actual rail capacity share, (2) crowd-weight CORRIDOR_HISTORY like the venue baseline, (3) event-day DOW correction for destination mega-events (weekends worse, not better), (4) staggered-egress mode for festivals (event_type or layout flag).
- Ground-truth confidence: high for Coldplay/Lolla/Klang (press-verified), medium for cricket/Ed/Diljit, low for Dua Lipa.

## Backtest done — Lollapalooza India 2025 @ Mahalaxmi
Compared model vs documented reality (Mumbai Traffic Police advisory + ground reports):
- ✅ Vehicular hotspots: model predicted Sane Guruji Marg, Senapati Bapat, Keshavrao Khadye, Dr E Moses — matches police advisory.
- ✅ Single south main entry crush — matches Lolla map.
- ✅ Sharp egress on all gates — matches reality.
- ❌ → ✅ **Phantom parking** (model said 3,600 cars / 257% fill; reality = parking banned, ~0 parked). Root cause: CAB_SHARE too low (treated 52% of car arrivals as parkers). Fixed by raising CAB_SHARE for central venues.

This is the credibility proof point for sales conversations.

## Key recent fixes (worth knowing)
- Disruption matcher bug: empty road-name (`""`) was substring-matching every disruption → unnamed roads inherited Sea Link ×1.9 multiplier. Now requires `len(name) >= 5`.
- Local-road background scale reduced (`_rt_bg_scale local 0.55→0.32`) so side lanes don't glow at peak hour.
- Crowd-size field is now a **typeable number input** (`<input type="number">`) with a datalist of presets (5k/6k/10k/20k/33k/40k/50k/75k). Accepts any value 100–120,000.
- **Security + algorithm audit (Jul 2026):**
  - `/api/predict` numeric params now parsed safely + clamped (`_int_param`) — garbage input no longer 500s.
  - `_road_cache_path` whitelists venue_id (path-traversal guard); unknown venue → 404.
  - Research-panel innerHTML now escapes all LLM/web-derived strings (`esc()`) — XSS guard.
  - `_research_cache` bounded at 200 entries (1h TTL); rate-limit errors surfaced friendly.
  - `app.run` explicitly binds 127.0.0.1; debug gated on `FLASK_DEBUG` env (Werkzeug debugger = RCE if exposed).
- **Weather factor added** — `weather=clear|light_rain|heavy_rain` param scales all ECI blends (×1.0/×1.15/×1.35, folded into `dow_factor`) and shifts foot→cab share (+5%/+10%). UI dropdown next to Event Start; auto-filled from research `weather_forecast` keywords. Verified: dome 6k T-30 goes LOS C→D under heavy rain.
- **Historical baseline improved** — `calculate_historical_baseline` now (a) weights past events by crowd proximity, (b) shrinks toward 0.70 prior by n/(n+3) so 1 lone event doesn't dominate. Legacy 2-arg calls (corridor fallback) unchanged.
- **Research API** now returns `venue`, `event_type`, `event_date`, `event_time`, `resale_evidence` (told to check Viagogo/StubHub/Twickets/Instagram/OLX and NOT invent resale numbers). Apply button switches venue/type/date/time/weather.
- **Thorough PDF report** — `exportThoroughReport()` in index.html captures the map at T-3h/T-1h/T-30/+30/+1h via html2canvas, builds multi-page report (exec summary, 5 phase maps + KPIs, corridor/gate/parking/staff tables, deployments, advisory, sign-off block). Chrome works best.
- **Crowd-scaled egress (Jul 2026)** — `egress_tail_minutes(people, capacity_pph)` in app.py: egress pulse duration = crowd ÷ exit capacity ×1.3 +15 min, clamped 35–180 (pickups 240). Applied to gate egress curve, parking-exit curve, and pickup-zone curve separately. Verified: DY Patil 5k fully clear at +45; 50k still 76% gate / 210% pickup at +90. Response exposes `foot_traffic.egress_tail_min`, `parking_pickup.egress_tail_park_min` / `egress_tail_pickup_min`.
- **Per-venue live weather** — `/api/weather?venue&date&hour` calls Open-Meteo (free, keyless) with the venue's own lat/lng (monsoon cells are hyper-local; DY Patil ≠ Worli). Buckets: ≥4 mm/h → heavy_rain, ≥0.4 mm/h or >60% prob → light_rain. 30-min cache; 0–15 days out only. Frontend auto-fetches on venue/date/time change and sets the weather dropdown unless the user picked manually (`weatherManual` flag); forecast note shown under the dropdown.
- **Foot traffic v2 (Jul 2026)** — three fixes in `compute_foot_traffic`:
  1. *Corridor allocation bug fixed* — was capacity-proportional (flow/cap cancels → every corridor at a venue had an identical PCI). Now demand-driven: corridors whose `from`/`name` mention station/stn/metro/railway carry the train-walker flow (`train_metro × 0.75`), the rest carry local walk-ups; split by capacity within group. Verified: Wankhede corridors now differ (0.93 station routes vs 0.83 cab-drop).
  2. *Fluid-queue gate waits* — `_gate_queue()` steps the arrival/egress curve in 5-min slices and accumulates arrivals beyond capacity (drains when below). Replaces the flat `(util−0.85)×40` heuristic. Gates now expose `queue_people` + meaningful `est_wait_min` that GROWS while oversaturated (DY Patil 50k egress: 2.3k queued at +30 → 3.3k at +60).
  3. *Station-side load module* — `STATION_GATELINE_CAPACITY` per venue + `foot_traffic.station` block: train-walker flow vs station ticket-gate/stairs capacity, fluid queue on egress, staggered-release/RPF recommendations at ≥80%. The Elphinstone failure mode: venue gates clear while the station crushes. Rendered as 🚉 marker + density blob on the foot view, card in foot panel, and a section in the thorough PDF.
- **Parking Plan NOC annexure (Jul 2026)** — `compute_parking_plan()` in app.py, returned as `parking_plan` in `/api/predict`. Nearest-first allocation (by walk_min) to 90% of each lot (10% VIP/ops reserve), fill-by clock times from `_occupancy_fraction` arrival curve, marshals = ceil(alloc/150) min 2, exit-clear = alloc/throughput. Shortfall triggers 4 overflow recs (pre-booked parking, park-and-ride shuttle, mode-shift surcharge, tow-away comms); >75% util triggers 2 lighter recs. Rendered as a dedicated "Parking Plan (Traffic NOC Annexure)" page in the thorough PDF with a verification-note footer (lot capacities are desk estimates pending site survey — that survey is the accuracy step + data moat). Verified live: Wankhede 33k → 952 cars/83%/no shortfall; Dome 8k premium → shortfall 22 → 4 recs; DY Patil 15k → 42% clean.
- **Dev server config** — `.claude/launch.json` defines `eventtraffic-flask` (`python app.py`, port 5000) for the preview panel. Note: Flask's stat reloader can race rapid successive edits — if an API change seems missing after save, `touch app.py` to force a clean reload.
- **Extended feeder corridors (Jul 2026)** — `EXTENDED_FEEDERS` + `compute_extended_feeders()` in app.py: long approach-highway polylines (Coastal Road + Sea Link for dome/mahalaxmi, Marine Drive + Eastern Freeway for wankhede, Sion-Panvel both directions + Palm Beach for dypatil, WEH both directions for nesco, WEH/BKC + Sion for mmrda) that extend congestion FAR beyond the 1 km OSM circle. Jam length = `(crowd/50k)×10 km × severity × car-share × phase`, clamped 0.4–15 km — calibrated to Coldplay 2025's documented 8–10 km Sion-Panvel queue. ECI gradient decays from near-venue value to 0.25 background at the jam front; polylines ordered far-end→venue. In API as `extended_feeders` (per-feeder `jam_km`, `total_km`, colour-graded segments). Rendered as wide gradient polylines on the vehicular view (popup shows queue stretch), plus an "Approach-highway queue forecast" table in the PDF corridor section. Verified: Mahalaxmi 60k → 5.8 km on the Coastal Road; DY Patil 50k → ≈9 km total on Sion-Panvel (matches press) + 7 km Palm Beach; Dome 6k → 0.8 km. **Geometry is REAL OSM road alignment**: `FEEDER_OSM_QUERIES` (name-regex + bbox per route) → Overpass `out geom` → `feeders_cache_<venue>.json` (30-day TTL); each OSM way is coloured by its straight-line km from the venue (`geometry: "osm"` in the response). **Hand-drawn fallback REMOVED** (it cut across the sea) — feeders render ONLY from complete OSM geometry, all-or-nothing per venue: if any of the venue's routes fails to fetch, none render (a lone half-fetched line looks broken). **Feeder fetching NEVER blocks a prediction** (fixed Jul 2026 after MMRDA's first uncached call hung past a 120 s client timeout — 3 mirrors × 40 s): `fetch_feeder_geometry(venue, _blocking=False)` is the request path — on a cache miss it spawns a daemon thread (`_warm_feeder_cache`, guarded by `_feeder_inflight`/`_feeder_lock`) and returns `[]` immediately; the lines appear on the next prediction once cached. Only the warmer passes `_blocking=True`. Verified: cold MMRDA call 120 s+ timeout → **3.4 s**. Failed fetches also set a 30-min cooldown (`_feeder_fail_ts`); retried automatically after. Mirrors tried in order: overpass-api.de → kumi.systems → private.coffee. Way filter: main carriageway classes only (no _link ramps), ≥3 vertices, ≥120 m. **wankhede has NO extended feeders by design** (removed 15 Jul 2026): Marine Drive only reached ~1.5 km — already inside the 1 km OSM layer — and the Eastern Freeway isn't a genuine Wankhede approach, so it drew a disconnected line. Wankhede is rail-dominated (Churchgate/Marine Lines), so long car-queue feeders are the wrong model there. Entry omitted from both `EXTENDED_FEEDERS` and `FEEDER_OSM_QUERIES`. As of 15 Jul 2026: dome/nesco cached with real geometry ✓; mahalaxmi/dypatil/mmrda pending (Overpass 504s) — they fill in automatically on a later prediction and cache for 30 days.
- **Floor-plan intelligence (Jul 2026)** — `/api/floorplan` (POST image/PDF · GET · DELETE, per venue). Claude vision (sonnet-4-5) extracts entrances/exits (label, width, GA/VIP/staff), zones (vip/ga/backstage/lounge/washroom/bar/food/medical), stage position, VIP×GA crossings, pinch points, floor-area estimate → stored `floorplan_<venue>.json`. PDF pages need `pip install pymupdf` (graceful error otherwise). Predict() then: (a) overrides `VENUE_FLOOR_AREAS` with the extracted area, (b) scales exit capacity by extracted-vs-modeled access-point ratio (clamp 0.7–1.3), (c) `compute_floorplan_hotspots()` — per-zone local density = avg density × `ZONE_PHASE_MULT` (stage front 3×, exits 3.5× egress, entrances 3.2× ingress, bars 2.5×, washrooms 2.2×, VIP×GA crossings 2.2× egress, lounge/backstage <1×) across ingress/show/egress, worst phase reported, Fruin-graded, each with a mitigation line. Response block `floorplan` incl. `comparison` (modeled vs extracted). UI: 📐 Upload plan button (control bar, ✕ to remove), cramped-zones card in Staff Ops panel, "Micro-congestion map" table + calibration footnote in the PDF density page. Verified end-to-end with a simulated Dome analysis (fixture deleted after test — upload the real TWKTK drawing).
- **Internal-density safety module (Jul 2026)** — `compute_internal_density()` + `VENUE_FLOOR_AREAS` in app.py (motivated by Klangkuenstler @ Dome death, Jun 2026). Occupancy curve (2%→97% from T-300 to T-0, drains with egress tail) × crowd ÷ floor area; front-of-stage ≈ 3× avg, capped 9.5. Levels: <2 comfortable · ≥2 busy · ≥3.5 high · ≥4.7 danger (Green Guide standing max) · ≥7 critical (fatality risk), with per-level mitigation lists. Concerts only (seated sport n/a). In API as `internal_density`, rendered as a card in the Staff Ops panel (`sp-density`) and a "Crowd Density Safety Assessment" page in the thorough PDF. Verified: Dome 6k @ T-0 → 4.99/m² front-of-stage → DANGER (would have flagged the real incident); Dome 9k → CRITICAL; Mahalaxmi 60k → busy. Floor areas are satellite-footprint estimates — calibrate per event layout when selling.

## Known stuff to handle next (for selling)
1. **Hosting** — currently `app.run(debug=True)` on localhost. Will die when laptop closes. Deploy to Render free tier (or DigitalOcean ₹400/mo) with `waitress-serve --port=5000 app:app` for a stable URL.
2. **Waze "live" feed** is unofficial scraping of `waze.com/live-map/api/georss`. Fine for demo, **not legal for commercial**. Swap to Mappls/MapmyIndia traffic API, TomTom, or HERE before charging customers.
3. **Authentication** — no login. Add Flask-HTTPBasicAuth (or simple session) before sharing the URL.
4. **More backtests** — Coldplay @ DY Patil + a Wankhede IPL match → 3-event validation table for pitch.
5. **Pitch one-pager / pilot proposal** — for event organizers (BookMyShow Live, District by Zomato, Wizcraft) using the PDF report as the deliverable.

## File layout
- `app.py` — Flask backend, ~2,600 lines. All the model.
- `index.html` — frontend, Leaflet map + 3 views + control panel.
- `event_history.json` — 18 historical events across the 3 original venues (for similar-event lookup + corridor baselines).
- `disruptions.json` — active disruption list (road closures, construction).
- `roads_cache_<venue>.json` — OSM road geometry cache (72h TTL).
- `PROJECT_STATE.md` — this file.

## Important code locations
- `VENUES`, `BASE_TRANSPORT_SPLIT`, `NEAREST_STATIONS`, `ROAD_CORRIDORS` — top of `app.py`.
- `VENUE_FOOTPRINTS` — building polygons.
- `VENUE_GATES` + `EVENT_LAYOUTS` — gate config per venue + per event type.
- `PEDESTRIAN_CORRIDORS` — walking routes.
- `PARKING_FACILITIES` + `PICKUP_ZONES` + `CAB_SHARE`.
- `compute_foot_traffic` (~line 1230), `compute_parking_pickup` (~line 1380), `compute_staff_ops` (~line 1463).
- `compute_all_roads_eci` — main road model.
- `fetch_osm_roads` — Overpass query + disk cache.

## What to say to start the next session
> "Pick up the EventTraffic platform at C:\Users\ADMIN\Desktop\traffic-platform. Read PROJECT_STATE.md first. Server runs with `python app.py`. Next thing I want to do is: ___."
