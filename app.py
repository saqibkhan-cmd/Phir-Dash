"""
Uniware Redash Query Builder
----------------------------
A no-LLM, pure-Python Streamlit tool that lets a non-SQL user pick a
starting table, discover related tables from an inferred relationship
map, choose fields/filters, and get a working SQL query generated for
them.

Data source: data/schema_data.json — produced offline from a Redash
"SHOW COLUMNS"-style schema export by build_schema_data.py. That script
infers table relationships from `<name>_id` column naming conventions.
Relationships are tagged with a confidence level:
    verified -> manually confirmed
    high     -> direct name match to an existing table (col stem == table name)
    low      -> resolved by stripping a role prefix (parent_/return_/etc.) —
                worth double-checking with a real query before trusting.
Anything the script couldn't resolve lands in `ambiguous` for manual review.

Run with:  streamlit run app.py
"""

import json
import re
from pathlib import Path
from collections import defaultdict

import streamlit as st

DATA_PATH = Path(__file__).parent / "data" / "schema_data.json"
REPLICA_MAP_PATH = Path(__file__).parent / "data" / "replica_map.json"

st.set_page_config(page_title="Redash Query Builder", layout="wide")


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
@st.cache_data
def load_schema():
    with open(DATA_PATH) as f:
        data = json.load(f)
    return data


@st.cache_data
def load_replica_map():
    with open(REPLICA_MAP_PATH) as f:
        return json.load(f)


@st.cache_data
def build_relationship_index(relationships):
    """Build table -> list of relationship dicts, for both directions."""
    outgoing = defaultdict(list)   # table -> [rel where table is from_table]
    incoming = defaultdict(list)   # table -> [rel where table is to_table]
    for r in relationships:
        outgoing[r["from_table"]].append(r)
        incoming[r["to_table"]].append(r)
    return outgoing, incoming


schema = load_schema()
REPLICA_MAP = load_replica_map()
TABLES = schema["tables"]
RELATIONSHIPS = schema["relationships"]
COMPOSITE_RELATIONSHIPS = schema.get("composite_relationships", [])
POLYMORPHIC_RELATIONSHIPS = schema.get("polymorphic_relationships", [])
AMBIGUOUS = schema["ambiguous"]
STARTER_TABLES = schema.get("starter_tables", [])
OUTGOING, INCOMING = build_relationship_index(RELATIONSHIPS)

ALL_TABLE_NAMES = sorted(TABLES.keys())

CONFIDENCE_ORDER = {"verified": 0, "high": 1, "low": 2}
CONFIDENCE_LABEL = {
    "verified": "✅ verified",
    "high": "🟢 high confidence",
    "low": "🟡 needs verification",
}

OPERATORS = ["=", "!=", ">", ">=", "<", "<=", "LIKE", "IN", "IS NULL", "IS NOT NULL", "BETWEEN"]


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------
def init_state():
    st.session_state.setdefault("query_tables", [])       # ordered list of table names in the query
    st.session_state.setdefault("joins", [])               # list of dicts: {left, left_col, right, right_col, confidence}
    st.session_state.setdefault("selected_fields", {})      # table -> list[str columns]
    st.session_state.setdefault("filters", [])              # list of dicts: {table, column, operator, value}
    st.session_state.setdefault("replica", list(REPLICA_MAP.keys())[0])
    first_replica_schemas = REPLICA_MAP[st.session_state["replica"]]["schemas"]
    st.session_state.setdefault("cloud_schema", first_replica_schemas[0] if first_replica_schemas else "")


init_state()


def col_names(table):
    return [c["name"] for c in TABLES[table]["columns"]]


def col_meta(table, column):
    for c in TABLES[table]["columns"]:
        if c["name"] == column:
            return c
    return None


CONFIDENCE_RANK = {"verified": 0, "high": 1, "low": 2}


def find_relationship(table_a, table_b):
    """Find the best relationship between two already-known tables, either direction.
    When more than one relationship connects the same pair (e.g. an ordinary FK
    plus a verified shared-PK link), prefer the higher-confidence one rather
    than whichever appears first in the data."""
    candidates = []
    for r in RELATIONSHIPS:
        if r["from_table"] == table_a and r["to_table"] == table_b:
            candidates.append((r, "a_to_b"))
        elif r["from_table"] == table_b and r["to_table"] == table_a:
            candidates.append((r, "b_to_a"))
    if not candidates:
        return None, None
    candidates.sort(key=lambda x: CONFIDENCE_RANK.get(x[0]["confidence"], 9))
    return candidates[0]


