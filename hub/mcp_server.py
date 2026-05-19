"""
Zambia GeoHub MCP Server
========================
Exposes the GeoHub data layer as Model Context Protocol (MCP) tools so that
any MCP-compatible AI client (Claude, OpenAI, etc.) can call them during
inference rather than requiring data to be pre-fetched and injected into prompts.

Run standalone:
    python -m hub.mcp_server

Or import get_tools() to use tool definitions directly in the Streamlit app
with Anthropic's tool_use API without running a separate server process.

Tools exposed:
    search_datasets        — find relevant datasets by keyword
    fetch_features         — get GeoJSON features from a dataset
    count_features         — exact feature count (no geometry download)
    count_features_in_bbox — count features within a drawn bounding box
    overpass_count         — count OSM features (mines/dams/churches/mosques/markets)
    get_catalog            — list all available datasets
"""

import json
from typing import Any

# ---------------------------------------------------------------------------
# Tool definitions — used by both the MCP server and direct tool_use calls
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "search_datasets",
        "description": (
            "Search the Zambia GeoHub dataset catalogue for datasets relevant to a query. "
            "Returns up to 5 matching datasets with their names, descriptions, field names, "
            "and ArcGIS FeatureServer URLs. Call this first to find which dataset to query."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Plain-English search query, e.g. 'health facilities', 'schools in Lusaka', 'flood risk'"
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of datasets to return (default 5)",
                    "default": 5
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "fetch_features",
        "description": (
            "Fetch GeoJSON features from a Zambia GeoHub ArcGIS FeatureServer dataset. "
            "Returns feature properties (name, type, district, province, coordinates) "
            "for up to 30 features. Optionally filter by district or province."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "dataset_url": {
                    "type": "string",
                    "description": "The ArcGIS FeatureServer URL for the dataset layer, e.g. https://services3.arcgis.com/BU6Aadhn6tbBEdyk/arcgis/rest/services/GRID3_ZMB_HealthFac_v01beta/FeatureServer/0"
                },
                "query_hint": {
                    "type": "string",
                    "description": "The user's original question — used to filter Points of Interest by type (Religion, Commercial, Farm, etc.)",
                    "default": ""
                },
                "district": {
                    "type": "string",
                    "description": "Filter results to a specific district, e.g. 'Lusaka', 'Kafue', 'Ndola'",
                    "default": ""
                },
                "province": {
                    "type": "string",
                    "description": "Filter results to a specific province, e.g. 'Lusaka', 'Copperbelt', 'Southern'",
                    "default": ""
                },
                "max_features": {
                    "type": "integer",
                    "description": "Maximum number of features to return (default 30, max 50)",
                    "default": 30
                }
            },
            "required": ["dataset_url"]
        }
    },
    {
        "name": "count_features",
        "description": (
            "Get an exact count of features in a dataset, optionally filtered by district or province. "
            "This is a lightweight query — no geometry is downloaded. "
            "Use this when the user asks 'how many' questions to get a verified number."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "dataset_url": {
                    "type": "string",
                    "description": "The ArcGIS FeatureServer URL for the dataset layer"
                },
                "district": {
                    "type": "string",
                    "description": "Filter to a specific district",
                    "default": ""
                },
                "province": {
                    "type": "string",
                    "description": "Filter to a specific province",
                    "default": ""
                }
            },
            "required": ["dataset_url"]
        }
    },
    {
        "name": "count_features_in_bbox",
        "description": (
            "Count features from an ArcGIS dataset within a bounding box (drawn rectangle on the map). "
            "Returns the count plus feature names and the nearest feature to the centre point. "
            "Used for the Draw Area spatial analysis feature."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "dataset_url": {
                    "type": "string",
                    "description": "The ArcGIS FeatureServer URL"
                },
                "min_lat": {"type": "number", "description": "South boundary of the bounding box"},
                "max_lat": {"type": "number", "description": "North boundary of the bounding box"},
                "min_lon": {"type": "number", "description": "West boundary of the bounding box"},
                "max_lon": {"type": "number", "description": "East boundary of the bounding box"},
                "name_fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Field names to try for feature names, in priority order",
                    "default": ["Name", "name", "NAME"]
                }
            },
            "required": ["dataset_url", "min_lat", "max_lat", "min_lon", "max_lon"]
        }
    },
    {
        "name": "overpass_count",
        "description": (
            "Count OpenStreetMap features (mines, dams, churches, mosques, markets) "
            "within a bounding box using the Overpass API. "
            "Use this for features not available as point layers on ArcGIS."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "feature_type": {
                    "type": "string",
                    "enum": ["mines", "dams", "churches", "mosques", "markets"],
                    "description": "The type of OSM feature to count"
                },
                "min_lat": {"type": "number"},
                "max_lat": {"type": "number"},
                "min_lon": {"type": "number"},
                "max_lon": {"type": "number"}
            },
            "required": ["feature_type", "min_lat", "max_lat", "min_lon", "max_lon"]
        }
    },
    {
        "name": "get_catalog",
        "description": (
            "List all datasets currently available on the Zambia GeoHub. "
            "Returns dataset names, descriptions, and URLs. "
            "Use this when the user asks 'what data is available' or 'what datasets do you have'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    }
]


# ---------------------------------------------------------------------------
# Tool executor — runs when the AI calls a tool
# ---------------------------------------------------------------------------

def execute_tool(tool_name: str, tool_input: dict) -> Any:
    """
    Execute a tool call and return the result.
    Called by both the MCP server handler and the direct Streamlit tool_use path.
    """
    from hub.client import HubClient
    import requests as _req

    hub = HubClient()

    # ── search_datasets ──────────────────────────────────────────────────
    if tool_name == "search_datasets":
        query      = tool_input.get("query", "")
        max_results = int(tool_input.get("max_results", 5))
        datasets   = hub.search_datasets(query, max_results=max_results)
        result = []
        for ds in datasets:
            fields = [f["name"] for f in ds.get("fields", [])[:15]]
            result.append({
                "name":        ds["name"],
                "description": ds["description"][:300],
                "url":         ds["url"],
                "fields":      fields,
                "tags":        ds.get("tags", [])[:10],
            })
        return {"datasets": result, "count": len(result)}

    # ── fetch_features ───────────────────────────────────────────────────
    elif tool_name == "fetch_features":
        url         = tool_input["dataset_url"]
        query_hint  = tool_input.get("query_hint", "")
        district    = tool_input.get("district", "")
        province    = tool_input.get("province", "")
        max_feat    = min(int(tool_input.get("max_features", 30)), 50)
        try:
            geojson = hub.fetch_geojson(
                url,
                max_features=max_feat,
                query_hint=query_hint,
                district_filter=district,
                province_filter=province,
            )
            features = []
            for feat in geojson.get("features", []):
                props = feat.get("properties") or {}
                geom  = feat.get("geometry") or {}
                row   = {k: v for k, v in props.items() if v not in (None, "", "null", "None")}
                if geom.get("type") == "Point":
                    coords = geom.get("coordinates", [])
                    if len(coords) >= 2:
                        row["_lat"] = coords[1]
                        row["_lon"] = coords[0]
                features.append(row)
            source = geojson.get("_source", "live")
            return {
                "features":     features,
                "count":        len(features),
                "source":       source,
                "dataset_url":  url,
            }
        except Exception as exc:
            return {"error": str(exc), "features": [], "count": 0}

    # ── count_features ───────────────────────────────────────────────────
    elif tool_name == "count_features":
        url      = tool_input["dataset_url"]
        district = tool_input.get("district", "")
        province = tool_input.get("province", "")
        try:
            count = hub.count_features(url, district_filter=district, province_filter=province)
            location = district or province or "all of Zambia"
            return {
                "count":    count,
                "location": location,
                "url":      url,
                "verified": True,   # came from live API — equivalent to PCN "Verified"
            }
        except Exception as exc:
            return {"error": str(exc), "count": None, "verified": False}

    # ── count_features_in_bbox ───────────────────────────────────────────
    elif tool_name == "count_features_in_bbox":
        from utils.geo_utils import haversine_km
        url  = tool_input["dataset_url"]
        s, w = tool_input["min_lat"], tool_input["min_lon"]
        n, e = tool_input["max_lat"], tool_input["max_lon"]
        bbox_str = f"{w},{s},{e},{n}"
        name_fields = tool_input.get("name_fields", ["Name", "name", "NAME"])
        ctr_lat = (s + n) / 2
        ctr_lon = (w + e) / 2

        _hdr = {
            "Referer": "https://zmb-geowb.hub.arcgis.com",
            "Origin":  "https://zmb-geowb.hub.arcgis.com",
            "Accept":  "application/json",
        }
        try:
            # Count
            r = _req.get(f"{url}/query", params={
                "geometry": bbox_str, "geometryType": "esriGeometryEnvelope",
                "spatialRel": "esriSpatialRelIntersects",
                "returnCountOnly": "true", "f": "json"
            }, headers=_hdr, timeout=10)
            count = r.json().get("count", 0)

            names, nearest, nearest_dist = [], None, float("inf")
            if count and count > 0:
                r2 = _req.get(f"{url}/query", params={
                    "geometry": bbox_str, "geometryType": "esriGeometryEnvelope",
                    "spatialRel": "esriSpatialRelIntersects",
                    "outFields": "*", "resultRecordCount": 20,
                    "returnGeometry": "true", "f": "json"
                }, headers=_hdr, timeout=12)
                for feat in r2.json().get("features", []):
                    props = feat.get("attributes") or {}
                    nm = next((str(props[f]).strip() for f in name_fields
                               if props.get(f) and str(props[f]).strip() not in ("None","null","","0")), None)
                    if nm:
                        names.append(nm)
                    geom = feat.get("geometry") or {}
                    fx, fy = geom.get("x"), geom.get("y")
                    if fx and fy:
                        d = haversine_km(ctr_lat, ctr_lon, fy, fx)
                        if d < nearest_dist:
                            nearest_dist = d
                            nearest = nm or "Unnamed"

            return {
                "count": count, "names": names[:20],
                "nearest": nearest,
                "nearest_dist_km": round(nearest_dist, 2) if nearest_dist < float("inf") else None,
                "verified": True,
            }
        except Exception as exc:
            return {"error": str(exc), "count": 0}

    # ── overpass_count ───────────────────────────────────────────────────
    elif tool_name == "overpass_count":
        ftype = tool_input["feature_type"]
        s, w  = tool_input["min_lat"], tool_input["min_lon"]
        n, e  = tool_input["max_lat"], tool_input["max_lon"]
        bbox  = f"({s},{w},{n},{e})"

        _QUERIES = {
            "mines":    f'[out:json][timeout:20];(node["industrial"="mine"]{bbox};way["industrial"="mine"]{bbox};node["landuse"="quarry"]{bbox};way["landuse"="quarry"]{bbox};);out count;',
            "dams":     f'[out:json][timeout:20];(node["waterway"="dam"]{bbox};way["waterway"="dam"]{bbox};node["man_made"="dam"]{bbox};way["man_made"="dam"]{bbox};);out count;',
            "churches": f'[out:json][timeout:20];(node["amenity"="place_of_worship"]["religion"="christian"]{bbox};way["amenity"="place_of_worship"]["religion"="christian"]{bbox};);out count;',
            "mosques":  f'[out:json][timeout:20];(node["amenity"="place_of_worship"]["religion"="muslim"]{bbox};way["amenity"="place_of_worship"]["religion"="muslim"]{bbox};);out count;',
            "markets":  f'[out:json][timeout:20];(node["amenity"="marketplace"]{bbox};way["amenity"="marketplace"]{bbox};node["shop"~"supermarket|mall|convenience|general"]{bbox};);out count;',
        }
        _MIRRORS = [
            "https://overpass.kumi.systems/api/interpreter",
            "https://overpass-api.de/api/interpreter",
            "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
        ]
        query = _QUERIES.get(ftype, "")
        if not query:
            return {"error": f"Unknown feature type: {ftype}", "count": 0}

        for mirror in _MIRRORS:
            try:
                r = _req.post(mirror, data={"data": query},
                              headers={"User-Agent": "ZambiaGeoHubAI/1.0"}, timeout=20)
                if r.status_code == 200:
                    count = int((r.json().get("elements") or [{}])[0].get("tags", {}).get("total", 0))
                    return {"count": count, "feature_type": ftype, "source": "OpenStreetMap", "verified": True}
            except Exception:
                continue
        return {"error": "All Overpass mirrors failed", "count": 0, "verified": False}

    # ── get_catalog ──────────────────────────────────────────────────────
    elif tool_name == "get_catalog":
        catalog = hub.get_catalog()
        return {
            "datasets": [
                {"name": ds["name"], "description": ds["description"][:150], "url": ds["url"]}
                for ds in catalog
            ],
            "total": len(catalog),
        }

    return {"error": f"Unknown tool: {tool_name}"}


# ---------------------------------------------------------------------------
# MCP Server — run as a standalone process
# ---------------------------------------------------------------------------

def run_server():
    """Run the GeoHub MCP server using the mcp package."""
    try:
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
        from mcp.types import Tool, TextContent
        import asyncio

        server = Server("zambia-geohub")

        @server.list_tools()
        async def list_tools():
            return [
                Tool(
                    name=t["name"],
                    description=t["description"],
                    inputSchema=t["input_schema"],
                )
                for t in TOOLS
            ]

        @server.call_tool()
        async def call_tool(name: str, arguments: dict):
            result = execute_tool(name, arguments)
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        async def main():
            async with stdio_server() as (read_stream, write_stream):
                await server.run(read_stream, write_stream,
                                 server.create_initialization_options())

        asyncio.run(main())

    except ImportError:
        print("mcp package not installed. Run: pip install mcp")
        print("The tool definitions in TOOLS and execute_tool() still work")
        print("for direct tool_use calls via the Anthropic API.")


if __name__ == "__main__":
    run_server()
