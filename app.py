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
    path = DATA_DIR / name
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        st.error(
            f"`data/{name}` is missing from this deployment - it looks like it "
            f"never got uploaded, or the upload didn't go through. Re-upload "
            f"`data/{name}` from the project files."
        )
        st.stop()
    except json.JSONDecodeError:
        st.error(
            f"`data/{name}` exists but isn't valid JSON - it may have been "
            f"partially uploaded or corrupted. Re-upload `data/{name}` from "
            f"the project files."
        )
        st.stop()


SCHEMA_DATA = load_json("schema_data.json")
if "tables" not in SCHEMA_DATA or "relationships" not in SCHEMA_DATA:
    st.error(
        "data/schema_data.json on this deployment is missing expected data "
        "(no 'tables' or 'relationships' key). Re-upload the latest "
        "data/schema_data.json from the project files."
    )
    st.stop()
TABLES = SCHEMA_DATA["tables"]
RELATIONSHIPS = SCHEMA_DATA["relationships"]
COMPOSITE_RELATIONSHIPS = SCHEMA_DATA.get("composite_relationships", [])
REPLICA_MAP = load_json("replica_map.json")
if not REPLICA_MAP or not all("schemas" in v for v in REPLICA_MAP.values()):
    st.error(
        "data/replica_map.json on this deployment looks malformed (missing "
        "'schemas' for one or more replicas). Re-upload the latest "
        "data/replica_map.json from the project files."
    )
    st.stop()
TAXONOMY_DATA = load_json("taxonomy.json")
if isinstance(TAXONOMY_DATA, dict) and "categories" in TAXONOMY_DATA:
    CATEGORIES = TAXONOMY_DATA["categories"]
elif isinstance(TAXONOMY_DATA, dict) and TAXONOMY_DATA and all(isinstance(v, dict) for v in TAXONOMY_DATA.values()):
    # older taxonomy.json format (flat major->sub dict, no "categories"
    # wrapper, possibly with a stray "personas" key from an in-between
    # version) - degrade gracefully instead of crashing on a KeyError
    CATEGORIES = {k: v for k, v in TAXONOMY_DATA.items() if k != "personas"}
    st.warning(
        "data/taxonomy.json on this deployment looks like an older version "
        "(missing the current file's structure). The app is still running "
        "using what it can read, but re-upload the latest data/taxonomy.json "
        "to get the current categories and fields.",
        icon="⚠️",
    )
else:
    st.error(
        "data/taxonomy.json couldn't be read in any recognized format. "
        "Re-upload the latest data/taxonomy.json from the project files - "
        "this usually means an old file update didn't fully go through."
    )
    st.stop()

