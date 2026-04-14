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


def _suggestion_chips(question: str, has_location: bool, has_data: bool, ds_name: str, key_prefix: str = ""):
    """Show clickable follow-up suggestion buttons after an answer."""
    suggestions = []

    q = question.lower()
    # Map suggestion — if answer didn't already show one meaningfully
    if has_location and has_data:
        suggestions.append(f"Show me a map of {question.split('in')[-1].strip() if ' in ' in q else 'this area'}")
    # Table suggestion
    if has_data and "table" not in q and "summarise" not in q and "summary" not in q:
        suggestions.append(f"Show me this data as a table")
    # Report suggestion
    if has_data and "report" not in q:
        suggestions.append(f"Generate a report on {ds_name}")
    # Summary suggestion
    if has_data and "summar" not in q and "report" not in q:
        suggestions.append(f"Summarise {ds_name}")
    # Flood/risk follow-up
    if has_location and "flood" not in q and "risk" not in q:
        suggestions.append(f"What is the flood risk in this area?")
    # Settlement follow-up
    if has_location and "settlement" not in q and "village" not in q:
        suggestions.append(f"How many settlements are in this area?")

    if not suggestions:
        return

    st.markdown("<div style='margin-top:6px;margin-bottom:2px;font-size:12px;color:#666'>💡 Follow-up suggestions:</div>", unsafe_allow_html=True)
    cols = st.columns(min(len(suggestions), 3))
    for j, sug in enumerate(suggestions[:3]):
        with cols[j]:
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

    # Button row
    _ba, _bb, _bc, _bz = st.columns([1.2, 1.2, 1.2, 8])
    with _ba:
        if has_geojson and not msg.get("map_shown"):
            if st.button("🗺️ Map", key=f"mapbtn_{msg_idx}", use_container_width=True):
                msg["map_shown"] = True
                st.rerun()
    with _bb:
        if has_data and not msg.get("table_shown"):
            if st.button("📊 Table", key=f"tblbtn_{msg_idx}", use_container_width=True):
                msg["table_shown"] = True
                st.rerun()
    with _bc:
        if has_data and not msg.get("chart_shown"):
            if st.button("📈 Chart", key=f"chtbtn_{msg_idx}", use_container_width=True):
                msg["chart_shown"] = True
                st.rerun()

    # Render requested components
    if msg.get("map_shown") and has_geojson:
        gjson = msg["geojson"]
        layers = ctx_layers if ctx_layers else _CONTEXT_LAYERS
        st_folium(
            make_folium_map(gjson, msg.get("ds_name", ""), context_layers=layers if _is_point_geojson(gjson) else None, highlight_location=msg.get("location", "")),
            width=720, height=340, returned_objects=[], key=f"map_{msg_idx}"
        )

    if msg.get("table_shown") and has_data:
        _render_data_tables(msg["sample_features"], msg.get("ds_name", "Data"), key_prefix=f"tbl_{msg_idx}")

    if msg.get("chart_shown") and has_data:
        _render_charts_only(msg["sample_features"], msg.get("ds_name", "Data"), key_prefix=f"cht_{msg_idx}")


def _render_charts_only(sample_features: list, ds_name: str, key_prefix: str = ""):
    """Bar charts only — no dataframe."""
    import pandas as _pd
    rows = [r for r in sample_features if "_note" not in r]
    if not rows:
        return
    df = _pd.DataFrame(rows)
    cat_fields = ["District", "DISTRICT", "Province", "PROVINCE", "Type", "TYPE",
                  "SubType", "Facility_T", "fclass", "surface", "Status", "STATUS",
                  "Classifica", "S_CLASS"]
    shown = 0
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

            # On-demand panel — map / table / chart buttons for chat answers
            if msg.get("intent", "chat") == "chat" and msg.get("ds_name"):
                _render_ondemand_panel(i, msg)

            # Compact action toolbar — edit / regenerate / copy
            st.markdown(
                """<style>
                div[data-testid="stHorizontalBlock"] button[kind="secondary"] {
                    padding: 2px 8px; font-size: 13px; min-height: 0;
                }
                </style>""", unsafe_allow_html=True
            )
            _ta, _tb, _tc, _td = st.columns([1, 1, 1, 10])
            with _ta:
                if st.button("✏️", key=f"edit_{i}", help="Edit question"):
                    st.session_state.edit_idx = i - 1
                    st.rerun()
            with _tb:
                if st.button("🔄", key=f"regen_{i}", help="Regenerate answer"):
                    # Replay the preceding user message
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
                                   mime="text/plain", key=f"copy_{i}", help="Download answer as text")

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
                            "resultRecordCount": 200, "f": "geojson"},
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
                            # Sample fetch — up to 200 records for table + AI analysis
                            _resp = _req.get(f"{_base_url}/query",
                                params={"where": _where, "outFields": "*",
                                        "resultRecordCount": 200, "f": "geojson"},
                                headers=_headers, timeout=30)
                            _resp.raise_for_status()
                            _gjson = _resp.json()
                            live_feats = _gjson.get("features", [])

                        if live_feats and "error" not in _gjson:
                            geojson = _gjson
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
            user_p = chatbot_user_prompt(question, datasets, sample_features, all_catalog=hub.get_catalog(), total_count=_total_count, location=_location or "", cross_context=_cross_context)
            history.append({"role": "user", "content": user_p})

            # Show a Stop button while the AI is streaming
            st.session_state.stop_streaming = False
            _stop_col, _ = st.columns([1, 8])
            _stop_placeholder = _stop_col.empty()

            def _stoppable_stream():
                for chunk in claude.stream_with_history(chatbot_system_prompt(), history, max_tokens=1500):
                    if st.session_state.get("stop_streaming"):
                        break
                    yield chunk

            with _stop_placeholder:
                if st.button("⏹ Stop", key="stop_btn", use_container_width=True):
                    st.session_state.stop_streaming = True

            _ai_error = False
            try:
                response = st.write_stream(_stoppable_stream())
            except Exception as e:
                _ai_error = True
                _stop_placeholder.empty()
                st.session_state.stop_streaming = False
                _err_str = str(e)
                if "overloaded" in _err_str.lower():
                    response = "⚠️ The AI is temporarily overloaded. Please try again in a few seconds."
                elif "rate_limit" in _err_str.lower():
                    response = "⚠️ Rate limit reached. Please wait a moment and try again."
                else:
                    response = "⚠️ Something went wrong with the AI response. Please try again."
                st.warning(response)
            finally:
                _stop_placeholder.empty()
                st.session_state.stop_streaming = False

            if not _ai_error:
                if datasets:
                    with st.expander("Datasets used"):
                        for d in datasets:
                            st.markdown(f"- **{d['name']}** — {d['description'][:120]}")

                _suggestion_chips(question, has_location=bool(_location), has_data=bool(sample_features),
                                  ds_name=ds.get("name", "this dataset"), key_prefix="new_chat")

            # Append message first, then show on-demand panel using the stored message
            _new_msg = {
                "role": "assistant", "content": response, "intent": intent,
                "ds_name": ds.get("name", ""), "geojson": map_geojson if not _ai_error else None,
                "location": _location or "",
                "sample_features": sample_features if not _ai_error else [],
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
            _clear_chat_storage()
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