def find_composite_relationship(table_a, table_b):
    """Find a multi-column relationship between two tables, from composite_relationships."""
    for cr in COMPOSITE_RELATIONSHIPS:
        if set(cr["tables"]) == {table_a, table_b}:
            return cr
    return None


def add_table_to_query(table):
    if table in st.session_state.query_tables:
        return
    # try to auto-derive a join against any table already in the query
    join_found = False
    if st.session_state.query_tables:
        for existing in st.session_state.query_tables:
            rel, direction = find_relationship(existing, table)
            if rel:
                if direction == "a_to_b":
                    left, left_col, right, right_col = existing, rel["from_column"], table, rel["to_column"]
                else:
                    left, left_col, right, right_col = table, rel["from_column"], existing, rel["to_column"]
                st.session_state.joins.append({
                    "left": left, "left_col": left_col,
                    "right": right, "right_col": right_col,
                    "confidence": rel["confidence"],
                    "join_type": "JOIN",
                })
                join_found = True
                break
            # no id/code relationship - try a composite (multi-column) one
            cr = find_composite_relationship(existing, table)
            if cr:
                left, right = existing, table
                conditions = cr["conditions"]
                st.session_state.joins.append({
                    "left": left, "right": right,
                    "is_composite": True, "conditions": conditions,
                    "confidence": cr["confidence"],
                    "join_type": "LEFT JOIN" if cr["confidence"] != "verified" else "JOIN",
                    "note": cr.get("note", ""),
                })
                join_found = True
                break
    st.session_state.query_tables.append(table)
    # default field selection: primary key + first few non-id columns
    pk = TABLES[table]["primary_key"]
    defaults = list(pk)
    st.session_state.selected_fields[table] = defaults
    if not join_found and len(st.session_state.query_tables) > 1:
        st.session_state["_join_warning"] = table


def remove_table_from_query(table):
    st.session_state.query_tables = [t for t in st.session_state.query_tables if t != table]
    st.session_state.joins = [
        j for j in st.session_state.joins if j["left"] != table and j["right"] != table
    ]
    st.session_state.selected_fields.pop(table, None)
    st.session_state.filters = [f for f in st.session_state.filters if f["table"] != table]


# ---------------------------------------------------------------------------
# SQL generation
# ---------------------------------------------------------------------------
def qualified(table):
    """Schema-qualify a table name with the currently selected Cloud/tenant schema."""
    prefix = st.session_state.get("cloud_schema", "")
    return f"{prefix}.{table}" if prefix else table


