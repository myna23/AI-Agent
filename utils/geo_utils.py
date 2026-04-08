"""
Geospatial utility helpers:
- make_folium_map    : build a Folium map from GeoJSON
- summarize_geojson  : compute feature stats for Claude prompts
- geojson_to_sample_rows : extract sample feature properties
"""

import folium
import json


# Zambia center coordinates and default zoom
ZAMBIA_CENTER = [-13.5, 28.5]
ZAMBIA_ZOOM = 6


def make_folium_map(
    geojson: dict,
    dataset_name: str = "",
    context_layers: list = None,
) -> folium.Map:
    """
    Build a Folium map from a GeoJSON FeatureCollection.

    - Points → CircleMarker with popup (+ optional district/road context layers)
    - Lines / Polygons → GeoJson overlay
    - No geometry → plain Zambia-centered basemap

    context_layers: list of {"geojson": dict, "name": str, "type": "boundary"|"road"}
      Added as background layers when the primary data is points, so users can
      see which district/road is nearest to each facility or POI.
    """
    m = folium.Map(location=ZAMBIA_CENTER, zoom_start=ZAMBIA_ZOOM, tiles="CartoDB positron")

    features = geojson.get("features", [])

    # Add context layers (districts, roads) before the main data so points sit on top
    if context_layers:
        for ctx in context_layers:
            ctx_feats = ctx["geojson"].get("features", [])
            if not ctx_feats:
                continue
            ctx_type = ctx.get("type", "boundary")
            if ctx_type == "boundary":
                def _boundary_style(_feature):
                    return {"fillColor": "transparent", "color": "#888888", "weight": 1, "fillOpacity": 0}
                label_fields = _pick_label_fields(ctx_feats)
                folium.GeoJson(
                    ctx["geojson"],
                    name=ctx["name"],
                    style_function=_boundary_style,
                    tooltip=folium.GeoJsonTooltip(fields=label_fields, aliases=label_fields, localize=True) if label_fields else None,
                ).add_to(m)
            elif ctx_type == "road":
                def _road_style(_feature):
                    return {"color": "#b5838d", "weight": 1.5, "opacity": 0.6}
                label_fields = _pick_label_fields(ctx_feats)
                folium.GeoJson(
                    ctx["geojson"],
                    name=ctx["name"],
                    style_function=_road_style,
                    tooltip=folium.GeoJsonTooltip(fields=label_fields, aliases=label_fields, localize=True) if label_fields else None,
                ).add_to(m)

    if not features:
        return m

    # Check geometry presence
    first_geom = features[0].get("geometry")
    if not first_geom:
        return m

    geom_type = first_geom.get("type", "")

    if geom_type == "Point":
        for feat in features:
            geom = feat.get("geometry") or {}
            coords = geom.get("coordinates", [])
            if len(coords) < 2:
                continue
            lon, lat = coords[0], coords[1]
            props = feat.get("properties") or {}
            popup_html = _props_to_html(props)
            folium.CircleMarker(
                location=[lat, lon],
                radius=5,
                color="#e63946",
                fill=True,
                fill_color="#e63946",
                fill_opacity=0.7,
                popup=folium.Popup(popup_html, max_width=300),
                tooltip=props.get("Name") or props.get("NAME") or props.get("name") or props.get("DISTRICT") or props.get("District") or "Feature",
            ).add_to(m)
        # Fit bounds to points
        lats = [f["geometry"]["coordinates"][1] for f in features if f.get("geometry") and len(f["geometry"].get("coordinates", [])) >= 2]
        lons = [f["geometry"]["coordinates"][0] for f in features if f.get("geometry") and len(f["geometry"].get("coordinates", [])) >= 2]
        if lats:
            m.fit_bounds([[min(lats), min(lons)], [max(lats), max(lons)]])

    else:
        # Polygon / LineString — use GeoJson layer
        def style_fn(feature):
            return {
                "fillColor": "#457b9d",
                "color": "#1d3557",
                "weight": 1.5,
                "fillOpacity": 0.4,
            }

        def highlight_fn(feature):
            return {"weight": 3, "color": "#e63946", "fillOpacity": 0.6}

        folium.GeoJson(
            geojson,
            name=dataset_name,
            style_function=style_fn,
            highlight_function=highlight_fn,
            tooltip=folium.GeoJsonTooltip(
                fields=_pick_label_fields(features),
                aliases=_pick_label_fields(features),
                localize=True,
            ),
        ).add_to(m)
        try:
            m.fit_bounds(m.get_bounds())
        except Exception:
            pass

    return m


