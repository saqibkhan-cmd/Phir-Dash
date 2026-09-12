"""
Phir-Dash - unified investigation UI
-------------------------------------
One app for both a newcomer CSM/TAM analyst and an expert Redash/SQL user.

Designed around intent, not schema:
  1. Pick a Cloud (Replica is looked up automatically).
  2. Pick your role - narrows which categories show first, doesn't hide
     the rest.
  3. Pick a Major category -> Sub-category (or "Advanced / Custom Table"
     for picking any of the 377 tables directly - the expert path, same
     screen, not a separate app).
  4. "What are you searching for?" comes FIRST - the filters real analysts
     actually use for this report (pulled from real export configs), not
     a wall of output columns. Fill in what you know (an order code, a
     SKU, a date range).
  5. "What do you want to see?" comes second, with a small sensible
     default already checked - not all 25+ available columns at once.
     Expand "Show more fields" only if you want to customize.
  6. "Add this selection" locks it into your running list at the top
     (editable/removable). Add as many categories as you need, then
     "Generate Final Query" stitches them - only across categories with a
     *verified* relationship, never forcing a baseless join.

Every field/filter for the 13 taxonomy categories comes from a real
production Redash export config - nothing here is guessed. "Advanced /
Custom Table" covers everything else via the same relationship engine.

Run with: streamlit run app.py
"""
import json
import html
from pathlib import Path

import streamlit as st

