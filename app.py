"""
Zambia GeoHub AI — Single intelligent chat interface.

The AI detects intent from the user's message:
  - Questions / exploration  → answer with map
  - "generate a report"      → build report, offer Word + PDF download
  - "summarise / summarize"  → plain-language dataset summary
  - "what data is available" → list the catalog

Run locally:
    streamlit run app.py
"""

import streamlit as st
from streamlit_folium import st_folium

from hub.client import HubClient
import hub.client as _hub_client_module
from ai.claude_client import ClaudeClient
from ai.prompts import (
    chatbot_system_prompt,
    chatbot_user_prompt,
    summarizer_system_prompt,
    summarizer_prompt,
    report_system_prompt,
    report_prompt,
)
from reports.builder import ReportBuilder
from utils.geo_utils import (
    make_folium_map, summarize_geojson, geojson_to_sample_rows,
    haversine_km, features_within_km, polygon_centroid, assign_districts,
    _point_in_polygon,
)
import json as _json_mod
import os as _os_mod
import base64 as _base64_mod
import io as _io_mod

# Module-level buffer for capturing partial streamed responses across reruns.
# Keyed by a session identifier derived from session_state id.
_STREAM_BUFFERS: dict = {}

# Pre-load context layers once (district boundaries + roads) for map background.
# These are shown underneath point datasets so users can see which district/road
# is nearest to each church, market, health facility, school, etc.
@st.cache_resource(show_spinner=False)
def _load_context_layers():
    data_dir = _os_mod.path.join(_os_mod.path.dirname(__file__), "data")
    layers = []
    _dist_path = _os_mod.path.join(data_dir, "districts.json")
    if _os_mod.path.exists(_dist_path):
        with open(_dist_path) as f:
            layers.append({"geojson": _json_mod.load(f), "name": "Districts", "type": "boundary"})
    _road_path = _os_mod.path.join(data_dir, "roads.json")
    if _os_mod.path.exists(_road_path):
        with open(_road_path) as f:
            layers.append({"geojson": _json_mod.load(f), "name": "Major Roads", "type": "road"})
    return layers

@st.cache_resource(show_spinner=False)
def _load_water_layer():
    """Load wetlands/lakes polygon layer for use as water-body context."""
    data_dir = _os_mod.path.join(_os_mod.path.dirname(__file__), "data")
    path = _os_mod.path.join(data_dir, "wetlands.json")
    if _os_mod.path.exists(path):
        with open(path) as f:
            return {"geojson": _json_mod.load(f), "name": "Lakes & Wetlands", "type": "water"}
    return None

_CONTEXT_LAYERS = _load_context_layers()
_WATER_LAYER = _load_water_layer()


def _map(geojson: dict, name: str, with_context: bool = False, highlight_location: str = "") -> object:
    """Wrapper: adds district + road context layers for point datasets."""
    ctx = _CONTEXT_LAYERS if with_context else None
    return make_folium_map(geojson, name, context_layers=ctx, highlight_location=highlight_location)


def _build_suggestions(question: str, has_location: bool, has_data: bool, ds_name: str) -> list:
    """Return up to 3 context-aware follow-up suggestion strings."""
    suggestions = []
    q = question.lower()
    location = question.split(" in ")[-1].strip().rstrip("?") if " in " in q else ""

    # Dataset-specific suggestions
    if "health" in ds_name.lower() or "hospital" in q or "clinic" in q:
        if has_location and location:
            suggestions.append(f"Which district in {location} has the most health facilities?")
        if "primary" not in q:
            suggestions.append("Compare primary vs secondary health facilities")
        if has_location and "school" not in q:
            suggestions.append(f"How many schools are in {location}?" if location else "How many schools are in Lusaka?")

    elif "school" in ds_name.lower() or "school" in q or "education" in q:
        if has_location and location:
            suggestions.append(f"Which district in {location} has the most schools?")
        if "primary" not in q:
            suggestions.append("Compare primary vs secondary schools")
        if has_location:
            suggestions.append(f"How many health facilities are in {location}?" if location else "What are the health facilities near schools?")

    elif "settlement" in ds_name.lower() or "settlement" in q or "village" in q:
        if has_location and location:
            suggestions.append(f"What is the flood risk for settlements in {location}?")
            suggestions.append(f"How many schools are in {location}?")
        suggestions.append("Which province has the most settlements?")

    elif "road" in ds_name.lower() or "road" in q:
        if has_location and location:
            suggestions.append(f"What is the road surface breakdown in {location}?")
        suggestions.append("Which roads are unpaved or in poor condition?")
        if has_location and location:
            suggestions.append(f"How many settlements are accessible in {location}?")

    elif "flood" in ds_name.lower() or "flood" in q:
        if has_location and location:
            suggestions.append(f"How many settlements are in flood-prone areas in {location}?")
        suggestions.append("Which province has the most flood-prone districts?")
        if has_location and location:
            suggestions.append(f"What are the roads like in {location}?")

    # Generic fallbacks if no specific suggestions added yet
    if len(suggestions) < 2:
        if has_location and location and "compare" not in q:
            suggestions.append(f"Compare districts in {location}")
        if has_data and "table" not in q:
            suggestions.append(f"Show me a breakdown of {ds_name} by district")
        if has_data and "report" not in q:
            suggestions.append(f"Generate a report on {ds_name}")
        if has_location and "flood" not in q and "risk" not in q:
            suggestions.append(f"What is the flood risk in {location or 'this area'}?")
        if has_location and "settlement" not in q:
            suggestions.append(f"How many settlements are in {location or 'this area'}?")

    return suggestions[:3]


def _suggestion_chips(question: str, has_location: bool, has_data: bool, ds_name: str, key_prefix: str = ""):
    """Follow-up suggestions — right-aligned, stacked vertically below the answer."""
    suggestions = _build_suggestions(question, has_location, has_data, ds_name)
    if not suggestions:
        return
    st.markdown("<div style='margin-top:6px'></div>", unsafe_allow_html=True)
    _spacer, _sug_col = st.columns([3, 7])
    with _sug_col:
        for j, sug in enumerate(suggestions):
            if st.button(sug, key=f"{key_prefix}_sug_{j}", use_container_width=True):
                st.session_state.messages.append({"role": "user", "content": sug})
                st.session_state._pending_question = sug
                st.rerun()


def _render_ondemand_panel(msg_idx: int, msg: dict, ctx_layers: list = None):
    """
    Show Map / Table / Chart buttons after an answer.
    Only renders the component when the user explicitly clicks the button.
    State is stored in the message dict so it survives reruns.
    """
    has_geojson = bool(msg.get("geojson") and msg["geojson"].get("features"))
    has_data = bool(msg.get("sample_features"))
    if not has_geojson and not has_data:
        return

    # Toggle buttons — label changes to hide when already shown
    _ba, _bb, _bc, _bz = st.columns([1.4, 1.4, 1.4, 8])
    with _ba:
        if has_geojson:
            if st.button("🗺️ Map", key=f"mapbtn_{msg_idx}", use_container_width=True):
                msg["map_shown"] = not msg.get("map_shown", False)
                st.rerun()
    with _bb:
        if has_data:
            if st.button("📊 Table", key=f"tblbtn_{msg_idx}", use_container_width=True):
                msg["table_shown"] = not msg.get("table_shown", False)
                st.rerun()
    with _bc:
        if has_data:
            if st.button("📈 Chart", key=f"chtbtn_{msg_idx}", use_container_width=True):
                msg["chart_shown"] = not msg.get("chart_shown", False)
                st.rerun()

    # Render requested components
    if msg.get("map_shown") and has_geojson:
        gjson = msg["geojson"]
        ds_name_lower = (msg.get("ds_name", "") or "").lower()
        layers = ctx_layers if ctx_layers else _CONTEXT_LAYERS
        # For non-point datasets (polygons/lines), use water layer as context for dam datasets
        if _is_point_geojson(gjson):
            map_layers = layers
        elif ("dam" in ds_name_lower or "reservoir" in ds_name_lower) and _WATER_LAYER:
            map_layers = [_WATER_LAYER]
        else:
            map_layers = None
        st_folium(
            make_folium_map(
                gjson,
                msg.get("ds_name", ""),
                context_layers=map_layers,
                highlight_location=msg.get("location", ""),
                buffer_center=msg.get("buffer_center"),
                buffer_radius_km=msg.get("buffer_radius_km"),
                buffer_label=(
                    f"{msg.get('buffer_radius_km')} km radius — {msg.get('location', '')}"
                    if msg.get("buffer_radius_km") else ""
                ),
            ),
            width=720, height=340, returned_objects=[], key=f"map_{msg_idx}"
        )

    if msg.get("table_shown") and has_data:
        _render_data_tables(msg["sample_features"], msg.get("ds_name", "Data"), key_prefix=f"tbl_{msg_idx}")

    if msg.get("chart_shown") and has_data:
        _render_charts_only(msg["sample_features"], msg.get("ds_name", "Data"), key_prefix=f"cht_{msg_idx}")


def _render_charts_only(sample_features: list, ds_name: str, key_prefix: str = ""):
    """Bar charts for categorical fields; line/bar chart for numeric fields as fallback."""
    import pandas as _pd
    rows = [r for r in sample_features if "_note" not in r]
    if not rows:
        st.info("No data available for chart.")
        return
    df = _pd.DataFrame(rows)
    # Drop non-useful columns
    drop_cols = [c for c in df.columns if c.lower() in ("objectid", "fid", "globalid", "geometry", "shape_area", "shape_length")]
    df = df.drop(columns=[c for c in drop_cols if c in df.columns])

    shown = 0
    # 1. Categorical bar charts
    cat_fields = ["District", "DISTRICT", "Province", "PROVINCE", "Type", "TYPE",
                  "SubType", "Facility_T", "fclass", "surface", "Status", "STATUS",
                  "Classifica", "S_CLASS", "DOMINANT"]
    for field in cat_fields:
        if field not in df.columns or shown >= 2:
            continue
        counts = df[field].dropna().astype(str)
        counts = counts[counts != "None"].value_counts().head(15)
        if len(counts) < 2:
            continue
        chart_df = counts.reset_index()
        chart_df.columns = [field, "Count"]
        st.markdown(f"**{ds_name} — by {field}**")
        st.bar_chart(chart_df.set_index(field)["Count"])
        shown += 1

    # 2. Numeric bar charts as fallback if no categoricals found
    if shown == 0:
        skip = {"lat", "long", "lat_", "long_", "x", "y", "objectid", "fid"}
        num_cols = [c for c in df.select_dtypes(include="number").columns if c.lower() not in skip][:3]
        for col in num_cols:
            series = df[col].dropna()
            if len(series) < 2:
                continue
            # Use index label if available, else row number
            label_col = next((c for c in ["Name", "NAME", "District", "DISTRICT", "Province"] if c in df.columns), None)
            chart_df = df[[label_col, col]].dropna() if label_col else series.reset_index()
            if label_col:
                chart_df = chart_df.set_index(label_col)
            st.markdown(f"**{ds_name} — {col}**")
            st.bar_chart(chart_df[col] if label_col else chart_df)
            shown += 1

    if shown == 0:
        st.info("No suitable fields found for a chart with this dataset.")


