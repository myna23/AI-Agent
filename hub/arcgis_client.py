"""
ArcGIS API for Python client — parallel fetch path alongside hub/client.py.

Uses the official Esri SDK for richer catalog search and feature queries.
Falls back gracefully if the arcgis package is unavailable or a request fails.

Authentication: anonymous by default (public datasets only).
When Hub admin provides a token, pass it to ArcGISClient(token="...").
That same token works in the Streamlit Cloud app — no hosting changes needed.
"""

from __future__ import annotations

import json
import os
from typing import Optional

try:
    from arcgis.gis import GIS
    from arcgis.features import FeatureLayer
    _ARCGIS_AVAILABLE = True
except ImportError:
    _ARCGIS_AVAILABLE = False

_ORG_ID = "iQ1dY19aHwbSDYIF"   # Zambia GeoHub ArcGIS Online org
MAX_FEATURES = int(os.getenv("MAX_FEATURES", "200"))


class ArcGISClient:
    """
    ArcGIS API for Python wrapper for the Zambia GeoHub.

    Provides richer catalog search and feature querying than raw requests:
      - Handles auth transparently (token / username+password)
      - Consistent SDK headers that ArcGIS servers accept from cloud IPs
      - Clean pagination via result_record_count
      - Ready for private dataset access once admin provides credentials

    Usage::
        client = ArcGISClient()                    # anonymous
        client = ArcGISClient(token="<token>")     # authenticated
        geojson = client.query_features(url, district_filter="Kalomo")
    """

    def __init__(self, token: str = "", username: str = "", password: str = ""):
        if not _ARCGIS_AVAILABLE:
            raise ImportError(
                "arcgis package not installed. Run: pip install arcgis"
            )

        if token:
            self._gis = GIS("https://www.arcgis.com", token=token)
        elif username and password:
            self._gis = GIS(
                "https://www.arcgis.com", username=username, password=password
            )
        else:
            self._gis = GIS()  # anonymous — public datasets only

    # ------------------------------------------------------------------
    # Catalog search
    # ------------------------------------------------------------------

    def search_catalog(self, max_items: int = 100) -> list[dict]:
        """
        Search the Zambia GeoHub for public Feature Services.

        Queries both the zmb tag and the Hub org ID, deduplicates, and
        returns a list of dataset dicts that match the HubClient schema.
        """
        seen_ids: set = set()
        raw_items: list = []

        for q in (
            'tags:zmb type:"Feature Service"',
            f'orgid:{_ORG_ID} type:"Feature Service"',
        ):
            try:
                items = self._gis.content.search(query=q, max_items=max_items)
                raw_items.extend(items)
            except Exception:
                pass

        catalog: list[dict] = []
        for item in raw_items:
            if item.itemid in seen_ids:
                continue
            seen_ids.add(item.itemid)

            url = (item.url or "").rstrip("/")
            if not url or "FeatureServer" not in url:
                continue

            # For org results, skip non-Zambia items that slipped through
            text = f"{item.title} {item.snippet or ''} {' '.join(item.tags or [])}"
            if _ORG_ID in (item.owner or "") and not any(
                kw in text.lower()
                for kw in ("zambia", "zmb", "lusaka", "copperbelt")
            ):
                continue

            catalog.append(
                {
                    "id": item.itemid,
                    "name": item.title,
                    "description": (item.snippet or item.description or "")[:500],
                    "url": url,
                    "tags": item.tags or [],
                    "fields": [],  # populated on demand
                }
            )

        return catalog

    # ------------------------------------------------------------------
    # Feature queries
    # ------------------------------------------------------------------

    def query_features(
        self,
        feature_url: str,
        where: str = "1=1",
        max_features: int = MAX_FEATURES,
        out_fields: str = "*",
        return_geometry: bool = True,
        district_filter: str = "",
        province_filter: str = "",
    ) -> dict:
        """
        Query a FeatureServer layer and return a GeoJSON-compatible dict.

        The ArcGIS SDK sends proper Esri headers so requests reach servers
        that may reject raw HTTP calls from cloud datacenter IPs.

        Parameters
        ----------
        feature_url     : Full FeatureLayer URL (ending in /0, /1, etc.)
        where           : SQL WHERE clause (default: all features)
        max_features    : Cap on records returned
        district_filter : Add a District= clause automatically
        province_filter : Add a Province= clause automatically
        """
        # Append location filters
        if district_filter:
            clause = f"District='{district_filter}' OR DISTRICT='{district_filter}'"
            where = clause if where == "1=1" else f"({where}) AND ({clause})"
        if province_filter:
            clause = f"Province='{province_filter}' OR PROVINCE='{province_filter}'"
            where = clause if where == "1=1" else f"({where}) AND ({clause})"

        layer = FeatureLayer(feature_url, gis=self._gis)
        feature_set = layer.query(
            where=where,
            out_fields=out_fields,
            result_record_count=max_features,
            return_geometry=return_geometry,
        )

        raw = feature_set.to_geojson
        geojson: dict = json.loads(raw) if isinstance(raw, str) else raw

        # Normalise: some SDK versions return a FeatureCollection; others a dict
        if "features" not in geojson:
            # Try converting from the features list directly
            geojson = {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": f.geometry.__geo_interface__
                        if (return_geometry and f.geometry)
                        else None,
                        "properties": dict(f.attributes),
                    }
                    for f in feature_set.features
                ],
            }

        return geojson

    def get_feature_count(
        self, feature_url: str, where: str = "1=1"
    ) -> Optional[int]:
        """Return the total record count for a where clause without fetching features."""
        try:
            layer = FeatureLayer(feature_url, gis=self._gis)
            return layer.query(where=where, return_count_only=True)
        except Exception:
            return None

    def get_layer_fields(self, feature_url: str) -> list[dict]:
        """
        Return field metadata for a FeatureLayer as a list of dicts
        compatible with the HubClient fields format.
        """
        try:
            layer = FeatureLayer(feature_url, gis=self._gis)
            return [
                {
                    "name": f["name"],
                    "alias": f.get("alias") or f["name"],
                    "type": f.get("type", "esriFieldTypeString"),
                }
                for f in (layer.properties.get("fields") or [])
            ]
        except Exception:
            return []


# ------------------------------------------------------------------
# Module-level helpers (safe to call even if arcgis isn't installed)
# ------------------------------------------------------------------

def is_available() -> bool:
    """True if the arcgis package is installed."""
    return _ARCGIS_AVAILABLE


# Singleton — created once, reused across calls (anonymous connection)
_default_client: Optional[ArcGISClient] = None


def get_client(token: str = "") -> Optional[ArcGISClient]:
    """
    Return a shared ArcGISClient instance, or None if package unavailable.

    Pass token= to create an authenticated client (invalidates the singleton).
    """
    global _default_client
    if not _ARCGIS_AVAILABLE:
        return None
    if token:
        # Always create fresh when a token is provided
        return ArcGISClient(token=token)
    if _default_client is None:
        try:
            _default_client = ArcGISClient()
        except Exception:
            return None
    return _default_client
