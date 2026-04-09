"""
Prompt template functions for the three AI features.

Each function returns either a system-prompt string or a user-prompt string
ready to pass directly to ClaudeClient.ask() / ClaudeClient.stream().
"""

import json


# ---------------------------------------------------------------------------
# Feature 1 — AI Chatbot
# ---------------------------------------------------------------------------

def chatbot_system_prompt() -> str:
    return (
        "You are an AI assistant for the Zambia GeoHub (zmb-geowb.hub.arcgis.com). "
        "Your job is to help users understand and explore Zambia's geospatial data.\n\n"
        "RULES:\n"
        "- Answer using the dataset names, descriptions, and sample records provided to you.\n"
        "- If sample records are provided, USE THEM to give specific, concrete answers "
        "— name actual places, districts, provinces, and values from the records.\n"
        "- The 'Type' field in the Points of Interest dataset uses these categories: "
        "Commercial (markets, shops, businesses), Religion (churches, mosques), "
        "Farm (farming areas, agriculture), Well, Borehole, Bridge, Dam, Airport, "
        "Bank, Police, Post Office, Mining, Fisheries, Cooperative, Pharmacy, "
        "Cemetery, Railway, Bus Stop, Mill, Recreation, Administration. "
        "When sample records show Type='Religion', that means churches and mosques. "
        "When Type='Commercial', that means marketplaces and shops. Etc.\n"
        "- For 'which district has the most X' questions: count occurrences in the "
        "sample records by District field and report the top districts from the sample, "
        "noting it is based on the loaded sample.\n"
        "- If the question asks what data is available, list the datasets provided to you.\n"
        "- NEVER invent statistics or facts not present in the data.\n"
        "- Do NOT answer from general world knowledge — only from what is given to you.\n"
        "- Always cite the dataset name you used.\n"
        "- Be concise and helpful — use bullet points or short paragraphs.\n"
        "- Only say data is unavailable if NO sample records and NO relevant dataset "
        "description exists. If records are present, answer from them."
    )


def _aggregate(features: list[dict], field: str) -> dict:
    """Count features by a categorical field value."""
    counts: dict = {}
    for f in features:
        val = str(f.get(field, "Unknown") or "Unknown")
        counts[val] = counts.get(val, 0) + 1
    return dict(sorted(counts.items(), key=lambda x: x[1], reverse=True))


def chatbot_user_prompt(
    question: str,
    datasets: list[dict],
    sample_features: list[dict],
    all_catalog: list[dict] = None,
    total_count: int = None,
    location: str = "",
    cross_context: dict = None,
) -> str:
    """
    Build the user-turn prompt for the chatbot.

    datasets       : matched datasets from HubClient search
    sample_features: feature property dicts (may be empty if fetch failed)
    all_catalog    : full catalog list to show what data exists on the Hub
    """
    # Matched datasets context
    dataset_context = ""
    for i, ds in enumerate(datasets[:5], 1):
        fields_str = ", ".join(f["name"] for f in ds.get("fields", [])[:15])
        dataset_context += (
            f"\nDataset {i}: {ds['name']}\n"
            f"  Description: {ds['description'][:300]}\n"
        )
        if fields_str:
            dataset_context += f"  Fields: {fields_str}\n"

    if not dataset_context:
        dataset_context = "No matching datasets found for this query.\n"

    # Total count banner — shown when we have an exact figure from a count-only API query
    total_count_note = ""
    if total_count is not None and location:
        total_count_note = (
            f"\n⚡ EXACT TOTAL COUNT (from live API, not a sample): "
            f"There are {total_count:,} records in {location} in this dataset. "
            f"Use this number directly when answering 'how many' questions — do not hedge or say it is a sample.\n"
        )
    elif total_count is not None:
        total_count_note = (
            f"\n⚡ EXACT TOTAL COUNT (from live API): {total_count:,} total records in this dataset.\n"
        )

    # Sample records + pre-aggregated counts
    if sample_features:
        # Show up to 15 sample records
        sample_section = (
            f"Sample records from top dataset ({len(sample_features)} records loaded):\n"
            f"```json\n{json.dumps(sample_features[:15], indent=2)}\n```\n"
        )
        # Pre-aggregate by key categorical fields so AI can answer counting questions
        agg_section = ""
        for field in ["District", "Province", "Type", "SubType", "Facility_T", "PROVINCE", "DISTRICT"]:
            if sample_features and field in sample_features[0]:
                counts = _aggregate(sample_features, field)
                top = list(counts.items())[:10]
                agg_section += f"\nCount by {field} (from loaded sample):\n"
                for val, cnt in top:
                    agg_section += f"  {val}: {cnt}\n"
        if agg_section:
            sample_section += f"\nAggregated counts:{agg_section}"
    else:
        sample_section = (
            "No sample records were loaded (dataset may be empty or unavailable). "
            "Answer based on the dataset names and descriptions above.\n"
        )

    # Full catalog overview (so AI knows what exists)
    catalog_overview = ""
    if all_catalog:
        catalog_overview = "\nAll datasets currently available on the Zambia GeoHub:\n"
        for ds in all_catalog:
            catalog_overview += f"  - {ds['name']}: {ds['description'][:100]}\n"

    # Cross-dataset context (flood, risk) fetched alongside the main dataset
    cross_section = ""
    if cross_context:
        if cross_context.get("flood_note"):
            cross_section += f"\nFlood status: {cross_context['flood_note']}\n"
        if cross_context.get("flood"):
            cross_section += "Flood-prone district records:\n"
            cross_section += json.dumps(cross_context["flood"], indent=2) + "\n"
        if cross_context.get("risk"):
            cross_section += "\nSocioeconomic / WASH risk data for this province:\n"
            cross_section += json.dumps(cross_context["risk"], indent=2) + "\n"
        if cross_section:
            cross_section = "\nRelated datasets (flood & risk):\n" + cross_section

    return (
        f"Question: {question}\n\n"
        f"Matched datasets from the Zambia GeoHub:\n{dataset_context}\n"
        f"{total_count_note}"
        f"{sample_section}"
        f"{cross_section}"
        f"{catalog_overview}\n"
        "Answer the question using the information above. "
        "If an EXACT TOTAL COUNT is provided above, state it confidently as the definitive answer. "
        "Use the aggregated counts and sample records for breakdowns by type, subtype, or other fields. "
        "If flood or risk data is provided, use it to answer questions about flood exposure or vulnerability. "
        "Note when breakdowns are based on a sample of the full dataset."
    )


