"""
Zambia GeoHub data client.

Strategy:
  - Dynamically queries ArcGIS Online for ALL datasets tagged 'zmb' (public, no API key needed).
    Admin confirmed: "All data is tagged zmb so you can do an organisation query for that."
  - Results are cached at startup so search is fast.
  - New datasets added to the Hub with the zmb tag are automatically picked up.

No API key required — all zmb-tagged datasets used here are publicly accessible.
"""

import json
import os
import requests
from dotenv import load_dotenv

# ArcGIS API for Python — optional parallel fetch path
try:
    from hub import arcgis_client as _agis
    _ARCGIS_AVAILABLE = _agis.is_available()
except Exception:
    _agis = None  # type: ignore
    _ARCGIS_AVAILABLE = False

load_dotenv()

MAX_FEATURES = int(os.getenv("MAX_FEATURES", "200"))
REQUEST_TIMEOUT = 30

# ArcGIS token — unlocks private datasets in the Zambia GeoHub org.
# Set via ARCGIS_TOKEN in .env (run get_token.py to obtain).
_ARCGIS_TOKEN = os.getenv("ARCGIS_TOKEN", "")

# Org IDs whose services require the token
_TOKEN_ORGS = {"iQ1dY19aHwbSDYIF", "P3ePLMYs2RVChkJx"}

# Set to True when a 499 Token Required error is detected — signals app.py to show refresh UI
token_expired: bool = False


def set_token(new_token: str):
    """
    Update the active token at runtime (called from app.py when user pastes a new token).
    Also persists it to .env so it survives restarts.
    """
    global _ARCGIS_TOKEN, token_expired
    _ARCGIS_TOKEN = new_token.strip()
    token_expired = False
    # Persist to .env
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    try:
        try:
            with open(env_path) as f:
                content = f.read()
        except FileNotFoundError:
            content = ""
        if "ARCGIS_TOKEN=" in content:
            lines = [
                f"ARCGIS_TOKEN={_ARCGIS_TOKEN}" if l.startswith("ARCGIS_TOKEN=") else l
                for l in content.splitlines()
            ]
            content = "\n".join(lines) + "\n"
        else:
            content = content.rstrip() + f"\nARCGIS_TOKEN={_ARCGIS_TOKEN}\n"
        with open(env_path, "w") as f:
            f.write(content)
    except Exception:
        pass  # Non-fatal — token is still active in memory


def _needs_token(url: str) -> bool:
    """Return True if this URL belongs to a private org that requires the token."""
    return any(org in url for org in _TOKEN_ORGS)


def _token_params(url: str) -> dict:
    """Return {"token": ...} if needed, else {}."""
    if _ARCGIS_TOKEN and _needs_token(url):
        return {"token": _ARCGIS_TOKEN}
    return {}

# ---------------------------------------------------------------------------
# Static sample data — used as fallback when the live FeatureServer is
# unreachable from cloud hosting (IP-level block by the ArcGIS server).
# Files are pre-downloaded in /data/ and committed to the repo.
# ---------------------------------------------------------------------------
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")

# Maps URL substring → static JSON filename (and optional POI type override)
_STATIC_MAP = {
    "GRID3_ZMB_HealthFac":                                          "health_facilities.json",
    "GRID3_ZMB_School":                                             "schools.json",
    "GRID3_Zambia_Operational_Points_of_Interest":                  "poi_all.json",
    "GRID3_Zambia_Operational_Settlement_Points_and_Names":         "settlements.json",
    "Zambia_Administrative_Boundaries_Districts_2020":              "districts.json",
    "Zambia_Flood_Prone_Districts":                                 "flood_prone.json",
    "Zambia_Risk_Layers_Aggregated_Districts_Provinces":            "risk_layers.json",
    "Zambia_Fraym_Risk_Aggregations":                               "risk_layers.json",
    "Lusaka_Townships_Risk_Layers":                                 "lusaka_risk.json",
    "OSM_rivers":                                                   "rivers.json",
    "Zambia_wetlands_lakes":                                        "wetlands.json",
    "glc_ZMB_trs_roads_major_b_view":                              "roads.json",
    "Zambia_Biodiversity_Data":                                     "biodiversity.json",
    "Zambia_Forests_Data":                                          "forests.json",
    "Zambia_Biodiversity_Point_Data":                               "biodiversity_points.json",
}

# POI type → dedicated pre-filtered file (better for type-specific queries)
_POI_TYPE_FILES = {
    "Commercial": "poi_commercial.json",
    "Religion":   "poi_religion.json",
    "Farm":       "poi_farm.json",
}


# Module-level copies of the class dicts — safe to import from app.py
# without depending on a cached HubClient instance.
_POI_TYPE_MAP_MODULE = {
    "marketplace": "Commercial", "marketplaces": "Commercial", "market": "Commercial",
    "markets": "Commercial", "shop": "Commercial", "shops": "Commercial",
    "business": "Commercial", "trade": "Commercial", "commercial": "Commercial",
    "church": "Religion", "mosque": "Religion", "religion": "Religion",
    "farm": "Farm", "farming": "Farm", "agriculture": "Farm",
    "well": "Well", "borehole": "Borehole",
    "bridge": "Bridge", "dam": "Dam", "airport": "Airport",
    "bank": "Bank", "police": "Police", "post office": "Post Office",
    "mining": "Mining", "fisheries": "Fisheries", "cooperative": "Cooperative",
    "pharmacy": "Pharmacy", "cemetery": "Cemetery", "railway": "Railway",
    "bus stop": "Bus Stop", "prison": "Prison", "mill": "Mill",
}

_SUBJECT_BOOST_MODULE = {
    "school": "GRID3_ZMB_School", "schools": "GRID3_ZMB_School", "education": "GRID3_ZMB_School",
    "road": "glc_ZMB_trs_roads", "roads": "glc_ZMB_trs_roads", "highway": "glc_ZMB_trs_roads",
    "transport": "glc_ZMB_trs_roads",
    "river": "OSM_rivers", "rivers": "OSM_rivers", "stream": "OSM_rivers",
    "wetland": "Zambia_wetlands_lakes", "lake": "Zambia_wetlands_lakes",
    "forest": "Zambia_Forests_Data", "woodland": "Zambia_Forests_Data", "tree": "Zambia_Forests_Data",
    "flood": "Zambia_Flood_Prone_Districts", "flooding": "Zambia_Flood_Prone_Districts",
    "biodiversity": "Zambia_Biodiversity_Data", "wildlife": "Zambia_Biodiversity_Data",
    "park": "ZMB_National_Parks", "national park": "ZMB_National_Parks", "game": "ZMB_National_Parks",
    "protected area": "ZMB_National_Parks", "conservation": "ZMB_National_Parks",
    "forest reserve": "ZMB_Forest_Reserves_2", "reserve": "ZMB_Forest_Reserves_2",
    "erosion": "ZMB_Erosion_Hazards_Classification", "land degradation": "ZMB_Erosion_Hazards_Classification",
    "soil": "ZMB_Soil_Type_Classification", "soils": "ZMB_Soil_Type_Classification",
    "constituency": "ZMB_Constituencies", "constituencies": "ZMB_Constituencies", "electoral": "ZMB_Constituencies",
    "agriculture block": "ZMB_Agriculture_Blocks_Camps", "camp": "ZMB_Agriculture_Blocks_Camps",
    "settlement": "GRID3_Zambia_Operational_Settlement", "settlements": "GRID3_Zambia_Operational_Settlement",
    "village": "GRID3_Zambia_Operational_Settlement", "hamlet": "GRID3_Zambia_Operational_Settlement",
    "town": "GRID3_Zambia_Operational_Settlement", "community": "GRID3_Zambia_Operational_Settlement",
    "populated": "GRID3_Zambia_Operational_Settlement", "locality": "GRID3_Zambia_Operational_Settlement",
    "health": "GRID3_ZMB_HealthFac", "hospital": "GRID3_ZMB_HealthFac",
    "clinic": "GRID3_ZMB_HealthFac", "facility": "GRID3_ZMB_HealthFac",
    "facilities": "GRID3_ZMB_HealthFac",
    "flood": "Zambia_Flood_Prone_Districts", "flooding": "Zambia_Flood_Prone_Districts",
    "flood prone": "Zambia_Flood_Prone_Districts", "risk": "Zambia_Risk_Layers_Aggregated",
    # New datasets
    "population": "Zambia_Population_2025_WP", "people": "Zambia_Population_2025_WP",
    "mine": "zmb_mines_osm_20251009py", "mines": "zmb_mines_osm_20251009py",
    "mining": "zmb_mines_osm_20251009py", "copper": "AGO_COD_ZMB_Mines_pt",
    "power": "Zambia_Power_Infrastructure", "electricity": "Zambia_Power_Infrastructure",
    "microgrid": "Existing_Microgrids", "microgrids": "Existing_Microgrids",
    "electrification": "zambia_dre_settlement_points",
    "renewable": "zmb_DRE_Atlas", "solar": "zmb_DRE_Atlas",
    "wealth": "Zambia_Relative_Wealth_Index",
    "poverty": "zmb_ADM3_const_Poverty", "poor": "zmb_ADM3_const_Poverty",
    "marketplace": "Zambia_Marketplaces", "marketplaces": "Zambia_Marketplaces",
    "dam": "zmb_dams_20251009", "dams": "zmb_dams_20251009",
    "aquifer": "main_Zambia_aquifers_polygons", "groundwater": "main_Zambia_aquifers_polygons",
    "railway": "LC_MergedRailways", "rail": "LC_MergedRailways", "lobito": "LC_MergedRailways",
    "migration": "Zambia_Net_Migration_2000_to_2019",
    "building": "Zambia_Building_Footprints", "buildings": "Zambia_Building_Footprints",
}


