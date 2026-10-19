# Navisense V2 — Full-Stack Rebuild Plan

**Goal:** Rebuild the `Navisense_V2_1410.html` AIS Traffic Console as a production
system: React + shadcn/ui + Leaflet frontend, Python (FastAPI) backend, and
PostgreSQL + PostGIS on a Raspberry Pi, fed by a real AIS receiver.

**Status:** Approved decisions (2026-10-14) — see "Decisions" section.

---

## 1. Source Analysis (what the HTML does today)

The existing single-file HTML is a client-side demo with synthetic data:

| Feature | Current implementation | Target |
|---|---|---|
| World map | Custom canvas, equirectangular, 3 palettes (nautical/satellite/terrain), pan/zoom/reset | Leaflet + react-leaflet |
| Vessels | 7 classes w/ colors, dead-reckoned positions, rotated heading triangles, SAT dashed ring | Leaflet markers on `L.canvas` renderer, GeoJSON from API |
| Timeline | 24 h window, scrubber 0–1440 min, play/pause, 3 rates, interpolation | Replay of **real stored data** via `/api/snapshot?t=` |
| Filters | Feed source SAT/TER, class checkboxes w/ counts, search name/MMSI/IMO | Same UI, server-side filtering |
| Display | Density heatmap, name labels, port markers | leaflet.heat + `/api/heatmap`, labels, `/api/ports` |
| Right rail | Vessel list (load-more 50), detail panel (MMSI/IMO/callsign/flag/SOG/COG/HDG/L×B/draught/dest/ETA/pos/last report) | Same, from `/api/vessels` + `/api/vessels/{mmsi}` |
| Readouts | Tracked/SAT/TER counts, avg SOG, underway count, UTC clock, feed status | `/api/stats`, `/api/health`, WebSocket live |
| Demo data | 140 seeded-random vessels | Removed; real receiver data |

## 2. Architecture

```
Raspberry Pi (all components run here)
├── AIS receiver: RTL-SDR / dAISy HAT + AIS-catcher
│        └── NMEA sentences over UDP/TCP (localhost)
├── Ingest worker (Python, separate process)
│        └── parses AIS → validates → upserts into PostgreSQL
├── PostgreSQL 15 + PostGIS 3
├── FastAPI (uvicorn) — REST + WebSocket
└── React SPA (static build) served by FastAPI (StaticFiles) or nginx
```

```
 Browser (React SPA)
   │ REST: vessels, snapshot, stats, heatmap, ref, window, health
   │ WS : /ws/live  (position pushes in live mode)
   ▼
 FastAPI ──► PostgreSQL/PostGIS
```

**Networking:** single host deployment — everything binds to the Pi; the browser
connects to `http://<pi-ip>:8000`. CORS enabled for dev (Vite on the PC hitting
the Pi API), locked down for production build.

## 3. Database Schema (PostGIS)

```sql
-- Reference: vessel classes (drives colors + filters)
CREATE TABLE vessel_classes (
  key        TEXT PRIMARY KEY,          -- cargo, tanker, passenger, fishing, tug, pleasure, other
  name       TEXT NOT NULL,
  color      TEXT NOT NULL,             -- hex, matches HTML palette
  sort_order INT  NOT NULL DEFAULT 0
);

CREATE TABLE vessels (
  mmsi         BIGINT PRIMARY KEY,
  imo          TEXT,
  callsign     TEXT,
  name         TEXT,
  flag_code    TEXT,                    -- ISO 3166-1 alpha-2
  flag_name    TEXT,
  class_key    TEXT REFERENCES vessel_classes(key),
  ship_type    INT,                     -- raw AIS ship type code
  length       REAL,                    -- metres
  beam         REAL,
  draught      REAL,
  destination  TEXT,
  eta_ts       TIMESTAMPTZ,
  first_seen   TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen    TIMESTAMPTZ
);

CREATE TABLE positions (
  id         BIGSERIAL PRIMARY KEY,
  mmsi       BIGINT NOT NULL REFERENCES vessels(mmsi),
  ts         TIMESTAMPTZ NOT NULL,
  geom       GEOMETRY(Point, 4326) NOT NULL,
  sog        REAL,                      -- knots
  cog        REAL,                      -- degrees
  heading    REAL,                      -- degrees
  nav_status TEXT,                      -- AIS navigational status text
  source     TEXT NOT NULL CHECK (source IN ('SAT','TER'))
);

CREATE INDEX idx_positions_ts      ON positions (ts);
CREATE INDEX idx_positions_mmsi_ts ON positions (mmsi, ts);
CREATE INDEX idx_positions_geom    ON positions USING GIST (geom);
-- optional: partial index for latest-24h hot path
CREATE INDEX idx_positions_recent  ON positions (ts DESC)
  WHERE ts > now() - interval '36 hours';

CREATE TABLE ports (
  id        SERIAL PRIMARY KEY,
  name      TEXT NOT NULL,
  unlocode  TEXT,
  country   TEXT,
  geom      GEOMETRY(Point, 4326) NOT NULL
);

CREATE TABLE ingest_stats (           -- feed health / uptime
  ts         TIMESTAMPTZ NOT NULL DEFAULT now(),
  source     TEXT NOT NULL,
  msg_count  INT  NOT NULL
);
CREATE INDEX idx_ingest_stats_ts ON ingest_stats (ts);
```