def generate_sql():
    if not st.session_state.query_tables:
        return "-- Add at least one table to build a query."

    base = st.session_state.query_tables[0]
    lines = []

    select_parts = []
    for t in st.session_state.query_tables:
        for c in st.session_state.selected_fields.get(t, []):
            select_parts.append(f"{qualified(t)}.{c}")
    if not select_parts:
        select_parts = [f"{qualified(base)}.*"]

    lines.append("SELECT " + ",\n       ".join(select_parts))
    lines.append(f"FROM {qualified(base)}")

    joined = {base}
    # order joins so each new table has its anchor already present
    remaining_joins = list(st.session_state.joins)
    safety = 0
    while remaining_joins and safety < 100:
        safety += 1
        progressed = False
        for j in list(remaining_joins):
            jt = j.get("join_type", "JOIN")
            if j.get("is_composite"):
                if j["left"] in joined and j["right"] not in joined:
                    on_parts = [f"{qualified(j['left'])}.{c['left_col']} {c['op']} {qualified(j['right'])}.{c['right_col']}"
                                for c in j["conditions"]]
                    lines.append(f"  {jt} {qualified(j['right'])} ON " + "\n    AND ".join(on_parts))
                    joined.add(j["right"]); remaining_joins.remove(j); progressed = True
                elif j["right"] in joined and j["left"] not in joined:
                    on_parts = [f"{qualified(j['right'])}.{c['right_col']} {c['op']} {qualified(j['left'])}.{c['left_col']}"
                                for c in j["conditions"]]
                    lines.append(f"  {jt} {qualified(j['left'])} ON " + "\n    AND ".join(on_parts))
                    joined.add(j["left"]); remaining_joins.remove(j); progressed = True
                continue
            if j["left"] in joined and j["right"] not in joined:
                lines.append(
                    f"  {jt} {qualified(j['right'])} ON {qualified(j['left'])}.{j['left_col']} = {qualified(j['right'])}.{j['right_col']}"
                )
                joined.add(j["right"])
                remaining_joins.remove(j)
                progressed = True
            elif j["right"] in joined and j["left"] not in joined:
                lines.append(
                    f"  {jt} {qualified(j['left'])} ON {qualified(j['right'])}.{j['right_col']} = {qualified(j['left'])}.{j['left_col']}"
                )
                joined.add(j["left"])
                remaining_joins.remove(j)
                progressed = True
        if not progressed:
            break

    # any query tables with no discovered join path -> flag with a placeholder
    for t in st.session_state.query_tables:
        if t not in joined:
            lines.append(f"  -- ⚠️ CROSS JOIN {qualified(t)}  -- no relationship found; add ON condition manually")
            lines.append(f"  JOIN {qualified(t)} ON 1=1")
            joined.add(t)

    if st.session_state.filters:
        where_parts = []
        for f in st.session_state.filters:
            t, c, op, v = f["table"], f["column"], f["operator"], f["value"]
            col_ref = f"{qualified(t)}.{c}"
            if op in ("IS NULL", "IS NOT NULL"):
                where_parts.append(f"{col_ref} {op}")
            elif op == "IN":
                items = [x.strip() for x in v.split(",") if x.strip()]
                meta = col_meta(t, c)
                is_numeric = meta and any(k in meta["type"] for k in ["int", "decimal", "float", "double"])
                if is_numeric:
                    formatted = ", ".join(items)
                else:
                    formatted = ", ".join(f"'{x}'" for x in items)
                where_parts.append(f"{col_ref} IN ({formatted})")
            elif op == "BETWEEN":
                parts = [x.strip() for x in v.split(",")]
                if len(parts) == 2:
                    where_parts.append(f"{col_ref} BETWEEN '{parts[0]}' AND '{parts[1]}'")
                else:
                    where_parts.append(f"-- ⚠️ BETWEEN needs two comma-separated values for {col_ref}")
            elif op == "LIKE":
                where_parts.append(f"{col_ref} LIKE '%{v}%'")
            else:
                meta = col_meta(t, c)
                is_numeric = meta and any(k in meta["type"] for k in ["int", "decimal", "float", "double"])
                val = v if is_numeric else f"'{v}'"
                where_parts.append(f"{col_ref} {op} {val}")
        lines.append("WHERE " + "\n  AND ".join(where_parts))

    lines.append("LIMIT 500;")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
st.title("🔗 Redash Query Builder")
st.caption(
    "Pick a table, follow the relationships, choose fields and filters — get a working SQL query. "
    "No preset queries, no LLM: this is a static relationship map built from the schema's `_id` "
    "naming conventions."
)

