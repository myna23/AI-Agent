"""
Prompt template functions for the three AI features.

Each function returns either a system-prompt string or a user-prompt string
ready to pass directly to ModelClient.ask() / ModelClient.stream().
"""

import json


# ---------------------------------------------------------------------------
# MCP / Tool-use system prompt — used when AI calls tools directly
# ---------------------------------------------------------------------------

def tool_use_system_prompt() -> str:
    return (
        "You are an AI assistant for the Zambia GeoHub (zmb-geowb.hub.arcgis.com). "
        "You have access to live geospatial data tools for Zambia. "
        "Use them to answer the user's question with real, verified data.\n\n"
        "TOOL-USE RULES:\n"
        "- Always call search_datasets first if you are unsure which dataset to use.\n"
        "- Always call count_features when the user asks 'how many' — never guess a number.\n"
        "- Call fetch_features to get actual names, types, districts, and provinces.\n"
        "- For Draw Area questions, use count_features_in_bbox and overpass_count.\n"
        "- A result with 'verified: true' means the number came from the live API — "
        "state it directly and confidently, like: 'There are exactly 312 health facilities in Lusaka.'\n"
        "- Never invent statistics or dataset names. If a tool returns an error, say so.\n"
        "- Cite the dataset name in your final answer.\n"
        "- Be concise and analytical — use bullet points with specific numbers.\n"
        "- Do not add follow-up suggestions at the end."
    )


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
        "- GO BEYOND surface answers. Always include at least one of:\n"
        "  * A ranking (largest, smallest, highest, lowest) from the data\n"
        "  * A comparison between districts, provinces, or types\n"
        "  * A practical implication (what does this mean for planning, health, education?)\n"
        "  * A notable pattern or anomaly in the records\n"
        "- When asked to show data as a table: do NOT just list rows. Instead provide:\n"
        "  1. A 2-3 sentence analytical summary of what the table shows\n"
        "  2. Key highlights (top/bottom values, outliers, patterns)\n"
        "  3. One practical insight for planners or decision-makers\n"
        "  The raw table is shown separately by the UI — focus on the analysis.\n"
        "- The 'Type' field in the Points of Interest dataset uses these categories: "
        "Commercial (markets, shops, businesses), Religion (churches, mosques), "
        "Farm (farming areas, agriculture), Well, Borehole, Bridge, Dam, Airport, "
        "Bank, Police, Post Office, Mining, Fisheries, Cooperative, Pharmacy, "
        "Cemetery, Railway, Bus Stop, Mill, Recreation, Administration.\n"
        "- For 'which district has the most X' questions: count occurrences in the "
        "sample records by District field and report the top districts from the sample.\n"
        "- If records include a 'distance_km' field, these features have been filtered to "
        "a specific radius. Mention the actual distances in your answer — e.g. 'Kalomo "
        "Basic School is 2.3 km from the center'. List features from nearest to farthest.\n"
        "- If the question asks what data is available, list the datasets provided to you.\n"
        "- NEVER invent statistics, field names, or facts not present in the data.\n"
        "- NEVER make up or guess a dataset name. Only reference datasets that appear in "
        "'Matched datasets' or 'All datasets currently available' sections below.\n"
        "- Do NOT answer from general world knowledge — only from what is given to you.\n"
        "- When a '_note' field is present in the records, it means live data is temporarily "
        "unavailable but cached/sample data is being used. Still answer the question fully "
        "from whatever sample records and dataset information are provided — do NOT refuse "
        "to answer just because of the '_note' field.\n"
        "- NEVER expose internal system notes, status codes, API messages, or technical "
        "infrastructure language to the user. Do NOT write phrases like 'Exact status:', "
        "'System note:', '0 records from the live API', 'no offline cache', 'live server "
        "temporarily unavailable', or any similar technical jargon. If data is unavailable, "
        "say it plainly: 'I don't have data on that topic right now.'\n"
        "- For coverage/distribution questions ('which areas have the lowest X', 'which "
        "districts have the fewest Y'): count the sample records by District field, rank "
        "districts from lowest to highest count, and list the bottom districts with their "
        "counts. Do NOT give only a total — give the district-level breakdown.\n"
        "- Population records may contain a 'Population' field with the total population for "
        "a district or area, and a 'Source' field such as '2022 Census'. When population data "
        "is provided this way, state the figure clearly and cite the source year. Do NOT say "
        "population data is unavailable if Population records are present in the sample.\n"
        "- If no matching datasets are found AND no sample records are provided, say: "
        "'I don't have data on that topic in the Zambia GeoHub.' Do NOT describe "
        "what the dataset might contain, do NOT use technical language, do NOT invent field names.\n"
        "- Always cite the dataset name you used. You MUST copy the dataset name EXACTLY as it appears "
        "after 'Dataset N:' — character for character. Do NOT add version numbers (v1.0, v2, etc.), "
        "dashes, extra words, or any other changes. Do NOT include any hyperlink or URL in your answer "
        "— the UI displays the source link automatically.\n"
        "- Be concise and analytical — use bullet points with specific numbers and names.\n"
        "- Only say data is unavailable if NO sample records and NO relevant dataset "
        "description exists. If records are present, answer from them.\n"
        "- Do NOT add follow-up suggestions or '💡' lines at the end of your answer. "
        "The UI handles follow-up suggestions automatically."
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
        import re as _re
        _ds_id = str(ds.get("id", ""))
        _svc_url = ds.get("url", "")
        _HUB_CATALOG_URLS = {
            # --- hex IDs ---
            "3fb6aa51dc9a4df1a1b7f4e48df5a374": "https://zmb-geowb.hub.arcgis.com/datasets/3fb6aa51dc9a4df1a1b7f4e48df5a374",
            "e31efc4a98774524a93e2eb838dd8fc3": "https://zmb-geowb.hub.arcgis.com/datasets/e31efc4a98774524a93e2eb838dd8fc3",
            "068949f7d4d841f3bc6b485a0a44f3f0": "https://zmb-geowb.hub.arcgis.com/datasets/2ad9f5337d434266a1dc31f8b6834412/about",
            "0c748bfc945c49ce81d07034b1560a68": "https://zmb-geowb.hub.arcgis.com/datasets/89eab6b828c34316ac94b31f346403af/about",
            "4c90a57457fb4e1cb90888d0d394fb78": "https://zmb-geowb.hub.arcgis.com/datasets/4c90a57457fb4e1cb90888d0d394fb78_0",
            "51ac30a1d6454db18a5dbba03524724d": "https://zmb-geowb.hub.arcgis.com/datasets/51ac30a1d6454db18a5dbba03524724d_0",
            "7b3cf3848be947739d4cdf4e7c836f4c": "https://zmb-geowb.hub.arcgis.com/datasets/7b3cf3848be947739d4cdf4e7c836f4c_0",
            "7be52e48252c464bbb8e1c713f87a5d1": "https://zmb-geowb.hub.arcgis.com/datasets/7be52e48252c464bbb8e1c713f87a5d1_0",
            "7d9e73eb624448c79826d3c3274bf790": "https://zmb-geowb.hub.arcgis.com/datasets/7d9e73eb624448c79826d3c3274bf790_0",
            "883e648672134f6488ffbc9f31533a65": "https://zmb-geowb.hub.arcgis.com/datasets/883e648672134f6488ffbc9f31533a65_0",
            "8f73c42ed3884256904ae12440fae558": "https://zmb-geowb.hub.arcgis.com/datasets/a4cd238299354939818dd012c7f2fbf4/about",
            "a0293a6e84c143298227518eb3418d23": "https://zmb-geowb.hub.arcgis.com/datasets/a0293a6e84c143298227518eb3418d23_0",
            "a235535d12314d5b87122c8ee4aac7a2": "https://zmb-geowb.hub.arcgis.com/datasets/a235535d12314d5b87122c8ee4aac7a2_0",
            "bb0ba0c4ee1945f0ae35c1430b12574c": "https://zmb-geowb.hub.arcgis.com/datasets/bb0ba0c4ee1945f0ae35c1430b12574c_0",
            "c17e97e2947040e1a0ea547a083533ad": "https://zmb-geowb.hub.arcgis.com/datasets/c17e97e2947040e1a0ea547a083533ad_0",
            "c571868321cc41ef99ed27535ffa964d": "https://zmb-geowb.hub.arcgis.com/datasets/e7bb69fa761042888cca066a0db132f6/about",
            "c6d0ce455cae4f4c96ef98e7d44f9793": "https://zmb-geowb.hub.arcgis.com/datasets/c6d0ce455cae4f4c96ef98e7d44f9793_0",
            "d27357c640394f11943316e36cebaba3": "https://zmb-geowb.hub.arcgis.com/datasets/d27357c640394f11943316e36cebaba3_0",
            "d50e882d14d8454cb15c7467fa050205": "https://zmb-geowb.hub.arcgis.com/datasets/d50e882d14d8454cb15c7467fa050205_0",
            "ef791bcb05db473a9dc4eb04e41664b5": "https://zmb-geowb.hub.arcgis.com/datasets/ef791bcb05db473a9dc4eb04e41664b5_0",
            "f310fa8209cb4685b56e309cf6d1388f": "https://zmb-geowb.hub.arcgis.com/datasets/f310fa8209cb4685b56e309cf6d1388f_0",
            "f523a78b0e2b4c6a8719ef05a165ab4e": "https://zmb-geowb.hub.arcgis.com/datasets/6032d4c1eb6d4260a3ae86528aa255f0/about",
            "fbff7250ebc94120a1f9d8e332317bbe": "https://zmb-geowb.hub.arcgis.com/datasets/fbff7250ebc94120a1f9d8e332317bbe_0",
            "fc6fc1b222fd400abfdb1158dc27e3bc": "https://zmb-geowb.hub.arcgis.com/datasets/fc6fc1b222fd400abfdb1158dc27e3bc_0",
            "b55592d29ac145ad824bc8531ab75224": "https://zmb-geowb.hub.arcgis.com/maps/b55592d29ac145ad824bc8531ab75224/about",
            # --- named IDs (zmb_*) ---
            "zmb_lobito_stations":       "https://zmb-geowb.hub.arcgis.com/datasets/31e16550a8c04f88a79cd40647efb0de/about",
            "zmb_railways":              "https://zmb-geowb.hub.arcgis.com/datasets/54610f95f3d14bf788edfe9f21a9869c/about",
            "zmb_population_2025":       "https://zmb-geowb.hub.arcgis.com/datasets/754d3922644c407c95d24f9fe75bb561/about",
            "zmb_districts_2022":        "https://zmb-geowb.hub.arcgis.com/datasets/66e907d64a164c81966dce4d29dac7e4/about",
            "zmb_dams":                  "https://zmb-geowb.hub.arcgis.com/datasets/8577bcb6cc484b5186c2421f19cd259e/about",
            "zmb_dre_atlas":             "https://zmb-geowb.hub.arcgis.com/datasets/ab805a5456a543c3a8910e9913204cdc/about",
            "zmb_dre_settlement_poly":   "https://zmb-geowb.hub.arcgis.com/datasets/6931a6c09ea6422bbe4343ae38d54b14/about",
            "zmb_dre_settlement_pt":     "https://zmb-geowb.hub.arcgis.com/datasets/58d613a4ca3742e2a5cf0776904938a6/about",
            "zmb_mines_cod":             "https://zmb-geowb.hub.arcgis.com/datasets/ca34592fd0f44ce4a1a5b74fe1bb6ded/about",
            "zmb_mines_osm":             "https://zmb-geowb.hub.arcgis.com/datasets/b5154eee7c3e43c087d4e5a5da6357f3/about",
            "zmb_microgrids":            "https://zmb-geowb.hub.arcgis.com/datasets/411e400b3c3048679c8962f150a6f308/about",
            "zmb_constituency_poverty":  "https://zmb-geowb.hub.arcgis.com/datasets/bdb28465de274b5a9dbf8396008825ff/about",
            "zmb_net_migration":         "https://zmb-geowb.hub.arcgis.com/datasets/ef23c27bad6440a89196b61a92f0e8de/about",
            "zmb_osr_local_authority":   "https://zmb-geowb.hub.arcgis.com/datasets/05a6847cd8dc4beabfb9b62c07de3210/about",
            "zmb_power_lines":           "https://zmb-geowb.hub.arcgis.com/maps/70aba11f010c4892896f001271dce65f/about",
            "zmb_power_stations":        "https://zmb-geowb.hub.arcgis.com/maps/70aba11f010c4892896f001271dce65f/about",
            "zmb_power_polygons":        "https://zmb-geowb.hub.arcgis.com/maps/70aba11f010c4892896f001271dce65f/about",
            "zmb_poi_overture":          "https://zmb-geowb.hub.arcgis.com/datasets/a4cd238299354939818dd012c7f2fbf4/about",
            "zmb_building_footprints":   "https://zmb-geowb.hub.arcgis.com/datasets/6b4022a9961b42958c5ee97c57f7de26/about",
            "zmb_relative_wealth":       "https://zmb-geowb.hub.arcgis.com/datasets/3908b9b76c17428295d74fcff24a9ecc/about",
            "zmb_road_network":          "https://zmb-geowb.hub.arcgis.com/datasets/e7bb69fa761042888cca066a0db132f6/about",
            "zmb_health_2025":           "https://zmb-geowb.hub.arcgis.com/datasets/046547b42f004685913078e998827fb9/about",
            "zmb_health_poly":           "https://zmb-geowb.hub.arcgis.com/maps/6bfd2553b1d849ba921881308ca98844/about",
            "zmb_health_pt_overture":    "https://zmb-geowb.hub.arcgis.com/maps/6bfd2553b1d849ba921881308ca98844/about",
            "zmb_education_poly":        "https://zmb-geowb.hub.arcgis.com/maps/84d7c9601f8948ae84e3e5b9763a5c08/about",
            "zmb_education_pt":          "https://zmb-geowb.hub.arcgis.com/maps/84d7c9601f8948ae84e3e5b9763a5c08/about",
            "zmb_marketplaces_poly":     "https://zmb-geowb.hub.arcgis.com/maps/b55592d29ac145ad824bc8531ab75224/about",
            "zmb_marketplaces_pt":       "https://zmb-geowb.hub.arcgis.com/maps/b55592d29ac145ad824bc8531ab75224/about",
        }
        _hub_link = _HUB_CATALOG_URLS.get(_ds_id)
        if not _hub_link and _ds_id and _re.fullmatch(r"[0-9a-f]{32}", _ds_id):
            _hub_link = f"https://www.arcgis.com/home/item.html?id={_ds_id}"
        if not _hub_link:
            _hub_link = "https://zmb-geowb.hub.arcgis.com/search?collection=dataset&tags=zmb"
        dataset_context += (
            f"\nDataset {i}: {ds['name']}\n"
            f"  Description: {ds['description'][:300]}\n"
        )
        dataset_context += f"  Source URL: {_hub_link}\n"
        if fields_str:
            dataset_context += f"  Fields: {fields_str}\n"

    if not dataset_context:
        dataset_context = (
            "⚠️ NO MATCHING DATASETS FOUND for this query on the Zambia GeoHub.\n"
            "You MUST respond: 'This data is not currently available on the Zambia GeoHub.' "
            "Do NOT describe fields, invent dataset names, or answer from general knowledge.\n"
        )

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

    # Cross-dataset context (settlements, flood, risk) fetched alongside main dataset
    cross_section = ""
    if cross_context:
        if cross_context.get("settlement_count") is not None:
            cross_section += (
                f"\n⚡ EXACT SETTLEMENT COUNT for {location}: "
                f"{cross_context['settlement_count']:,} settlements (from live spatial query). "
                f"State this number directly when answering settlement count questions.\n"
            )
        if cross_context.get("settlement_counts_by_district"):
            cross_section += "Settlement counts by flood-prone district:\n"
            for dist, cnt in cross_context["settlement_counts_by_district"].items():
                cross_section += f"  {dist}: {cnt:,} settlements\n"
        if cross_context.get("settlement_sample"):
            cross_section += "Settlement sample records:\n"
            cross_section += json.dumps(cross_context["settlement_sample"], indent=2) + "\n"
        if cross_context.get("flood_note"):
            cross_section += f"\nFlood status: {cross_context['flood_note']}\n"
        if cross_context.get("flood"):
            cross_section += "Flood-prone district records:\n"
            cross_section += json.dumps(cross_context["flood"], indent=2) + "\n"
        if cross_context.get("risk"):
            cross_section += "\nSocioeconomic / WASH risk data for this province:\n"
            cross_section += json.dumps(cross_context["risk"], indent=2) + "\n"
        if cross_context.get("road_count") is not None:
            cross_section += (
                f"\n⚡ ROAD COUNT for {location}: "
                f"{cross_context['road_count']:,} road segments in this area (from live spatial query).\n"
            )
        if cross_context.get("road_sample"):
            cross_section += "Road sample records (name, number, surface, class):\n"
            cross_section += json.dumps(cross_context["road_sample"], indent=2) + "\n"
        if cross_section:
            cross_section = "\nRelated datasets (settlements, flood, risk & roads):\n" + cross_section

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


