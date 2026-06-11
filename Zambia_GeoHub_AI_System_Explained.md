# Zambia GeoHub AI Assistant — Full System Explanation
### From Architecture to Plain Language

---

## PART 1: THE BIG PICTURE (Plain Language)

Think of this system like a very smart research assistant who sits between you and Zambia's national map database. When you ask a question like *"which district has the most schools?"*, the assistant does six things automatically:

1. **Understands what you're asking** — it figures out if you want a quick answer, a full report, or a dataset summary
2. **Finds the right data** — it searches the Zambia GeoHub (the government's online map library) for the most relevant dataset
3. **Fetches real records** — it pulls live data from the GeoHub's API (think of it like a government filing cabinet that you can query remotely)
4. **Reads the data** — it loads the actual feature records (school locations, district names, coordinates)
5. **Asks AI (the AI)** — it sends the question + the data to AI's AI for analysis
6. **Shows you the answer** — text answer + interactive map + data table, all on one screen

The whole thing runs on a website built with **Streamlit** (a Python tool for building data apps quickly) and is hosted on **Streamlit Cloud** (so anyone with the link can use it without installing anything).

---

## PART 2: THE TECHNICAL ARCHITECTURE

### System Stack

```
User's Browser
     │
     ▼
Streamlit App  (app.py — Python web app)
     │
     ├── hub/client.py          ← talks to Zambia GeoHub & ArcGIS API
     ├── ai/model_client.py    ← talks to AI API
     ├── ai/prompts.py          ← builds the instructions sent to AI
     ├── utils/geo_utils.py     ← geometry tools (distances, polygons)
     └── data/*.json            ← offline fallback datasets (500 facilities etc.)
```

### External Services Used

| Service | Purpose | How Accessed |
|---|---|---|
| **Zambia GeoHub** (zmb-geowb.hub.arcgis.com) | Source of all Zambia spatial datasets | ArcGIS REST API (HTTPS) |
| **ArcGIS FeatureServer** | Serves the actual feature records (schools, health, POIs) | REST API — `/FeatureServer/0/query` |
| **AI API** | The AI brain that reads data and writes answers | Python SDK (`openai` library) |
| **Streamlit Cloud** | Hosts the web app publicly | GitHub → auto-deploys on push |

---

## PART 3: WHAT HAPPENS WHEN YOU ASK A QUESTION

### Step-by-Step Query Flow

```
You type: "How many schools are in Chongwe District?"
                         │
                         ▼
          ┌──────────────────────────────┐
          │  1. Intent Detection         │
          │  detect_intent(question)     │
          │  → "chat" (not report/summary)│
          └──────────────┬───────────────┘
                         │
                         ▼
          ┌──────────────────────────────┐
          │  2. Location Extraction      │
          │  _extract_location(question) │
          │  → location = "Chongwe"      │
          │  → loc_type = "district"     │
          └──────────────┬───────────────┘
                         │
                         ▼
          ┌──────────────────────────────┐
          │  3. Dataset Search           │
          │  hub.search_datasets(q)      │
          │  → ranks catalog by keywords │
          │  → "school" boosts GRID3     │
          │     Schools dataset (+30pts) │
          └──────────────┬───────────────┘
                         │
                         ▼
          ┌──────────────────────────────┐
          │  4. Live Data Fetch          │
          │  hub.fetch_geojson(url)      │
          │  → calls ArcGIS REST API     │
          │  → WHERE District='Chongwe' │
          │  → returns up to 200 records │
          └──────────────┬───────────────┘
                         │ (if server down)
                         ▼
          ┌──────────────────────────────┐
          │  4b. Static Fallback         │
          │  loads data/schools.json     │
          │  filters by district name    │
          └──────────────┬───────────────┘
                         │
                         ▼
          ┌──────────────────────────────┐
          │  5. Prompt Building          │
          │  chatbot_user_prompt(...)    │
          │  → combines: question +      │
          │    dataset info + records +  │
          │    location note + bbox note │
          └──────────────┬───────────────┘
                         │
                         ▼
          ┌──────────────────────────────┐
          │  6. AI API Call          │
          │  ai_client.stream_with_history()│
          │  → system prompt (rules) +   │
          │    conversation history +    │
          │    user prompt with data     │
          │  → streams answer token by   │
          │    token to the screen       │
          └──────────────┬───────────────┘
                         │
                         ▼
          ┌──────────────────────────────┐
          │  7. Display                  │
          │  → answer text (markdown)    │
          │  → interactive Folium map    │
          │  → data table (st.dataframe) │
          │  → download buttons          │
          └──────────────────────────────┘
```

---

## PART 4: THE THREE AI FEATURES

### Feature 1 — AI Chatbot (default)
- **What it does:** Answers any question about Zambia's geography, facilities, infrastructure
- **How:** Finds relevant dataset → fetches records → passes to AI with instructions
- **AI's rules:** Only answer from provided data, cite dataset names, never invent statistics, include map links

### Feature 2 — Dataset Summarizer
- **Triggered by:** Questions starting with "summarise" or "summarize"
- **What it does:** Loads a full dataset and produces a structured summary (overview, key fields, insights, use cases)
- **Output:** Text summary + download as `.txt` file

### Feature 3 — Report Generator
- **Triggered by:** Questions starting with "generate a report on" or "report on"
- **What it does:** Produces a formal analytical report with executive summary, statistics, observations, recommendations
- **Output:** Streaming text report + download as `.docx` Word document

---

## PART 5: THE DATA SOURCES

### Live Data — ArcGIS FeatureServer API

The Zambia GeoHub stores datasets on ArcGIS Online servers. Each dataset is accessible via a URL like:

```
https://services3.arcgis.com/.../FeatureServer/0/query
  ?where=District='Lusaka'
  &outFields=*
  &resultRecordCount=200
  &f=geojson
  &token=<ARCGIS_TOKEN>
```

This returns a GeoJSON response — a standard geographic data format — with feature records containing coordinates and attributes (name, type, district, etc.)

### The Seed Catalog
Because the live Hub API sometimes returns incomplete results, we maintain a hand-curated list of ~80 important datasets in `hub/client.py` called `_SEED_CATALOG`. This includes:
- Health facilities, schools, settlements, POIs
- Roads, rivers, forests, wetlands
- Flood risk, poverty indices, population data
- Mines, power infrastructure, railways
- Marketplaces, biodiversity data

### Offline Fallback Data (`data/*.json`)
When the live server is unavailable, the app loads pre-saved GeoJSON files:
- `health_facilities.json` — 500 health facilities across Zambia
- `schools.json` — school locations
- `settlements.json` — village/town points
- `poi_all.json`, `poi_commercial.json` — points of interest
- `districts.json` — district boundary polygons (used for map rendering and location detection)
- Plus rivers, forests, flood zones, roads, etc.

---

## PART 6: DATASET RANKING — HOW THE RIGHT DATASET IS FOUND

When you ask a question, the system scores every dataset in the catalog using this formula:

```
score = keyword_matches × 2
      + partial_matches × 0.5
      + subject_boost (0 or +30 from keyword→dataset map)
      + POI_boost (if relevant POI type detected)
      + province/district override (+100 to override wrong dataset)
```

**Subject Boost Examples:**
- "school" → GRID3 Schools dataset +30
- "hospital" → GRID3 Health Facilities +30
- "flood" → Flood Prone Districts +30
- "marketplace" → Zambia Marketplaces +30
- "market" / "shop" → NOT boosted (uses POI dataset instead, which has Province/District fields)

The top 5 ranked datasets are fetched and the best one with actual data "wins".

---

## PART 7: THE MAP DRAW TOOL

The draw tool uses **Folium** (a Python library that renders Leaflet.js maps) with the `folium-draw` plugin. When you draw a rectangle on the map:

1. The coordinates (min_lat, max_lat, min_lon, max_lon) are saved to `st.session_state["draw_bbox"]`
2. Every subsequent API call adds a **spatial filter**:
   ```
   ?geometry=min_lon,min_lat,max_lon,max_lat
   &geometryType=esriGeometryEnvelope
   &spatialRel=esriSpatialRelIntersects
   ```
3. The app identifies which district/province the center point falls in using **ray-casting** (a geometric algorithm that checks if a point is inside a polygon)
4. Measurements (area in km², perimeter in km) are calculated using the **Haversine formula** — the correct way to measure distances on a curved Earth

---

## PART 8: COORDINATE INPUT FEATURE

You can now type coordinates directly in the chat instead of drawing on the map:

**Formats accepted:**
- `-15.416, 28.283` (negative lat = south of equator)
- `15.4S, 28.3E` (hemisphere letters)
- `lat -15.4 lon 28.3` (labelled)

**What happens:**
1. The system detects the coordinate pattern using regex (text pattern matching)
2. Validates it falls within Zambia's bounds (lat -18 to -8, lon 21 to 34)
3. Builds a search box around the point (default 5 km radius, or whatever radius you specify)
4. Queries the API exactly like a drawn area
5. Uses ray-casting to identify the district/province automatically

---

## PART 9: DOCUMENT AND IMAGE UPLOAD

### Document Upload (PDF, Word, TXT)
When you attach a document:
1. The file is read into memory by the browser and sent to Python
2. Text is extracted (pypdf for PDFs, python-docx for Word, direct read for TXT)
3. The first 6,000 characters are injected into the AI prompt as extra context
4. AI reads both the document AND the GeoHub data and answers from both

### Map Image Upload (PNG, JPG)
When you attach a map image:
1. The image is encoded to base64 (a text-safe format for binary files)
2. It's sent to AI as a **vision message** — AI can actually see the image
3. AI describes what geographic features it sees and connects them to GeoHub data
4. Useful for: satellite images, printed maps, spatial analysis screenshots

---

## PART 10: THE AUTHENTICATION (API TOKEN)

The ArcGIS token is required to access private/restricted datasets on the GeoHub. Without it, only public datasets are accessible.

**Two types:**
- **URL Token** (current): Generated when logged into ArcGIS Online. Copy from browser URL → paste into the app sidebar. Expires ~14 days.
- **API Key** (permanent, needs admin): Generated by a GeoHub admin (like Walker). Never expires. Waiting on admin access.

**How to get a fresh token:**
1. Go to any FeatureServer URL in your browser while logged in as admin
2. The URL will contain `?token=eyJ...` or `&token=eyJ...`
3. Copy the full URL and paste into the token field — the app auto-extracts the token

The app shows a countdown: how many days until the token expires, with a warning at 3 days and an error when expired.

---

## PART 11: HOW THE AI IS INSTRUCTED

The system prompt (the rules AI must follow) is defined in `ai/prompts.py`. Key rules:

- **Only use provided data** — never answer from general world knowledge
- **Be specific** — name actual places, districts, exact numbers from the records
- **Cite sources** — always name the dataset used, include a Hub link if available
- **No hallucination** — if data is unavailable, say so honestly
- **No follow-up suggestions** — the UI handles suggested follow-ups separately
- **Count by province/district** — when asked about distribution, aggregate by geographic field

The user prompt contains:
- The actual question
- Matched dataset names + descriptions + Hub links
- Sample records (up to 15, in JSON format)
- Pre-aggregated counts by Province, District, Type
- Any bbox/coordinate note from the draw tool
- Cross-dataset context (flood risk, settlements, roads) when relevant
- Uploaded document text (if any)

---

## PART 12: DEPLOYMENT

**Platform:** Streamlit Community Cloud (free tier)
**URL:** `4l97k96qxsp3wet6nafhui.streamlit.app`
**Deployment:** Automatic — every `git push` to the `main` branch on GitHub triggers a redeploy within 2–3 minutes

**Environment variables** (stored in Streamlit Cloud secrets, not in code):
- `OPENAI_API_KEY` — access to AI
- `ARCGIS_TOKEN` — access to GeoHub private datasets
- `AI_MODEL` — which AI version to use (currently gpt-4o)
- `HUB_BASE_URL` — the GeoHub base URL
- `MAX_FEATURES` — cap on features fetched per query (200)

---

## PART 13: SUMMARY FOR NON-TECHNICAL AUDIENCES

**What is this?**
A web tool that lets anyone — planners, NGO workers, government officials — ask questions about Zambia's geography in plain English and get smart, data-backed answers instantly.

**Where does the data come from?**
The Zambia GeoHub — the government's official open spatial data platform managed by the World Bank, containing 74+ datasets on health, education, infrastructure, environment, and more.

**Is the AI making things up?**
No. The AI (AI) is strictly instructed to only use data provided to it from the GeoHub. If data is unavailable, it says so. It cannot access the internet or make up statistics.

**What happens when the server is down?**
The system falls back to pre-loaded offline data (health facilities, schools, settlements, etc.) and filters it to the drawn area or district. Users see a notice explaining this.

**Who built it and how?**
Built in Python using Streamlit (web app), Folium (maps), and the AI API (AI). Deployed on Streamlit Cloud and connected to GitHub for automatic updates.

**What could it do in the future?**
- Permanent API key from admin (access to all 74 datasets, including private ones)
- Population estimates within drawn areas
- Distance calculations to nearest road/facility
- Excel/CSV file upload for custom data overlays
- Multi-language support (Bemba, Nyanja)
- Integration into the actual GeoHub website as an embedded widget

---

*Document prepared for the Zambia GeoHub AI Assistant project.*
*Last updated: April 2026*