**Retention & Pi tuning**
- Retention: keep full-resolution for N days (config), then down-sample or
  `DELETE` via a nightly `cron` job — prevents SD-card bloat.
- `shared_buffers`, `effective_cache_size` tuned for Pi RAM; `autovacuum` on
  `positions` more aggressive.
- If ingest grows, migrate `positions` to TimescaleDB hypertable — schema is
  compatible, no app change.

## 4. Backend — FastAPI (Python)

Stack: `fastapi`, `uvicorn`, `sqlalchemy`/`psycopg3`, `geoalchemy2`, `pydantic`,
`websockets`, `python-dotenv`.

### 4.1 Reference & system

| Method | Path | Query/Params | Response |
|---|---|---|---|
| GET | `/api/health` | — | `{status, db:"ok", feed:{online, last_msg_age_s, msg_rate_min}, uptime_s}` |
| GET | `/api/ref` | — | `{classes:[{key,name,color}], nav_statuses:[...], layers:[{id,name,tile_url,attribution}]}` |
| GET | `/api/window` | — | `{min_ts, max_ts, resolution_hint_min}` — scrubber bounds |

### 4.2 Vessels & positions

| Method | Path | Query/Params | Response |
|---|---|---|---|
| GET | `/api/vessels` | `source` (SAT/TER), `class`, `q` (name/MMSI/IMO), `page`, `page_size`, `sort` | `{total, page, page_size, items:[VesselSummary], counts:{SAT,TER,classes:{...}}}` |
| GET | `/api/vessels/{mmsi}` | — | `VesselDetail` (all static fields + latest position + nav status) |
| GET | `/api/vessels/{mmsi}/track` | `start`, `end` | GeoJSON LineString + `{start,end,points}` (trail feature) |
| GET | `/api/snapshot` | `t` (ISO ts), `max_age_m` (default 30), optional `source`, `class`, `q` | GeoJSON FeatureCollection — one Point per vessel with properties `{mmsi, name, class_key, color, sog, cog, heading, nav_status, source, ts}`. Interpolated position = latest report ≤ `t` within `max_age_m` |
| GET | `/api/positions/latest` | same filters as snapshot | Alias of snapshot at `now()` — live map |

`/api/snapshot` powers BOTH the live map and the timeline replay; caching
(30–60 s for `t=now`, longer for fixed historical `t`) keeps the Pi cheap.

### 4.3 Stats & analytics

| Method | Path | Query/Params | Response |
|---|---|---|---|
| GET | `/api/stats` | `t`, optional `source`, `class`, `q` | `{total, by_source:{SAT,TER}, avg_sog, underway, counts_by_class}` — matches topbar/right-rail readouts |
| GET | `/api/heatmap` | `t`, `bbox` (W,S,E,N), `cell_deg` | `[{lat, lon, weight}]` via PostGIS `ST_SnapToGrid` aggregate → leaflet.heat |
| GET | `/api/ports` | optional `bbox` | GeoJSON Point FeatureCollection |