def summarize_geojson(geojson: dict) -> dict:
    """
    Compute summary statistics for a GeoJSON FeatureCollection.

    Returns:
        feature_count   : int
        geometry_type   : str
        fields          : list of field names
        numeric_stats   : {field: {min, max, mean}} for numeric fields
        bbox            : [min_lon, min_lat, max_lon, max_lat] or None
        exceeded_limit  : bool — true if server returned transfer-limit warning
    """
    features = geojson.get("features", [])
    exceeded = geojson.get("properties", {}) or {}
    exceeded_limit = exceeded.get("exceededTransferLimit", False)

    if not features:
        return {
            "feature_count": 0,
            "geometry_type": "None",
            "fields": [],
            "numeric_stats": {},
            "bbox": None,
            "exceeded_limit": exceeded_limit,
        }

    # Geometry type
    first_geom = features[0].get("geometry") or {}
    geometry_type = first_geom.get("type", "Unknown")

    # Fields from first feature's properties
    first_props = features[0].get("properties") or {}
    fields = list(first_props.keys())

    # Numeric stats
    numeric_data: dict[str, list[float]] = {}
    for feat in features:
        props = feat.get("properties") or {}
        for k, v in props.items():
            if isinstance(v, (int, float)) and v is not None:
                numeric_data.setdefault(k, []).append(float(v))

    numeric_stats = {}
    for field, values in numeric_data.items():
        if values:
            numeric_stats[field] = {
                "min": round(min(values), 4),
                "max": round(max(values), 4),
                "mean": round(sum(values) / len(values), 4),
            }

    # Bounding box (points only, fast path)
    bbox = None
    if geometry_type == "Point":
        lons = [f["geometry"]["coordinates"][0] for f in features if f.get("geometry") and len(f["geometry"].get("coordinates", [])) >= 2]
        lats = [f["geometry"]["coordinates"][1] for f in features if f.get("geometry") and len(f["geometry"].get("coordinates", [])) >= 2]
        if lons:
            bbox = [min(lons), min(lats), max(lons), max(lats)]

    return {
        "feature_count": len(features),
        "geometry_type": geometry_type,
        "fields": fields,
        "numeric_stats": numeric_stats,
        "bbox": bbox,
        "exceeded_limit": exceeded_limit,
    }


def geojson_to_sample_rows(geojson: dict, n: int = 5) -> list[dict]:
    """
    Return the first *n* feature properties dicts from a GeoJSON FeatureCollection.
    Values are truncated to 200 chars to keep Claude prompts concise.
    """
    features = geojson.get("features", [])[:n]
    rows = []
    for feat in features:
        props = feat.get("properties") or {}
        truncated = {k: str(v)[:200] if v is not None else None for k, v in props.items()}
        rows.append(truncated)
    return rows


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _props_to_html(props: dict) -> str:
    """Build a simple HTML table from feature properties for popups."""
    rows = "".join(
        f"<tr><td><b>{k}</b></td><td>{str(v)[:100]}</td></tr>"
        for k, v in props.items()
        if v is not None
    )
    return f"<table style='font-size:12px'>{rows}</table>"


def _pick_label_fields(features: list[dict], max_fields: int = 3) -> list[str]:
    """Pick the most useful label fields for a GeoJson tooltip."""
    if not features:
        return []
    props = features[0].get("properties") or {}
    priority = ["Name", "NAME", "name", "District", "DISTRICT", "Province", "PROVINCE", "REGION", "Type", "TYPE", "type", "ID"]
    chosen = [f for f in priority if f in props]
    if not chosen:
        chosen = list(props.keys())
    return chosen[:max_fields]
