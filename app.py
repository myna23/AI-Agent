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
from utils.geo_utils import make_folium_map, summarize_geojson, geojson_to_sample_rows
import json as _json_mod
import os as _os_mod

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

_CONTEXT_LAYERS = _load_context_layers()


def _map(geojson: dict, name: str, with_context: bool = False, highlight_location: str = "") -> object:
    """Wrapper: adds district + road context layers for point datasets."""
    ctx = _CONTEXT_LAYERS if with_context else None
    return make_folium_map(geojson, name, context_layers=ctx, highlight_location=highlight_location)


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
def get_hub(_v=3): return HubClient()

@st.cache_resource(show_spinner=False)
def get_claude(_v=3): return ClaudeClient()

@st.cache_resource(show_spinner=False)
def get_builder(_v=3): return ReportBuilder()

hub = get_hub()
claude = get_claude()
builder = get_builder()

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
# Session state + localStorage persistence
# ---------------------------------------------------------------------------
import json as _json_mod2
import streamlit.components.v1 as _components

# On first load of a session, try to restore messages from the URL-encoded
# query param that localStorage writes back via JS.
if "messages" not in st.session_state:
    _saved_raw = st.query_params.get("_chat", "")
    if _saved_raw:
        try:
            import urllib.parse as _up
            _loaded = _json_mod2.loads(_up.unquote(_saved_raw))
            # Only restore text-only fields — skip bytes (docx/pdf) which can't survive URL
            st.session_state.messages = [
                {k: v for k, v in m.items() if not isinstance(v, (bytes, bytearray))}
                for m in _loaded
            ]
        except Exception:
            st.session_state.messages = []
    else:
        st.session_state.messages = []

if "edit_idx" not in st.session_state:
    st.session_state.edit_idx = None