from query_engine import (
    build_combined_query, substitute_filter_tokens, extract_tokens,
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
TAXONOMY_DATA = load_json("taxonomy.json")
CATEGORIES = TAXONOMY_DATA["categories"]

ADVANCED_LABEL = "Advanced / Custom Table"
MAJOR_CATEGORIES = list(CATEGORIES.keys()) + [ADVANCED_LABEL]


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
    st.session_state.setdefault("final_result", None)


init_state()


def checkbox_grid(items, key_ns, n_cols=4, defaults=None):
    """Render `items` as a grid of checkboxes, namespaced by key_ns so
    switching selections never restores a stale value into a mismatched
    options list (that was the cause of an earlier version crashing).
    `defaults` is a set of labels to pre-check."""
    if not items:
        return []
    defaults = defaults or set()
    selected = []
    cols = st.columns(n_cols)
    for i, label in enumerate(items):
        checked = cols[i % n_cols].checkbox(
            label, value=(label in defaults), key=f"{key_ns}__field__{i}"
        )
        if checked:
            selected.append(label)
    return selected


def render_filters(filters, key_ns, quick_labels=None):
    """Filters, most-common ones first and expanded by default, the rest
    tucked behind 'More search options' - so a newcomer sees 2-3 relevant
    search boxes, not 17 at once. Returns filled WHERE conditions."""
    filled_conditions = []
    if not filters:
        return filled_conditions

    quick_labels = set(quick_labels or [])
    quick = [f for f in filters if f["label"] in quick_labels]
    rest = [f for f in filters if f["label"] not in quick_labels]

    def render_one(filt, fi):
        condition = html.unescape(filt.get("condition") or "")
        tokens = list(dict.fromkeys(extract_tokens(condition)))
        if not tokens:
            st.checkbox(filt["label"], key=f"{key_ns}__filtuse__{fi}")
            return None
        use_it = st.checkbox(filt["label"], key=f"{key_ns}__filtuse__{fi}")
        if not use_it:
            return None
        token_values, all_filled = {}, True
        for tok in tokens:
            tok_key = f"{key_ns}__filtval__{fi}__{tok}"
            tok_lower = tok.lower()
            ftype = filt.get("type")
            if ftype in ("daterange", "datetime") or "date" in tok_lower or "start" in tok_lower or "end" in tok_lower:
                val = st.date_input(f"　{filt['label']} — {tok}", key=tok_key, value=None)
                if val is None:
                    all_filled = False
                else:
                    token_values[tok] = f"'{val}'"
            elif ftype == "number":
                val = st.text_input(f"　{filt['label']} — {tok}", key=tok_key)
                if not val.strip():
                    all_filled = False
                else:
                    token_values[tok] = val.strip()
            elif ftype == "boolean":
                val = st.checkbox(f"　{filt['label']} — {tok}", key=tok_key)
                token_values[tok] = "1" if val else "0"
            else:
                val = st.text_input(f"　{filt['label']} — {tok}", key=tok_key)
                if not val.strip():
                    all_filled = False
                else:
                    token_values[tok] = val.strip() if val.strip().replace(".", "").isdigit() else f"'{val.strip()}'"
        if all_filled and token_values:
            return substitute_filter_tokens(condition, token_values)
        st.caption(f"　(fill in every value above to apply \"{filt['label']}\")")
        return None

    if quick:
        st.caption("Common searches for this category:")
        for fi, filt in enumerate(quick):
            r = render_one(filt, f"q{fi}")
            if r:
                filled_conditions.append(r)
    if rest:
        with st.expander(f"More search options ({len(rest)})"):
            for fi, filt in enumerate(rest):
                r = render_one(filt, f"r{fi}")
                if r:
                    filled_conditions.append(r)
    return filled_conditions


st.title("Phir-Dash")
st.caption("Ask a business question, or build an ad-hoc query - both live here.")

# ---------------------------------------------------------------------------
# 1. Cloud -> Replica (reversed lookup)
# ---------------------------------------------------------------------------
cloud = st.selectbox("Cloud / client", [""] + ALL_CLOUDS, index=0, key="cloud_select")
if cloud:
    info = CLOUD_INDEX[cloud]
    c1, c2 = st.columns(2)
    c1.metric("Replica", info["replica"])
    c2.metric("SQL schema used", info["sql_schema"])
    schema_name = info["sql_schema"]
else:
    st.info("Pick a Cloud/client above to continue.")
    schema_name = None

if not schema_name:
    st.stop()

st.divider()

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
        try:
            st.session_state.final_result = build_combined_query(
                st.session_state.blocks, schema_name, RELATIONSHIPS, COMPOSITE_RELATIONSHIPS
            )
        except Exception as e:
            st.session_state.final_result = None
            st.error(f"Couldn't generate the combined query: {e}")

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
# 3. Add a category
# ---------------------------------------------------------------------------
st.subheader("Add a category")
major = st.selectbox("What's this about?", MAJOR_CATEGORIES, key="draft_major")

try:
    if major == ADVANCED_LABEL:
        # ---- Expert path: pick any table directly ----
        all_table_names = sorted(TABLES.keys())
        table = st.selectbox("Table", [""] + all_table_names, key="adv_table_select")
        if table:
            key_ns = f"adv__{table}"
            cols = [c["name"] for c in TABLES[table]["columns"]]
            pk = set(TABLES[table]["primary_key"])
            st.caption(f"Fields ({len(cols)} available) - primary key pre-checked")
            chosen_fields = checkbox_grid(cols, key_ns, n_cols=4, defaults=pk)
            if not chosen_fields:
                chosen_fields = list(pk) or cols[:1]

            st.caption("Filters (optional)")
            n_filters = st.number_input(
                "Number of filter conditions", 0, 10, 0, key=f"{key_ns}__nfilters"
            )
            filled_conditions = []
            for i in range(n_filters):
                fc1, fc2, fc3 = st.columns([2, 1, 2])
                fcol = fc1.selectbox("Column", cols, key=f"{key_ns}__fcol__{i}")
                fop = fc2.selectbox(
                    "Op", ["=", "!=", ">", ">=", "<", "<=", "LIKE"], key=f"{key_ns}__fop__{i}"
                )
                fval = fc3.text_input("Value", key=f"{key_ns}__fval__{i}")
                if fval:
                    meta = next((c for c in TABLES[table]["columns"] if c["name"] == fcol), None)
                    is_num = meta and any(k in meta["type"] for k in ["int", "decimal", "float", "double"])
                    if fop == "LIKE":
                        filled_conditions.append(f"{table}.{fcol} LIKE '%{fval}%'")
                    else:
                        lit = fval if is_num else f"'{fval}'"
                        filled_conditions.append(f"{table}.{fcol} {fop} {lit}")

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
            if st.button("➕ Add this selection", key=f"{key_ns}__add"):
                st.session_state.blocks.append(draft_block)
                st.rerun()
    else:
        subcats = CATEGORIES[major]
        sub = st.selectbox("Which report?", list(subcats.keys()), key=f"sub_select__{major}")
        d = subcats[sub]
        key_ns = f"tax__{major}__{sub}"
        if d["source"] == "raw":
            st.caption("⚠️ No matching business report exists for this yet - showing raw table columns.")

        st.markdown("**1. What are you searching for?**")
        st.caption("Fill in what you already know - an order code, a SKU, a date range. "
                    "Leave everything blank to see a broad, unfiltered preview.")
        filled_conditions = render_filters(d["filters"], key_ns, quick_labels=d.get("quick_filters"))
        if not d["filters"]:
            st.caption("_No search filters available for this report - it returns everything by default._")

        st.markdown("**2. What do you want to see?**")
        field_labels_all = [f["label"] for f in d["fields"]]
        quick_defaults = set(d.get("quick_fields") or field_labels_all[:1])
        quick_field_labels = [l for l in field_labels_all if l in quick_defaults]
        rest_field_labels = [l for l in field_labels_all if l not in quick_defaults]

        st.caption("A sensible default set of columns is pre-checked below:")
        chosen_quick = checkbox_grid(quick_field_labels, f"{key_ns}__quick", n_cols=3, defaults=quick_defaults)
        chosen_rest = []
        if rest_field_labels:
            with st.expander(f"Show more fields ({len(rest_field_labels)} more available)"):
                chosen_rest = checkbox_grid(rest_field_labels, f"{key_ns}__rest", n_cols=3)
        chosen_field_labels = chosen_quick + chosen_rest
        if not chosen_field_labels:
            chosen_field_labels = field_labels_all[:1]

        draft_fields = [f for f in d["fields"] if f["label"] in chosen_field_labels]
        draft_block = {
            "anchor_table": d["anchor_table"], "anchor_alias": d["anchor_alias"],
            "from_join_clause": d["from_join_clause"],
            "fields": draft_fields,
            "filled_filter_conditions": filled_conditions,
            "label": f"{major}: {sub}",
        }
        st.markdown("**Preview:**")
        preview = build_combined_query([draft_block], schema_name, RELATIONSHIPS, COMPOSITE_RELATIONSHIPS)
        st.code(preview["joined_sql"], language="sql")
        if st.button("➕ Add this selection", key=f"{key_ns}__add"):
            st.session_state.blocks.append(draft_block)
            st.rerun()
except Exception as e:
    st.error(
        f"Something went wrong building this selection ({e}). "
        "Try switching the category above and back - this shouldn't lose "
        "anything you've already added to 'Your selections'."
    )