### 4.4 Ingest & streaming

| Method | Path | Body | Notes |
|---|---|---|---|
| POST | `/api/ingest` | `[{mmsi, ts, lat, lon, sog, cog, heading, nav_status, source, ...static fields when first seen}]` | API-key auth (header `X-Api-Key`); upserts vessel + appends position; updates `ingest_stats`; internal endpoint |
| WS | `/ws/live` | — | Pushes `{type:"position", mmsi, lat, lon, sog, cog, heading, source, ts}` to subscribers on each ingest batch (fan-out, ~1 s flush) |
| GET | `/api/stream` | SSE fallback | Same events as `/ws/live` over Server-Sent Events |

### 4.5 API conventions

- GeoJSON everywhere for map data (Leaflet-native).
- All timestamps ISO-8601 UTC.
- Errors: `{detail, code}` with proper HTTP status.
- `GET /api/ref` bundled to avoid 3 round-trips on boot.

## 5. Frontend — React + shadcn/ui + Leaflet

### 5.1 Stack

- Vite + React 18 + TypeScript
- Tailwind CSS + shadcn/ui
- `react-leaflet` v4/5 + `leaflet` + `leaflet.heat`
- TanStack Query (server state: snapshot, vessels, stats, heatmap, ref)
- Zustand (UI state: filters, selection, scrub time, playback, layer)
- WebSocket hook for `/ws/live`

### 5.2 Theme

Port the HTML palette 1:1 into shadcn CSS variables:

```css
--background:#0a0f16; --card:#0f1722; --card-foreground:#e9f1f8;
--border:#1d2a3a; --muted:#16212f; --muted-foreground:#a6b6c6;
--primary:#2fd4c4; --primary-foreground:#05201d;
--destructive:#ff5d5d; --radius:3px;
/* class colors kept in /api/ref */
```

Fonts: display = Bahnschrift stack, mono = Cascadia Mono stack (as in HTML).

### 5.3 Layout & component mapping

Same 3-pane workspace (topbar / left rail / map / right rail / timebar):

| HTML today | React implementation |
|---|---|
| `.topbar` | `Topbar.tsx` — `Button` (icon), `Badge` (feed status w/ pulsing dot), `Separator`, UTC clock |
| Map layers radio | `RadioGroup` (shadcn) — Nautical / Dark Satellite / Terrain |
| Feed source buttons | 2 `Button` with `aria-pressed` toggle + counts |
| Vessel class list | `Checkbox` rows + color dot + live count from `/api/vessels` counts |
| Display switches | `Switch` × 3 (heatmap / labels / ports) |
| `.map-wrap` | `MapView.tsx` — `<MapContainer renderer={L.canvas()}>` + layers |
| Map chips | Absolute-positioned `Badge`/`Card` overlays |
| Zoom buttons | Buttons wired to `map.zoomIn()/zoomOut()`; `zoomControl={false}` |
| `.vessel-list` | `ScrollArea` + `VesselRow` buttons (infinite scroll or load-more 50) |
| `.detail` | `DetailPanel.tsx` — grid of label/value cells, nav-status `Badge` |
| `.timebar` | `TimeBar.tsx` — play `Button`, `Select` (rate), `Slider` (scrub), stat chips |
| Mobile filters | `Sheet` (shadcn) |

### 5.4 Map layers (tile providers)

| Layer | Provider |
|---|---|
| Nautical | CartoDB Dark Matter base + OpenSeaMap seamark overlay |
| Dark Satellite | Esri World Imagery |
| Terrain | OpenTopoMap |

Configured in `/api/ref` (or frontend config) so tiles can be swapped without
rebuilding the SPA. **⚠ Open decision:** if the Pi is offline/at sea, tile
sources must be replaced with locally-cached tiles or a vector base — resolve
before field deployment (see Open Items).

### 5.5 Realtime + replay flow

- **Live mode** (`t = now`): initial load from `/api/positions/latest`; then
  `/ws/live` pushes update markers in place (no full refetch).
- **Replay mode**: scrubber/play → debounced `useQuery(['snapshot', t])` →
  GeoJSON layer re-rendered; playback loop advances `t` at `rate`.