def _render_data_tables(sample_features: list, ds_name: str, key_prefix: str = ""):
    """
    Render an expandable data table + bar charts for sample feature records.
    - Shows all sample rows as a scrollable dataframe
    - For categorical fields (District, Province, Type, etc.) shows a bar chart
    """
    import pandas as _pd

    if not sample_features:
        return
    # Filter out internal placeholder notes
    rows = [r for r in sample_features if "_note" not in r]
    if not rows:
        return

    df = _pd.DataFrame(rows)

    # Drop geometry columns if any snuck in
    geo_cols = [c for c in df.columns if c.lower() in ("geometry", "shape", "shape_area", "shape_length", "objectid", "fid", "globalid")]
    df = df.drop(columns=[c for c in geo_cols if c in df.columns])

    with st.expander(f"Data table — {ds_name} ({len(rows)} records)", expanded=True):
        st.dataframe(df, use_container_width=True, height=260)

        # CSV download
        st.download_button(
            "⬇️ Download CSV",
            df.to_csv(index=False).encode("utf-8"),
            file_name=f"{ds_name.replace(' ', '_')}_data.csv",
            mime="text/csv",
            key=f"{key_prefix}_csv",
            use_container_width=False,
        )

        # Auto summary stats for numeric columns
        num_cols = df.select_dtypes(include="number").columns.tolist()
        num_cols = [c for c in num_cols if c.upper() not in ("OBJECTID", "FID", "LAT", "LONG", "LAT_", "LONG_", "X", "Y")]
        if num_cols:
            st.markdown("**Summary statistics**")
            st.dataframe(df[num_cols].describe().round(2), use_container_width=True)

        # Value counts for key categorical columns
        cat_priority = ["District", "DISTRICT", "Province", "PROVINCE", "Type", "TYPE",
                        "SubType", "Facility_T", "fclass", "surface", "Status", "STATUS"]
        shown_cat = 0
        for field in cat_priority:
            if field not in df.columns or shown_cat >= 2:
                continue
            counts = df[field].dropna().astype(str)
            counts = counts[counts != "None"].value_counts().head(10)
            if len(counts) < 2:
                continue
            st.markdown(f"**Breakdown by {field}**")
            count_df = counts.reset_index()
            count_df.columns = [field, "Count"]
            count_df["% of total"] = (count_df["Count"] / count_df["Count"].sum() * 100).round(1).astype(str) + "%"
            st.dataframe(count_df, use_container_width=True, hide_index=True)
            shown_cat += 1


def _is_point_geojson(geojson: dict) -> bool:
    feats = geojson.get("features", [])
    if not feats:
        return False
    g = feats[0].get("geometry")
    return bool(g and g.get("type") == "Point")

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Zambia GeoHub AI",
    page_icon="🗺️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ---------------------------------------------------------------------------
