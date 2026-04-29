# Developer Guide — Zambia GeoHub AI Assistant

This document covers everything a developer needs to understand, run, modify, and extend the system.

---

## Local Setup (Quick Start)

```bash
git clone https://github.com/myna23/-geohub-ai.git
cd -geohub-ai
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then fill in ANTHROPIC_API_KEY
streamlit run app.py
```

---

## Module Responsibilities

| File | Responsibility |
|---|---|
| `app.py` | Streamlit UI, session state, query orchestration, all rendering |
| `hub/client.py` | ArcGIS catalog loading, dataset ranking, feature fetching |
| `ai/claude_client.py` | Anthropic API wrapper — `ask()`, `stream()`, `stream_with_history()` |
| `ai/prompts.py` | Builds system + user prompts for chatbot, summarizer, and reporter |
| `utils/geo_utils.py` | Haversine, polygon centroid, ray-casting, GeoJSON utilities |
| `reports/builder.py` | `.docx` Word document generation |
| `data/*.json` | Offline GeoJSON fallbacks used when ArcGIS server is unavailable |

---

## Session State Keys (app.py)

These keys are stored in `st.session_state` and persist across reruns:

| Key | Type | Purpose |
|---|---|---|
| `messages` | `list[dict]` | Full chat history — `{role, content, intent, geojson, ...}` |
| `draw_bbox` | `dict` or `None` | `{min_lat, max_lat, min_lon, max_lon, district, province, measurement}` |
| `draw_map_version` | `int` | Incremented to force a fresh blank Folium map after Clear |
| `_bbox_cleared` | `bool` | One-shot flag to skip bbox re-processing after clear |
| `_draw_counts` | `dict` | Feature counts from "Count features" button |
| `uploaded_doc_text` | `str` | Extracted text from PDF/Word/TXT upload |
| `uploaded_doc_name` | `str` | Filename of uploaded document |
| `uploaded_img_b64` | `str` | Base64-encoded map image |
| `uploaded_img_mime` | `str` | MIME type of image (`image/png` or `image/jpeg`) |
| `uploaded_img_name` | `str` | Filename of uploaded image |
| `token_set_date` | `str` | ISO date when ArcGIS token was last set (for 14-day countdown) |
| `stop_streaming` | `bool` | Set to `True` when user clicks the stop button |
| `is_generating` | `bool` | `True` while Claude is streaming |
| `_scroll_to_bottom` | `bool` | Triggers JS scroll after answer is rendered |
| `edit_idx` | `int` or `None` | Index of message being edited (re-run mode) |

---

## Adding a New Dataset

### Step 1 — Add to seed catalog (`hub/client.py`)

```python
# In _SEED_CATALOG list:
{
    "id": "your_arcgis_item_id",      # 32-char hex GUID from ArcGIS Online
    "name": "My Dataset Name",
    "description": "What this dataset contains and why it matters.",
    "url": "https://services.arcgis.com/.../FeatureServer/0",
    "tags": ["tag1", "zambia", "zmb"],
    "fields": [],                      # leave empty; fetched live
    "geometry_type": "Point",          # Point / Polyline / Polygon
    "extent": {},
    "modified": "",
},
```

### Step 2 — Add keyword boosts (optional)

In `_SUBJECT_BOOST` (class dict, used by `_rank()`) and `_SUBJECT_BOOST_MODULE` (module-level, used by `_find_static()`):

```python
"my keyword": "URL_fragment_of_my_dataset",
```

The fragment just needs to match a substring of the dataset URL.

### Step 3 — Add offline fallback (optional but recommended)

1. Fetch sample GeoJSON: `GET {url}/query?where=1=1&outFields=*&resultRecordCount=500&f=geojson`
2. Save to `data/my_dataset.json`
3. Add to `_STATIC_MAP`:
   ```python
   "URL_fragment": "my_dataset.json",
   ```

---

## Adding a New AI Feature

### Step 1 — Detect the intent

In `detect_intent()` near the top of `app.py`, add a new branch:

```python
if text.startswith("compare ") or "vs" in text:
    return "comparison"
```

### Step 2 — Add prompt functions (`ai/prompts.py`)

```python
def my_feature_system_prompt() -> str:
    return "You are..."

def my_feature_prompt(data: dict) -> str:
    return f"Here is the data: {data}\n\nDo X."
```

### Step 3 — Add the rendering block (`app.py`)

In the `process_question()` function, add an `elif intent == "my_feature":` block following the pattern of the existing `summary` or `report` blocks.

---

## Prompt Engineering Rules

All three features share these hard constraints (enforced in `ai/prompts.py`):

