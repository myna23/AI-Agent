"""
Geospatial utility helpers:
- make_folium_map    : build a Folium map from GeoJSON
- summarize_geojson  : compute feature stats for Claude prompts
- geojson_to_sample_rows : extract sample feature properties
"""

import math
import folium
from folium.plugins import MarkerCluster


# Zambia center coordinates and default zoom
ZAMBIA_CENTER = [-13.5, 28.5]
ZAMBIA_ZOOM = 6

# Color palette for point features by dataset/type keyword
_TYPE_COLORS = {
    # Health
    "hospital": "#e63946", "clinic": "#e63946", "health": "#e63946",
    "dispensary": "#e63946", "medical": "#e63946",
    # Education
    "school": "#457b9d", "education": "#457b9d", "college": "#457b9d", "university": "#457b9d",
    # Commercial / Markets
    "commercial": "#2d6a4f", "market": "#2d6a4f", "shop": "#2d6a4f", "business": "#2d6a4f",
    # Religion
    "religion": "#7b2d8b", "church": "#7b2d8b", "mosque": "#7b2d8b",
    # Agriculture / Farm
    "farm": "#90be6d", "agriculture": "#90be6d", "cooperative": "#90be6d",
    # Water
    "well": "#48cae4", "borehole": "#48cae4", "water": "#48cae4",
    # Infrastructure
    "bridge": "#f4a261", "dam": "#f4a261", "airport": "#023e8a",
    "railway": "#6c757d", "bus stop": "#6c757d",
    # Finance / Admin
    "bank": "#d4a017", "police": "#1d3557", "post office": "#ff6b35",
    "administration": "#1d3557", "prison": "#343a40",
    # Natural resources
    "mining": "#495057", "fisheries": "#0077b6", "forest": "#2d6a4f",
    # Settlement
    "settlement": "#f4a261", "village": "#f4a261",
    # Default
    "default": "#e63946",
}

# Point-of-Interest type values → color
_POI_TYPE_COLORS = {
    "Commercial": "#2d6a4f",
    "Religion": "#7b2d8b",
    "Farm": "#90be6d",
    "Well": "#48cae4",
    "Borehole": "#48cae4",
    "Bridge": "#f4a261",
    "Dam": "#f4a261",
    "Airport": "#023e8a",
    "Bank": "#d4a017",
    "Police": "#1d3557",
    "Post Office": "#ff6b35",
    "Mining": "#495057",
    "Fisheries": "#0077b6",
    "Cooperative": "#90be6d",
    "Pharmacy": "#e63946",
    "Cemetery": "#6c757d",
    "Railway": "#6c757d",
    "Bus Stop": "#adb5bd",
    "Mill": "#f4a261",
    "Recreation": "#48cae4",
    "Administration": "#1d3557",
}


def _point_color(props: dict, dataset_name: str = "") -> str:
    """Determine marker color from feature properties or dataset name."""
    # Check POI Type field first (most specific)
    poi_type = props.get("Type") or props.get("TYPE") or props.get("type") or ""
    if poi_type in _POI_TYPE_COLORS:
        return _POI_TYPE_COLORS[poi_type]

    # Check Facility_Type field (health facilities)
    fac_type = (props.get("Facility_T") or props.get("Facility_Type") or "").lower()
    if fac_type:
        for kw, color in _TYPE_COLORS.items():
            if kw in fac_type:
                return color

    # Fall back to dataset name
    ds_lower = dataset_name.lower()
    for kw, color in _TYPE_COLORS.items():
        if kw in ds_lower:
            return color

    return _TYPE_COLORS["default"]