with st.sidebar:
    st.header("Replica / Schema")
    replica_names = list(REPLICA_MAP.keys())
    st.session_state.replica = st.selectbox(
        "Replica (connection)",
        replica_names,
        index=replica_names.index(st.session_state.replica) if st.session_state.replica in replica_names else 0,
    )
    replica_info = REPLICA_MAP[st.session_state.replica]
    schemas = replica_info["schemas"]

    if replica_info["type"] == "pending_clarification" or not schemas:
        st.warning("Schema list for this replica isn't confirmed yet.")
        st.session_state.cloud_schema = ""
    else:
        schema_label = "Cloud schema" if replica_info["type"].startswith("shared_cloud") else "Client schema"
        if st.session_state.cloud_schema not in schemas:
            st.session_state.cloud_schema = schemas[0]
        st.session_state.cloud_schema = st.selectbox(
            schema_label, schemas, index=schemas.index(st.session_state.cloud_schema)
        )
        if replica_info["type"] == "dedicated":
            st.caption("Dedicated server — one client per schema.")
        elif replica_info["type"] == "dedicated_single_schema":
            st.caption("Dedicated server — single schema, named to match the replica.")
        elif replica_info["type"] == "shared_cloud_foreign":
            st.caption(replica_info.get("note", ""))

    st.caption(f"All schemas share the same table structure (confirmed) — "
               f"queries are generated against `{st.session_state.cloud_schema or '<schema>'}.<table>`.")

    st.divider()
    st.header("Query")
    if st.session_state.query_tables:
        for t in st.session_state.query_tables:
            c1, c2 = st.columns([4, 1])
            c1.write(f"**{t}**")
            if c2.button("✕", key=f"remove_{t}"):
                remove_table_from_query(t)
                st.rerun()
    else:
        st.write("_No tables added yet._")

    if st.session_state.joins:
        st.caption("Joins (toggle INNER/LEFT):")
        for idx, j in enumerate(st.session_state.joins):
            label = f"{j['left']} ↔ {j['right']}"
            current = j.get("join_type", "JOIN")
            choice = st.selectbox(label, ["JOIN", "LEFT JOIN"],
                                   index=0 if current == "JOIN" else 1,
                                   key=f"jointype_{idx}")
            st.session_state.joins[idx]["join_type"] = choice
            if j.get("is_composite"):
                st.caption(f"　composite join · {j.get('note','')}")

    if st.session_state.query_tables:
        if st.button("Clear query", type="secondary"):
            st.session_state.query_tables = []
            st.session_state.joins = []
            st.session_state.selected_fields = {}
            st.session_state.filters = []
            st.rerun()

    st.divider()
    with st.expander(f"⚠️ Unresolved relationships ({len(AMBIGUOUS)})"):
        st.caption("Columns ending in `_id` that couldn't be confidently mapped to a table — "
                   "mostly generic-purpose columns or names needing more domain knowledge.")
        for a in AMBIGUOUS[:60]:
            cands = ", ".join(a["candidates"]) if a["candidates"] else "no match"
            st.write(f"`{a['from_table']}.{a['from_column']}` → {cands}")

    with st.expander(f"🔀 Polymorphic references ({len(POLYMORPHIC_RELATIONSHIPS)})"):
        st.caption("These columns point at *different* tables depending on a discriminator "
                   "column's value — not a fixed relationship, so add the target table manually "
                   "and filter on the discriminator yourself.")
        for p in POLYMORPHIC_RELATIONSHIPS:
            conf_icon = {"verified": "✅", "inferred": "🟡", "unresolved": "❓"}.get(p["confidence"], "")
            disc = p["discriminator_column"] or "unknown discriminator"
            st.write(f"{conf_icon} **{p['table']}**: `{disc}` decides what `{p['id_column']}` points to")
            st.caption(p["example"])

# --- Table picker ---------------------------------------------------------
st.subheader("1. Pick a starting table")
tab1, tab2 = st.tabs(["⭐ Starter tables", "🔍 Search all tables"])

with tab1:
    cols = st.columns(len(STARTER_TABLES) or 1)
    for i, t in enumerate(STARTER_TABLES):
        with cols[i]:
            if st.button(f"➕ {t}", key=f"starter_{t}", use_container_width=True):
                add_table_to_query(t)
                st.rerun()

with tab2:
    search = st.selectbox("Table name", options=[""] + ALL_TABLE_NAMES, index=0)
    if search:
        c1, c2 = st.columns([1, 3])
        with c1:
            if st.button(f"➕ Add {search}"):
                add_table_to_query(search)
                st.rerun()
        with c2:
            st.caption(f"{len(col_names(search))} columns, PK: {', '.join(TABLES[search]['primary_key'])}")

if "_join_warning" in st.session_state:
    st.warning(
        f"Added **{st.session_state['_join_warning']}** but found no direct relationship to the "
        f"other tables already in the query. It was added as a CROSS JOIN placeholder — fix the "
        f"ON condition manually, or add an intermediate table that bridges them."
    )
    del st.session_state["_join_warning"]