def _load_static(url: str, poi_type: str = ""):
    """Return cached static GeoJSON for a URL, or None if no match."""
    for key, fname in _STATIC_MAP.items():
        if key in url:
            # For POI, prefer the type-specific file if available
            if "Points_of_Interest" in url and poi_type and poi_type in _POI_TYPE_FILES:
                fname = _POI_TYPE_FILES[poi_type]
            path = os.path.join(_DATA_DIR, fname)
            if os.path.exists(path):
                with open(path) as f:
                    return json.load(f)
    return None

# Irrelevant items that happen to have zmb in their tags (not Zambia geospatial data)
_SKIP_TITLES = {
    "1893 chicago ucla 3d", "mpjb", "enriched_gadm41_zmb_shp___gadm41_zmb_0",
}

# ArcGIS Server hosts that return 400 / non-JSON for GeoJSON queries.
# Datasets from these hosts are excluded from the catalog at load time.
_BROKEN_HOSTS = {
    "services9.arcgis.com/ZNWWwa7zEkUIYLEA",  # all services return 400
    "services.arcgis.com/Xpv2nwwwvzUSJGCV",   # protected areas — returns 400
    "gis.logcluster.org",                       # MapServer not started (500)
    "utility.arcgis.com",                       # requires auth (403)
    "services.arcgis.com/P3ePLMYs2RVChkJx",   # returns non-JSON (broken)
    "services3.arcgis.com/JpMWbwty02wMiNWh",   # returns non-JSON (broken)
}

# Specific service URLs that are broken despite having a valid host
_BROKEN_URLS = {
    "https://services3.arcgis.com/BU6Aadhn6tbBEdyk/arcgis/rest/services/OSM_rivers_buffered_2km/FeatureServer/0",
    "https://services7.arcgis.com/dZosTnbDNAhfMkt3/arcgis/rest/services/ZMB_Form_1_view/FeatureServer/0",  # token required
    "https://services9.arcgis.com/zdTKtWQehTjbybEv/arcgis/rest/services/Affected_Adm2_ZMB/FeatureServer/0",  # token required
    "https://utility.arcgis.com/usrsvcs/servers/8b00a549b01f435eaafa076452e3ee05/rest/services/ZMB_Boundaries/FeatureServer/0",  # 403
    "https://gis.logcluster.org/server/rest/services/Zambia/zmb_trs_roads_s_w_viewer/MapServer/0",  # MapServer not started (500)
}