# Theme colors per dataset category — (border_color, fill_color, label)
_THEME_PALETTE = {
    "water":      ("#0096c7", "#90e0ef", "Water"),
    "river":      ("#0096c7", "#90e0ef", "River"),
    "wetland":    ("#0077b6", "#48cae4", "Wetland/Lake"),
    "lake":       ("#0077b6", "#48cae4", "Lake"),
    "borehole":   ("#0096c7", "#caf0f8", "Borehole/Well"),
    "well":       ("#0096c7", "#caf0f8", "Well"),
    "aquifer":    ("#0096c7", "#caf0f8", "Aquifer"),
    "dam":        ("#023e8a", "#48cae4", "Dam"),
    "road":       ("#d4790a", "#ffd166", "Road"),
    "highway":    ("#d4790a", "#ffd166", "Highway"),
    "railway":    ("#9d0208", "#e63946", "Railway"),
    "rail":       ("#9d0208", "#e63946", "Railway"),
    "health":     ("#c1121f", "#f4a261", "Health Facility"),
    "hospital":   ("#c1121f", "#f4a261", "Hospital"),
    "clinic":     ("#c1121f", "#f4a261", "Clinic"),
    "school":     ("#1d3557", "#457b9d", "School"),
    "education":  ("#1d3557", "#457b9d", "Education"),
    "settlement": ("#7b4f12", "#f4a261", "Settlement"),
    "village":    ("#7b4f12", "#f4a261", "Village"),
    "market":     ("#2d6a4f", "#74c69d", "Market"),
    "commercial": ("#2d6a4f", "#74c69d", "Commercial"),
    "forest":     ("#1b4332", "#2d6a4f", "Forest"),
    "woodland":   ("#1b4332", "#2d6a4f", "Forest"),
    "flood":      ("#4361ee", "#4cc9f0", "Flood Zone"),
    "mine":       ("#495057", "#adb5bd", "Mine"),
    "mining":     ("#495057", "#adb5bd", "Mine"),
    "biodiversity": ("#386641", "#6a994e", "Biodiversity"),
    "wildlife":   ("#386641", "#6a994e", "Wildlife"),
    "park":       ("#386641", "#6a994e", "National Park"),
    "power":      ("#f8961e", "#fee440", "Power"),
    "poverty":    ("#7b2d8b", "#c77dff", "Poverty/Risk"),
    "risk":       ("#7b2d8b", "#c77dff", "Risk"),
    "population": ("#3a0ca3", "#7209b7", "Population"),
}

