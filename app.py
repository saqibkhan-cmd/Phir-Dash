"""
Phir-Dash - unified investigation UI
-------------------------------------
One app for both a newcomer CSM/TAM analyst and an expert Redash/SQL user:

  1. Pick a Cloud (Replica is looked up automatically - no need to know it).
  2. Pick a Major category -> Sub-category (or "Advanced / Custom Table" for
     picking any of the 377 tables directly - the expert path, in the same
     screen, not a separate app).
  3. Check the fields/filters you want. The query previews live as you
     check boxes - no submit button needed.
  4. "Add this selection" locks it into your running list at the top
     (editable/removable). Add as many categories as you need.
  5. "Generate Final Query" stitches everything into one query - but only
     across categories that have a *verified* relationship between them.
     If two selections don't have one, they're returned as separate
     queries rather than forced together into something baseless.

Every field/filter shown for the 13 taxonomy categories comes from a real
production Redash export config (data/business_vocabulary.json / built by
build_taxonomy.py) - nothing here is guessed. "Advanced / Custom Table"
covers everything else via the same relationship engine, for open-ended
investigation.

Run with: streamlit run app.py
"""
import json
import html
from pathlib import Path

import streamlit as st

from query_engine import (
    build_combined_query, build_block_subquery, qualify_from_join_clause,
    substitute_filter_tokens, extract_tokens, find_relationship, find_composite,
)

DATA_DIR = Path(__file__).parent / "data"

st.set_page_config(page_title="Phir-Dash", layout="wide")


@st.cache_data
def load_json(name):
    with open(DATA_DIR / name) as f:
        return json.load(f)


SCHEMA_DATA = load_json("schema_data.json")
TABLES = SCHEMA_DATA["tables"]
RELATIONSHIPS = SCHEMA_DATA["relationships"]
COMPOSITE_RELATIONSHIPS = SCHEMA_DATA.get("composite_relationships", [])
REPLICA_MAP = load_json("replica_map.json")
TAXONOMY = load_json("taxonomy.json")

ADVANCED_LABEL = "Advanced / Custom Table"
MAJOR_CATEGORIES = list(TAXONOMY.keys()) + [ADVANCED_LABEL]


@st.cache_data
def build_cloud_index(replica_map):
    index = {}
    for replica, info in replica_map.items():
        for schema in info["schemas"]:
            index[schema] = {
                "replica": replica,
                "type": info["type"],
                "sql_schema": info.get("sql_schema_override", schema),
            }
    return index


CLOUD_INDEX = build_cloud_index(REPLICA_MAP)
ALL_CLOUDS = sorted(CLOUD_INDEX.keys())


def init_state():
    st.session_state.setdefault("blocks", [])
    st.session_state.setdefault("draft_major", MAJOR_CATEGORIES[0])
    st.session_state.setdefault("final_result", None)


init_state()

st.title("Phir-Dash")
st.caption("Ask a business question, or build an ad-hoc query - both live here.")

# ---------------------------------------------------------------------------
# 1. Cloud -> Replica (reversed lookup)
# ---------------------------------------------------------------------------
cloud = st.selectbox("Cloud / client", [""] + ALL_CLOUDS, index=0)
if cloud:
    info = CLOUD_INDEX[cloud]
    c1, c2 = st.columns(2)
    c1.metric("Replica", info["replica"])
    c2.metric("SQL schema used", info["sql_schema"])
    schema_name = info["sql_schema"]
else:
    st.info("Pick a Cloud/client above to continue.")
    schema_name = None

st.divider()

if not schema_name:
    st.stop()

# ---------------------------------------------------------------------------
# 2. Your selections so far (top, editable/removable)
# ---------------------------------------------------------------------------
st.subheader("Your selections")
if not st.session_state.blocks:
    st.caption("Nothing added yet - build one below.")
else:
    for i, blk in enumerate(st.session_state.blocks):
        c1, c2, c3 = st.columns([3, 5, 1])
        c1.markdown(f"**{blk['label']}**")
        field_names = ", ".join(f["label"] for f in blk["fields"])
        c2.caption(field_names[:120] + ("..." if len(field_names) > 120 else ""))
        if c3.button("✕", key=f"remove_block_{i}"):
            st.session_state.blocks.pop(i)
            st.session_state.final_result = None
            st.rerun()
    if st.button("🔄 Generate Final Query", type="primary"):
        st.session_state.final_result = build_combined_query(
            st.session_state.blocks, schema_name, RELATIONSHIPS, COMPOSITE_RELATIONSHIPS
        )

if st.session_state.final_result:
    res = st.session_state.final_result
    st.markdown("### Result")
    if res["joined_sql"]:
        if res["link_notes"]:
            st.success("Linked your selections on: " + " · ".join(res["link_notes"]))
        st.code(res["joined_sql"], language="sql")
    else:
        st.warning(
            "These selections don't have a verified relationship between them, so "
            "they're shown as separate queries instead of being forced together "
            "(that would risk a misleading result): "
            + ", ".join(f"**{a}** ↔ **{b}**" for a, b in res["unlinked_blocks"])
        )
        for label, sql in res["standalone_sqls"]:
            st.markdown(f"**{label}**")
            st.code(sql, language="sql")