# --- Related tables ---------------------------------------------------------
if st.session_state.query_tables:
    st.subheader("2. Explore related tables")
    for base_table in st.session_state.query_tables:
        with st.expander(f"Tables related to **{base_table}**", expanded=(len(st.session_state.query_tables) == 1)):
            out_rels = sorted(OUTGOING.get(base_table, []), key=lambda r: CONFIDENCE_ORDER.get(r["confidence"], 9))
            in_rels = sorted(INCOMING.get(base_table, []), key=lambda r: CONFIDENCE_ORDER.get(r["confidence"], 9))

            c1, c2 = st.columns(2)
            with c1:
                st.markdown(f"**{base_table} references →**")
                if not out_rels:
                    st.caption("No outgoing `_id` references found.")
                for r in out_rels:
                    already = r["to_table"] in st.session_state.query_tables
                    label = f"{CONFIDENCE_LABEL[r['confidence']]}  `{r['from_column']}` → **{r['to_table']}**"
                    bc1, bc2 = st.columns([3, 1])
                    bc1.write(label)
                    if not already:
                        if bc2.button("Add", key=f"add_out_{base_table}_{r['from_column']}_{r['to_table']}"):
                            add_table_to_query(r["to_table"])
                            st.rerun()
                    else:
                        bc2.caption("in query")

            with c2:
                st.markdown(f"**Tables that reference {base_table} →**")
                if not in_rels:
                    st.caption("No incoming references found.")
                for r in in_rels[:25]:
                    already = r["from_table"] in st.session_state.query_tables
                    label = f"{CONFIDENCE_LABEL[r['confidence']]}  **{r['from_table']}**.`{r['from_column']}`"
                    bc1, bc2 = st.columns([3, 1])
                    bc1.write(label)
                    if not already:
                        if bc2.button("Add", key=f"add_in_{base_table}_{r['from_table']}_{r['from_column']}"):
                            add_table_to_query(r["from_table"])
                            st.rerun()
                    else:
                        bc2.caption("in query")
                if len(in_rels) > 25:
                    st.caption(f"...and {len(in_rels) - 25} more referencing tables not shown.")

# --- Fields & filters --------------------------------------------------------
if st.session_state.query_tables:
    st.subheader("3. Choose fields")
    field_cols = st.columns(len(st.session_state.query_tables))
    for i, t in enumerate(st.session_state.query_tables):
        with field_cols[i]:
            st.markdown(f"**{t}**")
            chosen = st.multiselect(
                "Fields",
                options=col_names(t),
                default=st.session_state.selected_fields.get(t, TABLES[t]["primary_key"]),
                key=f"fields_{t}",
                label_visibility="collapsed",
            )
            st.session_state.selected_fields[t] = chosen

    st.subheader("4. Add filters")
    for idx, f in enumerate(st.session_state.filters):
        c1, c2, c3, c4, c5 = st.columns([2, 2, 1.5, 2, 0.5])
        f["table"] = c1.selectbox("Table", st.session_state.query_tables,
                                   index=st.session_state.query_tables.index(f["table"]),
                                   key=f"filt_table_{idx}")
        f["column"] = c2.selectbox("Column", col_names(f["table"]),
                                    index=col_names(f["table"]).index(f["column"]) if f["column"] in col_names(f["table"]) else 0,
                                    key=f"filt_col_{idx}")
        f["operator"] = c3.selectbox("Op", OPERATORS,
                                      index=OPERATORS.index(f["operator"]),
                                      key=f"filt_op_{idx}")
        if f["operator"] not in ("IS NULL", "IS NOT NULL"):
            f["value"] = c4.text_input("Value", value=f["value"], key=f"filt_val_{idx}",
                                        placeholder="comma-separate for IN / BETWEEN")
        else:
            c4.caption("(no value needed)")
        if c5.button("✕", key=f"filt_remove_{idx}"):
            st.session_state.filters.pop(idx)
            st.rerun()

    if st.button("➕ Add filter"):
        default_table = st.session_state.query_tables[0]
        st.session_state.filters.append({
            "table": default_table,
            "column": col_names(default_table)[0],
            "operator": "=",
            "value": "",
        })
        st.rerun()

    # --- SQL output ---------------------------------------------------------
    st.subheader("5. Generated SQL")
    sql = generate_sql()
    st.code(sql, language="sql")
    st.caption(
        f"{st.session_state.replica} → {st.session_state.cloud_schema} · "
        f"LIMIT 500 added by default — remove it in Redash if you need the full result."
    )
else:
    st.info("Add a table above to start building a query.")