ADVANCED_LABEL = "Advanced / Custom Table"
ENTRY_POINTS = ["Reports", ADVANCED_LABEL]
DOMAINS = list(CATEGORIES.keys())


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
    search boxes, not 17 at once. Returns (filled WHERE conditions, set of
    filter labels that were successfully filled)."""
    filled_conditions = []
    satisfied_labels = set()
    if not filters:
        return filled_conditions, satisfied_labels

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
                    # a bare date string ('2026-09-12') implicitly means
                    # midnight - for an "end" bound that silently excludes
                    # the entire day it's meant to cover. Anchor explicitly
                    # to start/end of day so the range actually includes
                    # what the user expects.
                    time_part = "23:59:59" if "end" in tok_lower else "00:00:00"
                    token_values[tok] = f"'{val} {time_part}'"
            elif ftype == "number":
                val = st.text_input(f"　{filt['label']} — {tok}", key=tok_key)
                if not val.strip():
                    all_filled = False
                else:
                    token_values[tok] = val.strip()
            elif ftype == "boolean":
                val = st.checkbox(f"　{filt['label']} — {tok}", key=tok_key)
                token_values[tok] = "1" if val else "0"
            elif ftype == "multi":
                val = st.text_input(f"　{filt['label']} — {tok}", key=tok_key,
                                     placeholder="value1, value2, value3 ...")
                if not val.strip():
                    all_filled = False
                else:
                    parts = [p.strip() for p in val.split(",") if p.strip()]
                    quoted = ", ".join(
                        p if p.replace(".", "", 1).isdigit() else f"'{p}'" for p in parts
                    )
                    token_values[tok] = f"({quoted})"
            else:
                val = st.text_input(f"　{filt['label']} — {tok}", key=tok_key)
                if not val.strip():
                    all_filled = False
                else:
                    token_values[tok] = val.strip() if val.strip().replace(".", "").isdigit() else f"'{val.strip()}'"
        if all_filled and token_values:
            satisfied_labels.add(filt["label"])
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
    return filled_conditions, satisfied_labels


st.title("Phir-Dash")
st.caption("Ask a business question, or build an ad-hoc query - both live here.")

# ---------------------------------------------------------------------------
# 1. Server -> Replica (reversed lookup)
# ---------------------------------------------------------------------------
cloud = st.selectbox("Choose Server", [""] + ALL_CLOUDS, index=0, key="cloud_select")
if cloud:
    info = CLOUD_INDEX[cloud]
    c1, c2 = st.columns(2)
    c1.metric("Replica", info["replica"])
    c2.metric("SQL schema used", info["sql_schema"])
    schema_name = info["sql_schema"]
else:
    st.info("Choose a server above to continue.")
    schema_name = None

if not schema_name:
    st.stop()

st.divider()

# ---------------------------------------------------------------------------
# 2. Your selections so far (top, editable/removable)
# ---------------------------------------------------------------------------
with st.container(border=True):
    sel_header_col, sel_clear_col = st.columns([5, 1])
    sel_header_col.subheader("Your selections")
    if st.session_state.blocks or st.session_state.get("search_box"):
        if sel_clear_col.button("🗑️ Clear", help="Clears your selections and search - keeps your server choice"):
            st.session_state.blocks = []
            st.session_state.final_result = None
            st.session_state.search_box = ""
            st.rerun()

    if not st.session_state.blocks:
        st.caption("Nothing added yet - build one below.")
    else:
        for i, blk in enumerate(st.session_state.blocks):
            c1, c2, c3 = st.columns([3, 5, 1])
            c1.markdown(f"**{blk['label']}**")
            if blk.get("custom_sql_template"):
                c2.caption("Multi-part query (all fields included)")
            else:
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

search_query = st.text_input(
    "🔍 What do you have? (try \"blocked inventory\", \"purchase order\", \"GRN\", \"who disabled\")",
    key="search_box",
)

# Small synonym set for common alternate phrasings of the same business
# concept - not full NLP, but covers the terms that came up directly in
# testing/feedback so a user isn't forced to guess the exact label wording.
SEARCH_SYNONYMS = {
    "blocked": ["reserved", "allocated", "hold", "stuck"],
    "stuck": ["blocked", "pending", "held"],
    "reserved": ["blocked", "allocated"],
    "sync": ["synchronization", "synced"],
    "disabled": ["enabled", "inactive"],
    "po": ["purchase order"],
    "grn": ["goods received", "inflow receipt"],
    "sku": ["item", "item type"],
    "vendor": ["supplier"],
    "return": ["reverse pickup", "rto"],
}
STOPWORDS = {"is", "my", "the", "a", "an", "to", "for", "of", "in", "on", "not",
             "why", "what", "who", "how", "does", "do", "did", "this", "that",
             "and", "or", "with", "be", "it", "i", "am", "are", "was", "were"}


def expand_query_words(query_lower):
    words = [w for w in query_lower.split() if w not in STOPWORDS]
    if not words:
        words = query_lower.split()  # if it was ALL stopwords, keep something to search on
    expanded = set(words)
    for w in words:
        expanded.update(SEARCH_SYNONYMS.get(w, []))
    return words, expanded


if search_query:
    content_words, query_words = expand_query_words(search_query.lower())
    matches = []
    for maj, subs in CATEGORIES.items():
        for sub, d in subs.items():
            name_text = f"{maj} {sub}".lower()
            desc_text = d.get("description", "").lower()
            field_text = " ".join(f["label"] for f in d["fields"]).lower()
            filter_text = " ".join(f["label"] for f in d["filters"]).lower()

            score = 0
            covered = 0
            for w in query_words:
                hit = False
                if w in name_text:
                    score += 10; hit = True
                if w in desc_text:
                    score += 5; hit = True
                if w in field_text:
                    score += 2; hit = True
                if w in filter_text:
                    score += 1; hit = True
                if hit:
                    covered += 1
            # exact phrase (the literal content words, in order) appearing in
            # the combined name is a strong signal; a near-exact match to the
            # SUB-CATEGORY name alone (ignoring a trailing plural s) is a far
            # stronger one - decisively prefers "Purchase Orders" over
            # "Unwanted Purchase Orders" for the query "purchase order"
            # rather than leaving it to incidental field/filter overlap
            content_phrase = " ".join(content_words)
            if content_phrase in name_text:
                score += 50
            sub_text = sub.lower()
            if content_phrase in sub_text:
                score += 30
            if sub_text.rstrip("s") == content_phrase.rstrip("s"):
                score += 100

            # require at least half the meaningful (non-stopword) query
            # terms to appear somewhere - not literally every token (natural
            # sentences carry words no report will ever mention) and not
            # just one incidental word either
            needed = max(1, (len(content_words) + 1) // 2)
            if covered >= needed:
                matches.append((score, maj, sub))
    matches.sort(key=lambda x: (-x[0], len(x[2])))
    if matches:
        st.caption(f"Found {len(matches)} match(es) - click one to jump straight there:")
        with st.container(border=True):
            for _, maj, sub in matches[:8]:
                if st.button(f"{maj} → {sub}", key=f"searchjump__{maj}__{sub}", use_container_width=True):
                    st.session_state["domain_select"] = maj
                    st.session_state[f"sub_select__{maj}"] = sub
                    st.rerun()
    else:
        st.caption(
            "No matches for that exact phrasing - try fewer/different words, or browse the "
            "areas below. (Full question-style search like \"why is my order stuck\" needs "
            "the guided Help flow, not keyword search - not built yet.)"
        )
    st.divider()

major = st.selectbox("...or browse an area", DOMAINS, key="domain_select")
use_advanced = st.checkbox(
    "🔧 Advanced: search any table directly instead",
    key="use_advanced_toggle",
    help="For open-ended investigation across any of the 377 tables - not needed for most searches.",
)
if use_advanced:
    major = ADVANCED_LABEL

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
        st.caption("Not sure which one? The search box above usually gets you there faster - "
                   "this list is here for browsing.")
        sub = st.selectbox("Specific report", list(subcats.keys()), key=f"sub_select__{major}")
        d = subcats[sub]
        key_ns = f"tax__{major}__{sub}"
        if d.get("description"):
            st.info(d["description"])
        if d["source"] == "raw":
            st.caption("⚠️ No matching business report exists for this yet - showing raw table columns.")

        if d.get("custom_sql_template"):
            # special case: a hand-built multi-part query (e.g. a UNION ALL)
            # that doesn't fit the generic single-table field/filter model -
            # just collect its required inputs directly
            st.markdown("**What do you have?**")
            template_values = {}
            all_filled = True
            for filt in d["filters"]:
                token_name = filt["label"].split(" (")[0].strip().lower().replace(" ", "_")
                val = st.text_input(filt["label"], key=f"{key_ns}__tmpl__{token_name}")
                if val.strip():
                    template_values[token_name] = val.strip()
                else:
                    all_filled = False
            if all_filled:
                draft_block = {
                    "custom_sql_template": d["custom_sql_template"],
                    "template_values": template_values,
                    "fields": [],
                    "label": f"{major}: {sub}",
                }
                st.markdown("**Preview:**")
                preview = build_combined_query([draft_block], schema_name, RELATIONSHIPS, COMPOSITE_RELATIONSHIPS)
                st.code(preview["joined_sql"], language="sql")
                if st.button("➕ Add this selection", key=f"{key_ns}__add"):
                    st.session_state.blocks.append(draft_block)
                    st.rerun()
            else:
                st.warning("⚠️ Fill in every field above - this report needs all of them to run correctly.")
                st.button("➕ Add this selection", key=f"{key_ns}__add", disabled=True)
        else:
            st.markdown("**What do you have?**")
            st.caption("Fill in what you already know - an order code, a SKU, a date range. "
                        "Leave everything blank to see a broad, unfiltered preview.")

            # filters that resolve a token embedded in the JOIN itself (not just
            # the WHERE clause) need special handling - collected separately so
            # render_filters only deals with ordinary WHERE-clause filters
            join_token_filters = [f for f in d["filters"] if f.get("resolves_join_token")]
            ordinary_filters = [f for f in d["filters"] if not f.get("resolves_join_token")]
            join_token_values = {}
            for jtf in join_token_filters:
                val = st.text_input(f"🔑 {jtf['label']}", key=f"{key_ns}__jointoken__{jtf['resolves_join_token']}")
                if val.strip():
                    join_token_values[jtf["resolves_join_token"]] = val.strip()

            filled_conditions, satisfied_labels = render_filters(
                ordinary_filters, key_ns, quick_labels=d.get("quick_filters")
            )
            for jtf in join_token_filters:
                if jtf["resolves_join_token"] in join_token_values:
                    satisfied_labels.add(jtf["label"])
            if not d["filters"]:
                st.caption("_No search filters available for this report - it returns everything by default._")

            resolved_from_join = d["from_join_clause"]
            for token, val in join_token_values.items():
                # resolve the code the user typed to the internal id via a
                # nested lookup, right inside the join - so the user never has
                # to run a separate query first just to find an internal id
                table_hint = "tenant" if "tenant" in token.lower() else "facility"
                resolved_from_join = resolved_from_join.replace(
                    f":{token}", f"(SELECT id FROM {table_hint} WHERE code = '{val}')"
                )

            st.markdown("**What do you want to see?**")
            field_labels_all = [f["label"] for f in d["fields"]]
            quick_defaults = set(d.get("quick_fields") or field_labels_all[:1])
            quick_field_labels = [l for l in field_labels_all if l in quick_defaults]
            rest_field_labels = [l for l in field_labels_all if l not in quick_defaults]

            st.caption(f"These {len(quick_field_labels)} columns are included by default for this report - "
                       f"uncheck any you don't need:")
            chosen_quick = checkbox_grid(quick_field_labels, f"{key_ns}__quick", n_cols=3, defaults=quick_defaults)
            chosen_rest = []
            if rest_field_labels:
                with st.expander(f"+ Add more columns ({len(rest_field_labels)} more available)"):
                    chosen_rest = checkbox_grid(rest_field_labels, f"{key_ns}__rest", n_cols=3)
            chosen_field_labels = chosen_quick + chosen_rest
            if not chosen_field_labels:
                chosen_field_labels = field_labels_all[:1]

            draft_fields = [f for f in d["fields"] if f["label"] in chosen_field_labels]
            draft_block = {
                "anchor_table": d["anchor_table"], "anchor_alias": d["anchor_alias"],
                "from_join_clause": d["from_join_clause"],
                "_resolved_from_join_clause": resolved_from_join,
                "fields": draft_fields,
                "filled_filter_conditions": filled_conditions,
                "label": f"{major}: {sub}",
            }
            st.markdown("**Preview:**")
            if filled_conditions or join_token_values:
                st.caption(f"🔎 Search scope: {len(filled_conditions) + len(join_token_values)} filter(s) "
                           f"applied within **{schema_name}**")
            else:
                st.caption(f"🔎 Search scope: **no filters applied** - this will search all of **{schema_name}**")
            preview = build_combined_query([draft_block], schema_name, RELATIONSHIPS, COMPOSITE_RELATIONSHIPS)
            st.code(preview["joined_sql"], language="sql")

            required_labels = d.get("required_filter_labels")
            if required_labels:
                missing = [l for l in required_labels if l not in satisfied_labels]
                if missing:
                    st.warning(
                        f"⚠️ This report requires the following before it can be added, to keep "
                        f"the search fast and precise: {', '.join(missing)}."
                    )
                    st.button("➕ Add this selection", key=f"{key_ns}__add", disabled=True)
                else:
                    if st.button("➕ Add this selection", key=f"{key_ns}__add"):
                        st.session_state.blocks.append(draft_block)
                        st.rerun()
            elif d.get("requires_scope") and not filled_conditions:
                st.warning(
                    "⚠️ This report can cover a very large table (potentially every tenant/facility "
                    "in this schema). Fill in at least one search filter above - a Facility, SKU, or "
                    "similar - before adding this to your query, to avoid an unintentionally broad, "
                    "expensive search."
                )
                st.button("➕ Add this selection", key=f"{key_ns}__add", disabled=True)
            else:
                if st.button("➕ Add this selection", key=f"{key_ns}__add"):
                    st.session_state.blocks.append(draft_block)
                    st.rerun()
except Exception as e:
    st.error(
        f"Something went wrong building this selection ({e}). "
        "Try switching the category above and back - this shouldn't lose "
        "anything you've already added to 'Your selections'."
    )