- **Filters/search**: pushed as query params to snapshot/vessels/stats so the
  Pi does the filtering (fast on PostGIS indexes).
- **Selection**: nearest-marker hit test client-side (marker positions already
  in memory); detail panel fetches `/api/vessels/{mmsi}`.
- **Heatmap**: `leaflet.heat` fed by `/api/heatmap`, throttled on zoom/pan/scrub.

### 5.6 Project structure (proposed)

```
NavisenseV2/
├── backend/
│   ├── app/
│   │   ├── main.py            # FastAPI app, CORS, static serving
│   │   ├── config.py          # env: DB DSN, API key, retention, tile URLs
│   │   ├── db.py              # engine/session
│   │   ├── models.py          # SQLAlchemy models
│   │   ├── schemas.py         # Pydantic response/request models
│   │   ├── routers/
│   │   │   ├── health.py      # /api/health, /api/ref, /api/window
│   │   │   ├── vessels.py     # vessels, detail, track
│   │   │   ├── positions.py   # snapshot, latest
│   │   │   ├── stats.py       # stats, heatmap, ports
│   │   │   ├── ingest.py      # POST /api/ingest (api-key)
│   │   │   └── ws.py          # /ws/live, /api/stream
│   │   └── geo.py             # PostGIS query helpers, interpolation
│   ├── ingest_worker/
│   │   ├── main.py            # connects AIS-catcher → parse → POST /api/ingest
│   │   └── ais_parser.py
│   ├── requirements.txt
│   └── .env.example
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── Topbar.tsx
│   │   │   ├── FilterRail.tsx      # layers, sources, classes, switches
│   │   │   ├── MapView.tsx         # react-leaflet + vessel/heat/port layers
│   │   │   ├── VesselList.tsx
│   │   │   ├── DetailPanel.tsx
│   │   │   ├── TimeBar.tsx
│   │   │   └── ui/                 # shadcn components
│   │   ├── lib/                    # api client, ws hook, utils
│   │   ├── store/                  # zustand stores
│   │   └── hooks/
│   └── ...
└── deploy/
    ├── postgres-setup.sql
    ├── navisense.service         # systemd: uvicorn
    ├── ingest.service            # systemd: ingest worker
    └── README.md                 # Pi provisioning (apt, postgis, ais-catcher)
```

## 6. Build Phases

1. **Pi foundation** — provision Pi: PostgreSQL + PostGIS, schema DDL, seed
   `vessel_classes`/`ports`, AIS-catcher install + config, ingest worker
   parsing and writing real messages.
2. **API layer** — all REST + WS endpoints, unit-tested with real ingest data;
   `/api/snapshot` interpolation logic validated.
3. **Frontend port** — React/shadcn/Leaflet UI mirroring the HTML layout,
   theme, and interactions, wired to the real endpoints.
4. **Realtime + replay polish** — WebSocket live mode, scrubber replay at
   rate, heatmap, mobile responsive, load testing with realistic vessel counts
   (500+ markers) to confirm `L.canvas` performance.

## 7. Decisions (locked)

| # | Decision | Choice |
|---|---|---|
| 1 | Data source | Real receiver on Raspberry Pi (RTL-SDR/dAISy + AIS-catcher) |
| 2 | Map library | Leaflet + react-leaflet |
| 3 | Database | PostgreSQL + PostGIS on the Pi |
| 4 | Timeline | Replays real stored historical data (snapshot API) |
| 5 | Frontend | React + shadcn/ui + TanStack Query + Zustand |
| 6 | Backend | Python FastAPI + WebSocket |

## 8. Open Items (resolve before/at deploy)

1. **Tile source offline behavior** — Pi without internet needs cached/vector
   tiles or a self-hosted tile server.
2. **Retention policy** — how many days of full-resolution positions to keep
   (SD card budget).
3. **Auth for the SPA** — LAN-only assumed; if exposed beyond LAN, add simple
   JWT/session auth.
4. **Receiver hardware** — confirm dAISy HAT vs RTL-SDR dongle before writing
   the ingest worker's transport config.
5. **Vessel count expectations** — bounds heatmap aggregation and snapshot
   payload sizing.