def _persist_chat():
    """Write current messages into localStorage via JS so they survive refresh."""
    try:
        import urllib.parse as _up
        # Serialise text-only fields (skip bytes)
        _serialisable = [
            {k: v for k, v in m.items() if isinstance(v, (str, int, float, bool, list, dict, type(None)))}
            for m in st.session_state.messages
        ]
        _encoded = _up.quote(_json_mod2.dumps(_serialisable))
        _components.html(
            f"""<script>
            (function() {{
                const encoded = "{_encoded}";
                try {{
                    const url = new URL(window.parent.location.href);
                    url.searchParams.set('_chat', encoded);
                    window.parent.history.replaceState(null, '', url.toString());
                }} catch(e) {{}}
            }})();
            </script>""",
            height=0,
        )
    except Exception:
        pass

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

            # Map — always show (empty basemap if no data loaded)
            if msg.get("ds_name"):
                hist_geojson = msg.get("geojson") or {"type": "FeatureCollection", "features": []}
                m = _map(hist_geojson, msg["ds_name"], with_context=_is_point_geojson(hist_geojson), highlight_location=msg.get("location", ""))
                st_folium(m, width=720, height=340, returned_objects=[], key=f"map_{i}")

            # Edit prompt button
            col_e, col_blank = st.columns([1, 6])
            with col_e:
                if st.button("✏️ Edit prompt", key=f"edit_{i}"):
                    st.session_state.edit_idx = i - 1
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
                _resp = _req.get(
                    f"{_base_url}/query",
                    params={"where": _where, "outFields": "*",
                            "resultRecordCount": 30, "f": "geojson"},
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
                map_geojson = {"type": "FeatureCollection", "features": live_feats[:50]}
            except Exception as _ctx_e:
                st.warning(f"⚠️ Could not load data for {_location or 'this dataset'}: {_ctx_e}")
    else:
        # Free search mode — find the most relevant dataset
        with st.spinner("Searching Zambia GeoHub..."):
            try:
                datasets = hub.search_datasets(question, max_results=5)
            except Exception:
                datasets = []

        from hub.client import _load_static, _POI_TYPE_MAP_MODULE, _SUBJECT_BOOST_MODULE

        _poi_type = ""
        for kw, ptype in _POI_TYPE_MAP_MODULE.items():
            if kw in question.lower():
                _poi_type = ptype
                break

        def _find_static(q_lower):
            catalog = hub.get_catalog()
            for kw, frag in _SUBJECT_BOOST_MODULE.items():
                if kw in q_lower:
                    for ds in catalog:
                        if frag in ds["url"]:
                            sd = _load_static(ds["url"], poi_type=_poi_type)
                            if sd and sd.get("features"):
                                return sd, ds
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

                        # Settlements dataset has no District field — use bounding box of
                        # the district polygon from our local districts.json instead.
                        _is_settlement = "Settlement" in _live_candidate.get("url", "")
                        if _is_settlement and _loc_type == "district":
                            _dist_feat = next(
                                (f for f in _CONTEXT_LAYERS[0]["geojson"]["features"]
                                 if _location.lower() in (f["properties"].get("DISTRICT") or "").lower()),
                                None
                            )
                            if _dist_feat:
                                from utils.geo_utils import _polygon_bounds
                                _bnds = _polygon_bounds(_dist_feat["geometry"])
                                if _bnds:
                                    _bbox_str = f"{_bnds[0][1]},{_bnds[0][0]},{_bnds[1][1]},{_bnds[1][0]}"
                                    _cnt_resp = _req.get(f"{_base_url}/query",
                                        params={"geometry": _bbox_str,
                                                "geometryType": "esriGeometryEnvelope",
                                                "spatialRel": "esriSpatialRelContains",
                                                "returnCountOnly": "true", "f": "json"},
                                        headers=_headers, timeout=15)
                                    _cnt_data = _cnt_resp.json()
                                    if "count" in _cnt_data:
                                        _total_count = _cnt_data["count"]
                                    _resp = _req.get(f"{_base_url}/query",
                                        params={"geometry": _bbox_str,
                                                "geometryType": "esriGeometryEnvelope",
                                                "spatialRel": "esriSpatialRelContains",
                                                "outFields": "*", "resultRecordCount": 30,
                                                "f": "geojson"},
                                        headers=_headers, timeout=30)
                                    _resp.raise_for_status()
                                    _gjson = _resp.json()
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
                                    params={"where": _where, "returnCountOnly": "true", "f": "json"},
                                    headers=_headers, timeout=15)
                                _cnt_data = _cnt_resp.json()
                                if "count" in _cnt_data:
                                    _total_count = _cnt_data["count"]
                            except Exception:
                                pass
                            # Sample fetch — 30 records for AI analysis and map
                            _resp = _req.get(f"{_base_url}/query",
                                params={"where": _where, "outFields": "*",
                                        "resultRecordCount": 30, "f": "geojson"},
                                headers=_headers, timeout=30)
                            _resp.raise_for_status()
                            _gjson = _resp.json()
                            live_feats = _gjson.get("features", [])

                        if live_feats and "error" not in _gjson:
                            geojson = _gjson
                            sample_features = geojson_to_sample_rows(geojson, n=len(live_feats))
                            map_geojson = {"type": "FeatureCollection", "features": live_feats[:50]}
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

                    # Settlement count — always fetch for any district query so the AI
                    # can give exact totals even when the main dataset isn't settlements.
                    _settle_ds = next((d for d in catalog if "Settlement_Points" in d["url"] or
                                      ("Settlement" in d["url"] and "Extent" not in d["url"])), None)
                    if _settle_ds and _loc_type == "district":
                        _dist_feat = next(
                            (f for f in _CONTEXT_LAYERS[0]["geojson"]["features"]
                             if _location.lower() in (f["properties"].get("DISTRICT") or "").lower()),
                            None
                        )
                        if _dist_feat:
                            from utils.geo_utils import _polygon_bounds
                            _bnds = _polygon_bounds(_dist_feat["geometry"])
                            if _bnds:
                                _sbbox = f"{_bnds[0][1]},{_bnds[0][0]},{_bnds[1][1]},{_bnds[1][0]}"
                                _settle_base = _settle_ds["url"].rstrip("/")
                                # Exact count
                                _scnt = _req.get(f"{_settle_base}/query",
                                    params={"geometry": _sbbox, "geometryType": "esriGeometryEnvelope",
                                            "spatialRel": "esriSpatialRelContains",
                                            "returnCountOnly": "true", "f": "json"},
                                    headers=_headers, timeout=15)
                                _scnt_data = _scnt.json()
                                if "count" in _scnt_data:
                                    _cross_context["settlement_count"] = _scnt_data["count"]
                                # Sample for map (if main dataset didn't give point features)
                                _sresp = _req.get(f"{_settle_base}/query",
                                    params={"geometry": _sbbox, "geometryType": "esriGeometryEnvelope",
                                            "spatialRel": "esriSpatialRelContains",
                                            "outFields": "*", "resultRecordCount": 30, "f": "geojson"},
                                    headers=_headers, timeout=30)
                                _sgjson = _sresp.json()
                                _sfeats = _sgjson.get("features", [])
                                if _sfeats:
                                    _cross_context["settlement_sample"] = [
                                        f["properties"] for f in _sfeats[:5]
                                    ]
                                    # Use settlement points for the map if nothing else loaded
                                    if not map_geojson:
                                        map_geojson = {"type": "FeatureCollection", "features": _sfeats[:50]}
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
            user_p = chatbot_user_prompt(question, datasets, sample_features, all_catalog=hub.get_catalog(), total_count=_total_count, location=_location or "", cross_context=_cross_context)
            history.append({"role": "user", "content": user_p})

            try:
                response = st.write_stream(
                    claude.stream_with_history(chatbot_system_prompt(), history, max_tokens=1500)
                )
            except Exception as e:
                response = f"AI error: {e}"
                st.error(response)

            if datasets:
                with st.expander("Datasets used"):
                    for d in datasets:
                        st.markdown(f"- **{d['name']}** — {d['description'][:120]}")

            display_geojson = map_geojson or {"type": "FeatureCollection", "features": []}
            st_folium(_map(display_geojson, ds.get("name", ""), with_context=_is_point_geojson(display_geojson), highlight_location=_location or ""), width=720, height=340, returned_objects=[], key="map_new_chat")

            st.session_state.messages.append({
                "role": "assistant", "content": response, "intent": intent,
                "ds_name": ds.get("name", ""), "geojson": map_geojson,
                "location": _location or "",
            })

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
st.markdown("---")
col_input, col_save, col_clear = st.columns([7, 1, 1])
with col_save:
    if st.session_state.messages:
        lines = []
        for m in st.session_state.messages:
            role = "You" if m["role"] == "user" else "AI"
            lines.append(f"[{role}]\n{m.get('content','')}\n")
        st.download_button(
            "💾 Save", "\n".join(lines).encode(),
            file_name="zambia_geohub_chat.txt", mime="text/plain",
            use_container_width=True,
            help="Download this conversation as a text file",
        )
with col_clear:
    if st.session_state.messages:
        if st.button("🗑️ Clear", use_container_width=True):
            st.session_state.messages = []
            st.session_state.edit_idx = None
            st.query_params.clear()
            st.rerun()

if question := st.chat_input("Ask a question, say 'generate a report on...', or 'summarise...'"):
    if st.session_state.edit_idx is None:
        st.session_state.messages.append({"role": "user", "content": question})
        with st.chat_message("user"):
            st.markdown(question)
        process_question(question)
        st.rerun()

# Persist current chat to URL after every render so refresh restores it
_persist_chat()