def _dataset_theme(dataset_name: str) -> tuple:
    """
    Return (border_color, fill_color, label) for a dataset based on its name.
    Used to consistently color buffers, bbox overlays, and markers.
    """
    ds = dataset_name.lower()
    for keyword, theme in _THEME_PALETTE.items():
        if keyword in ds:
            return theme
    return ("#e63946", "#ff6b6b", "Features")  # default red


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the great-circle distance in kilometres between two lat/lon points."""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def polygon_centroid(geometry: dict):
    """Return (lat, lon) centroid of a Polygon or MultiPolygon, or None."""
    coords = []
    gtype = geometry.get("type", "")
    if gtype == "Polygon":
        coords = geometry.get("coordinates", [[]])[0]
    elif gtype == "MultiPolygon":
        for poly in geometry.get("coordinates", []):
            coords.extend(poly[0] if poly else [])
    if not coords:
        return None
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    return (sum(lats) / len(lats), sum(lons) / len(lons))


def _point_in_polygon(lat: float, lon: float, polygon_coords: list) -> bool:
    """Ray-casting algorithm — returns True if (lat, lon) is inside the polygon ring."""
    x, y = lon, lat
    inside = False
    n = len(polygon_coords)
    j = n - 1
    for i in range(n):
        xi, yi = polygon_coords[i][0], polygon_coords[i][1]
        xj, yj = polygon_coords[j][0], polygon_coords[j][1]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def _geom_representative_point(geometry: dict):
    """Return a (lat, lon) representative point for any geometry type."""
    gtype = geometry.get("type", "")
    coords = geometry.get("coordinates", [])
    if gtype == "Point":
        return (coords[1], coords[0]) if len(coords) >= 2 else None
    if gtype == "LineString" and coords:
        mid = coords[len(coords) // 2]
        return (mid[1], mid[0]) if len(mid) >= 2 else None
    if gtype == "MultiLineString" and coords:
        line = coords[len(coords) // 2]
        mid = line[len(line) // 2] if line else None
        return (mid[1], mid[0]) if mid and len(mid) >= 2 else None
    return polygon_centroid(geometry)


def assign_districts(features: list, district_geojson: dict) -> list:
    """
    Spatially assign a 'district' and 'province' property to each feature
    by checking which district polygon contains the feature's representative point.
    Returns the same list with properties mutated in place.
    Only assigns if the feature doesn't already have a District field.
    """
    dist_features = district_geojson.get("features", [])
    for feat in features:
        props = feat.get("properties") or {}
        # Skip if already has a district value
        if props.get("District") or props.get("DISTRICT"):
            continue
        geom = feat.get("geometry") or {}
        pt = _geom_representative_point(geom)
        if not pt:
            continue
        lat, lon = pt
        for df in dist_features:
            dp = df.get("properties") or {}
            dgeom = df.get("geometry") or {}
            dcoords = dgeom.get("coordinates", [])
            # Handle both Polygon and MultiPolygon
            rings = []
            if dgeom.get("type") == "Polygon":
                rings = [dcoords[0]] if dcoords else []
            elif dgeom.get("type") == "MultiPolygon":
                rings = [poly[0] for poly in dcoords if poly]
            for ring in rings:
                if _point_in_polygon(lat, lon, ring):
                    props["district"] = dp.get("DISTRICT") or dp.get("District", "")
                    props["province"] = dp.get("PROVINCE") or dp.get("Province", "")
                    break
            if props.get("district"):
                break
    return features


def features_within_km(features: list, center_lat: float, center_lon: float, radius_km: float) -> list:
    """
    Filter a list of GeoJSON Point features to those within radius_km of a center point.
    Returns (filtered_features, distances) where distances is a list of (feature, km) tuples.
    """
    results = []
    for feat in features:
        geom = feat.get("geometry") or {}
        if geom.get("type") != "Point":
            continue
        coords = geom.get("coordinates", [])
        if len(coords) < 2:
            continue
        lon, lat = coords[0], coords[1]
        dist = haversine_km(center_lat, center_lon, lat, lon)
        if dist <= radius_km:
            results.append((feat, round(dist, 2)))
    results.sort(key=lambda x: x[1])
    return results


def _polygon_bounds(geometry: dict):
    """Return [[min_lat, min_lon], [max_lat, max_lon]] for a Polygon or MultiPolygon."""
    coords = []
    gtype = geometry.get("type", "")
    if gtype == "Polygon":
        for ring in geometry.get("coordinates", []):
            coords.extend(ring)
    elif gtype == "MultiPolygon":
        for poly in geometry.get("coordinates", []):
            for ring in poly:
                coords.extend(ring)
    if not coords:
        return None
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    return [[min(lats), min(lons)], [max(lats), max(lons)]]


def make_folium_map(
    geojson: dict,
    dataset_name: str = "",
    context_layers: list = None,
    highlight_location: str = "",
    buffer_center: tuple = None,
    buffer_radius_km: float = None,
    buffer_label: str = "",
    draw_bbox: dict = None,
) -> folium.Map:
    """
    Build a Folium map from a GeoJSON FeatureCollection.

    - Points → CircleMarker with popup (+ optional district/road context layers)
    - Lines / Polygons → GeoJson overlay
    - No geometry → plain Zambia-centered basemap

    context_layers: list of {"geojson": dict, "name": str, "type": "boundary"|"road"}
      Added as background layers when the primary data is points.

    highlight_location: district or province name — when set, the matching boundary
      polygon is highlighted and the map zooms to its extent.

    buffer_center: (lat, lon) — center point for a buffer circle.
    buffer_radius_km: radius in kilometres — draws a dashed circle on the map.
    buffer_label: label shown in the circle tooltip.
    """
    m = folium.Map(location=ZAMBIA_CENTER, zoom_start=ZAMBIA_ZOOM, tiles="CartoDB positron")

    features = geojson.get("features", [])
    highlight_bounds = None  # bounds of the highlighted district/province polygon

    # Add context layers (districts, roads) before the main data so points sit on top
    if context_layers:
        hl_lower = highlight_location.lower() if highlight_location else ""
        for ctx in context_layers:
            ctx_feats = ctx["geojson"].get("features", [])
            if not ctx_feats:
                continue
            ctx_type = ctx.get("type", "boundary")
            if ctx_type == "boundary":
                # Separate highlighted feature from the rest so we can style it distinctly
                if hl_lower:
                    normal_feats, hl_feat = [], None
                    for feat in ctx_feats:
                        p = feat.get("properties") or {}
                        name_val = (p.get("DISTRICT") or p.get("District") or
                                    p.get("PROVINCE") or p.get("Province") or "").lower()
                        if hl_lower in name_val or name_val in hl_lower:
                            hl_feat = feat
                            if highlight_bounds is None and feat.get("geometry"):
                                highlight_bounds = _polygon_bounds(feat["geometry"])
                        else:
                            normal_feats.append(feat)
                else:
                    normal_feats, hl_feat = ctx_feats, None

                # Regular district boundaries — thin grey outline
                if normal_feats:
                    def _boundary_style(_feature):
                        return {"fillColor": "transparent", "color": "#888888", "weight": 1, "fillOpacity": 0}
                    label_fields = _safe_label_fields(normal_feats)
                    tooltip = folium.GeoJsonTooltip(fields=label_fields, aliases=label_fields, localize=True) if label_fields else None
                    folium.GeoJson(
                        {"type": "FeatureCollection", "features": normal_feats},
                        name=ctx["name"],
                        style_function=_boundary_style,
                        tooltip=tooltip,
                    ).add_to(m)

                # Highlighted district — filled with accent colour + thicker border
                if hl_feat:
                    def _hl_style(_feature):
                        return {"fillColor": "#457b9d", "color": "#1d3557", "weight": 2.5, "fillOpacity": 0.25}
                    label_fields = _safe_label_fields([hl_feat])
                    tooltip = folium.GeoJsonTooltip(fields=label_fields, aliases=label_fields, localize=True) if label_fields else None
                    folium.GeoJson(
                        {"type": "FeatureCollection", "features": [hl_feat]},
                        name=f"{highlight_location} boundary",
                        style_function=_hl_style,
                        tooltip=tooltip,
                    ).add_to(m)

            elif ctx_type == "road":
                def _road_style(_feature):
                    return {"color": "#b5838d", "weight": 1.5, "opacity": 0.6}
                label_fields = _safe_label_fields(ctx_feats)
                tooltip = folium.GeoJsonTooltip(fields=label_fields, aliases=label_fields, localize=True) if label_fields else None
                folium.GeoJson(
                    ctx["geojson"],
                    name=ctx["name"],
                    style_function=_road_style,
                    tooltip=tooltip,
                ).add_to(m)

            elif ctx_type == "water":
                def _water_style(_feature):
                    return {"fillColor": "#90e0ef", "color": "#0096c7", "weight": 1, "fillOpacity": 0.55}
                label_fields = _safe_label_fields(ctx_feats)
                tooltip = folium.GeoJsonTooltip(fields=label_fields, aliases=label_fields, localize=True) if label_fields else None
                folium.GeoJson(
                    ctx["geojson"],
                    name=ctx["name"],
                    style_function=_water_style,
                    tooltip=tooltip,
                ).add_to(m)

    if not features:
        return m

    # Check geometry presence
    first_geom = features[0].get("geometry")
    if not first_geom:
        return m

    geom_type = first_geom.get("type", "")

    if geom_type == "Point":
        # Use marker clustering for dense datasets (>30 points)
        use_cluster = len(features) > 30
        cluster = MarkerCluster(name=dataset_name or "Points").add_to(m) if use_cluster else None

        for feat in features:
            geom = feat.get("geometry") or {}
            coords = geom.get("coordinates", [])
            if len(coords) < 2:
                continue
            lon, lat = coords[0], coords[1]
            props = feat.get("properties") or {}
            color = _point_color(props, dataset_name)
            popup_html = _props_to_html(props)
            marker = folium.CircleMarker(
                location=[lat, lon],
                radius=5,
                color=color,
                fill=True,
                fill_color=color,
                fill_opacity=0.8,
                popup=folium.Popup(popup_html, max_width=300),
                tooltip=props.get("Name") or props.get("NAME") or props.get("name") or props.get("DISTRICT") or props.get("District") or "Feature",
            )
            marker.add_to(cluster if use_cluster else m)

        # Fit bounds: prefer the highlighted district polygon extent, fall back to points
        if highlight_bounds:
            m.fit_bounds(highlight_bounds)
        else:
            lats = [f["geometry"]["coordinates"][1] for f in features if f.get("geometry") and len(f["geometry"].get("coordinates", [])) >= 2]
            lons = [f["geometry"]["coordinates"][0] for f in features if f.get("geometry") and len(f["geometry"].get("coordinates", [])) >= 2]
            if lats:
                m.fit_bounds([[min(lats), min(lons)], [max(lats), max(lons)]])

    else:
        # Polygon / LineString — use GeoJson layer
        # Pick colors based on dataset type
        ds_lower = dataset_name.lower()
        if "dam" in ds_lower or "reservoir" in ds_lower:
            fill_color, line_color = "#0077b6", "#023e8a"
        elif "wetland" in ds_lower or "lake" in ds_lower or "water" in ds_lower:
            fill_color, line_color = "#48cae4", "#0077b6"
        elif "aquifer" in ds_lower or "groundwater" in ds_lower:
            fill_color, line_color = "#90e0ef", "#0096c7"
        elif "forest" in ds_lower or "woodland" in ds_lower or "reserve" in ds_lower:
            fill_color, line_color = "#2d6a4f", "#1b4332"
        elif "mine" in ds_lower or "mining" in ds_lower:
            fill_color, line_color = "#495057", "#212529"
        elif "flood" in ds_lower:
            fill_color, line_color = "#4cc9f0", "#4361ee"
        elif "railway" in ds_lower or "rail" in ds_lower or "lobito" in ds_lower or "corridor" in ds_lower:
            fill_color, line_color = "#e63946", "#9d0208"
        elif "road" in ds_lower or "highway" in ds_lower:
            fill_color, line_color = "#adb5bd", "#6c757d"
        else:
            fill_color, line_color = "#457b9d", "#1d3557"

        _is_railway = "railway" in ds_lower or "rail" in ds_lower or "lobito" in ds_lower or "corridor" in ds_lower
        _line_weight = 3.5 if _is_railway else 2

        def style_fn(feature, _fc=fill_color, _lc=line_color, _lw=_line_weight):
            return {
                "fillColor": _fc,
                "color": _lc,
                "weight": _lw,
                "fillOpacity": 0.45,
            }

        def highlight_fn(feature):
            return {"weight": 3.5, "color": "#e63946", "fillOpacity": 0.65}

        label_fields = _pick_label_fields(features)
        tooltip = folium.GeoJsonTooltip(fields=label_fields, aliases=label_fields, localize=True) if label_fields else None
        folium.GeoJson(
            geojson,
            name=dataset_name,
            style_function=style_fn,
            highlight_function=highlight_fn,
            tooltip=tooltip,
        ).add_to(m)

        # Compute bounds directly from feature coordinates (more reliable than m.get_bounds())
        all_lats, all_lons = [], []
        for feat in features:
            geom = feat.get("geometry") or {}
            gtype = geom.get("type", "")
            coords = geom.get("coordinates", [])
            if gtype in ("Polygon", "MultiPolygon"):
                b = _polygon_bounds(geom)
                if b:
                    all_lats += [b[0][0], b[1][0]]
                    all_lons += [b[0][1], b[1][1]]
            elif gtype == "LineString":
                # coords = [[lon,lat], [lon,lat], ...]
                for pt in coords:
                    if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                        all_lons.append(pt[0])
                        all_lats.append(pt[1])
            elif gtype == "MultiLineString":
                # coords = [[[lon,lat],...], [[lon,lat],...], ...]
                for line in coords:
                    for pt in line:
                        if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                            all_lons.append(pt[0])
                            all_lats.append(pt[1])
        if all_lats and all_lons:
            m.fit_bounds([[min(all_lats), min(all_lons)], [max(all_lats), max(all_lons)]])

    # Resolve theme color once — used for both buffer and bbox
    _border_color, _fill_color, _theme_label = _dataset_theme(dataset_name)

    # Drawn bbox overlay — colored rectangle matching the dataset theme
    if draw_bbox:
        _bb = draw_bbox
        _bbox_coords = [
            [_bb["min_lat"], _bb["min_lon"]],
            [_bb["min_lat"], _bb["max_lon"]],
            [_bb["max_lat"], _bb["max_lon"]],
            [_bb["max_lat"], _bb["min_lon"]],
            [_bb["min_lat"], _bb["min_lon"]],
        ]
        folium.Polygon(
            locations=_bbox_coords,
            color=_border_color,
            weight=2,
            dash_array="6 3",
            fill=True,
            fill_color=_fill_color,
            fill_opacity=0.12,
            tooltip=f"Search area — {_theme_label}",
        ).add_to(m)

    # Buffer circle — drawn on top of all other layers, colored by dataset theme
    if buffer_center and buffer_radius_km and buffer_radius_km > 0:
        blat, blon = buffer_center
        radius_m = buffer_radius_km * 1000
        tooltip_text = buffer_label or f"{buffer_radius_km} km buffer"
        folium.Circle(
            location=[blat, blon],
            radius=radius_m,
            color=_border_color,
            weight=2,
            dash_array="8 4",
            fill=True,
            fill_color=_fill_color,
            fill_opacity=0.12,
            tooltip=tooltip_text,
        ).add_to(m)
        # Pin at the center
        folium.CircleMarker(
            location=[blat, blon],
            radius=5,
            color=_border_color,
            fill=True,
            fill_color=_fill_color,
            fill_opacity=1.0,
            tooltip=f"Buffer center — {tooltip_text}",
        ).add_to(m)
        # Zoom to fit the buffer
        offset_deg = buffer_radius_km / 111.0
        m.fit_bounds([
            [blat - offset_deg, blon - offset_deg],
            [blat + offset_deg, blon + offset_deg],
        ])

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


def _pick_label_fields(features: list, max_fields: int = 3) -> list:
    """Pick the most useful label fields for a GeoJson tooltip."""
    if not features:
        return []
    props = features[0].get("properties") or {}
    # Fields that are never useful as labels
    _skip = {
        "OBJECTID", "FID", "GlobalID", "Shape_Area", "Shape_Length",
        "Shape__Area", "Shape__Length", "latitude", "longitude",
        "distance_km", "objectid", "fid", "globalid",
    }
    priority = [
        # Name fields
        "Name", "NAME", "name", "label", "LABEL",
        # Railway / transport
        "operator", "Operator", "OPERATOR",
        "railway", "Railway", "line_name", "LineName",
        "line", "route", "ref", "REF",
        # Administrative
        "District", "DISTRICT", "Province", "PROVINCE", "REGION",
        # Classification
        "Type", "TYPE", "type", "class", "CLASS", "category",
    ]
    chosen = [f for f in priority if f in props]
    if not chosen:
        # Fall back to any field with a meaningful string value
        for k, v in props.items():
            if k in _skip:
                continue
            if isinstance(v, str) and v.strip():
                chosen.append(k)
            if len(chosen) >= max_fields:
                break
    return chosen[:max_fields]


def _safe_label_fields(features: list, max_fields: int = 2) -> list:
    """Like _pick_label_fields but only returns fields that exist in the data.
    Used for context layers (districts, roads) where field names vary."""
    if not features:
        return []
    props = features[0].get("properties") or {}
    available = set(props.keys())
    priority = ["DISTRICT", "District", "PROVINCE", "Province", "NAME", "Name",
                "name", "road_name", "ref", "highway", "TYPE", "type"]
    chosen = [f for f in priority if f in available]
    if not chosen:
        chosen = [k for k in props if k not in ("OBJECTID", "FID", "Shape_Area", "Shape_Length")]
    return chosen[:max_fields]