# ---------------------------------------------------------------------------
# Feature 4 — Map Image Analysis
# ---------------------------------------------------------------------------

def map_analysis_system_prompt() -> str:
    return (
        "You are an expert geospatial analyst for the Zambia GeoHub (zmb-geowb.hub.arcgis.com). "
        "The user has uploaded a map image and wants you to analyse it.\n\n"
        "RULES:\n"
        "- Describe what you can visually observe in the map: geographic features, boundaries, "
        "labels, colours, symbols, patterns, and any data layers visible.\n"
        "- Identify the region, district, or province shown if it is determinable from the map.\n"
        "- Identify the type of map (road, health facility, land use, flood, satellite, etc.).\n"
        "- If the user asks a specific question about the map, answer it directly using what "
        "you can see in the image — do NOT invent data you cannot see.\n"
        "- If related GeoHub dataset records are provided, cross-reference them with what the "
        "map shows and highlight agreements or discrepancies.\n"
        "- Structure your response with these sections using ## headings:\n"
        "  ## Map Overview\n"
        "  ## Key Observations\n"
        "  ## Findings (answer to user's question)\n"
        "  ## Recommendations\n"
        "- Use bullet points with specific observations. Be concise and analytical.\n"
        "- Do NOT add follow-up suggestions at the end — the UI handles these automatically.\n"
        "- Do NOT invent geographic facts not visible in the image."
    )


def map_analysis_user_prompt(question: str, image_name: str) -> str:
    return (
        f"I have uploaded a map image: **{image_name}**\n\n"
        f"My question: {question}\n\n"
        "Please analyse the map image and answer my question. Structure your response with "
        "## Map Overview, ## Key Observations, ## Findings, and ## Recommendations sections."
    )
