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

load_dotenv()

MAX_FEATURES = int(os.getenv("MAX_FEATURES", "200"))
REQUEST_TIMEOUT = 30

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
    "park": "Zambia_Biodiversity_Data", "conservation": "Zambia_Biodiversity_Data",
    "settlement": "GRID3_Zambia_Operational_Settlement", "settlements": "GRID3_Zambia_Operational_Settlement",
    "village": "GRID3_Zambia_Operational_Settlement", "hamlet": "GRID3_Zambia_Operational_Settlement",
    "town": "GRID3_Zambia_Operational_Settlement", "community": "GRID3_Zambia_Operational_Settlement",
    "populated": "GRID3_Zambia_Operational_Settlement", "locality": "GRID3_Zambia_Operational_Settlement",
    "health": "GRID3_ZMB_HealthFac", "hospital": "GRID3_ZMB_HealthFac",
    "clinic": "GRID3_ZMB_HealthFac", "facility": "GRID3_ZMB_HealthFac",
    "facilities": "GRID3_ZMB_HealthFac",
    "flood": "Zambia_Flood_Prone_Districts", "flooding": "Zambia_Flood_Prone_Districts",
    "flood prone": "Zambia_Flood_Prone_Districts", "risk": "Zambia_Risk_Layers_Aggregated",
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

        params = {
            "where": where,
            "outFields": "*",
            "resultRecordCount": geom_limit,
            "f": "geojson",
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
        try:
            resp = self.session.get(
                "https://www.arcgis.com/sharing/rest/search",
                params={
                    "q": 'tags:zmb type:"Feature Service"',
                    "f": "json",
                    "num": 100,
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
        "park": "Zambia_Biodiversity_Data",
        "conservation": "Zambia_Biodiversity_Data",
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

        # Build boosted URL sets: POI + subject-specific
        boost_urls: dict = {}  # url → extra score
        for keyword in self._POI_TYPE_MAP:
            if keyword in query_lower:
                for ds in catalog:
                    if "Points_of_Interest" in ds["url"] or "POI" in ds["url"]:
                        boost_urls[ds["url"]] = boost_urls.get(ds["url"], 0) + 20
                break
        for keyword, frag in self._SUBJECT_BOOST.items():
            if keyword in query_lower:
                for ds in catalog:
                    if frag in ds["url"]:
                        boost_urls[ds["url"]] = boost_urls.get(ds["url"], 0) + 25

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
]
