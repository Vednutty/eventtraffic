# EventTraffic backtest — 2026-09-30 14:37

Out-of-sample: each event is hidden from event_history.json while it is predicted; live Waze alerts off; disruptions filtered to the event date; weather assumed clear.

**Mappls:** no API keys (set MAPPLS_STATIC_KEY or MAPPLS_CLIENT_ID + MAPPLS_CLIENT_SECRET)

## Scorecard (events with reported congestion only)

- Events scored: **3** of 11 verified (8 have only a pre-event advisory — see bottom)
- Hotspot hit rate (reported road predicted LOS E/F): **4/6**
- Severity exact grade: **2/3** · within one grade: **3/3**

> Sample is far too small to quote as an accuracy figure. It shows the harness works and where ground truth is missing.

### Coldplay — Music of the Spheres, Night 1 — 2025-01-18 (dypatil)

Crowd 52,500 · ⚠ start time unsourced

Severity: reported **severe** (≈ LOS F) · model peak **LOS F** at T-60 · 0 grade(s) off

| Reported road | Reported | Model road | Model LOS (phase) | Model queue | Hit | Mappls same-day |
|---|---|---|---|---|---|---|
| Sion-Panvel Highway | 60 min delay | Sion-Panvel Highway | F (T-60) | 5.9 km | ✅ | no data |
| Thane-Belapur Road | 60 min delay | Thane-Belapur Road | F (T-60) | — | ✅ | no data |
| Mumbai-Pune Expressway, Turbhe to Kharghar | 8–10 km | Sion-Panvel Highway | F (T-60) | 5.9 km | ✅ | no data |

### Lollapalooza India 2025 (Mar 8–9) — 2025-03-08 (mahalaxmi)

Crowd 32,000 · ⚠ crowd ASSUMED 32,000, start time unsourced

Severity: reported **heavy** (≈ LOS E) · model peak **LOS D** at start · 1 grade(s) off

| Reported road | Reported | Model road | Model LOS (phase) | Model queue | Hit | Mappls same-day |
|---|---|---|---|---|---|---|
| Haji Ali to Mahalaxmi Station (KK Road), southbound | congested | Keshavrao Khadye Marg | D (start) | — | ❌ | no data |
| Lotus to Haji Ali Junction, southbound | congested | Worli — Dr Annie Besant approach | D (T+30) | — | ❌ | no data |

### T20 World Cup victory parade + Wankhede felicitation — 2024-07-04 (wankhede)

Crowd 40,000

Severity: reported **severe** (≈ LOS F) · model peak **LOS F** at start · 0 grade(s) off

| Reported road | Reported | Model road | Model LOS (phase) | Model queue | Hit | Mappls same-day |
|---|---|---|---|---|---|---|
| Marine Drive (NCPA to Wankhede) | congested | Marine Drive | F (start) | — | ✅ | no data |

## Advisory-only events (predictions shown, not scored)

| Event | Date | Venue | Crowd | T-60 | Start | T+30 | Worst corridor |
|---|---|---|---|---|---|---|---|
| Coldplay — Music of the Spheres, Night 2 | 2025-01-19 | dypatil | 52,500 | F | F | F | Thane-Belapur Road |
| Coldplay — Music of the Spheres, Night 3 | 2025-01-21 | dypatil | 52,500 | F | F | F | Thane-Belapur Road |
| Ed Sheeran — +–=÷× Tour | 2024-03-16 | mahalaxmi | 55,000 | E | E | E | Keshavrao Khadye Marg |
| Diljit Dosanjh — Dil-Luminati Tour | 2024-12-19 | mahalaxmi | 32,000 (assumed) | D | D | D | Dr E Moses Road |
| Zomato Feeding India Concert — Dua Lipa | 2024-11-30 | mmrda | 30,000 (assumed) | E | E | E | Bandra Kurla Complex Road |
| ICC World Cup semi-final: India vs New Zealand | 2023-11-15 | wankhede | 33,000 | D | D | D | Churchgate north |
| IPL 2024 Match 29: MI vs CSK | 2024-04-14 | wankhede | 33,000 | E | D | E | Marine Drive |
| ICC Women's World Cup final: India vs South Africa | 2025-11-02 | dypatil | 40,000 | E | E | E | Thane-Belapur Road |