st.divider()

# ---------------------------------------------------------------------------
# 3. Add a category (the builder - reactive, no submit button for preview)
# ---------------------------------------------------------------------------
st.subheader("Add a category")
major = st.selectbox("Major category", MAJOR_CATEGORIES, key="draft_major")

if major == ADVANCED_LABEL:
    # ---- Expert path: pick any table directly ----
    all_table_names = sorted(TABLES.keys())
    table = st.selectbox("Table", [""] + all_table_names)
    if table:
        cols = [c["name"] for c in TABLES[table]["columns"]]
        chosen_fields = st.multiselect("Fields", cols, default=TABLES[table]["primary_key"])

        st.caption("Filters (optional)")
        n_filters = st.number_input("Number of filter conditions", 0, 10, 0, key="adv_nfilters")
        filled_conditions = []
        for i in range(n_filters):
            fc1, fc2, fc3 = st.columns([2, 1, 2])
            fcol = fc1.selectbox("Column", cols, key=f"adv_fcol_{i}")
            fop = fc2.selectbox("Op", ["=", "!=", ">", ">=", "<", "<=", "LIKE"], key=f"adv_fop_{i}")
            fval = fc3.text_input("Value", key=f"adv_fval_{i}")
            if fval:
                meta = next((c for c in TABLES[table]["columns"] if c["name"] == fcol), None)
                is_num = meta and any(k in meta["type"] for k in ["int", "decimal", "float", "double"])
                lit = fval if is_num else f"'{fval}'"
                op = f"LIKE '%{fval}%'" if fop == "LIKE" else f"{fop} {lit}"
                filled_conditions.append(f"{table}.{fcol} " + (op if fop == "LIKE" else op))

        if chosen_fields:
            draft_block = {
                "anchor_table": table, "anchor_alias": table,
                "from_join_clause": f"FROM {table} {table}",
                "fields": [{"label": c, "expr": f"{table}.{c}"} for c in chosen_fields],
                "filled_filter_conditions": filled_conditions,
                "label": f"Advanced: {table}",
            }
            st.markdown("**Live preview:**")
            preview = build_combined_query([draft_block], schema_name, RELATIONSHIPS, COMPOSITE_RELATIONSHIPS)
            st.code(preview["joined_sql"], language="sql")
            if st.button("➕ Add this selection"):
                st.session_state.blocks.append(draft_block)
                st.rerun()
else:
    subcats = TAXONOMY[major]
    sub = st.selectbox("Sub-category", list(subcats.keys()))
    d = subcats[sub]
    if d["source"] == "raw":
        st.caption("⚠️ No matching business report exists for this yet - showing raw table columns.")

    field_labels_all = [f["label"] for f in d["fields"]]
    chosen_field_labels = st.multiselect(f"Fields ({len(field_labels_all)} available)", field_labels_all)

    filled_conditions = []
    if d["filters"]:
        st.caption("Filters (optional)")
        for fi, filt in enumerate(d["filters"]):
            use_it = st.checkbox(filt["label"], key=f"filt_use_{major}_{sub}_{fi}")
            if use_it:
                condition = html.unescape(filt["condition"] or "")
                tokens = extract_tokens(condition)
                token_values = {}
                for tok in tokens:
                    tok_lower = tok.lower()
                    if filt["type"] in ("daterange", "datetime") or "date" in tok_lower or "start" in tok_lower or "end" in tok_lower:
                        val = st.date_input(f"{filt['label']} - {tok}", key=f"filt_val_{major}_{sub}_{fi}_{tok}")
                        token_values[tok] = f"'{val}'"
                    elif filt["type"] == "number":
                        val = st.text_input(f"{filt['label']} - {tok}", key=f"filt_val_{major}_{sub}_{fi}_{tok}")
                        token_values[tok] = val or "0"
                    elif filt["type"] == "boolean":
                        val = st.checkbox(f"{filt['label']} - {tok}", key=f"filt_val_{major}_{sub}_{fi}_{tok}")
                        token_values[tok] = "1" if val else "0"
                    else:
                        val = st.text_input(f"{filt['label']} - {tok}", key=f"filt_val_{major}_{sub}_{fi}_{tok}")
                        token_values[tok] = f"'{val}'"
                if all(v not in ("''", "", None) for v in token_values.values()):
                    filled_conditions.append(substitute_filter_tokens(condition, token_values))

    if chosen_field_labels:
        draft_fields = [f for f in d["fields"] if f["label"] in chosen_field_labels]
        draft_block = {
            "anchor_table": d["anchor_table"], "anchor_alias": d["anchor_alias"],
            "from_join_clause": d["from_join_clause"],
            "fields": draft_fields,
            "filled_filter_conditions": filled_conditions,
            "label": f"{major}: {sub}",
        }
        st.markdown("**Live preview:**")
        preview = build_combined_query([draft_block], schema_name, RELATIONSHIPS, COMPOSITE_RELATIONSHIPS)
        st.code(preview["joined_sql"], language="sql")
        if st.button("➕ Add this selection", key="add_taxonomy_block"):
            st.session_state.blocks.append(draft_block)
            st.rerun()
    else:
        st.caption("Check at least one field above to see a preview.")