1. **Never answer from world knowledge** — only from provided GeoJSON records
2. **Never invent dataset names** — only reference datasets in the prompt
3. **When `_note` placeholder is in data** — only state server status, nothing else
4. **Always cite the dataset used** — with a Hub search link if available
5. **No follow-up suggestions** — the UI handles these separately

When modifying prompts, maintain these rules. They prevent hallucination which is critical for a data-grounded tool.

---

## ArcGIS REST API Patterns

The app talks directly to ArcGIS FeatureServer REST endpoints. Key patterns:

**Basic feature query:**
```
GET {service_url}/query
  ?where=District='Lusaka'
  &outFields=*
  &resultRecordCount=200
  &f=geojson
  &token={ARCGIS_TOKEN}
```

**Spatial (bbox) query:**
```
GET {service_url}/query
  ?geometry=min_lon,min_lat,max_lon,max_lat
  &geometryType=esriGeometryEnvelope
  &spatialRel=esriSpatialRelIntersects
  &outFields=*
  &resultRecordCount=200
  &f=geojson
  &token={ARCGIS_TOKEN}
```

**Count only (no features):**
```
GET {service_url}/query
  ?where=Province='Lusaka'
  &returnCountOnly=true
  &f=json
```

Always add `Referer: https://zmb-geowb.hub.arcgis.com` header for token-authenticated requests.

---

## Dataset Ranking Algorithm

`HubClient._rank(query, catalog)` scores datasets like this:

```
score = 0
for each word in query (excluding stop words):
    if word in (name + description + tags):  score += 2
    if word partially matches any token:      score += 0.5

score += subject_boost.get(url, 0)   # keyword → dataset boost (+30 typical)
score += poi_boost                    # if POI type keyword detected (+20)
score += province_market_boost        # if province + market terms (+100, overrides)
```

Datasets with `score > 0` are returned, sorted descending. Top 5 are used.

---

## Geometry Utilities (`utils/geo_utils.py`)

| Function | Purpose |
|---|---|
| `haversine_km(lat1, lon1, lat2, lon2)` | Great-circle distance in km |
| `features_within_km(features, lat, lon, km)` | Filter features to radius, return with distances |
| `polygon_centroid(geometry)` | Centroid of a GeoJSON Polygon/MultiPolygon |
| `_point_in_polygon(lat, lon, ring)` | Ray-casting point-in-polygon test |
| `geojson_to_sample_rows(geojson, n)` | Flatten GeoJSON features to list of property dicts |
| `summarize_geojson(geojson)` | Compute numeric stats, feature count, geometry type |
| `assign_districts(features, districts_geojson)` | Spatial join features to district polygons |

---

## Known Issues / Tech Debt

| Issue | Location | Priority |
|---|---|---|
| `app.py` is ~3,000 lines — monolithic | `app.py` | High |
| `schools.json` only covers 3 provinces | `data/schools.json` | Medium |
| ArcGIS token expires every ~14 days | `.env` / Streamlit secrets | High (permanent API Key pending) |
| No user authentication | `app.py` | Medium |
| No test suite | — | High |
| Streamlit `st.cache_resource` version bumps manual | `app.py:450` | Low |

---

## Environment Variables Reference

| Variable | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | **Required.** Anthropic API key |
| `ARCGIS_TOKEN` | `""` | ArcGIS URL token or API Key. Without it, only public datasets work |
| `CLAUDE_MODEL` | `claude-sonnet-4-6` | Any valid Anthropic model ID |
| `MAX_FEATURES` | `200` | Max features fetched per ArcGIS query |
| `HUB_BASE_URL` | `https://zmb-geowb.hub.arcgis.com` | GeoHub base URL |

To change the Claude model — update this env var only. No code changes needed.

---

## Deployment

### Streamlit Cloud (current)
- Connect GitHub repo at [share.streamlit.io](https://share.streamlit.io)
- Set secrets (same as `.env` variables)
- Every push to `main` auto-deploys

### Self-hosted (Linux server)
```bash
pip install -r requirements.txt
streamlit run app.py --server.port 8501 --server.address 0.0.0.0
```
Use nginx as a reverse proxy for HTTPS. Use `systemd` or `supervisord` to keep the process running.

### Docker (optional path)
A `Dockerfile` can be added:
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
EXPOSE 8501
CMD ["streamlit", "run", "app.py", "--server.address", "0.0.0.0"]
```

---

## Embedding in the GeoHub

The app accepts URL parameters for Hub iframe embedding:

```
https://your-app.streamlit.app/?dataset_url={url}&dataset_name={name}&embed=true
```

Embed in any Hub page:
```html
<iframe 
  src="https://your-app.streamlit.app/?embed=true"
  width="100%" height="900px" frameborder="0">
</iframe>
```

The app auto-detects the `dataset_url` param and pre-loads that dataset for contextual Q&A.
