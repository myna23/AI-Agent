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
def get_hub(): return HubClient()

@st.cache_resource(show_spinner=False)
def get_claude(): return ClaudeClient()

@st.cache_resource(show_spinner=False)
def get_builder(): return ReportBuilder()

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
# Session state
# ---------------------------------------------------------------------------
if "messages" not in st.session_state:
    st.session_state.messages = []
if "edit_idx" not in st.session_state:
    st.session_state.edit_idx = None

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
# Header
# ---------------------------------------------------------------------------
col_logo, col_title = st.columns([1, 8])
with col_title:
    st.markdown("## Zambia GeoHub AI Assistant")
    st.caption(
        "Ask questions about Zambia's geospatial data • Say **'generate a report on...'** for Word/PDF reports "
        "• Say **'summarise...'** for dataset summaries • Ask **'what data is available?'** to explore the Hub"
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

            # Map
            if msg.get("geojson") and msg.get("ds_name"):
                m = make_folium_map(msg["geojson"], msg["ds_name"])
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
    sample_features = []

    if context_dataset:
        # Hub context mode — the dataset is already known from the page the user is on
        datasets = [context_dataset]
        with st.spinner(f"Loading data from '{context_dataset['name']}'..."):
            try:
                geojson = hub.fetch_geojson(context_dataset["url"], query_hint=question)
                sample_features = geojson_to_sample_rows(geojson, n=200)
            except Exception:
                pass
    else:
        # Free search mode — find the most relevant dataset
        with st.spinner("Searching Zambia GeoHub..."):
            try:
                datasets = hub.search_datasets(question, max_results=5)
            except Exception:
                datasets = []

        # Try each ranked dataset until one returns actual features
        for candidate in datasets:
            with st.spinner(f"Loading data from '{candidate['name']}'..."):
                try:
                    geojson = hub.fetch_geojson(candidate["url"], query_hint=question)
                    sample_features = geojson_to_sample_rows(geojson, n=200)
                    if sample_features:
                        # Reorder so the dataset that actually returned data is first
                        datasets = [candidate] + [d for d in datasets if d != candidate]
                        break
                except Exception:
                    pass

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
                stats = summarize_geojson(geojson) if geojson else {"feature_count": 0, "geometry_type": "Unknown", "fields": [], "numeric_stats": {}, "exceeded_limit": False}
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

            if geojson:
                st_folium(make_folium_map(geojson, ds["name"]), width=720, height=340, returned_objects=[], key="map_new_rpt")

            st.session_state.messages.append({
                "role": "assistant", "content": rpt_text, "intent": intent,
                "docx_bytes": docx_bytes, "pdf_bytes": pdf_bytes,
                "ds_name": ds["name"], "geojson": geojson,
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

            stats = summarize_geojson(geojson) if geojson else {"feature_count": 0, "geometry_type": "Unknown", "fields": [], "numeric_stats": {}, "exceeded_limit": False}
            with st.spinner("Generating summary..."):
                summary = claude.ask(
                    system=summarizer_system_prompt(),
                    user=summarizer_prompt(ds["name"], ds["description"], ds.get("fields", []), sample_features, stats["feature_count"]),
                    max_tokens=1024,
                )

            if geojson:
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

            if geojson:
                st_folium(make_folium_map(geojson, ds["name"]), width=720, height=340, returned_objects=[], key="map_new_sum")

            st.session_state.messages.append({
                "role": "assistant", "content": summary, "intent": intent,
                "summary_txt": summary, "ds_name": ds["name"], "geojson": geojson,
            })

    # --- CHAT (default) ---
    else:
        with st.chat_message("assistant"):
            st.markdown('<span class="intent-badge intent-chat">Answer</span>', unsafe_allow_html=True)
            user_p = chatbot_user_prompt(question, datasets, sample_features, all_catalog=hub.get_catalog())
            try:
                response = st.write_stream(claude.stream(chatbot_system_prompt(), user_p, max_tokens=1500))
            except Exception as e:
                response = f"AI error: {e}"
                st.error(response)

            if datasets:
                with st.expander("Datasets used"):
                    for d in datasets:
                        st.markdown(f"- **{d['name']}** — {d['description'][:120]}")

            if geojson:
                st_folium(make_folium_map(geojson, ds["name"]), width=720, height=340, returned_objects=[], key="map_new_chat")

            st.session_state.messages.append({
                "role": "assistant", "content": response, "intent": intent,
                "ds_name": ds.get("name", ""), "geojson": geojson,
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
col_input, col_clear = st.columns([8, 1])
with col_clear:
    if st.session_state.messages:
        if st.button("🗑️ Clear", use_container_width=True):
            st.session_state.messages = []
            st.session_state.edit_idx = None
            st.rerun()

if question := st.chat_input("Ask a question, say 'generate a report on...', or 'summarise...'"):
    if st.session_state.edit_idx is None:
        st.session_state.messages.append({"role": "user", "content": question})
        with st.chat_message("user"):
            st.markdown(question)
        process_question(question)
        st.rerun()