# Styling — clean, Hub-like look
# ---------------------------------------------------------------------------
st.markdown("""
<style>
/* Floating chat bubble (for Hub iframe embed) */
#zmb-chat-btn {
    position: fixed; bottom: 28px; right: 28px;
    width: 58px; height: 58px; border-radius: 50%;
    background: #1d3557; color: white; font-size: 26px;
    border: none; cursor: pointer;
    box-shadow: 0 4px 16px rgba(0,0,0,0.25);
    z-index: 9999; display: flex;
    align-items: center; justify-content: center;
}
#zmb-chat-btn:hover { background: #457b9d; }
#zmb-chat-panel {
    position: fixed; bottom: 100px; right: 28px;
    width: 380px; height: 520px; background: white;
    border-radius: 16px; box-shadow: 0 8px 32px rgba(0,0,0,0.2);
    z-index: 9998; display: none; flex-direction: column;
    overflow: hidden; border: 1px solid #e0e0e0;
}
#zmb-chat-panel.open { display: flex; }
#zmb-chat-header {
    background: #1d3557; color: white;
    padding: 14px 18px; font-weight: 600; font-size: 15px;
    display: flex; justify-content: space-between; align-items: center;
}
#zmb-chat-close { cursor: pointer; font-size: 20px; background: none; border: none; color: white; }
#zmb-chat-body { flex: 1; overflow-y: auto; padding: 14px; font-size: 13px; background: #f8fbfd; }
#zmb-chat-footer { padding: 10px; border-top: 1px solid #eee; display: flex; gap: 8px; background: white; }
#zmb-chat-input { flex: 1; border: 1px solid #ccc; border-radius: 8px; padding: 8px 12px; font-size: 13px; outline: none; }
#zmb-chat-send { background: #1d3557; color: white; border: none; border-radius: 8px; padding: 8px 14px; cursor: pointer; font-size: 13px; }
#zmb-chat-send:hover { background: #457b9d; }
.zmb-msg-user { background: #1d3557; color: white; border-radius: 12px 12px 2px 12px; padding: 8px 12px; margin: 6px 0 6px 30px; font-size: 13px; }
.zmb-msg-ai { background: white; border: 1px solid #dde; border-radius: 12px 12px 12px 2px; padding: 8px 12px; margin: 6px 30px 6px 0; font-size: 13px; }

/* Intent badge */
.intent-badge {
    display: inline-block; font-size: 11px; font-weight: 600;
    padding: 2px 10px; border-radius: 20px; margin-bottom: 6px;
}
.intent-chat    { background: #e8f4fd; color: #1d3557; }
.intent-report  { background: #e8f8f0; color: #1a6b3c; }
.intent-summary { background: #fff4e6; color: #7a4800; }
</style>

<button id="zmb-chat-btn" title="Ask the Zambia GeoHub AI">🗺️</button>
<div id="zmb-chat-panel">
  <div id="zmb-chat-header">
    <span>Zambia GeoHub AI</span>
    <button id="zmb-chat-close">✕</button>
  </div>
  <div id="zmb-chat-body">
    <div class="zmb-msg-ai" id="zmb-welcome">Hi! Ask me anything about Zambia's geospatial data, or say "generate a report on health facilities" or "summarise the schools dataset".</div>
  </div>
  <div id="zmb-chat-footer">
    <input id="zmb-chat-input" type="text" placeholder="Ask about Zambia data..." />
    <button id="zmb-chat-send">Send</button>
  </div>
</div>
<script>
const btn = document.getElementById('zmb-chat-btn');
const panel = document.getElementById('zmb-chat-panel');
const closeBtn = document.getElementById('zmb-chat-close');
const input = document.getElementById('zmb-chat-input');
const sendBtn = document.getElementById('zmb-chat-send');
const body = document.getElementById('zmb-chat-body');
btn.onclick = () => panel.classList.toggle('open');
closeBtn.onclick = () => panel.classList.remove('open');
function addMsg(text, role) {
    const div = document.createElement('div');
    div.className = role === 'user' ? 'zmb-msg-user' : 'zmb-msg-ai';
    div.innerText = text;
    body.appendChild(div);
    body.scrollTop = body.scrollHeight;
}
// Detect dataset context from URL params and customise welcome message
(function() {
    const params = new URLSearchParams(window.location.search);
    const dsName = params.get('dataset_name');
    if (dsName) {
        const welcome = document.getElementById('zmb-welcome');
        if (welcome) {
            welcome.innerText = 'Hi! I can see you\'re viewing the ' + dsName + ' dataset. Ask me any question about it — for example "What districts are covered?" or "Generate a report on this dataset".';
        }
    }
})();
sendBtn.onclick = () => {
    const q = input.value.trim();
    if (!q) return;
    addMsg(q, 'user');
    input.value = '';
    setTimeout(() => addMsg('Use the main chat above for full AI responses with maps and downloads.', 'ai'), 800);
};
input.addEventListener('keydown', e => { if (e.key === 'Enter') sendBtn.click(); });
</script>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------
@st.cache_resource(show_spinner=False)
def get_hub(_v=5): return HubClient()

@st.cache_resource(show_spinner=False)
def get_claude(_v=3): return ClaudeClient()

@st.cache_resource(show_spinner=False)
def get_builder(_v=3): return ReportBuilder()

hub = get_hub()
claude = get_claude()
builder = get_builder()

# ---------------------------------------------------------------------------
# Token refresh UI — shown in sidebar when token is missing or expired
# ---------------------------------------------------------------------------
_token_missing = not _hub_client_module._ARCGIS_TOKEN
_token_expired = _hub_client_module.token_expired

with st.sidebar:
    st.markdown("### Zambia GeoHub")
    if _token_missing or _token_expired:
        if _token_expired:
            st.warning("🔑 Access token expired — private datasets unavailable.")
        else:
            st.info("🔑 No access token — some datasets require authentication.")

        st.markdown(
            "**To unlock all datasets:**\n\n"
            "1. Go to [zmb-geowb.hub.arcgis.com](https://zmb-geowb.hub.arcgis.com) "
            "and make sure you are logged in\n"
            "2. Press **F12** → **Network** tab → reload the page\n"
            "3. Filter by `token` → click `self?f=json&token=...`\n"
            "4. Copy the long string after `token=` in the Request URL\n"
            "5. Paste it below"
        )
        _new_token = st.text_area(
            "Paste the full URL or just the token",
            height=80,
            placeholder="https://services.arcgis.com/...?token=abc123... or just the token",
            key="token_input",
        )
        if st.button("Apply Token", type="primary", use_container_width=True):
            _raw = _new_token.strip()
            if _raw:
                # Auto-extract token if user pasted a full URL
                if "token=" in _raw:
                    _raw = _raw.split("token=", 1)[1].split("&")[0].strip()
                import datetime as _dt
                _hub_client_module.set_token(_raw)
                st.session_state["token_set_date"] = _dt.date.today().isoformat()
                st.success("✅ Token saved — private datasets are now unlocked.")
                st.rerun()
            else:
                st.error("Please paste a URL or token first.")
    else:
        st.success("🔓 Authenticated — all datasets available")

        # Token expiry reminder — stored when token is first applied
        import datetime as _dt
        _token_set_date = st.session_state.get("token_set_date")
        if not _token_set_date:
            # Default: assume token was set today if not recorded
            st.session_state["token_set_date"] = _dt.date.today().isoformat()
            _token_set_date = st.session_state["token_set_date"]

        _set_date = _dt.date.fromisoformat(_token_set_date)
        _expiry_date = _set_date + _dt.timedelta(days=14)
        _days_left = (_expiry_date - _dt.date.today()).days

        if _days_left <= 0:
            st.error(f"⚠️ Token likely expired — please refresh it now.")
        elif _days_left <= 3:
            st.warning(f"⏰ Token expires in **{_days_left} day(s)** ({_expiry_date.strftime('%d %b %Y')}). Refresh soon.")
        else:
            st.caption(f"🔑 Token expires: {_expiry_date.strftime('%d %b %Y')} ({_days_left} days left)")

        if st.button("Update Token", use_container_width=True):
            st.session_state["_show_token_input"] = True

        if st.session_state.get("_show_token_input"):
            _new_token2 = st.text_area(
                "Paste the full URL or just the token",
                height=80,
                placeholder="https://services.arcgis.com/...?token=abc123... or just the token",
                key="token_update",
            )
            if st.button("Save New Token", type="primary", use_container_width=True):
                _raw2 = _new_token2.strip()
                if _raw2:
                    # Auto-extract token if user pasted a full URL
                    if "token=" in _raw2:
                        _raw2 = _raw2.split("token=", 1)[1].split("&")[0].strip()
                    _hub_client_module.set_token(_raw2)
                    st.session_state["_show_token_input"] = False
                    st.session_state["token_set_date"] = _dt.date.today().isoformat()
                    st.success("✅ Token updated.")
                    st.rerun()

    # ------------------------------------------------------------------
    # Draw tool — compact map in sidebar, always visible
    # ------------------------------------------------------------------
    st.markdown("---")
    st.markdown("### 🗺️ Draw an Area")
    st.caption("Draw a rectangle on the map then ask your question — the AI will query only within your drawn area.")

    import folium as _folium_draw_mod
    from folium.plugins import Draw as _FoliumDraw
    _draw_map = _folium_draw_mod.Map(location=[-13.5, 28.5], zoom_start=5, tiles="CartoDB positron")
    _FoliumDraw(
        export=False,
        draw_options={
            "rectangle": {"shapeOptions": {"color": "#e63946"}},
            "polygon": {"shapeOptions": {"color": "#e63946"}},
            "circle": {"shapeOptions": {"color": "#e63946"}},
            "polyline": {"shapeOptions": {"color": "#e63946", "weight": 3}},
            "marker": False,
            "circlemarker": False,
        },
        edit_options={"edit": True, "remove": True},
    ).add_to(_draw_map)
    # Use a version key so clearing forces a fresh map with no drawn shapes
    _draw_map_version = st.session_state.get("draw_map_version", 0)
    _draw_result = st_folium(_draw_map, width="100%", height=320,
                             returned_objects=["last_active_drawing"],
                             key=f"draw_tool_map_{_draw_map_version}")
    _drawn = (_draw_result or {}).get("last_active_drawing")
    # Only process the drawn shape if user hasn't just cleared it
    if _drawn and not st.session_state.get("_bbox_cleared"):
        _dgeom = _drawn.get("geometry", {})
        _dtype = _dgeom.get("type", "")
        _dcoords = _dgeom.get("coordinates", [])
        _flat_pts = []

        if _dtype in ("Polygon", "Rectangle") and _dcoords:
            _flat_pts = _dcoords[0]
        elif _dtype == "LineString" and _dcoords:
            _flat_pts = _dcoords
        elif _dtype == "Point" and _dcoords:
            # Circle — use the radius property to build a bbox
            _clon, _clat = _dcoords[0], _dcoords[1]
            _crad = (_drawn.get("properties") or {}).get("radius", 50000)
            # Rough degree offset: 1 degree ≈ 111km
            _deg_offset = _crad / 111000
            _flat_pts = [
                [_clon - _deg_offset, _clat - _deg_offset],
                [_clon + _deg_offset, _clat + _deg_offset],
            ]

        if _flat_pts:
            _lons = [p[0] for p in _flat_pts]
            _lats = [p[1] for p in _flat_pts]
            _bbox = {"min_lon": min(_lons), "max_lon": max(_lons),
                     "min_lat": min(_lats), "max_lat": max(_lats)}
            st.session_state["draw_bbox"] = _bbox
            st.success("✅ Area set — now ask your question.")

    # Reset cleared flag after one rerun
    st.session_state.pop("_bbox_cleared", None)

    if st.session_state.get("draw_bbox"):
        _b = st.session_state["draw_bbox"]
        st.caption(f"Active: {_b['min_lat']:.2f}–{_b['max_lat']:.2f}°N, "
                   f"{_b['min_lon']:.2f}–{_b['max_lon']:.2f}°E")
        if st.button("🗑️ Clear area", use_container_width=True, key="clear_bbox_sidebar"):
            st.session_state.pop("draw_bbox", None)
            st.session_state["_bbox_cleared"] = True
            # Increment map version to force a fresh blank map
            st.session_state["draw_map_version"] = _draw_map_version + 1
            st.rerun()
    else:
        st.caption("No area selected — draw on the map above.")

    # ------------------------------------------------------------------
    # Document upload for AI analysis
    # ------------------------------------------------------------------
    st.markdown("---")
    st.markdown("### 📄 Attach a Document")
    st.caption("PDF, Word, or TXT — AI will use it alongside the GeoHub data.")

    _uploaded_file = st.file_uploader(
        "Choose a file", type=["pdf", "docx", "txt"],
        key="doc_upload", label_visibility="collapsed",
    )
    if _uploaded_file:
        try:
            if _uploaded_file.name.endswith(".pdf"):
                import pypdf as _pypdf
                _reader = _pypdf.PdfReader(_uploaded_file)
                _doc_text = "\n".join(p.extract_text() or "" for p in _reader.pages)
            elif _uploaded_file.name.endswith(".docx"):
                import docx as _docx
                _doc = _docx.Document(_uploaded_file)
                _doc_text = "\n".join(p.text for p in _doc.paragraphs if p.text.strip())
            else:
                _doc_text = _uploaded_file.read().decode("utf-8", errors="ignore")
            _doc_text = _doc_text.strip()
            if _doc_text:
                st.session_state["uploaded_doc_text"] = _doc_text
                st.session_state["uploaded_doc_name"] = _uploaded_file.name
                st.success(f"✅ **{_uploaded_file.name}** ready")
            else:
                st.warning("No text could be extracted.")
        except Exception as _ue:
            st.error(f"Could not read file: {_ue}")

    if st.session_state.get("uploaded_doc_name"):
        st.info(f"📄 **{st.session_state['uploaded_doc_name']}**")
        if st.button("Remove", use_container_width=True, key="sidebar_clear_doc"):
            st.session_state.pop("uploaded_doc_text", None)
            st.session_state.pop("uploaded_doc_name", None)
            st.rerun()

# ---------------------------------------------------------------------------
# Context detection — dataset passed from Hub iframe embed
#
# The Hub page embeds this app as:
#   <iframe src="https://...streamlit.app/?dataset_url=...&dataset_name=...">
#
# Supported params:
#   dataset_url  — FeatureServer layer URL of the open dataset
#   dataset_name — Human-readable name (shown in the banner)
# ---------------------------------------------------------------------------
params = st.query_params
_ctx_url  = params.get("dataset_url", "")
_ctx_name = params.get("dataset_name", "")

# Resolve the context dataset from the catalog (by URL) or create a minimal entry
context_dataset = None
if _ctx_url:
    catalog = hub.get_catalog()
    # Try exact URL match first
    for ds in catalog:
        if ds["url"].rstrip("/") == _ctx_url.rstrip("/"):
            context_dataset = ds
            break
    # If not found in catalog, create a minimal entry so we can still query it
    if context_dataset is None:
        context_dataset = {
            "id": "ctx",
            "name": _ctx_name or "Selected Dataset",
            "description": f"Dataset loaded from the Zambia GeoHub: {_ctx_url}",
            "url": _ctx_url,
            "tags": ["zambia"],
            "fields": hub._fetch_fields(_ctx_url),
            "geometry_type": "Unknown",
            "extent": {},
            "modified": "",
        }

# ---------------------------------------------------------------------------
# Session state — chat persistence via URL query param (pure Python, no JS)
# ---------------------------------------------------------------------------
# Strategy: after every message pair, serialize messages → compress → base64
# → store in st.query_params["c"]. On refresh Streamlit reads the same URL
# so the param is still there and messages are restored.
# Geojson / sample_features are stripped before encoding to keep URL short.
# ---------------------------------------------------------------------------
import json as _json_mod2
import zlib as _zlib
import base64 as _b64

_CHAT_PARAM = "c"
_MAX_CHAT_MESSAGES = 10  # keep last N messages to avoid URL length limits


def _encode_chat(messages: list) -> str:
    """Serialize messages to a compact URL-safe string."""
    slim = []
    for m in messages[-_MAX_CHAT_MESSAGES:]:
        # Exclude large fields — geojson is too big for a URL param
        entry = {k: v for k, v in m.items()
                 if k not in ("geojson", "sample_features", "docx_bytes", "pdf_bytes")
                 and isinstance(v, (str, int, float, bool, list, dict, type(None)))}
        # Truncate long AI responses to keep URL short
        if "content" in entry and isinstance(entry["content"], str) and len(entry["content"]) > 800:
            entry["content"] = entry["content"][:800] + "…"
        slim.append(entry)
    raw = _json_mod2.dumps(slim, separators=(",", ":"))
    compressed = _zlib.compress(raw.encode("utf-8"), level=6)
    return _b64.urlsafe_b64encode(compressed).decode("ascii")


def _decode_chat(encoded: str) -> list:
    """Restore messages from URL param string."""
    compressed = _b64.urlsafe_b64decode(encoded)
    raw = _zlib.decompress(compressed).decode("utf-8")
    return _json_mod2.loads(raw)


def _persist_chat():
    """Write current messages into the URL query param."""
    try:
        if st.session_state.messages:
            st.query_params[_CHAT_PARAM] = _encode_chat(st.session_state.messages)
        else:
            st.query_params.pop(_CHAT_PARAM, None)
    except Exception:
        pass


def _clear_chat_storage():
    """Remove chat from URL."""
    try:
        st.query_params.pop(_CHAT_PARAM, None)
    except Exception:
        pass


# Restore messages from URL on first load
if "messages" not in st.session_state:
    _encoded = st.query_params.get(_CHAT_PARAM, "")
    if _encoded:
        try:
            st.session_state.messages = _decode_chat(_encoded)
        except Exception:
            st.session_state.messages = []
    else:
        st.session_state.messages = []

if "edit_idx" not in st.session_state:
    st.session_state.edit_idx = None
if "stop_streaming" not in st.session_state:
    st.session_state.stop_streaming = False
if "is_generating" not in st.session_state:
    st.session_state.is_generating = False

# ---------------------------------------------------------------------------
# Fixed-bottom stop button — shown while AI is generating (matches Claude UI)
# ---------------------------------------------------------------------------
if st.session_state.get("is_generating"):
    # Dim the chat input so users know not to type while generating
    st.markdown(
        """<style>
        [data-testid="stChatInput"] textarea { opacity: 0.4; }
        [data-testid="stChatInput"] button[data-testid="stChatInputSubmitButton"] { opacity: 0; pointer-events: none; }
        </style>""",
        unsafe_allow_html=True,
    )
    # Stop button rendered in the sidebar-like area above the chat input
    _stop_top_col, _ = st.columns([1, 5])
    with _stop_top_col:
        if st.button("⏹ Stop generating", key="global_stop_btn", type="primary", use_container_width=True):
            st.session_state.stop_streaming = True
            st.session_state.is_generating = False

# ---------------------------------------------------------------------------
# Intent detection
# ---------------------------------------------------------------------------
def detect_intent(text: str) -> str:
    t = text.lower()
    if any(w in t for w in ["report", "generate report", "write report", "create report"]):
        return "report"
    if any(w in t for w in ["summarise", "summarize", "summary", "overview", "brief"]):
        return "summary"
    return "chat"


def _extract_comparison_locations(text: str):
    """
    Detect 'compare X and Y' or 'X vs Y' patterns.
    Returns list of location strings if found, else empty list.
    """
    t = text.lower()
    # "compare X and Y" or "X vs Y" or "X versus Y"
    for pattern in [
        _re.search(r'compare\s+([a-z][a-z\s]{2,20})\s+and\s+([a-z][a-z\s]{2,20})', t),
        _re.search(r'([a-z][a-z\s]{2,15})\s+vs\.?\s+([a-z][a-z\s]{2,15})', t),
        _re.search(r'([a-z][a-z\s]{2,15})\s+versus\s+([a-z][a-z\s]{2,15})', t),
    ]:
        if pattern:
            locs = [pattern.group(1).strip().title(), pattern.group(2).strip().title()]
            # Filter out generic words
            locs = [l for l in locs if l.lower() not in {"the", "a", "an", "all", "zambia", "africa"}]
            if len(locs) == 2:
                return locs
    return []


# ---------------------------------------------------------------------------
# Location filter helpers
# ---------------------------------------------------------------------------
import re as _re

# Zambia districts and provinces (lowercase) for map filtering
_ZAMBIA_PROVINCES = {
    "lusaka", "copperbelt", "central", "eastern", "northern", "southern",
    "western", "northwestern", "north-western", "luapula", "muchinga",
}

def _extract_location(text: str):
    """
    Extract a district/province name from a question.
    Returns (location_str, location_type) or (None, None).
    E.g. 'schools in Chadiza' → ('Chadiza', 'district')
         'hospitals in Lusaka Province' → ('Lusaka', 'province')
    Case-insensitive: works for 'kalomo' and 'Kalomo' alike.
    """
    t = text.lower()
    # Check for explicit province mention
    for prov in _ZAMBIA_PROVINCES:
        if prov in t:
            return (prov.title(), "province")
    # Look for "in/within/around/near/at <place>" — case-insensitive
    match = _re.search(r'\b(?:in|within|around|near|at)\s+([a-zA-Z][a-z]{2,}(?:\s+[a-zA-Z][a-z]{2,})?)', text, _re.IGNORECASE)
    if match:
        loc = _re.sub(r'\s+(?:district|province|region)\s*$', '', match.group(1).strip(), flags=_re.IGNORECASE).title()
        if loc.lower() not in {"zambia", "the", "all", "zambia province", "africa"}:
            return (loc, "district")
    # Also catch "<place> district" pattern (e.g. "Kalomo district hospitals")
    match2 = _re.search(r'\b([A-Za-z][a-z]{2,}(?:\s+[A-Za-z][a-z]{2,})?)\s+district\b', text, _re.IGNORECASE)
    if match2:
        loc = match2.group(1).strip().title()
        if loc.lower() not in {"zambia", "the", "all", "africa"}:
            return (loc, "district")
    return (None, None)


def _extract_radius_km(text: str):
    """
    Detect a buffer radius in the question.
    Returns float km or None.
    Examples:
      "within 5km"  → 5.0
      "10 km radius" → 10.0
      "within 500m"  → 0.5
      "within 2 kilometres" → 2.0
    """
    import re as _re2
    t = text.lower()
    # kilometres
    m = _re2.search(r'(\d+(?:\.\d+)?)\s*(?:km|kms|kilometre|kilometres|kilometer|kilometers)', t)
    if m:
        return float(m.group(1))
    # metres → convert
    m = _re2.search(r'(\d+(?:\.\d+)?)\s*(?:m|metres|meters|metre|meter)\b', t)
    if m:
        val = float(m.group(1))
        if val >= 50:          # ignore single-digit metres that are likely typos
            return round(val / 1000, 3)
    return None


def _filter_by_location(features: list, location: str, loc_type: str) -> list:
    """Filter features to only those matching district or province."""
    loc_lower = location.lower()
    result = []
    for f in features:
        props = f.get("properties") or {}
        if loc_type == "province":
            val = (props.get("Province") or props.get("PROVINCE") or "").lower()
        else:
            val = (
                props.get("District") or props.get("DISTRICT") or
                props.get("Province") or props.get("PROVINCE") or ""
            ).lower()
        if loc_lower in val or val in loc_lower:
            result.append(f)
    return result

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
col_logo, col_title = st.columns([1, 8])
with col_title:
    st.markdown("## Zambia GeoHub AI Assistant")
    st.caption(
        "Ask questions about Zambia's geospatial data • Say **'generate a report on...'** for Word/PDF reports "
        "• Say **'summarise...'** for dataset summaries • Ask **'what data is available?'** to explore the Hub"
        " • v2.1-district-filter"
    )

# Context banner — shown when a dataset is passed from the Hub page
if context_dataset:
    st.info(
        f"📍 **Context loaded:** You are viewing **{context_dataset['name']}** on the Hub. "
        f"Your questions will be answered from this dataset first.",
        icon=None,
    )

st.markdown("---")

# ---------------------------------------------------------------------------
# Render chat history
# ---------------------------------------------------------------------------
# Index of the last assistant message — only show suggestions there
_last_assistant_idx = max(
    (i for i, m in enumerate(st.session_state.messages) if m["role"] == "assistant"),
    default=-1
)

for i, msg in enumerate(st.session_state.messages):
    if msg["role"] == "user":
        with st.chat_message("user"):
            st.markdown(msg["content"])
    else:
        with st.chat_message("assistant"):
            # Intent badge
            intent = msg.get("intent", "chat")
            badge_label = {"chat": "Answer", "report": "Report", "summary": "Summary"}.get(intent, "Answer")
            badge_class = {"chat": "intent-chat", "report": "intent-report", "summary": "intent-summary"}.get(intent, "intent-chat")
            st.markdown(f'<span class="intent-badge {badge_class}">{badge_label}</span>', unsafe_allow_html=True)

            st.markdown(msg["content"])

            # Download buttons for reports
            if msg.get("docx_bytes") and msg.get("pdf_bytes"):
                st.markdown("**Download report:**")
                c1, c2 = st.columns(2)
                ds_name = msg.get("ds_name", "report")
                c1.download_button(
                    "⬇️ Word (.docx)", msg["docx_bytes"],
                    file_name=f"{ds_name.replace(' ','_')}_report.docx",
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    key=f"docx_{i}", use_container_width=True,
                )
                c2.download_button(
                    "⬇️ PDF", msg["pdf_bytes"],
                    file_name=f"{ds_name.replace(' ','_')}_report.pdf",
                    mime="application/pdf",
                    key=f"pdf_{i}", use_container_width=True,
                )

            # Download button for summaries
            if msg.get("summary_txt"):
                st.download_button(
                    "⬇️ Download Summary (.txt)", msg["summary_txt"],
                    file_name=f"{msg.get('ds_name','summary').replace(' ','_')}_summary.txt",
                    mime="text/plain",
                    key=f"sum_{i}",
                )

            # On-demand panel — map / table / chart buttons for chat answers
            if msg.get("intent", "chat") == "chat" and msg.get("ds_name"):
                _render_ondemand_panel(i, msg)

            # Row 1: compact icon toolbar (left-aligned)
            _prev_q_hist = next((m["content"] for m in reversed(st.session_state.messages[:i]) if m["role"] == "user"), "")
            _ta, _tb, _tc, _td, _te, _ = st.columns([0.6, 0.6, 0.6, 0.6, 0.6, 10])
            with _ta:
                if st.button("✏️", key=f"edit_{i}", help="Edit question"):
                    st.session_state.edit_idx = i - 1
                    st.rerun()
            with _tb:
                if st.button("🔄", key=f"regen_{i}", help="Regenerate answer"):
                    _prev_user = next(
                        (m["content"] for m in reversed(st.session_state.messages[:i])
                         if m["role"] == "user"), None
                    )
                    if _prev_user:
                        st.session_state.messages = st.session_state.messages[:i - 1]
                        st.session_state._pending_question = _prev_user
                        st.rerun()
            with _tc:
                _copy_text = msg.get("content", "")
                st.download_button("📋", _copy_text, file_name="answer.txt",
                                   mime="text/plain", key=f"copy_{i}", help="Copy answer")
            with _td:
                _prev_q_save = next((m["content"] for m in reversed(st.session_state.messages[:i]) if m["role"] == "user"), "Question")
                _save_text = f"Question:\n{_prev_q_save}\n\nAnswer:\n{msg.get('content','')}"
                st.download_button("💾", _save_text, file_name="saved_answer.txt",
                                   mime="text/plain", key=f"save_{i}", help="Save answer")
            with _te:
                if st.button("🗑️", key=f"clear_{i}", help="Delete this answer"):
                    _start = max(0, i - 1)
                    st.session_state.messages = st.session_state.messages[:_start] + st.session_state.messages[i + 1:]
                    st.rerun()

            # Row 2: follow-up suggestions — only on the most recent answer
            if i == _last_assistant_idx and msg.get("intent", "chat") == "chat" and _prev_q_hist:
                _hist_sugs = _build_suggestions(_prev_q_hist, bool(msg.get("location")), bool(msg.get("sample_features")), msg.get("ds_name", ""))
                if _hist_sugs:
                    st.markdown("<div style='margin-top:6px'></div>", unsafe_allow_html=True)
                    _spacer, _sug_col = st.columns([3, 7])
                    with _sug_col:
                        for _si, _stxt in enumerate(_hist_sugs[:3]):
                            if st.button(_stxt, key=f"hsug_{i}_{_si}", use_container_width=True):
                                st.session_state.messages.append({"role": "user", "content": _stxt})
                                st.session_state._pending_question = _stxt
                                st.rerun()

# ---------------------------------------------------------------------------
# Edit prompt UI
# ---------------------------------------------------------------------------
if st.session_state.edit_idx is not None:
    idx = st.session_state.edit_idx
    if idx < len(st.session_state.messages):
        original = st.session_state.messages[idx]["content"]
        st.markdown("---")
        st.markdown("**Edit your prompt:**")
        edited = st.text_area("", value=original, height=80, key="edit_area")
        ca, cb = st.columns([1, 6])
        with ca:
            submit = st.button("Submit", type="primary", key="submit_edit")
        with cb:
            if st.button("Cancel", key="cancel_edit"):
                st.session_state.edit_idx = None
                st.rerun()
        if submit:
            st.session_state.messages = st.session_state.messages[:idx]
            st.session_state.edit_idx = None
            st.session_state._pending_question = edited
            st.rerun()

# ---------------------------------------------------------------------------
# Process a question (either new or edited)
# ---------------------------------------------------------------------------
def process_question(question: str):
    intent = detect_intent(question)

    geojson = None
    map_geojson = None   # lightweight copy for map rendering (≤50 features)
    sample_features = []
    _total_count = None   # exact count from live API, set when location query succeeds
    _cross_context = {}   # flood + risk data fetched alongside main query

    # Detect location once, at the top — used in both context mode and free search mode
    _location, _loc_type = _extract_location(question)
    _comparison_locs = _extract_comparison_locations(question)  # ["Lusaka", "Copperbelt"] for compare queries
    _radius_km = _extract_radius_km(question)      # e.g. 5.0 for "within 5km"
    _buffer_center = None                           # (lat, lon) set later if radius detected

    # Drawn bounding box from the draw tool — overrides location filter when set
    _draw_bbox = st.session_state.get("draw_bbox")
    _draw_bbox_location_note = ""
    if _draw_bbox:
        _bbox_str_draw = (
            f"{_draw_bbox['min_lon']},{_draw_bbox['min_lat']},"
            f"{_draw_bbox['max_lon']},{_draw_bbox['max_lat']}"
        )
        # Identify which district(s)/province the drawn area falls in
        _bbox_center_lat = (_draw_bbox['min_lat'] + _draw_bbox['max_lat']) / 2
        _bbox_center_lon = (_draw_bbox['min_lon'] + _draw_bbox['max_lon']) / 2
        _bbox_districts = []
        _bbox_provinces = []
        if _CONTEXT_LAYERS:
            for _df in _CONTEXT_LAYERS[0]["geojson"].get("features", []):
                _dp = _df.get("properties", {})
                _dd = _dp.get("DISTRICT") or _dp.get("District") or ""
                _dpr = _dp.get("PROVINCE") or _dp.get("Province") or ""
                _dg = _df.get("geometry", {})
                if _dg.get("type") == "Polygon":
                    _rings = _dg.get("coordinates", [])
                    for _ring in _rings:
                        if _point_in_polygon(_bbox_center_lat, _bbox_center_lon, _ring):
                            if _dd and _dd not in _bbox_districts:
                                _bbox_districts.append(_dd)
                            if _dpr and _dpr not in _bbox_provinces:
                                _bbox_provinces.append(_dpr)
                            break
                elif _dg.get("type") == "MultiPolygon":
                    for _poly in _dg.get("coordinates", []):
                        for _ring in _poly:
                            if _point_in_polygon(_bbox_center_lat, _bbox_center_lon, _ring):
                                if _dd and _dd not in _bbox_districts:
                                    _bbox_districts.append(_dd)
                                if _dpr and _dpr not in _bbox_provinces:
                                    _bbox_provinces.append(_dpr)
                                break

        if _bbox_districts:
            _draw_bbox_location_note = (
                f"District(s): {', '.join(_bbox_districts)}"
                + (f" | Province(s): {', '.join(_bbox_provinces)}" if _bbox_provinces else "")
            )
            st.info(
                f"Querying within drawn area — **{_draw_bbox_location_note}** "
                f"({_draw_bbox['min_lat']:.3f}–{_draw_bbox['max_lat']:.3f}°N, "
                f"{_draw_bbox['min_lon']:.3f}–{_draw_bbox['max_lon']:.3f}°E)"
            )
        else:
            st.info(
                f"Querying within drawn area: "
                f"lat {_draw_bbox['min_lat']:.3f}–{_draw_bbox['max_lat']:.3f}, "
                f"lon {_draw_bbox['min_lon']:.3f}–{_draw_bbox['max_lon']:.3f}"
            )

    if context_dataset:
        # Hub context mode — direct request so no hub/client.py reload needed
        datasets = [context_dataset]
        with st.spinner(f"Loading data from '{context_dataset['name']}'..."):
            try:
                import requests as _req
                _base_url = context_dataset["url"].rstrip("/")
                if _base_url.endswith("/query"):
                    _base_url = _base_url[:-6]
                if _location and _loc_type == "district":
                    _where = f"District='{_location}' OR DISTRICT='{_location}'"
                elif _location and _loc_type == "province":
                    _where = f"Province='{_location}' OR PROVINCE='{_location}'"
                else:
                    _where = "1=1"
                _ctx_tok = _hub_client_module._ARCGIS_TOKEN
                _ctx_tok_p = {"token": _ctx_tok} if _ctx_tok and any(
                    org in _base_url for org in ("iQ1dY19aHwbSDYIF", "P3ePLMYs2RVChkJx")
                ) else {}
                _resp = _req.get(
                    f"{_base_url}/query",
                    params={"where": _where, "outFields": "*",
                            "resultRecordCount": 200, "f": "geojson", **_ctx_tok_p},
                    headers={"Referer": "https://zmb-geowb.hub.arcgis.com",
                             "Origin": "https://zmb-geowb.hub.arcgis.com"},
                    timeout=30,
                )
                _resp.raise_for_status()
                geojson = _resp.json()
                live_feats = geojson.get("features", [])
                if live_feats and _location:
                    st.info(f"🌐 Showing {len(live_feats)} live records for {_location}.")
                sample_features = geojson_to_sample_rows(geojson, n=len(live_feats))
                map_geojson = {"type": "FeatureCollection", "features": live_feats[:200]}
            except Exception as _ctx_e:
                st.warning(f"⚠️ Could not load data for {_location or 'this dataset'}: {_ctx_e}")
    else:
        # Free search mode — find the most relevant dataset
        with st.spinner("Searching Zambia GeoHub..."):
            try:
                datasets = hub.search_datasets(question, max_results=5)
            except Exception:
                datasets = []

        from hub.client import _load_static, _POI_TYPE_MAP_MODULE, _SUBJECT_BOOST_MODULE, _SEED_CATALOG

        _poi_type = ""
        for kw, ptype in _POI_TYPE_MAP_MODULE.items():
            if kw in question.lower():
                _poi_type = ptype
                break

        def _find_static(q_lower):
            # Use _SEED_CATALOG for subject-boost lookup — it has correct layer numbers.
            # The dynamic catalog from hub.get_catalog() enumerates all layers of each
            # FeatureServer service (0, 1, 2, …) and layer 0 always appears first,
            # which is wrong for multi-layer services like mines (/12), poverty (/50), etc.
            catalog = hub.get_catalog()
            for kw, frag in _SUBJECT_BOOST_MODULE.items():
                if kw in q_lower:
                    for ds in _SEED_CATALOG:
                        if frag in ds["url"]:
                            sd = _load_static(ds["url"], poi_type=_poi_type)
                            if sd and sd.get("features"):
                                return sd, ds
                            else:
                                # No local static file but this IS the right dataset —
                                # return it as the candidate so live fetch runs on it.
                                return None, ds
            for kw in _POI_TYPE_MAP_MODULE:
                if kw in q_lower:
                    for ds in catalog:
                        if "Points_of_Interest" in ds["url"]:
                            sd = _load_static(ds["url"], poi_type=_poi_type)
                            if sd and sd.get("features"):
                                return sd, ds
                    break
            for candidate in datasets:
                sd = _load_static(candidate["url"], poi_type=_poi_type)
                if sd and sd.get("features"):
                    return sd, candidate
            return None, None

        # If location is mentioned, always try live fetch first with a district/province
        # filter — this gives accurate counts for ALL 116 Zambia districts regardless of
        # whether the local static sample covers that area.
        # Fallback chain: live filtered → static filtered → static full
        if _location:
            _static_data, _static_candidate = _find_static(question.lower())
            _live_candidate = _static_candidate or (datasets[0] if datasets else None)

            # 1. Direct location-filtered fetch — handles both district-field datasets
            # (schools, health, POI) and spatial datasets (settlements, which have no
            # District field and need a bounding-box query against the district polygon).
            _live_error = ""
            if _live_candidate:
                with st.spinner(f"Querying live data for {_location}..."):
                    try:
                        import requests as _req
                        _base_url = _live_candidate["url"].rstrip("/")
                        if _base_url.endswith("/query"):
                            _base_url = _base_url[:-6]
                        _headers = {"Referer": "https://zmb-geowb.hub.arcgis.com",
                                    "Origin": "https://zmb-geowb.hub.arcgis.com"}

                        # Initialise so we never hit NameError if a branch doesn't set these
                        live_feats = []
                        _gjson = {}

                        # Datasets with no District/Province field use a bounding-box
                        # spatial query against the district polygon from districts.json.
                        # This covers: Settlements, Building Footprints, Population,
                        # DRE Atlas, Wealth Index, and other raster/polygon datasets.
                        _BBOX_DATASETS = (
                            "Settlement", "Building_Footprints", "Population",
                            "zambia_dre", "Relative_Wealth", "Aquifer",
                            "Microgrids", "zmb_dams", "zmb_mines",
                        )
                        _needs_bbox = any(kw in _live_candidate.get("url", "") for kw in _BBOX_DATASETS)
                        # Token for private-org datasets
                        _tok = _hub_client_module._ARCGIS_TOKEN
                        _tok_params = {"token": _tok} if _tok and any(
                            org in _base_url for org in ("iQ1dY19aHwbSDYIF", "P3ePLMYs2RVChkJx")
                        ) else {}

                        if _needs_bbox and _loc_type in ("district", "province"):
                            # Find the matching boundary polygon(s) and compute bounding box
                            _boundary_feats = _CONTEXT_LAYERS[0]["geojson"]["features"] if _CONTEXT_LAYERS else []
                            _field = "DISTRICT" if _loc_type == "district" else "PROVINCE"
                            _alt_field = "District" if _loc_type == "district" else "Province"
                            # For provinces, collect ALL matching district features to get full extent
                            _matched_feats = [
                                f for f in _boundary_feats
                                if _location.lower() in (
                                    f["properties"].get(_field) or
                                    f["properties"].get(_alt_field) or ""
                                ).lower()
                            ]
                            from utils.geo_utils import _polygon_bounds
                            # Merge bounds across all matched features
                            _bnds = None
                            for _mf in _matched_feats:
                                _b = _polygon_bounds(_mf["geometry"])
                                if _b:
                                    if _bnds is None:
                                        _bnds = [list(_b[0]), list(_b[1])]
                                    else:
                                        _bnds[0][0] = min(_bnds[0][0], _b[0][0])
                                        _bnds[0][1] = min(_bnds[0][1], _b[0][1])
                                        _bnds[1][0] = max(_bnds[1][0], _b[1][0])
                                        _bnds[1][1] = max(_bnds[1][1], _b[1][1])
                            if _bnds:
                                _bbox_str = f"{_bnds[0][1]},{_bnds[0][0]},{_bnds[1][1]},{_bnds[1][0]}"
                                _bbox_params = {
                                    "geometry": _bbox_str,
                                    "geometryType": "esriGeometryEnvelope",
                                    "spatialRel": "esriSpatialRelIntersects",
                                    **_tok_params,
                                }
                                try:
                                    _cnt_resp = _req.get(f"{_base_url}/query",
                                        params={**_bbox_params, "returnCountOnly": "true", "f": "json"},
                                        headers=_headers, timeout=15)
                                    _cnt_data = _cnt_resp.json()
                                    if "count" in _cnt_data:
                                        _total_count = _cnt_data["count"]
                                except Exception:
                                    pass
                                _resp = _req.get(f"{_base_url}/query",
                                    params={**_bbox_params, "outFields": "*",
                                            "resultRecordCount": 30, "f": "geojson"},
                                    headers=_headers, timeout=30)
                                _resp.raise_for_status()
                                _gjson = _resp.json()
                                if "error" in _gjson:
                                    _err = _gjson["error"]
                                    raise RuntimeError(f"API error {_err.get('code')}: {_err.get('message')}")
                                live_feats = _gjson.get("features", [])
                        else:
                            # Standard district/province field filter
                            _where = (
                                f"District='{_location}' OR DISTRICT='{_location}'"
                                if _loc_type == "district"
                                else f"Province='{_location}' OR PROVINCE='{_location}'"
                            )
                            # Count-only query for exact total
                            try:
                                _cnt_resp = _req.get(f"{_base_url}/query",
                                    params={"where": _where, "returnCountOnly": "true", "f": "json",
                                            **_tok_params},
                                    headers=_headers, timeout=15)
                                _cnt_data = _cnt_resp.json()
                                if "count" in _cnt_data:
                                    _total_count = _cnt_data["count"]
                            except Exception:
                                pass
                            # Sample fetch — up to 200 records for table + AI analysis
                            _resp = _req.get(f"{_base_url}/query",
                                params={"where": _where, "outFields": "*",
                                        "resultRecordCount": 200, "f": "geojson",
                                        **_tok_params},
                                headers=_headers, timeout=30)
                            _resp.raise_for_status()
                            _gjson = _resp.json()
                            if "error" in _gjson:
                                _err = _gjson["error"]
                                raise RuntimeError(f"API error {_err.get('code')}: {_err.get('message')}")
                            live_feats = _gjson.get("features", [])

                        if live_feats and "error" not in _gjson:
                            geojson = _gjson
                            # Spatially assign district/province to features that lack them
                            if _CONTEXT_LAYERS:
                                assign_districts(live_feats, _CONTEXT_LAYERS[0]["geojson"])
                            sample_features = geojson_to_sample_rows(geojson, n=len(live_feats))
                            map_geojson = {"type": "FeatureCollection", "features": live_feats[:200]}
                            datasets = [_live_candidate] + [d for d in datasets if d != _live_candidate]
                            _count_label = f" (total in full dataset: {_total_count:,})" if _total_count is not None else ""
                            st.info(f"🌐 Showing {len(live_feats)} live records for {_location}{_count_label}.")
                    except Exception as _e:
                        _live_error = str(_e)

            # 1b. Cross-dataset context — flood and risk fetched alongside main dataset.
            # Flood DistName field is stored in ALL CAPS so we use UPPER() for matching.
            # If the specific district isn't flood-prone, we fetch its province neighbours
            # so the AI can say "Mongu isn't listed but 4 neighbouring districts are."
            _cross_context = {}
            if _location:
                try:
                    import requests as _req
                    _headers = {"Referer": "https://zmb-geowb.hub.arcgis.com",
                                "Origin": "https://zmb-geowb.hub.arcgis.com"}
                    catalog = hub.get_catalog()

                    # Flood-prone districts — DistName stored as UPPER CASE
                    _flood_ds = next((d for d in catalog if "Flood" in d["url"]), None)
                    if _flood_ds:
                        _flood_base = _flood_ds["url"].rstrip("/")
                        # Try exact district first (case-insensitive via UPPER())
                        if _loc_type == "district":
                            _flood_where = f"UPPER(DistName)='{_location.upper()}'"
                        else:
                            _flood_where = f"UPPER(PovName)='{_location.upper()}'"
                        _fr = _req.get(f"{_flood_base}/query",
                            params={"where": _flood_where, "outFields": "DistName,PovName",
                                    "resultRecordCount": 20, "f": "json"},
                            headers=_headers, timeout=10)
                        _fd = _fr.json()
                        if _fd.get("features"):
                            _cross_context["flood"] = [f["attributes"] for f in _fd["features"]]
                            _cross_context["flood_note"] = f"{_location} IS listed as flood-prone."
                        elif _loc_type == "district":
                            # District not flood-prone — fetch province neighbours for context
                            _prov = next(
                                (f["properties"].get("PROVINCE") for f in _CONTEXT_LAYERS[0]["geojson"]["features"]
                                 if _location.lower() in (f["properties"].get("DISTRICT") or "").lower()),
                                None
                            )
                            if _prov:
                                _prov_where = f"UPPER(PovName)='{_prov.upper()}'"
                                _fr2 = _req.get(f"{_flood_base}/query",
                                    params={"where": _prov_where, "outFields": "DistName,PovName",
                                            "resultRecordCount": 20, "f": "json"},
                                    headers=_headers, timeout=10)
                                _fd2 = _fr2.json()
                                if _fd2.get("features"):
                                    _cross_context["flood"] = [f["attributes"] for f in _fd2["features"]]
                                    _cross_context["flood_note"] = (
                                        f"{_location} district itself is NOT listed as flood-prone in the dataset. "
                                        f"However, these districts in {_prov} Province are: "
                                        f"{', '.join(f['attributes'].get('DistName','') for f in _fd2['features'])}."
                                    )
                                else:
                                    _cross_context["flood_note"] = (
                                        f"Neither {_location} nor any district in {_prov} Province "
                                        f"appears in the flood-prone districts dataset."
                                    )

                    # Risk layers — province-level aggregated data
                    _risk_ds = next((d for d in catalog if "Risk" in d["url"] and "Lusaka" not in d["url"]), None)
                    if _risk_ds:
                        _prov_for_risk = _location if _loc_type == "province" else next(
                            (f["properties"].get("PROVINCE") for f in _CONTEXT_LAYERS[0]["geojson"]["features"]
                             if _location.lower() in (f["properties"].get("DISTRICT") or "").lower()),
                            None
                        )
                        _risk_where = f"UPPER(PovName)='{_prov_for_risk.upper()}'" if _prov_for_risk else "1=1"
                        _rr = _req.get(f"{_risk_ds['url'].rstrip('/')}/query",
                            params={"where": _risk_where, "outFields": "*",
                                    "resultRecordCount": 5, "f": "json"},
                            headers=_headers, timeout=10)
                        _rd = _rr.json()
                        if _rd.get("features"):
                            _cross_context["risk"] = [f["attributes"] for f in _rd["features"]]

                    # Settlement fetch — works for both districts and provinces.
                    # When flood data is present, fetches settlements for flood-prone
                    # districts specifically so the AI can name actual communities at risk.
                    _settle_ds = next((d for d in catalog if "Settlement_Points" in d["url"] or
                                      ("Settlement" in d["url"] and "Extent" not in d["url"])), None)
                    if _settle_ds:
                        _settle_base = _settle_ds["url"].rstrip("/")
                        from utils.geo_utils import _polygon_bounds

                        # Build list of districts to fetch settlements for:
                        # — if flood-prone districts found, use those (most relevant)
                        # — otherwise use the asked district/province directly
                        _target_districts = []
                        _flood_records = _cross_context.get("flood", [])
                        if _flood_records and _loc_type == "province":
                            # Flood-prone districts in this province — fetch each
                            _target_districts = [
                                r.get("DistName", "").title()
                                for r in _flood_records if r.get("DistName")
                            ]
                        elif _loc_type == "district":
                            _target_districts = [_location]

                        _all_settle_feats = []
                        _total_settle_count = 0
                        for _td in _target_districts:
                            _td_feat = next(
                                (f for f in _CONTEXT_LAYERS[0]["geojson"]["features"]
                                 if _td.lower() in (f["properties"].get("DISTRICT") or "").lower()),
                                None
                            )
                            if not _td_feat:
                                continue
                            _bnds = _polygon_bounds(_td_feat["geometry"])
                            if not _bnds:
                                continue
                            _sbbox = f"{_bnds[0][1]},{_bnds[0][0]},{_bnds[1][1]},{_bnds[1][0]}"
                            # Count
                            try:
                                _scnt = _req.get(f"{_settle_base}/query",
                                    params={"geometry": _sbbox, "geometryType": "esriGeometryEnvelope",
                                            "spatialRel": "esriSpatialRelContains",
                                            "returnCountOnly": "true", "f": "json"},
                                    headers=_headers, timeout=15)
                                _c = _scnt.json().get("count", 0)
                                _total_settle_count += _c
                                _cross_context.setdefault("settlement_counts_by_district", {})[_td] = _c
                            except Exception:
                                pass
                            # Sample (up to 15 per district, cap total at 50)
                            if len(_all_settle_feats) < 50:
                                try:
                                    _sresp = _req.get(f"{_settle_base}/query",
                                        params={"geometry": _sbbox, "geometryType": "esriGeometryEnvelope",
                                                "spatialRel": "esriSpatialRelContains",
                                                "outFields": "*", "resultRecordCount": 15, "f": "geojson"},
                                        headers=_headers, timeout=20)
                                    _all_settle_feats.extend(_sresp.json().get("features", []))
                                except Exception:
                                    pass

                        # Province-level total (when no specific districts targeted)
                        if not _target_districts and _loc_type == "province":
                            try:
                                _sw = f"Province='{_location}' OR PROVINCE='{_location}'"
                                _scnt2 = _req.get(f"{_settle_base}/query",
                                    params={"where": _sw, "returnCountOnly": "true", "f": "json"},
                                    headers=_headers, timeout=15)
                                _total_settle_count = _scnt2.json().get("count", 0)
                                # Sample
                                _sr2 = _req.get(f"{_settle_base}/query",
                                    params={"where": _sw, "outFields": "*",
                                            "resultRecordCount": 30, "f": "geojson"},
                                    headers=_headers, timeout=20)
                                _all_settle_feats = _sr2.json().get("features", [])
                            except Exception:
                                pass

                        if _total_settle_count:
                            _cross_context["settlement_count"] = _total_settle_count
                        if _all_settle_feats:
                            _cross_context["settlement_sample"] = [
                                f["properties"] for f in _all_settle_feats[:8]
                            ]
                            _cross_context["settlement_geojson"] = {
                                "type": "FeatureCollection", "features": _all_settle_feats[:50]
                            }

                    # Roads fetch — spatial bbox query (no District field), LineString geometry.
                    # Fetches road names, numbers, surface, and functional class for the queried
                    # district or province.
                    _road_base = "https://services3.arcgis.com/t6lYS2Pmd8iVx1fy/arcgis/rest/services/glc_ZMB_trs_roads_major_b_view/FeatureServer/0"
                    _road_target_districts = (
                        [_location] if _loc_type == "district"
                        else [r.get("DistName", "").title() for r in _cross_context.get("flood", []) if r.get("DistName")]
                        if _loc_type == "province" and _cross_context.get("flood")
                        else []
                    )
                    _all_road_feats = []
                    _total_road_count = 0
                    try:
                        if _road_target_districts:
                            for _rd_dist in _road_target_districts:
                                _rd_feat = next(
                                    (f for f in _CONTEXT_LAYERS[0]["geojson"]["features"]
                                     if _rd_dist.lower() in (f["properties"].get("DISTRICT") or "").lower()),
                                    None
                                )
                                if not _rd_feat:
                                    continue
                                _rd_bnds = _polygon_bounds(_rd_feat["geometry"])
                                if not _rd_bnds:
                                    continue
                                _rd_bbox = f"{_rd_bnds[0][1]},{_rd_bnds[0][0]},{_rd_bnds[1][1]},{_rd_bnds[1][0]}"
                                # Count
                                _rcnt = _req.get(f"{_road_base}/query",
                                    params={"geometry": _rd_bbox, "geometryType": "esriGeometryEnvelope",
                                            "spatialRel": "esriSpatialRelIntersects",
                                            "returnCountOnly": "true", "f": "json"},
                                    headers=_headers, timeout=15)
                                _total_road_count += _rcnt.json().get("count", 0)
                                # Sample (up to 20 roads per district)
                                if len(_all_road_feats) < 60:
                                    _rresp = _req.get(f"{_road_base}/query",
                                        params={"geometry": _rd_bbox, "geometryType": "esriGeometryEnvelope",
                                                "spatialRel": "esriSpatialRelIntersects",
                                                "outFields": "name,roadnoloc,roadno,fclass,surface,numlanes,speedlimitkmh",
                                                "resultRecordCount": 20, "f": "geojson"},
                                        headers=_headers, timeout=20)
                                    _all_road_feats.extend(_rresp.json().get("features", []))
                        elif _loc_type == "province":
                            # Province-level: use a province bbox from context layers
                            # Find any district in province to get rough bbox
                            _prov_feats = [
                                f for f in _CONTEXT_LAYERS[0]["geojson"]["features"]
                                if _location.lower() in (f["properties"].get("PROVINCE") or "").lower()
                            ]
                            if _prov_feats:
                                # Merge all district bounds into province bbox
                                all_lats, all_lons = [], []
                                for _pf in _prov_feats:
                                    _pb = _polygon_bounds(_pf["geometry"])
                                    if _pb:
                                        all_lats += [_pb[0][0], _pb[1][0]]
                                        all_lons += [_pb[0][1], _pb[1][1]]
                                if all_lats:
                                    _prov_bbox = f"{min(all_lons)},{min(all_lats)},{max(all_lons)},{max(all_lats)}"
                                    _rcnt2 = _req.get(f"{_road_base}/query",
                                        params={"geometry": _prov_bbox, "geometryType": "esriGeometryEnvelope",
                                                "spatialRel": "esriSpatialRelIntersects",
                                                "returnCountOnly": "true", "f": "json"},
                                        headers=_headers, timeout=15)
                                    _total_road_count = _rcnt2.json().get("count", 0)
                                    _rresp2 = _req.get(f"{_road_base}/query",
                                        params={"geometry": _prov_bbox, "geometryType": "esriGeometryEnvelope",
                                                "spatialRel": "esriSpatialRelIntersects",
                                                "outFields": "name,roadnoloc,roadno,fclass,surface,numlanes,speedlimitkmh",
                                                "resultRecordCount": 40, "f": "geojson"},
                                        headers=_headers, timeout=20)
                                    _all_road_feats = _rresp2.json().get("features", [])
                    except Exception:
                        pass

                    if _total_road_count:
                        _cross_context["road_count"] = _total_road_count
                    if _all_road_feats:
                        _cross_context["road_sample"] = [
                            {k: v for k, v in (f.get("properties") or {}).items() if v is not None}
                            for f in _all_road_feats[:10]
                        ]
                        _cross_context["road_geojson"] = {
                            "type": "FeatureCollection", "features": _all_road_feats[:60]
                        }
                except Exception:
                    pass

            # 2. Live failed or was blocked — filter static data by location
            if not sample_features and _static_data and _static_data.get("features"):
                loc_feats = _filter_by_location(_static_data["features"], _location, _loc_type)
                if loc_feats:
                    sample_features = geojson_to_sample_rows(
                        {"type": "FeatureCollection", "features": loc_feats}, n=len(loc_feats)
                    )
                    map_geojson = {"type": "FeatureCollection", "features": loc_feats[:50]}
                    if _static_candidate:
                        datasets = [_static_candidate] + [d for d in datasets if d != _static_candidate]
                    st.info(f"📦 Showing {len(loc_feats)} pre-loaded records for {_location} (live server unavailable).")

            # 3. Location not found anywhere — tell the AI explicitly so it gives the right answer.
            # Do NOT pass nationwide data: that causes the AI to say "none in Kalomo" from wrong records.
            if not sample_features:
                if _live_candidate or _static_candidate:
                    datasets = [_live_candidate or _static_candidate] + [
                        d for d in datasets if d not in (_live_candidate, _static_candidate)
                    ]
                _err_detail = f" (error: {_live_error})" if _live_error else ""
                st.warning(
                    f"⚠️ Could not load live data for **{_location}**{_err_detail}. "
                    f"The live GeoHub server may be temporarily unavailable."
                )
                # Pass a placeholder so the AI knows to say it couldn't retrieve the data
                sample_features = [{"_note": f"No data could be retrieved for {_location}. "
                                    f"Inform the user that the live server is unavailable and suggest they try again later."}]

        # No location: try live fetch, fall back to static
        if not sample_features:
            _fetch_errors = []
            for candidate in datasets:
                with st.spinner(f"Loading data from '{candidate['name']}'..."):
                    try:
                        if _draw_bbox:
                            # Use drawn bounding box as spatial filter
                            import requests as _req
                            _base_url = candidate["url"].rstrip("/")
                            _query_url = f"{_base_url}/query"
                            _tok = _hub_client_module._ARCGIS_TOKEN
                            _draw_params = {
                                "geometry": _bbox_str_draw,
                                "geometryType": "esriGeometryEnvelope",
                                "spatialRel": "esriSpatialRelIntersects",
                                "outFields": "*",
                                "resultRecordCount": 200,
                                "f": "geojson",
                            }
                            if _tok:
                                _draw_params["token"] = _tok
                            _draw_headers = {"Referer": "https://zmb-geowb.hub.arcgis.com",
                                             "User-Agent": "Mozilla/5.0"}
                            _draw_resp = _req.get(_query_url, params=_draw_params,
                                                  headers=_draw_headers, timeout=30)
                            _draw_resp.raise_for_status()
                            geojson = _draw_resp.json()
                            if "error" in geojson:
                                _err = geojson["error"]
                                raise RuntimeError(f"API error {_err.get('code')}: {_err.get('message')}")
                        else:
                            geojson = hub.fetch_geojson(candidate["url"], query_hint=question)
                        sample_features = geojson_to_sample_rows(geojson, n=200)
                        if sample_features:
                            map_geojson = {"type": "FeatureCollection", "features": geojson.get("features", [])[:50]}
                            datasets = [candidate] + [d for d in datasets if d != candidate]
                            break
                    except Exception as e:
                        _fetch_errors.append(f"{candidate['name']}: {e}")

        # Last resort: static fallback
        if not sample_features:
            _static_data, _static_candidate = _find_static(question.lower())
            if _static_data and _static_data.get("features"):
                sample_features = geojson_to_sample_rows(_static_data, n=len(_static_data["features"]))
                map_geojson = {"type": "FeatureCollection", "features": _static_data["features"][:50]}
                if _static_candidate:
                    datasets = [_static_candidate] + [d for d in datasets if d != _static_candidate]
                st.info("📦 Using pre-loaded sample data (live server temporarily unavailable).")

    # ---- Buffer / proximity filter ----
    # If the question contains a radius (e.g. "within 5km of Mongu"), compute the
    # buffer center from the highlighted district centroid or the first fetched point,
    # filter map_geojson to only features inside the radius, and annotate each with
    # its distance so Claude can mention exact distances in its answer.
    if _radius_km and map_geojson and map_geojson.get("features"):
        # Determine center: prefer the district polygon centroid (most accurate for
        # location-named queries like "schools within 10km of Kalomo").
        _center = None
        if _location and _CONTEXT_LAYERS:
            _dist_feat = next(
                (f for f in _CONTEXT_LAYERS[0]["geojson"]["features"]
                 if _location.lower() in (
                     (f["properties"].get("DISTRICT") or f["properties"].get("PROVINCE") or "")
                 ).lower()),
                None,
            )
            if _dist_feat and _dist_feat.get("geometry"):
                _center = polygon_centroid(_dist_feat["geometry"])

        # Fallback: centroid of all fetched point features
        if not _center:
            _pt_feats = [
                f for f in map_geojson["features"]
                if (f.get("geometry") or {}).get("type") == "Point"
            ]
            if _pt_feats:
                _lats = [f["geometry"]["coordinates"][1] for f in _pt_feats]
                _lons = [f["geometry"]["coordinates"][0] for f in _pt_feats]
                _center = (sum(_lats) / len(_lats), sum(_lons) / len(_lons))

        if _center:
            _buffer_center = _center
            _within = features_within_km(map_geojson["features"], _center[0], _center[1], _radius_km)

            if _within:
                # Annotate each feature with distance_km and coordinates
                for feat, dist_km in _within:
                    if feat.get("properties") is not None:
                        feat["properties"]["distance_km"] = dist_km
                        # Add lat/lon from geometry so coordinates appear in table/popup
                        geom = feat.get("geometry") or {}
                        if geom.get("type") == "Point":
                            coords = geom.get("coordinates", [])
                            if len(coords) >= 2:
                                feat["properties"]["longitude"] = round(coords[0], 6)
                                feat["properties"]["latitude"] = round(coords[1], 6)
                map_geojson = {"type": "FeatureCollection", "features": [f for f, _ in _within]}
                # Rebuild sample_features from the filtered + annotated set
                sample_features = geojson_to_sample_rows(map_geojson, n=len(_within))
                st.info(
                    f"📍 Buffer filter: {len(_within)} feature(s) found within "
                    f"**{_radius_km} km** of {_location or 'selected point'}."
                )
            else:
                st.info(
                    f"📍 No features found within {_radius_km} km of "
                    f"{_location or 'selected point'}."
                )

    # If cross-context has settlement points and the current map has no Point features,
    # override with settlement points so the map always shows something useful.
    if _cross_context.get("settlement_geojson"):
        _has_points = map_geojson and any(
            (f.get("geometry") or {}).get("type") == "Point"
            for f in map_geojson.get("features", [])
        )
        if not _has_points:
            map_geojson = _cross_context["settlement_geojson"]

    # If cross-context has road lines, inject them as a context layer so they appear on the map.
    _ctx_layers = list(_CONTEXT_LAYERS)
    if _cross_context.get("road_geojson"):
        _ctx_layers = _ctx_layers + [
            {"geojson": _cross_context["road_geojson"], "name": "Roads", "type": "road"}
        ]

    ds = datasets[0] if datasets else {}

    # --- REPORT ---
    if intent == "report":
        with st.chat_message("assistant"):
            st.markdown('<span class="intent-badge intent-report">Report</span>', unsafe_allow_html=True)
            if not ds:
                response = "I could not find a matching dataset on the Zambia GeoHub for your report request. Please try a more specific dataset name."
                st.markdown(response)
                st.session_state.messages.append({"role": "assistant", "content": response, "intent": intent})
                return

            with st.spinner("Generating report (~15 seconds)..."):
                stats = summarize_geojson(map_geojson) if map_geojson else {"feature_count": 0, "geometry_type": "Unknown", "fields": [], "numeric_stats": {}, "exceeded_limit": False}
                rpt_text = claude.ask(
                    system=report_system_prompt(),
                    user=report_prompt(ds["name"], ds["description"], ds.get("fields", []), stats, sample_features),
                    max_tokens=3000,
                )

            with st.spinner("Building Word and PDF..."):
                docx_bytes = builder.to_docx(ds["name"], rpt_text, ds)
                pdf_bytes = builder.to_pdf(ds["name"], rpt_text, ds)

            st.markdown(f"**Report: {ds['name']}**")
            st.markdown(rpt_text)
            st.markdown("**Download report:**")
            c1, c2 = st.columns(2)
            c1.download_button("⬇️ Word (.docx)", docx_bytes,
                file_name=f"{ds['name'].replace(' ','_')}_report.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                key="dl_docx_new", use_container_width=True)
            c2.download_button("⬇️ PDF", pdf_bytes,
                file_name=f"{ds['name'].replace(' ','_')}_report.pdf",
                mime="application/pdf", key="dl_pdf_new", use_container_width=True)

            display_geojson = map_geojson or {"type": "FeatureCollection", "features": []}
            st_folium(_map(display_geojson, ds["name"], with_context=_is_point_geojson(display_geojson), highlight_location=_location or ""), width=720, height=340, returned_objects=[], key="map_new_rpt")

            st.session_state.messages.append({
                "role": "assistant", "content": rpt_text, "intent": intent,
                "docx_bytes": docx_bytes, "pdf_bytes": pdf_bytes,
                "ds_name": ds["name"], "geojson": map_geojson,
            })

    # --- SUMMARY ---
    elif intent == "summary":
        with st.chat_message("assistant"):
            st.markdown('<span class="intent-badge intent-summary">Summary</span>', unsafe_allow_html=True)
            if not ds:
                response = "I could not find a matching dataset to summarise. Try a more specific name like 'summarise health facilities'."
                st.markdown(response)
                st.session_state.messages.append({"role": "assistant", "content": response, "intent": intent})
                return

            stats = summarize_geojson(map_geojson) if map_geojson else {"feature_count": 0, "geometry_type": "Unknown", "fields": [], "numeric_stats": {}, "exceeded_limit": False}
            with st.spinner("Generating summary..."):
                summary = claude.ask(
                    system=summarizer_system_prompt(),
                    user=summarizer_prompt(ds["name"], ds["description"], ds.get("fields", []), sample_features, stats["feature_count"]),
                    max_tokens=1024,
                )

            if map_geojson:
                s = stats
                c1, c2, c3 = st.columns(3)
                c1.metric("Features", f"{s['feature_count']:,}")
                c2.metric("Geometry", s["geometry_type"].replace("esriGeometry", ""))
                c3.metric("Fields", len(s["fields"]))

            _render_data_tables(sample_features, ds["name"], key_prefix="new_sum")

            st.markdown(f"**Summary: {ds['name']}**")
            st.markdown(summary)
            st.download_button("⬇️ Download Summary (.txt)", summary,
                file_name=f"{ds['name'].replace(' ','_')}_summary.txt",
                mime="text/plain", key="dl_sum_new")

            display_geojson = map_geojson or {"type": "FeatureCollection", "features": []}
            st_folium(_map(display_geojson, ds["name"], with_context=_is_point_geojson(display_geojson), highlight_location=_location or ""), width=720, height=340, returned_objects=[], key="map_new_sum")

            st.session_state.messages.append({
                "role": "assistant", "content": summary, "intent": intent,
                "summary_txt": summary, "ds_name": ds["name"], "geojson": map_geojson,
                "sample_features": sample_features,
            })

    # --- CHAT (default) ---
    else:
        with st.chat_message("assistant"):
            st.markdown('<span class="intent-badge intent-chat">Answer</span>', unsafe_allow_html=True)

            # Build multi-turn message history for Claude so follow-up questions
            # reference previous answers (e.g. "how many of those are in Lusaka?")
            history = []
            for m in st.session_state.messages[:-1]:  # exclude the just-added user msg
                if m["role"] in ("user", "assistant") and m.get("content"):
                    history.append({"role": m["role"], "content": m["content"]})
            # Add current user prompt (with dataset context) as the final user turn
            _compare_note = (
                f"\n⚡ COMPARISON REQUEST: The user wants a side-by-side comparison of "
                f"{' and '.join(_comparison_locs)}. Use the sample records to compare "
                f"counts, types, and coverage between these locations. Present as a comparison table if possible."
                if _comparison_locs else ""
            )
            # Append uploaded document context if present
            _doc_ctx = ""
            _doc_text = st.session_state.get("uploaded_doc_text", "")
            _doc_name = st.session_state.get("uploaded_doc_name", "")
            if _doc_text:
                _doc_ctx = (
                    f"\n\n--- UPLOADED DOCUMENT: {_doc_name} ---\n"
                    f"{_doc_text[:6000]}"  # cap at 6000 chars to stay within token limits
                    f"\n--- END OF DOCUMENT ---\n"
                    "Use the above document as additional context when answering the question. "
                    "If the question is specifically about the document, prioritise its content."
                )
            _bbox_note = (
                f"\n[The user drew an area on the map. {_draw_bbox_location_note}. "
                f"Coordinates: lat {_draw_bbox['min_lat']:.3f}–{_draw_bbox['max_lat']:.3f}, "
                f"lon {_draw_bbox['min_lon']:.3f}–{_draw_bbox['max_lon']:.3f}. "
                f"All data shown is filtered to this area. When answering 'where is this', "
                f"state the district and province identified above.]"
                if _draw_bbox else ""
            )
            user_p = chatbot_user_prompt(question + _compare_note + _doc_ctx + _bbox_note, datasets, sample_features, all_catalog=hub.get_catalog(), total_count=_total_count, location=_location or "", cross_context=_cross_context)
            history.append({"role": "user", "content": user_p})

            # --- Streaming with stop button ---
            st.session_state.stop_streaming = False
            st.session_state.is_generating = True

            # Per-session buffer key (stable within a session)
            _sess_buf_key = id(st.session_state)
            _STREAM_BUFFERS[_sess_buf_key] = ""

            def _stoppable_stream():
                for chunk in claude.stream_with_history(chatbot_system_prompt(), history, max_tokens=1500):
                    if st.session_state.get("stop_streaming"):
                        break
                    _STREAM_BUFFERS[_sess_buf_key] = _STREAM_BUFFERS.get(_sess_buf_key, "") + chunk
                    yield chunk

            _ai_error = False
            try:
                response = st.write_stream(_stoppable_stream())
            except Exception as e:
                # Recover any partial text already streamed
                response = _STREAM_BUFFERS.get(_sess_buf_key, "")
                _err_str = str(e)
                if not response:
                    if "overloaded" in _err_str.lower():
                        response = "⚠️ The AI is temporarily overloaded. Please try again in a few seconds."
                    elif "rate_limit" in _err_str.lower():
                        response = "⚠️ Rate limit reached. Please wait a moment and try again."
                    else:
                        response = "⚠️ Something went wrong with the AI response. Please try again."
                    _ai_error = True
                    st.warning(response)
            finally:
                st.session_state.is_generating = False
                st.session_state.stop_streaming = False
                _STREAM_BUFFERS.pop(_sess_buf_key, None)

            if not _ai_error:
                if datasets:
                    with st.expander("📂 Data sources"):
                        for d in datasets:
                            _raw_id = d.get("id", "")
                            _item_id = (
                                _raw_id.rsplit("_", 1)[0]
                                if "_" in _raw_id and _raw_id.rsplit("_", 1)[1].isdigit()
                                else _raw_id
                            )
                            _link = (
                                f" — [View on GeoHub ↗](https://zmb-geowb.hub.arcgis.com/datasets/{_item_id})"
                                if _item_id else ""
                            )
                            st.markdown(f"- **{d['name']}**{_link}  \n  {d['description'][:180]}")

            # Append message first, then show on-demand panel using the stored message
            _new_msg = {
                "role": "assistant", "content": response, "intent": intent,
                "ds_name": ds.get("name", ""), "geojson": map_geojson if not _ai_error else None,
                "location": _location or "",
                "sample_features": sample_features if not _ai_error else [],
                "buffer_center": _buffer_center,
                "buffer_radius_km": _radius_km,
            }
            st.session_state.messages.append(_new_msg)

            # Show map/table/chart buttons for this fresh answer
            if not _ai_error:
                _render_ondemand_panel(len(st.session_state.messages) - 1, _new_msg, ctx_layers=_ctx_layers)

# ---------------------------------------------------------------------------
# Handle pending edited question
# ---------------------------------------------------------------------------
if hasattr(st.session_state, "_pending_question") and st.session_state._pending_question:
    q = st.session_state._pending_question
    st.session_state._pending_question = None
    st.session_state.messages.append({"role": "user", "content": q})
    with st.chat_message("user"):
        st.markdown(q)
    process_question(q)
    st.rerun()


# ---------------------------------------------------------------------------
# Chat input
# ---------------------------------------------------------------------------
if question := st.chat_input("Ask a question, say 'generate a report on...', or 'summarise...'"):
    if st.session_state.edit_idx is None:
        st.session_state.messages.append({"role": "user", "content": question})
        with st.chat_message("user"):
            st.markdown(question)
        process_question(question)
        st.rerun()

# Persist current chat to URL after every render so refresh restores it
_persist_chat()