# ---------------------------------------------------------------------------
# Feature 2 — Dataset Summarizer
# ---------------------------------------------------------------------------

def summarizer_system_prompt() -> str:
    return (
        "You are an AI assistant for the Zambia GeoHub (zmb-geowb.hub.arcgis.com). "
        "You ONLY summarise data that is explicitly provided to you from the Zambia GeoHub. "
        "Do NOT add any general knowledge, external facts, or information not present in the provided dataset. "
        "Write clearly for non-technical readers in Zambia's government and NGO sector. "
        "If the data is insufficient to make a meaningful summary, say so honestly."
    )


def summarizer_prompt(
    dataset_name: str,
    description: str,
    fields: list[dict],
    sample_features: list[dict],
    feature_count: int,
) -> str:
    """Build the user-turn prompt for the dataset summarizer."""
    field_lines = "\n".join(
        f"  - {f['alias'] or f['name']} ({f['type']})" for f in fields[:20]
    )
    sample_json = json.dumps(sample_features[:5], indent=2)

    return (
        f"Dataset: {dataset_name}\n"
        f"Description: {description[:300]}\n"
        f"Total features loaded: {feature_count}\n\n"
        f"Fields available:\n{field_lines}\n\n"
        f"Sample records (first {len(sample_features)}):\n"
        f"```json\n{sample_json}\n```\n\n"
        "Please produce:\n"
        "1. **Overview** (2–3 sentences) — what this dataset contains and why it matters for Zambia.\n"
        "2. **Key Fields** — a short bullet list explaining the most important fields.\n"
        "3. **Notable Insights** — 2–3 observations drawn from the sample data.\n"
        "4. **Suggested Use Cases** — 3 practical ways planners or NGOs could use this data.\n\n"
        "Write for a non-GIS audience. Avoid technical jargon."
    )


# ---------------------------------------------------------------------------
# Feature 3 — Report Generator
# ---------------------------------------------------------------------------

def report_system_prompt() -> str:
    return (
        "You are an AI report writer for the Zambia GeoHub (zmb-geowb.hub.arcgis.com). "
        "You ONLY write reports based on data explicitly provided to you from the Zambia GeoHub datasets. "
        "Do NOT include any general knowledge, external statistics, or facts not present in the provided data. "
        "Every claim in the report must be traceable to the dataset provided. "
        "If data is insufficient for a section, write 'Insufficient data available in this dataset' for that section. "
        "Reports are for Zambia government stakeholders and development partners — keep them formal and evidence-based."
    )


def report_prompt(
    dataset_name: str,
    description: str,
    fields: list[dict],
    stats: dict,
    sample_features: list[dict],
) -> str:
    """
    Build the user-turn prompt for the report generator.

    stats : output of geo_utils.summarize_geojson()
    """
    field_lines = "\n".join(
        f"  - {f['alias'] or f['name']} ({f['type']})" for f in fields[:20]
    )

    numeric_summary = ""
    for field, s in list(stats.get("numeric_stats", {}).items())[:8]:
        numeric_summary += f"  - {field}: min={s['min']}, max={s['max']}, mean={s['mean']}\n"

    numeric_block = numeric_summary if numeric_summary else "  (none computed)\n"

    sample_json = json.dumps(sample_features[:10], indent=2)

    exceeded_note = ""
    if stats.get("exceeded_limit"):
        exceeded_note = (
            "\n⚠️ Note: The dataset exceeds the transfer limit — "
            "statistics are based on a sample only.\n"
        )

    return (
        f"Dataset: {dataset_name}\n"
        f"Description: {description[:400]}\n"
        f"Geometry type: {stats.get('geometry_type', 'Unknown')}\n"
        f"Features loaded: {stats.get('feature_count', 0)}{exceeded_note}\n\n"
        f"Fields:\n{field_lines}\n\n"
        f"Numeric field statistics:\n{numeric_block}\n"
        f"Sample records:\n```json\n{sample_json}\n```\n\n"
        "Generate a formal analytical report with the following sections using ## headings:\n\n"
        "## Executive Summary\n"
        "## Dataset Overview\n"
        "## Key Fields and Attributes\n"
        "## Statistical Highlights\n"
        "## Observations and Analysis\n"
        "## Data Limitations\n"
        "## Recommended Next Steps\n\n"
        "Write in professional report style. Use bullet points where appropriate. "
        "Be specific — reference field names, numbers, and districts where the data supports it. "
        "The report should be suitable for printing and sharing with senior officials."
    )