class HubClient:
    """
    Zambia GeoHub data client.
    Searches ArcGIS Online for all public datasets tagged 'zmb'.
    """

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            # ArcGIS services check Referer to decide whether to serve public data.
            # Setting this to the Hub domain allows requests that would otherwise
            # return 0 features or 403 from cloud datacenter IPs.
            "Referer": "https://zmb-geowb.hub.arcgis.com",
            "Origin": "https://zmb-geowb.hub.arcgis.com",
        })
        self._catalog: list = []  # cached on first use

        # ArcGIS API for Python client — used as primary fetch path when available.
        # Falls back to the requests path below if it raises any exception.
        self._agis = _agis.get_client() if _ARCGIS_AVAILABLE and _agis else None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def search_datasets(self, query: str, max_results: int = 10) -> list:
        """
        Search the zmb catalog for datasets matching *query*.
        Loads and caches the full catalog on first call.
        """
        catalog = self._load_catalog()
        return self._rank(query, catalog)[:max_results]

    # Mapping of user query keywords to POI Type filter values
    _POI_TYPE_MAP = {
        "marketplace": "Commercial", "marketplaces": "Commercial", "market": "Commercial",
        "markets": "Commercial", "shop": "Commercial", "shops": "Commercial",
        "business": "Commercial", "trade": "Commercial", "commercial": "Commercial",
        "church": "Religion", "mosque": "Religion", "religion": "Religion",
        "farm": "Farm", "farming": "Farm", "agriculture": "Farm",
        "well": "Well", "borehole": "Borehole", "water facility": "Water Facility",
        "bridge": "Bridge", "dam": "Dam", "airport": "Airport",
        "bank": "Bank", "police": "Police", "post office": "Post Office",
        "mining": "Mining", "fisheries": "Fisheries", "cooperative": "Cooperative",
        "pharmacy": "Pharmacy", "cemetery": "Cemetery", "railway": "Railway",
        "bus stop": "Bus Stop", "prison": "Prison", "mill": "Mill",
    }

    def fetch_geojson(self, feature_url: str, max_features: int = MAX_FEATURES, query_hint: str = "", district_filter: str = "", province_filter: str = "") -> dict:
        """Fetch features from a FeatureServer layer as GeoJSON.

        query_hint: original user query — used to filter POI by type when applicable.
        district_filter: if set, adds a WHERE clause to restrict results to this district.
        province_filter: if set, adds a WHERE clause to restrict results to this province.

        For line/polygon datasets, fewer features are fetched to keep response sizes
        manageable (polylines/polygons are ~10-20x larger per feature than points).
        """
        base = feature_url.rstrip("/")
        if base.endswith("/query"):
            base = base[:-6]

        # Cap at 30 features — enough for AI analysis and map display,
        # keeps payloads under ~60KB for any geometry type on Streamlit Cloud.
        geom_limit = min(max_features, 30)

        # Build WHERE clause
        where = "1=1"

        # POI dataset: filter by Type based on the query keyword
        if "Points_of_Interest" in base or "POI" in base:
            hint_lower = query_hint.lower()
            for keyword, poi_type in self._POI_TYPE_MAP.items():
                if keyword in hint_lower:
                    where = f"Type='{poi_type}'"
                    break

        # Global datasets: restrict to Zambia to avoid worldwide results
        if "Border_Crossing" in base or "GLOBAL_Border" in base:
            where = "iso3_1='ZMB' OR iso3_2='ZMB'"

        # District filter: restrict to a specific district (e.g. "Kalomo")
        if district_filter:
            dist_clause = f"District='{district_filter}' OR DISTRICT='{district_filter}'"
            where = dist_clause if where == "1=1" else f"({where}) AND ({dist_clause})"

        # Province filter: restrict to a specific province (e.g. "Southern")
        if province_filter:
            prov_clause = f"Province='{province_filter}' OR PROVINCE='{province_filter}'"
            where = prov_clause if where == "1=1" else f"({where}) AND ({prov_clause})"

        # ---- Primary path: ArcGIS API for Python ----
        # Uses the official Esri SDK which sends headers accepted by more ArcGIS servers,
        # especially from cloud datacenter IPs that the raw requests path can't reach.
        if self._agis:
            try:
                geojson = self._agis.query_features(
                    feature_url=base,
                    where=where,
                    max_features=geom_limit,
                )
                if geojson.get("features"):
                    return geojson
            except Exception:
                pass  # Fall through to requests path

        # ---- Fallback path: raw requests ----
        params = {
            "where": where,
            "outFields": "*",
            "resultRecordCount": geom_limit,
            "f": "geojson",
            **_token_params(base),
        }
        try:
            resp = self.session.get(f"{base}/query", params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            geojson = resp.json()
        except requests.RequestException as exc:
            raise RuntimeError(f"Feature fetch failed: {exc}") from exc

        if "features" not in geojson:
            snippet = str(geojson)[:300]
            raise ValueError(f"Response is not GeoJSON — keys={list(geojson.keys())} snippet={snippet}")

        if "error" in geojson:
            err = geojson["error"]
            if err.get("code") in (499, 498):
                global token_expired
                token_expired = True
            raise ValueError(f"ArcGIS error: {geojson['error']}")

        # If GeoJSON came back empty, retry without geometry (attributes only via JSON).
        # Some ArcGIS servers refuse GeoJSON format from cloud IPs but serve plain JSON.
        if not geojson.get("features"):
            params_json = {
                "where": where,
                "outFields": "*",
                "resultRecordCount": geom_limit,
                "returnGeometry": "false",
                "f": "json",
                **_token_params(base),
            }
            try:
                resp2 = self.session.get(f"{base}/query", params=params_json, timeout=REQUEST_TIMEOUT)
                resp2.raise_for_status()
                json_resp = resp2.json()
                if "error" not in json_resp and json_resp.get("features"):
                    geojson = {
                        "type": "FeatureCollection",
                        "features": [
                            {"type": "Feature", "geometry": None,
                             "properties": f.get("attributes", {})}
                            for f in json_resp["features"]
                        ],
                    }
            except Exception:
                pass

        # Last resort: use pre-downloaded static sample data.
        # This ensures the app works even when the ArcGIS server blocks cloud IPs.
        if not geojson.get("features"):
            # Determine POI type from the WHERE clause so we serve the right file
            poi_type = ""
            if "Type='" in where:
                poi_type = where.split("Type='")[1].rstrip("'")
            static = _load_static(feature_url, poi_type=poi_type)
            if static and static.get("features"):
                # Apply district/province filter to static data so location queries
                # return only the relevant records even when the live server is blocked.
                loc_filter = district_filter or province_filter
                loc_field = "Province" if province_filter else "District"
                if loc_filter:
                    filtered = [
                        f for f in static["features"]
                        if loc_filter.lower() in (
                            (f.get("properties") or {}).get(loc_field, "") or
                            (f.get("properties") or {}).get(loc_field.upper(), "") or ""
                        ).lower()
                    ]
                    if filtered:
                        static = dict(static)
                        static["features"] = filtered
                static["_source"] = "static_sample"
                return static

        return geojson

    def get_catalog(self) -> list:
        """Return the full zmb dataset catalog."""
        return self._load_catalog()

    def get_field_metadata(self, dataset: dict) -> list:
        return dataset.get("fields", [])

    # ------------------------------------------------------------------
    # Catalog loading
    # ------------------------------------------------------------------

    def _load_catalog(self) -> list:
        """Load catalog from ArcGIS Online (cached after first call).

        Combines two sources:
          1. Public datasets tagged 'zmb' on ArcGIS Online (community/partner data)
          2. Public Feature Services in the Zambia GeoHub org (iQ1dY19aHwbSDYIF)
        Falls back to _SEED_CATALOG if network is unreachable.
        """
        if self._catalog:
            return self._catalog

        catalog = []
        seen_urls: set = set()

        # ---- Source 1: tags:zmb on ArcGIS Online ----
        _token_arg = {"token": _ARCGIS_TOKEN} if _ARCGIS_TOKEN else {}
        try:
            resp = self.session.get(
                "https://www.arcgis.com/sharing/rest/search",
                params={
                    "q": 'tags:zmb type:"Feature Service"',
                    "f": "json",
                    "num": 100,
                    **_token_arg,
                },
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            zmb_results = resp.json().get("results", [])
        except Exception:
            zmb_results = []

        # ---- Source 2: Zambia GeoHub org services ----
        try:
            resp2 = self.session.get(
                "https://www.arcgis.com/sharing/rest/search",
                params={
                    "q": 'orgid:iQ1dY19aHwbSDYIF type:"Feature Service"',
                    "f": "json",
                    "num": 100,
                    **_token_arg,
                },
                timeout=REQUEST_TIMEOUT,
            )
            resp2.raise_for_status()
            org_results = resp2.json().get("results", [])
            # Filter to Zambia-relevant items only
            org_results = [
                r for r in org_results
                if any(
                    kw in ((r.get("title") or "") + " " + (r.get("snippet") or "") + " " + " ".join(r.get("tags") or [])).lower()
                    for kw in ("zambia", "zmb", "lusaka", "copperbelt")
                )
            ]
        except Exception:
            org_results = []

        all_results = zmb_results + org_results

        if not all_results:
            self._catalog = _SEED_CATALOG
            return self._catalog

        for r in all_results:
            title = r.get("title", "") or ""
            if title.lower() in _SKIP_TITLES:
                continue

            url = (r.get("url") or "").rstrip("/")
            if not url or "FeatureServer" not in url:
                continue

            # Skip known broken hosts and specific broken URLs
            if any(host in url for host in _BROKEN_HOSTS):
                continue
            if url in _BROKEN_URLS or url + "/0" in _BROKEN_URLS:
                continue

            # For multi-layer services from the Hub org, enumerate all layers
            if r in org_results and not url.split("/")[-1].isdigit():
                layers = self._fetch_service_layers(url)
                for layer_id, layer_name in layers:
                    layer_url = f"{url}/{layer_id}"
                    if layer_url in seen_urls:
                        continue
                    seen_urls.add(layer_url)
                    fields = self._fetch_fields(layer_url)
                    tags = [t.lower() for t in (r.get("tags") or [])]
                    snippet = (r.get("snippet") or "")[:300]
                    catalog.append({
                        "id": f"{r.get('id', '')}_{layer_id}",
                        "name": f"{title} — {layer_name}" if layer_name != title else title,
                        "description": snippet,
                        "url": layer_url,
                        "tags": tags,
                        "fields": fields,
                        "geometry_type": "Unknown",
                        "extent": {},
                        "modified": str(r.get("modified", "")),
                    })
                continue

            # Single-layer or already layer-specific URL
            if not url.split("/")[-1].isdigit():
                url = url + "/0"

            if url in seen_urls:
                continue
            seen_urls.add(url)

            tags = r.get("tags") or []
            snippet = (r.get("snippet") or "")[:300]
            fields = self._fetch_fields(url)

            catalog.append({
                "id": r.get("id", ""),
                "name": title,
                "description": snippet,
                "url": url,
                "tags": [t.lower() for t in tags],
                "fields": fields,
                "geometry_type": "Unknown",
                "extent": {},
                "modified": str(r.get("modified", "")),
            })

        if not catalog:
            self._catalog = _SEED_CATALOG
            return self._catalog

        # Build a lookup from URL to seed entry for enrichment
        seed_by_url = {s["url"]: s for s in _SEED_CATALOG}

        # Enrich live catalog entries with extra tags/descriptions from seed
        # (ArcGIS Online tags are minimal; seed has curated marketplace/POI tags etc.)
        for ds in catalog:
            seed = seed_by_url.get(ds["url"])
            if seed:
                # Merge tags (deduplicated)
                existing_tags = set(ds["tags"])
                for t in seed["tags"]:
                    if t not in existing_tags:
                        ds["tags"].append(t)
                # Use seed description if richer
                if len(seed["description"]) > len(ds["description"]):
                    ds["description"] = seed["description"]

        # Add seed entries whose URL isn't in the live catalog at all
        live_urls = {ds["url"] for ds in catalog}
        for seed_entry in _SEED_CATALOG:
            if seed_entry["url"] not in live_urls:
                catalog.append(seed_entry)

        self._catalog = catalog
        return self._catalog

    # ------------------------------------------------------------------
    # Search ranking
    # ------------------------------------------------------------------

    # Words that are too generic to carry ranking weight (appear in nearly every dataset)
    _STOP_WORDS = {
        "zambia", "zmb", "area", "areas", "data", "layer", "dataset",
        "show", "where", "what", "many", "the", "which", "most", "has",
        "district", "province", "districts", "provinces",  # too generic — every dataset matches these
        "how", "are", "there", "in", "of",
    }

    # Explicit subject → dataset URL fragment mapping (overrides generic ranking)
    _SUBJECT_BOOST = {
        "school": "GRID3_ZMB_School",
        "schools": "GRID3_ZMB_School",
        "education": "GRID3_ZMB_School",
        # New datasets
        "population": "Zambia_Population_2025_WP",
        "people": "Zambia_Population_2025_WP",
        "demographics": "Zambia_Population_2025_WP",
        "mine": "zmb_mines_osm",
        "mines": "zmb_mines_osm",
        "mining": "zmb_mines_osm",
        "copper": "AGO_COD_ZMB_Mines_pt",
        "cobalt": "AGO_COD_ZMB_Mines_pt",
        "mineral": "AGO_COD_ZMB_Mines_pt",
        "power": "Zambia_Power_Infrastructure",
        "electricity": "Zambia_Power_Infrastructure",
        "substation": "Zambia_Power_Infrastructure",
        "power line": "Zambia_Power_Infrastructure",
        "microgrid": "Existing_Microgrids",
        "microgrids": "Existing_Microgrids",
        "off-grid": "Existing_Microgrids",
        "electrification": "zambia_dre_settlement_points",
        "energy access": "zambia_dre_settlement_points",
        "renewable": "zmb_DRE_Atlas",
        "solar": "zmb_DRE_Atlas",
        "wealth": "Zambia_Relative_Wealth_Index",
        "poverty": "zmb_ADM3_const_Poverty",
        "poor": "zmb_ADM3_const_Poverty",
        "income": "zmb_ADM3_const_Poverty",
        "consumption": "zmb_ADM3_const_Poverty",
        "marketplace": "Zambia_Marketplaces",
        "marketplaces": "Zambia_Marketplaces",
        "market": "Zambia_Marketplaces",
        "markets": "Zambia_Marketplaces",
        "shop": "Zambia_Marketplaces",
        "shops": "Zambia_Marketplaces",
        "place": "Zambia_Marketplaces",
        "places": "Zambia_Marketplaces",
        "commerce": "Zambia_Marketplaces",
        "trade": "Zambia_Marketplaces",
        "business": "Zambia_Marketplaces",
        "dam": "zmb_dams_20251009",
        "dams": "zmb_dams_20251009",
        "reservoir": "zmb_dams_20251009",
        "aquifer": "main_Zambia_aquifers_polygons",
        "groundwater": "main_Zambia_aquifers_polygons",
        "borehole": "main_Zambia_aquifers_polygons",
        "railway": "LC_MergedRailways",
        "rail": "LC_MergedRailways",
        "train": "LC_MergedRailways",
        "lobito": "LC_MergedRailways",
        "migration": "Zambia_Net_Migration_2000_to_2019",
        "building": "Zambia_Building_Footprints",
        "buildings": "Zambia_Building_Footprints",
        "urban density": "Zambia_Building_Footprints",
        "road": "glc_ZMB_trs_roads",
        "roads": "glc_ZMB_trs_roads",
        "highway": "glc_ZMB_trs_roads",
        "transport": "glc_ZMB_trs_roads",
        "river": "OSM_rivers",
        "rivers": "OSM_rivers",
        "stream": "OSM_rivers",
        "wetland": "Zambia_wetlands_lakes",
        "lake": "Zambia_wetlands_lakes",
        "forest": "Zambia_Forests_Data",
        "woodland": "Zambia_Forests_Data",
        "tree": "Zambia_Forests_Data",
        "flood": "Zambia_Flood_Prone_Districts",
        "flooding": "Zambia_Flood_Prone_Districts",
        "biodiversity": "Zambia_Biodiversity_Data",
        "wildlife": "Zambia_Biodiversity_Data",
        "park": "ZMB_National_Parks",
        "national park": "ZMB_National_Parks",
        "game": "ZMB_National_Parks",
        "protected area": "ZMB_National_Parks",
        "conservation": "ZMB_National_Parks",
        "forest reserve": "ZMB_Forest_Reserves_2",
        "reserve": "ZMB_Forest_Reserves_2",
        "erosion": "ZMB_Erosion_Hazards_Classification",
        "soil": "ZMB_Soil_Type_Classification",
        "soils": "ZMB_Soil_Type_Classification",
        "constituency": "ZMB_Constituencies",
        "constituencies": "ZMB_Constituencies",
        "electoral": "ZMB_Constituencies",
        "settlement": "GRID3_Zambia_Operational_Settlement",
        "village": "GRID3_Zambia_Operational_Settlement",
        "town": "GRID3_Zambia_Operational_Settlement",
        "health": "GRID3_ZMB_HealthFac",
        "hospital": "GRID3_ZMB_HealthFac",
        "clinic": "GRID3_ZMB_HealthFac",
        "facility": "GRID3_ZMB_HealthFac",
    }

    def _rank(self, query: str, catalog: list) -> list:
        """Rank catalog entries by relevance to query."""
        query_lower = query.lower()
        words = [w for w in query_lower.split() if len(w) > 2 and w not in self._STOP_WORDS]

        # Build boosted URL sets: subject-specific first, POI only as fallback
        boost_urls: dict = {}  # url → extra score
        # Keywords that already have a dedicated dataset — POI should NOT also get boosted
        subject_matched = set()
        for keyword, frag in self._SUBJECT_BOOST.items():
            if keyword in query_lower:
                subject_matched.add(keyword)
                for ds in catalog:
                    if frag in ds["url"]:
                        boost_urls[ds["url"]] = boost_urls.get(ds["url"], 0) + 30
        # Boost POI only for keywords that don't already have a dedicated dataset
        for keyword in self._POI_TYPE_MAP:
            if keyword in query_lower and keyword not in subject_matched:
                for ds in catalog:
                    if "Points_of_Interest" in ds["url"] or "POI" in ds["url"]:
                        boost_urls[ds["url"]] = boost_urls.get(ds["url"], 0) + 20
                break

        scored = []
        for ds in catalog:
            score = 0
            text = (ds["name"] + " " + ds["description"] + " " + " ".join(ds["tags"])).lower()
            for word in words:
                if word in text:
                    score += 2
                for token in text.split():
                    if word in token or token in word:
                        score += 0.5
            score += boost_urls.get(ds["url"], 0)
            if score > 0:
                scored.append((score, ds))

        scored.sort(key=lambda x: x[0], reverse=True)

        if not scored:
            # No keyword match — return top 5 most general datasets as fallback
            return catalog[:5]

        return [ds for _, ds in scored]

    # ------------------------------------------------------------------
    # Field metadata
    # ------------------------------------------------------------------

    def _fetch_service_layers(self, service_url: str) -> list:
        """Return list of (layer_id, layer_name) for a FeatureServer."""
        try:
            resp = self.session.get(f"{service_url}?f=json", timeout=10)
            layers = resp.json().get("layers", [])
            return [(l.get("id", 0), l.get("name", f"Layer {l.get('id',0)}")) for l in layers]
        except Exception:
            return [(0, "Layer 0")]

    def _fetch_fields(self, layer_url: str) -> list:
        """Fetch field definitions from a FeatureServer layer."""
        base = layer_url.rstrip("/")
        if base.endswith("/query"):
            base = base[:-6]
        try:
            resp = self.session.get(f"{base}?f=json", timeout=10)
            raw = resp.json().get("fields", [])
            return [
                {
                    "name": f.get("name", ""),
                    "alias": f.get("alias", f.get("name", "")),
                    "type": f.get("type", ""),
                }
                for f in raw
                if f.get("name") and not f.get("name", "").startswith("Shape")
            ]
        except Exception:
            return []


# ------------------------------------------------------------------
# Seed catalog — used as fallback if ArcGIS Online is unreachable
# Built from confirmed working zmb-tagged datasets (April 2025)
# ------------------------------------------------------------------
_SEED_CATALOG = [
    {"id": "f523a78b0e2b4c6a8719ef05a165ab4e", "name": "NSDI Zambia Operational Health Facility Layer",
     "description": "Operational health facilities across Zambia including hospitals, health centres, and clinics. Source: Ministry of Health and ZamStats.",
     "url": "https://services3.arcgis.com/BU6Aadhn6tbBEdyk/arcgis/rest/services/GRID3_ZMB_HealthFac_v01beta/FeatureServer/0",
     "tags": ["health", "facilities", "hospitals", "clinics", "zambia", "zmb"], "fields": [], "geometry_type": "Point", "extent": {}, "modified": ""},
    {"id": "0c748bfc945c49ce81d07034b1560a68", "name": "GRID3 ZMB Operational Schools",
     "description": "Operational schools across Zambia including primary and secondary schools. Source: ZamStats and Ministry of General Education.",
     "url": "https://services3.arcgis.com/BU6Aadhn6tbBEdyk/arcgis/rest/services/GRID3_ZMB_School_v01beta/FeatureServer/0",
     "tags": ["schools", "education", "primary", "secondary", "zambia", "zmb"], "fields": [], "geometry_type": "Point", "extent": {}, "modified": ""},
    {"id": "d27357c640394f11943316e36cebaba3", "name": "ZMB Operational Districts",
     "description": "Administrative district boundaries for Zambia 2020. Source: Office of the Surveyor General and Electoral Commission of Zambia.",
     "url": "https://services3.arcgis.com/BU6Aadhn6tbBEdyk/arcgis/rest/services/Zambia_Administrative_Boundaries_Districts_2020/FeatureServer/0",
     "tags": ["districts", "boundaries", "administrative", "zambia", "zmb"], "fields": [], "geometry_type": "Polygon", "extent": {}, "modified": ""},
    {"id": "a0293a6e84c143298227518eb3418d23", "name": "GRID3 ZMB Operational Settlement Names",
     "description": "Settlement point locations and names across Zambia including villages, towns, and urban areas. Fields: Province, District, Name, settlement type. Source: ZamStats 2010 census cartography.",
     "url": "https://services3.arcgis.com/BU6Aadhn6tbBEdyk/arcgis/rest/services/GRID3_Zambia_Operational_Settlement_Points_and_Names_Version01/FeatureServer/0",
     "tags": ["settlements", "villages", "towns", "urban", "rural", "communities", "population", "copperbelt", "lusaka", "zambia", "zmb"], "fields": [], "geometry_type": "Point", "extent": {}, "modified": ""},
    {"id": "8f73c42ed3884256904ae12440fae558", "name": "ZMB Operational Points of Interest",
     "description": (
         "Points of interest across Zambia from ZamStats 2010 census. "
         "Contains 90,000+ locations categorised by Type including: "
         "Commercial (marketplaces, shops, businesses, markets), "
         "Religion (churches, mosques), Farm (agriculture), Well (water), "
         "Mill, Bridge, Recreation, Storage Facility, Administration, "
         "Police, Bank, Post Office, Airport, Bus Stop, Mining, Fisheries, "
         "Cooperative, Pharmacy, Cemetery, Railway, Dam, Borehole, and more. "
         "Fields: Province, District, Type, Name, Theme, Source."
     ),
     "url": "https://services3.arcgis.com/BU6Aadhn6tbBEdyk/arcgis/rest/services/GRID3_Zambia_Operational_Points_of_Interest_Version01/FeatureServer/0",
     "tags": [
         "points of interest", "poi", "zambia", "zmb",
         "marketplace", "market", "markets", "marketplaces", "commercial", "shops", "business", "trade",
         "religion", "church", "mosque", "farm", "agriculture", "well", "water",
         "mill", "bridge", "recreation", "storage", "administration", "police",
         "bank", "post office", "airport", "bus stop", "mining", "fisheries",
         "cooperative", "pharmacy", "cemetery", "railway", "dam", "borehole",
         "community", "facilities", "infrastructure",
     ], "fields": [], "geometry_type": "Point", "extent": {}, "modified": ""},
    {"id": "3fb6aa51dc9a4df1a1b7f4e48df5a374", "name": "GRID3 ZMB Risk Indicators by District and Province",
     "description": "Risk index and population at risk by district and province — covering socioeconomic vulnerability, WASH, communication access.",
     "url": "https://services3.arcgis.com/BU6Aadhn6tbBEdyk/arcgis/rest/services/Zambia_Risk_Layers_Aggregated_Districts_Provinces/FeatureServer/0",
     "tags": ["risk", "vulnerability", "wash", "population", "districts", "provinces", "zambia", "zmb"], "fields": [], "geometry_type": "Polygon", "extent": {}, "modified": ""},
    {"id": "f310fa8209cb4685b56e309cf6d1388f", "name": "Flood Prone Districts in Zambia",
     "description": "Districts in Zambia prone to flooding.",
     "url": "https://services3.arcgis.com/BU6Aadhn6tbBEdyk/arcgis/rest/services/Zambia_Flood_Prone_Districts/FeatureServer/0",
     "tags": ["flood", "disaster", "risk", "districts", "environment", "zambia", "zmb"], "fields": [], "geometry_type": "Polygon", "extent": {}, "modified": ""},
    {"id": "7d9e73eb624448c79826d3c3274bf790", "name": "OSM Zambia Rivers",
     "description": "Rivers in Zambia from OpenStreetMap.",
     "url": "https://services3.arcgis.com/BU6Aadhn6tbBEdyk/arcgis/rest/services/OSM_rivers/FeatureServer/0",
     "tags": ["rivers", "water", "osm", "environment", "zambia", "zmb"], "fields": [], "geometry_type": "Polyline", "extent": {}, "modified": ""},
    {"id": "ef791bcb05db473a9dc4eb04e41664b5", "name": "Zambia Wetlands and Lakes",
     "description": "Wetlands and lakes across Zambia from OpenStreetMap. Includes named lakes (Kariba, Bangweulu, Mweru, Tanganyika) and wetland areas. Useful for water resource mapping and environmental planning.",
     "url": "https://services3.arcgis.com/BU6Aadhn6tbBEdyk/arcgis/rest/services/Zambia_wetlands_lakes/FeatureServer/0",
     "tags": ["wetlands", "lakes", "water", "environment", "kariba", "bangweulu", "mweru", "tanganyika", "zambia", "zmb"], "fields": [], "geometry_type": "Polygon", "extent": {}, "modified": ""},
    {"id": "7be52e48252c464bbb8e1c713f87a5d1", "name": "Zambia Biodiversity Data",
     "description": "Protected areas and biodiversity polygon data for Zambia including national parks, game management areas, and conservation areas. Source: RCMRD/CIFOR-ICRAF.",
     "url": "https://services6.arcgis.com/zOnyumh63cMmLBBH/arcgis/rest/services/Zambia_Biodiversity_Data/FeatureServer/0",
     "tags": ["biodiversity", "national parks", "conservation", "protected areas", "wildlife", "nature", "game", "zambia", "zmb"], "fields": [], "geometry_type": "Polygon", "extent": {}, "modified": ""},
    {"id": "c6d0ce455cae4f4c96ef98e7d44f9793", "name": "Zambia Forests Data",
     "description": "Forest polygon data for Zambia showing forest reserves and woodland areas. Source: RCMRD/CIFOR-ICRAF. Useful for land cover, deforestation, and environmental analysis.",
     "url": "https://services6.arcgis.com/zOnyumh63cMmLBBH/arcgis/rest/services/Zambia_Forests_Data/FeatureServer/0",
     "tags": ["forests", "forest reserves", "woodland", "trees", "land cover", "deforestation", "environment", "zambia", "zmb"], "fields": [], "geometry_type": "Polygon", "extent": {}, "modified": ""},
    {"id": "883e648672134f6488ffbc9f31533a65", "name": "Zambia Biodiversity Point Data",
     "description": "Biodiversity point observations across Zambia including species occurrence data. Source: RCMRD/CIFOR-ICRAF.",
     "url": "https://services6.arcgis.com/zOnyumh63cMmLBBH/arcgis/rest/services/Zambia_Biodiversity_Point_Data/FeatureServer/0",
     "tags": ["biodiversity", "species", "wildlife", "environment", "conservation", "zambia", "zmb"], "fields": [], "geometry_type": "Point", "extent": {}, "modified": ""},
    {"id": "c571868321cc41ef99ed27535ffa964d", "name": "Zambia Major Roads",
     "description": "Major road network in Zambia including highways, primary and secondary roads. Useful for transport planning, accessibility analysis, and infrastructure mapping.",
     "url": "https://services3.arcgis.com/t6lYS2Pmd8iVx1fy/arcgis/rest/services/glc_ZMB_trs_roads_major_b_view/FeatureServer/0",
     "tags": ["roads", "highway", "transport", "infrastructure", "network", "accessibility", "zambia", "zmb"], "fields": [], "geometry_type": "Polyline", "extent": {}, "modified": ""},
    {"id": "bb0ba0c4ee1945f0ae35c1430b12574c", "name": "Lusaka Townships Risk Layers",
     "description": "Risk index by Lusaka township — socioeconomic vulnerability and communication access.",
     "url": "https://services3.arcgis.com/BU6Aadhn6tbBEdyk/arcgis/rest/services/Lusaka_Townships_Risk_Layers/FeatureServer/0",
     "tags": ["lusaka", "townships", "risk", "urban", "vulnerability", "zambia", "zmb"], "fields": [], "geometry_type": "Polygon", "extent": {}, "modified": ""},
    # ---- From Zambia GeoHub org (iQ1dY19aHwbSDYIF) ----
    {"id": "zmb_borders_adm1", "name": "Zambia Provincial Boundaries (Admin 1)",
     "description": "Official province-level administrative boundaries for Zambia. ITOS/OCHA standard. Fields: ADM1_EN (province name), ADM1_PCODE.",
     "url": "https://services.arcgis.com/iQ1dY19aHwbSDYIF/arcgis/rest/services/Zambia Borders/FeatureServer/3",
     "tags": ["provinces", "boundaries", "administrative", "admin1", "zambia", "zmb", "itos", "ocha"], "fields": [], "geometry_type": "Polygon", "extent": {}, "modified": ""},
    {"id": "zmb_borders_adm2", "name": "Zambia District Boundaries (Admin 2)",
     "description": "Official district-level administrative boundaries for Zambia. ITOS/OCHA standard. Fields: ADM2_EN (district name), ADM2_PCODE.",
     "url": "https://services.arcgis.com/iQ1dY19aHwbSDYIF/arcgis/rest/services/Zambia Borders/FeatureServer/4",
     "tags": ["districts", "boundaries", "administrative", "admin2", "zambia", "zmb", "itos", "ocha"], "fields": [], "geometry_type": "Polygon", "extent": {}, "modified": ""},
    {"id": "zmb_borders_adm0", "name": "Zambia National Boundary (Admin 0)",
     "description": "Official national boundary polygon for Zambia. ITOS/OCHA standard.",
     "url": "https://services.arcgis.com/iQ1dY19aHwbSDYIF/arcgis/rest/services/Zambia Borders/FeatureServer/2",
     "tags": ["national", "boundary", "country", "admin0", "zambia", "zmb", "itos"], "fields": [], "geometry_type": "Polygon", "extent": {}, "modified": ""},
    {"id": "zmb_onshore_aquaculture", "name": "Zambia Onshore Aquaculture Suitability Zones",
     "description": "Suitability zones for onshore aquaculture across Zambia by province. Fields: suitability, area_km2, ADM1_EN (province).",
     "url": "https://services.arcgis.com/iQ1dY19aHwbSDYIF/arcgis/rest/services/Zambia_onshore_aquaculture/FeatureServer/0",
     "tags": ["aquaculture", "fisheries", "fish", "farming", "suitability", "zambia", "zmb"], "fields": [], "geometry_type": "Polygon", "extent": {}, "modified": ""},
    {"id": "zmb_kariba_aquaculture", "name": "Zambia Lake Kariba Cage Aquaculture Suitability Zones",
     "description": "Suitability zones for cage aquaculture on Lake Kariba (Zambia portion). Fields: suitability, area_km2.",
     "url": "https://services.arcgis.com/iQ1dY19aHwbSDYIF/arcgis/rest/services/Suitability_zones_for_cage_aquaculture_on_lake_Kariba_(Zambia_part)/FeatureServer/0",
     "tags": ["aquaculture", "fisheries", "kariba", "lake", "cage", "fish", "zambia", "zmb"], "fields": [], "geometry_type": "Polygon", "extent": {}, "modified": ""},
    {"id": "zmb_agriculture_blocks", "name": "Zambia Agriculture Blocks and Camps",
     "description": "Agricultural administrative blocks and camps across Zambia by province and district. Fields: Province, District, Block, Camp, Area_Sq_km, Area_ha.",
     "url": "https://services3.arcgis.com/t6lYS2Pmd8iVx1fy/ArcGIS/rest/services/ZMB_Agriculture_Blocks_Camps/FeatureServer/0",
     "tags": ["agriculture", "farming", "blocks", "camps", "district", "province", "zambia", "zmb"], "fields": [], "geometry_type": "Polygon", "extent": {}, "modified": ""},
    {"id": "zmb_constituencies", "name": "Zambia Constituencies",
     "description": "Electoral constituency boundaries across Zambia with province and district linkage. Fields: PovName (province), DistName (district), ConstName (constituency name), ConstNo.",
     "url": "https://services3.arcgis.com/t6lYS2Pmd8iVx1fy/ArcGIS/rest/services/ZMB_Constituencies/FeatureServer/0",
     "tags": ["constituencies", "electoral", "boundaries", "governance", "districts", "zambia", "zmb"], "fields": [], "geometry_type": "Polygon", "extent": {}, "modified": ""},
    {"id": "zmb_national_parks", "name": "Zambia National Parks and Game Management Areas",
     "description": "National parks and game management areas in Zambia with name, province, and status. Fields: NAME, PROVINCE, STATUS.",
     "url": "https://services3.arcgis.com/t6lYS2Pmd8iVx1fy/ArcGIS/rest/services/ZMB_National_Parks/FeatureServer/0",
     "tags": ["national parks", "wildlife", "conservation", "game", "protected areas", "environment", "zambia", "zmb"], "fields": [], "geometry_type": "Polygon", "extent": {}, "modified": ""},
    {"id": "zmb_forest_reserves", "name": "Zambia Forest Reserves",
     "description": "Forest reserve boundaries across Zambia. Useful for conservation, land use, and environmental planning.",
     "url": "https://services3.arcgis.com/t6lYS2Pmd8iVx1fy/ArcGIS/rest/services/ZMB_Forest_Reserves_2/FeatureServer/0",
     "tags": ["forests", "forest reserves", "conservation", "environment", "land use", "zambia", "zmb"], "fields": [], "geometry_type": "Polygon", "extent": {}, "modified": ""},
    {"id": "zmb_erosion_hazards", "name": "Zambia Erosion Hazard Classification",
     "description": "Erosion hazard classification zones across Zambia. Fields: Classifica (hazard class), erosion_ha (area in hectares). Useful for land management and environmental risk.",
     "url": "https://services3.arcgis.com/t6lYS2Pmd8iVx1fy/ArcGIS/rest/services/ZMB_Erosion_Hazards_Classification/FeatureServer/0",
     "tags": ["erosion", "hazard", "land degradation", "environment", "soil", "risk", "zambia", "zmb"], "fields": [], "geometry_type": "Polygon", "extent": {}, "modified": ""},
    {"id": "zmb_soil_types", "name": "Zambia Soil Type Classification",
     "description": "Soil type and landscape classification zones across Zambia. Fields: Type (soil class), S_CLASS, LSCAPE (landscape), DOMINANT (dominant soil), DOM_DESC, S_DESC (description). Useful for agriculture and land use planning.",
     "url": "https://services3.arcgis.com/t6lYS2Pmd8iVx1fy/ArcGIS/rest/services/ZMB_Soil_Type_Classification/FeatureServer/0",
     "tags": ["soil", "land use", "agriculture", "geology", "classification", "zambia", "zmb"], "fields": [], "geometry_type": "Polygon", "extent": {}, "modified": ""},
    # ---- New authenticated datasets (iQ1dY19aHwbSDYIF org) ----
    {"id": "zmb_settlement_extents", "name": "Zambia Settlement Extents (GRID3 v3.0)",
     "description": "Settlement boundary polygons and centroids across Zambia at 3-arc-second resolution. Fields: building_count, iso3, country. Source: GRID3.",
     "url": "https://services.arcgis.com/iQ1dY19aHwbSDYIF/arcgis/rest/services/GRID3_ZMB_Settlement_Extents_v3_0/FeatureServer/0",
     "tags": ["settlements", "settlement extents", "buildings", "population", "grid3", "zambia", "zmb"], "fields": [], "geometry_type": "Polygon", "extent": {}, "modified": ""},
    {"id": "zmb_power_lines", "name": "Zambia Power Lines",
     "description": "Electrical power transmission and distribution lines across Zambia. Source: Overture Maps Foundation. Fields: name, feature_type, voltage, cables.",
     "url": "https://services.arcgis.com/iQ1dY19aHwbSDYIF/arcgis/rest/services/Zambia_Power_Infrastructure/FeatureServer/0",
     "tags": ["power", "electricity", "power lines", "transmission", "energy", "infrastructure", "utilities", "zambia", "zmb"], "fields": [], "geometry_type": "LineString", "extent": {}, "modified": ""},
    {"id": "zmb_power_stations", "name": "Zambia Power Stations and Substations",
     "description": "Electrical power stations, substations, and generation facilities across Zambia. Source: Overture Maps Foundation. Fields: name, feature_type.",
     "url": "https://services.arcgis.com/iQ1dY19aHwbSDYIF/arcgis/rest/services/Zambia_Power_Infrastructure/FeatureServer/1",
     "tags": ["power", "electricity", "substation", "power station", "generation", "energy", "infrastructure", "zambia", "zmb"], "fields": [], "geometry_type": "Point", "extent": {}, "modified": ""},
    {"id": "zmb_power_polygons", "name": "Zambia Power Infrastructure Areas",
     "description": "Polygon areas for power infrastructure including generation sites across Zambia. Source: Overture Maps Foundation.",
     "url": "https://services.arcgis.com/iQ1dY19aHwbSDYIF/arcgis/rest/services/Zambia_Power_Infrastructure/FeatureServer/2",
     "tags": ["power", "electricity", "energy", "infrastructure", "zambia", "zmb"], "fields": [], "geometry_type": "Polygon", "extent": {}, "modified": ""},
    {"id": "zmb_marketplaces_poly", "name": "Zambia Marketplaces (Building Footprints)",
     "description": "Marketplace building footprint polygons across Zambia. Fields: name, building_class, category_primary. Source: Overture Maps Foundation.",
     "url": "https://services.arcgis.com/iQ1dY19aHwbSDYIF/arcgis/rest/services/Zambia_Marketplaces/FeatureServer/0",
     "tags": ["marketplace", "market", "markets", "trade", "commercial", "buildings", "zambia", "zmb"], "fields": [], "geometry_type": "Polygon", "extent": {}, "modified": ""},
    {"id": "zmb_marketplaces_pt", "name": "Zambia Marketplaces (Points)",
     "description": "Marketplace point locations across Zambia. Fields: name, category_primary, category_alt. Source: Overture Maps Foundation.",
     "url": "https://services.arcgis.com/iQ1dY19aHwbSDYIF/arcgis/rest/services/Zambia_Marketplaces/FeatureServer/1",
     "tags": ["marketplace", "market", "markets", "trade", "commercial", "shops", "zambia", "zmb"], "fields": [], "geometry_type": "Point", "extent": {}, "modified": ""},
    {"id": "zmb_poi_overture", "name": "Zambia Points of Interest (Overture)",
     "description": "Points of interest across Zambia from Overture Maps. Fields: name, category_primary, category_alt. Includes shops, services, amenities.",
     "url": "https://services.arcgis.com/iQ1dY19aHwbSDYIF/arcgis/rest/services/Zambia_Points_of_Interest/FeatureServer/0",
     "tags": ["points of interest", "poi", "amenities", "shops", "services", "zambia", "zmb"], "fields": [], "geometry_type": "Point", "extent": {}, "modified": ""},
    {"id": "zmb_road_network", "name": "Zambia Road Network (Overture)",
     "description": "Full road network across Zambia from Overture Maps. Fields: name, road_class, surface, speed_limit. More detailed than the major roads layer.",
     "url": "https://services.arcgis.com/iQ1dY19aHwbSDYIF/arcgis/rest/services/Zambia_Road_Network/FeatureServer/0",
     "tags": ["roads", "road network", "transport", "highway", "streets", "infrastructure", "zambia", "zmb"], "fields": [], "geometry_type": "LineString", "extent": {}, "modified": ""},
    {"id": "zmb_education_poly", "name": "Zambia Education Facilities (Building Footprints)",
     "description": "School and education facility building footprint polygons across Zambia. Source: Overture Maps Foundation.",
     "url": "https://services.arcgis.com/iQ1dY19aHwbSDYIF/arcgis/rest/services/Zambia_Education_Facilities/FeatureServer/0",
     "tags": ["schools", "education", "learning", "buildings", "facilities", "zambia", "zmb"], "fields": [], "geometry_type": "Polygon", "extent": {}, "modified": ""},
    {"id": "zmb_education_pt", "name": "Zambia Education Facilities (Points)",
     "description": "School and education facility point locations across Zambia. Fields: name, category_primary. Source: Overture Maps Foundation.",
     "url": "https://services.arcgis.com/iQ1dY19aHwbSDYIF/arcgis/rest/services/Zambia_Education_Facilities/FeatureServer/1",
     "tags": ["schools", "education", "learning", "college", "university", "facilities", "zambia", "zmb"], "fields": [], "geometry_type": "Point", "extent": {}, "modified": ""},
    {"id": "zmb_health_poly", "name": "Zambia Health Facilities (Building Footprints)",
     "description": "Hospital and health facility building footprint polygons across Zambia. Source: Overture Maps Foundation.",
     "url": "https://services.arcgis.com/iQ1dY19aHwbSDYIF/arcgis/rest/services/Zambia_Health_Facilities/FeatureServer/0",
     "tags": ["health", "hospitals", "clinics", "facilities", "buildings", "zambia", "zmb"], "fields": [], "geometry_type": "Polygon", "extent": {}, "modified": ""},
    {"id": "zmb_health_pt_overture", "name": "Zambia Health Facilities (Points, Overture)",
     "description": "Hospital and health facility point locations across Zambia from Overture Maps. Fields: name, category_primary.",
     "url": "https://services.arcgis.com/iQ1dY19aHwbSDYIF/arcgis/rest/services/Zambia_Health_Facilities/FeatureServer/1",
     "tags": ["health", "hospitals", "clinics", "medical", "facilities", "zambia", "zmb"], "fields": [], "geometry_type": "Point", "extent": {}, "modified": ""},
    {"id": "zmb_building_footprints", "name": "Zambia Building Footprints",
     "description": "Building footprint polygons across Zambia. Fields: name, building_class, category_primary. Source: Overture Maps Foundation. Useful for urban density analysis.",
     "url": "https://services.arcgis.com/iQ1dY19aHwbSDYIF/arcgis/rest/services/Zambia_Building_Footprints/FeatureServer/0",
     "tags": ["buildings", "building footprints", "urban", "structures", "density", "zambia", "zmb"], "fields": [], "geometry_type": "Polygon", "extent": {}, "modified": ""},
    {"id": "zmb_relative_wealth", "name": "Zambia Relative Wealth Index",
     "description": "Relative Wealth Index (RWI) score by grid cell across Zambia. Fields: rwi (wealth score), latitude, longitude. Higher values = wealthier areas. Source: Meta/Facebook AI Research.",
     "url": "https://services.arcgis.com/iQ1dY19aHwbSDYIF/arcgis/rest/services/Zambia_Relative_Wealth_Index/FeatureServer/0",
     "tags": ["wealth", "poverty", "relative wealth index", "rwi", "socioeconomic", "inequality", "zambia", "zmb"], "fields": [], "geometry_type": "Point", "extent": {}, "modified": ""},
    {"id": "zmb_lobito_stations", "name": "Lobito Corridor Railway Stations",
     "description": "Railway station locations along the Lobito Corridor in Zambia. Fields: fclass, name, osm_id. Relevant for trade and transport planning.",
     "url": "https://services.arcgis.com/iQ1dY19aHwbSDYIF/arcgis/rest/services/LobitoCorridor_Stations/FeatureServer/3",
     "tags": ["railway", "stations", "lobito", "corridor", "transport", "trade", "zambia", "zmb"], "fields": [], "geometry_type": "Point", "extent": {}, "modified": ""},
    {"id": "zmb_railways", "name": "Zambia Railway Network (Lobito Corridor)",
     "description": "Railway lines across Zambia including the Lobito Corridor. Fields: fclass, name, osm_id. Source: merged OSM railways.",
     "url": "https://services.arcgis.com/iQ1dY19aHwbSDYIF/arcgis/rest/services/LC_MergedRailways/FeatureServer/4",
     "tags": ["railway", "rail", "train", "lobito", "corridor", "transport", "infrastructure", "zambia", "zmb"], "fields": [], "geometry_type": "LineString", "extent": {}, "modified": ""},
    {"id": "zmb_population_2025", "name": "Zambia Population 2025 (WorldPop)",
     "description": "Estimated population by grid cell for Zambia in 2025. Fields: Population. Source: WorldPop. Use for population counts, density analysis, and service coverage planning.",
     "url": "https://services.arcgis.com/iQ1dY19aHwbSDYIF/arcgis/rest/services/Zambia_Population_2025_WP/FeatureServer/0",
     "tags": ["population", "census", "demographics", "people", "density", "worldpop", "zambia", "zmb"], "fields": [], "geometry_type": "Point", "extent": {}, "modified": ""},
    {"id": "zmb_districts_2022", "name": "Zambia Administrative District Boundaries 2022",
     "description": "Updated district administrative boundaries for Zambia (2022). Fields: PROVINCE, DISTRICT. Most current official boundary dataset.",
     "url": "https://services.arcgis.com/iQ1dY19aHwbSDYIF/arcgis/rest/services/Zambia___Administrative_District_Boundaries_2022/FeatureServer/0",
     "tags": ["districts", "boundaries", "administrative", "2022", "zambia", "zmb"], "fields": [], "geometry_type": "Polygon", "extent": {}, "modified": ""},
    {"id": "zmb_nsdi_schools", "name": "Zambia NSDI Operational Schools",
     "description": "Official NSDI school locations across Zambia from the Ministry of General Education. Fields: X, Y coordinates and facility attributes.",
     "url": "https://services.arcgis.com/iQ1dY19aHwbSDYIF/arcgis/rest/services/zmb_Operational_schools_NSDI/FeatureServer/0",
     "tags": ["schools", "education", "nsdi", "ministry", "official", "zambia", "zmb"], "fields": [], "geometry_type": "Point", "extent": {}, "modified": ""},
    {"id": "zmb_dams", "name": "Zambia Dams",
     "description": "Dam locations across Zambia from OpenStreetMap. Useful for water resource management, hydropower, and irrigation planning.",
     "url": "https://services.arcgis.com/iQ1dY19aHwbSDYIF/arcgis/rest/services/zmb_dams_20251009/FeatureServer/0",
     "tags": ["dams", "water", "hydropower", "irrigation", "reservoir", "infrastructure", "zambia", "zmb"], "fields": [], "geometry_type": "MultiLineString", "extent": {}, "modified": ""},
    {"id": "zmb_dre_atlas", "name": "Zambia Decentralised Renewable Energy Atlas",
     "description": "Renewable energy potential by grid cell across Zambia. Fields: geohash, lat, lon. Covers solar, wind, and off-grid energy potential. Source: DRE Atlas.",
     "url": "https://services.arcgis.com/iQ1dY19aHwbSDYIF/arcgis/rest/services/zmb_DRE_Atlas/FeatureServer/8",
     "tags": ["renewable energy", "solar", "energy", "off-grid", "dre", "electricity", "zambia", "zmb"], "fields": [], "geometry_type": "Polygon", "extent": {}, "modified": ""},
    {"id": "zmb_dre_settlement_poly", "name": "Zambia DRE Settlement Polygons (Energy Access)",
     "description": "Settlement polygons with decentralised renewable energy access data. Fields: geohash, lat, lon. Shows settlements by energy access status.",
     "url": "https://services.arcgis.com/iQ1dY19aHwbSDYIF/arcgis/rest/services/Zambia_DRE_Settlement_Polygons/FeatureServer/0",
     "tags": ["energy access", "electrification", "settlements", "off-grid", "renewable", "dre", "zambia", "zmb"], "fields": [], "geometry_type": "Polygon", "extent": {}, "modified": ""},
    {"id": "zmb_dre_settlement_pt", "name": "Zambia DRE Settlement Points (Energy Access)",
     "description": "Settlement point locations with decentralised renewable energy access data across Zambia.",
     "url": "https://services.arcgis.com/iQ1dY19aHwbSDYIF/arcgis/rest/services/zambia_dre_settlement_points/FeatureServer/0",
     "tags": ["energy access", "electrification", "settlements", "off-grid", "renewable", "dre", "zambia", "zmb"], "fields": [], "geometry_type": "Point", "extent": {}, "modified": ""},
    {"id": "zmb_osr_local_authority", "name": "Zambia Own Source Revenue by Local Authority 2024",
     "description": "Own-source revenue data consolidated by local authority for Zambia 2024. Fields: PROVINCE, DISTRICT. Useful for fiscal and governance analysis.",
     "url": "https://services.arcgis.com/iQ1dY19aHwbSDYIF/arcgis/rest/services/Zambia_OSR_LA_Consolidated_2024/FeatureServer/9",
     "tags": ["revenue", "local authority", "governance", "fiscal", "finance", "districts", "zambia", "zmb"], "fields": [], "geometry_type": "Polygon", "extent": {}, "modified": ""},
    {"id": "zmb_nsdi_health", "name": "Zambia NSDI Operational Health Facilities",
     "description": "Official NSDI health facility locations across Zambia from the Ministry of Health. Fields: MFL_Code, DHIS2_UID, Hims_code and facility attributes.",
     "url": "https://services.arcgis.com/iQ1dY19aHwbSDYIF/arcgis/rest/services/zmb_Operational_healthfac_NSDI/FeatureServer/0",
     "tags": ["health", "hospitals", "clinics", "nsdi", "ministry", "official", "mfl", "zambia", "zmb"], "fields": [], "geometry_type": "Point", "extent": {}, "modified": ""},
    {"id": "zmb_microgrids", "name": "Zambia Existing Microgrids",
     "description": "Existing microgrid electricity installations across Zambia. Fields: Village_Na (village name), District. Useful for off-grid electrification planning.",
     "url": "https://services.arcgis.com/iQ1dY19aHwbSDYIF/arcgis/rest/services/Existing_Microgrids/FeatureServer/10",
     "tags": ["microgrids", "electricity", "electrification", "off-grid", "energy", "villages", "zambia", "zmb"], "fields": [], "geometry_type": "Point", "extent": {}, "modified": ""},
    {"id": "zmb_mines_osm", "name": "Zambia Mines (OSM)",
     "description": "Mining area polygons across Zambia from OpenStreetMap. Fields: industrial, landuse. Shows active and historical mining areas.",
     "url": "https://services.arcgis.com/iQ1dY19aHwbSDYIF/arcgis/rest/services/zmb_mines_osm_20251009py/FeatureServer/12",
     "tags": ["mining", "mines", "minerals", "extraction", "industry", "copper", "zambia", "zmb"], "fields": [], "geometry_type": "Polygon", "extent": {}, "modified": ""},
    {"id": "zmb_mines_cod", "name": "Zambia Mines (COD Points)",
     "description": "Mining point locations across Zambia from the COD (Common Operational Dataset). Fields: landuse, type. Shows mine site locations.",
     "url": "https://services.arcgis.com/iQ1dY19aHwbSDYIF/arcgis/rest/services/AGO_COD_ZMB_Mines_pt/FeatureServer/2",
     "tags": ["mining", "mines", "minerals", "extraction", "copper", "cobalt", "industry", "zambia", "zmb"], "fields": [], "geometry_type": "Point", "extent": {}, "modified": ""},
    {"id": "zmb_constituency_poverty", "name": "Zambia Constituency Poverty Index",
     "description": "Poverty headcount and consumption estimates by constituency across Zambia. Fields: constituency_code, Mean_consump_adult_ZMW, pt_est_pov_headcount. Critical for targeting social programs.",
     "url": "https://services.arcgis.com/iQ1dY19aHwbSDYIF/arcgis/rest/services/zmb_ADM3_const_Poverty/FeatureServer/50",
     "tags": ["poverty", "consumption", "socioeconomic", "constituencies", "welfare", "inequality", "zambia", "zmb"], "fields": [], "geometry_type": "Polygon", "extent": {}, "modified": ""},
    {"id": "zmb_health_2025", "name": "Zambia Health Facilities 2025 (MFL)",
     "description": "Health facilities across Zambia from the 2025 Master Facility List. Fields: MFL_Code, DHIS2_UID, Hims_code. Most current official health facility dataset.",
     "url": "https://services.arcgis.com/iQ1dY19aHwbSDYIF/arcgis/rest/services/Zambia_HF_20251112/FeatureServer/36",
     "tags": ["health", "hospitals", "clinics", "mfl", "master facility list", "official", "zambia", "zmb"], "fields": [], "geometry_type": "Point", "extent": {}, "modified": ""},
    {"id": "zmb_aquifers", "name": "Zambia Aquifers",
     "description": "Aquifer polygon areas across Zambia showing groundwater resources. Fields: aqtyp (aquifer type), Shape__Area. Useful for water resource planning and borehole siting.",
     "url": "https://services.arcgis.com/iQ1dY19aHwbSDYIF/arcgis/rest/services/main_Zambia_aquifers_polygons/FeatureServer/0",
     "tags": ["aquifers", "groundwater", "water", "borehole", "geology", "environment", "zambia", "zmb"], "fields": [], "geometry_type": "MultiPolygon", "extent": {}, "modified": ""},
    {"id": "zmb_net_migration", "name": "Zambia Net Migration by District 2000-2019",
     "description": "Net migration rates by district for Zambia from 2000 to 2019. Fields: DISTRICT, PROVINCE, DIST_CODE. Shows population movement trends.",
     "url": "https://services.arcgis.com/iQ1dY19aHwbSDYIF/arcgis/rest/services/Zambia_Net_Migration_2000_to_2019/FeatureServer/0",
     "tags": ["migration", "population movement", "demographics", "districts", "zambia", "zmb"], "fields": [], "geometry_type": "Polygon", "extent": {}, "modified": ""},
    {"id": "zmb_boundaries_2023", "name": "Zambia Administrative Boundaries 2023",
     "description": "Administrative boundaries for Zambia 2023 with population data. Fields: NAME, TOTPOP_CY (total population current year). Source: Esri.",
     "url": "https://services.arcgis.com/P3ePLMYs2RVChkJx/arcgis/rest/services/ZMB_Boundaries_2023/FeatureServer/0",
     "tags": ["boundaries", "administrative", "population", "2023", "zambia", "zmb"], "fields": [], "geometry_type": "Polygon", "extent": {}, "modified": ""},
    {"id": "b55592d29ac145ad824bc8531ab75224", "name": "Zambia Marketplaces — Places",
     "description": "Marketplace locations and points of interest across Zambia. Includes named places categorised by primary and alternate category (e.g. market, shop, service). Fields: name, category_primary, category_alternate, confidence, data_sector.",
     "url": "https://services.arcgis.com/iQ1dY19aHwbSDYIF/ArcGIS/rest/services/Zambia_Marketplaces/FeatureServer/1",
     "tags": ["marketplace", "markets", "places", "points of interest", "commerce", "trade", "shops", "zambia", "zmb"],
     "fields": [
         {"name": "name"}, {"name": "category_primary"}, {"name": "category_alternate"},
         {"name": "confidence"}, {"name": "data_sector"}, {"name": "country_name"}
     ], "geometry_type": "Point", "extent": {}, "modified": ""},
    {"id": "b55592d29ac145ad824bc8531ab75224_0", "name": "Zambia Marketplaces — Buildings",
     "description": "Building footprints associated with Zambia marketplace areas. Includes building class, height, and sector. Fields: name, building_class, height_m, data_sector.",
     "url": "https://services.arcgis.com/iQ1dY19aHwbSDYIF/ArcGIS/rest/services/Zambia_Marketplaces/FeatureServer/0",
     "tags": ["buildings", "building footprints", "marketplace", "structures", "zambia", "zmb"],
     "fields": [
         {"name": "name"}, {"name": "building_class"}, {"name": "height_m"},
         {"name": "data_sector"}, {"name": "country_name"}
     ], "geometry_type": "Polygon", "extent": {}, "modified": ""},
]
