# Zambia GeoHub AI Assistant

An AI-powered geospatial data assistant built for the [Zambia GeoHub](https://zmb-geowb.hub.arcgis.com). Users can ask plain-English questions about Zambia's health, education, infrastructure, and environment data and receive answers grounded in live ArcGIS datasets — with interactive maps, data tables, and downloadable reports.

**Live app:** `https://4l97k96qxsp3wet6nafhui.streamlit.app`

---

## Features

- **AI Chatbot** — Ask any question about Zambia's geospatial data; answers cite actual records, districts, and values
- **Dataset Summarizer** — Generates structured summaries of any GeoHub dataset
- **Report Generator** — Produces formal analytical reports downloadable as `.docx`
- **Interactive Maps** — Folium maps rendered for every response
- **Draw Tool** — Draw a rectangle on the map to spatially filter all queries to that area
- **Coordinate Input** — Type `lat, lon` coordinates directly in the chat (e.g. `-15.416, 28.283`)
- **Radius Queries** — Ask "schools within 10km of Lusaka" — haversine distance filtering applied
- **Document Upload** — Attach PDF, Word, or TXT files; AI reads them alongside GeoHub data
- **Map Image Upload** — Attach a map screenshot; AI analyses it visually via vision API
- **Offline Fallback** — Pre-loaded static data (health, schools, POIs, settlements, etc.) serves answers when the live server is unavailable

---

## Architecture

```
app.py                  ← Main Streamlit application (~3,000 lines)
├── ai/
│   ├── model_client.py   ← AI API wrapper (streaming, retry)
│   └── prompts.py         ← System and user prompt builders for all 3 features
├── hub/
│   ├── client.py          ← HubClient: catalog search, dataset ranking, ArcGIS REST fetch
│   └── arcgis_client.py   ← Low-level ArcGIS token helpers
├── utils/
│   └── geo_utils.py       ← Haversine, polygon centroid, ray-casting, GeoJSON utilities
├── reports/
│   └── builder.py         ← .docx report generation (python-docx)
├── data/
│   └── *.json             ← Offline GeoJSON fallback datasets
└── components/
    └── chat_input/        ← Custom HTML component (not active on Cloud)
```

### Query Flow

```
User question
    → detect_intent()          # chat / summary / report
    → _extract_location()      # district or province name
    → _extract_radius_km()     # e.g. "5km" → 5.0
    → _extract_coordinates()   # typed lat/lon pair
    → hub.search_datasets()    # rank catalog by keyword relevance
    → hub.fetch_geojson()      # live ArcGIS REST API call
        ↓ (if server down)
    → static JSON fallback     # filter by bbox / district / radius
    → chatbot_user_prompt()    # build AI prompt with data
    → ai_client.stream_with_history()  # streamed answer
    → st_folium map + dataframe table + download buttons
```

---

## Setup — Local Development

### Prerequisites

- Python 3.9+
- Git

### 1. Clone and install

```bash
git clone https://github.com/myna23/-geohub-ai.git
cd -geohub-ai
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and fill in:

| Variable | Required | Description |
|---|---|---|
| `OPENAI_API_KEY` | Yes | Get from [console.openai.com](https://console.openai.com) |
| `ARCGIS_TOKEN` | Recommended | ArcGIS URL token — enables all 74 GeoHub datasets. See below. |
| `AI_MODEL` | No | Default: `gpt-4o` |
| `MAX_FEATURES` | No | Default: `200` |
| `HUB_BASE_URL` | No | Default: `https://zmb-geowb.hub.arcgis.com` |

### 3. Run

```bash
streamlit run app.py
```

Open `http://localhost:8501`

---

## ArcGIS Token

Without a token, only public datasets are accessible (~20 datasets). With a token, all 74+ GeoHub datasets are available including health, education, and administrative layers.

**Getting a URL token (expires ~14 days):**
1. Log into the Zambia GeoHub as an admin
2. Navigate to any FeatureServer dataset URL in the browser
3. The URL will contain `?token=...` — copy that value
4. Paste into the app sidebar → Token field (it auto-extracts from a full URL)

**Getting a permanent API Key (recommended for production):**
1. Log into ArcGIS Online as an admin
2. Go to Content → My Content → New Item → Developer Credentials
3. Select "Private application with selected privileges and access"
4. Grant: Data access (Read), Portal (Read)
5. Copy the generated API Key — store in `ARCGIS_TOKEN`

---

## Deployment — Streamlit Cloud

The app is deployed via [Streamlit Community Cloud](https://share.streamlit.io) connected to this GitHub repository. Every push to `main` auto-deploys.

**Secrets** (set in Streamlit Cloud dashboard → App Settings → Secrets):

```toml
OPENAI_API_KEY = "sk-ant-..."
ARCGIS_TOKEN = "..."
OPENAI_API_KEY = "sk-..."
MAX_FEATURES = "200"
HUB_BASE_URL = "https://zmb-geowb.hub.arcgis.com"
```

---

## Offline Fallback Data (`data/`)

Pre-loaded GeoJSON datasets used when the live ArcGIS server is unavailable:

| File | Contents | Records |
|---|---|---|
| `health_facilities.json` | Health facilities across Zambia | 500 |
| `schools.json` | Schools (Copperbelt, Eastern, Muchinga) | 500 |
| `settlements.json` | Village and town points | 300 |
| `poi_all.json` | Points of interest (all types) | 200 |
| `poi_commercial.json` | Commercial POIs (markets, shops) | 200 |
| `poi_religion.json` | Religious sites | 200 |
| `poi_farm.json` | Farming locations | 200 |
| `districts.json` | District boundary polygons | 116 |
| `flood_prone.json` | Flood-prone district polygons | — |
| `risk_layers.json` | WASH/socioeconomic risk by district | — |
| `roads.json` | Major road segments | — |
| `rivers.json` | River polylines | — |
| `wetlands.json` | Wetland/lake polygons | — |
| `forests.json` | Forest areas | — |
| `biodiversity.json` | Biodiversity polygons | — |
| `biodiversity_points.json` | Biodiversity points | — |
| `lusaka_risk.json` | Lusaka township risk data | — |

> **Note:** `schools.json` currently only covers Copperbelt, Eastern, and Muchinga provinces. Western, Northern, Southern, Central, and Luapula provinces require live API access.

---

## Key Design Decisions

### Dataset Ranking (`hub/client.py → _rank()`)
The catalog is ranked by keyword matching + subject boosts. Specific boosts:
- `"school"` → GRID3 Schools dataset (+30)
- `"hospital"` / `"health"` → GRID3 Health Facilities (+30)
- `"marketplace"` → Zambia Marketplaces (+30)
- Generic commerce terms (`"market"`, `"shop"`) → NOT boosted (routes to POI with Province/District fields)
- Province/district + commerce query → POI gets +100, Marketplaces demoted to 0

### Spatial Filtering Priority
1. **Drawn bbox** (map draw tool) — overrides location name detection
2. **Typed coordinates** — treated as point with configurable radius
3. **Named location** (district/province extracted from question)
4. **Radius filter** (haversine applied after fetch)
5. **No filter** (returns sample of full dataset)

### Static Fallback Chain
1. Bbox coordinate filter
2. District name filter (from bbox metadata)
3. Province name filter
4. Countrywide (for radius queries — haversine applied on full set)

### Prompt Rules (enforced in `ai/prompts.py`)
- Never answer from general world knowledge
- Never invent statistics, field names, or dataset names
- When `_note` placeholder present → only state server status, nothing else
- Always cite dataset name + Hub search link

---

## Extending the System

### Add a new dataset to the catalog
In `hub/client.py`, add to `_SEED_CATALOG`:
```python
{"id": "<arcgis_item_id>", "name": "My New Dataset",
 "description": "...",
 "url": "https://services.arcgis.com/.../FeatureServer/0",
 "tags": ["tag1", "tag2", "zambia", "zmb"],
 "fields": [], "geometry_type": "Point", "extent": {}, "modified": ""},
```

Then add keyword boosts in `_SUBJECT_BOOST` and `_SUBJECT_BOOST_MODULE` if needed.

### Add offline static data for a dataset
1. Fetch GeoJSON from the FeatureServer and save to `data/mydata.json`
2. Add to `_STATIC_MAP` in `hub/client.py`:
   ```python
   "My_Dataset_URL_Fragment": "mydata.json",
   ```

### Change the AI model
No code changes needed. Update `AI_MODEL` in `.env` (local) or Streamlit Cloud secrets (production).

### Add a new AI feature (beyond chat/summary/report)
1. Add a new intent keyword in `detect_intent()` in `app.py`
2. Add prompt functions in `ai/prompts.py`
3. Add the rendering block in the `process_question()` function

---

## Known Limitations

- **`app.py` is monolithic** — ~3,000 lines. Refactoring into `ui/`, `logic/` sub-modules is a recommended next step for maintainability.
- **ArcGIS token expires every ~14 days** — a permanent OAuth API Key from the GeoHub admin is needed for production (in progress).
- **Offline schools data** only covers 3 of 10 provinces — expand by fetching and saving data for remaining provinces once the live server is stable.
- **Streamlit Cloud free tier** — app may sleep after inactivity. Consider upgrading to a paid tier or migrating to a dedicated server for production.
- **No user authentication** — all users share the same app instance and session state is per-browser-tab only.

---

## Tech Stack

| Library | Version | Purpose |
|---|---|---|
| `streamlit` | ≥1.35 | Web app framework |
| `openai` | ≥0.40 | AI API |
| `folium` + `streamlit-folium` | ≥0.17 | Interactive maps |
| `requests` | ≥2.32 | ArcGIS REST API calls |
| `python-docx` | ≥1.1 | Report generation |
| `pypdf` | ≥4.0 | PDF text extraction |
| `pandas` | ≥2.0 | Data table display |
| `python-dotenv` | ≥1.0 | Environment variable loading |

---

## License

To be determined by the World Bank / Zambia GeoHub project team.

---

*Built for the Zambia GeoHub — World Bank Zambia Data Platform*
