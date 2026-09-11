"""
Phir-Dash - Business Investigation UI
--------------------------------------
The simple, question-first interface from the CSM/TAM requirements doc:
Cloud-first lookup (no need to know Replicas), fixed investigation patterns
grouped into business categories (no free-form query building, so no
baseless combinations are possible), plain-English results with the
generated SQL tucked away for anyone who wants to see it.

Every pattern here is grounded in a real, verified query - see patterns.py
for the source of each one. This is a first pass covering the patterns we
have evidence for, not an exhaustive system - categories with no verified
pattern yet say so rather than guessing.

For raw ad-hoc query building (any table, any join), see app.py - the
"Advanced Investigation" category links there.

Run with: streamlit run business_ui.py
"""

import json
from pathlib import Path

import streamlit as st

from patterns import PATTERNS, CATEGORIES

DATA_PATH = Path(__file__).parent / "data" / "schema_data.json"
REPLICA_MAP_PATH = Path(__file__).parent / "data" / "replica_map.json"

st.set_page_config(page_title="Phir-Dash", layout="centered")


@st.cache_data
def load_replica_map():
    with open(REPLICA_MAP_PATH) as f:
        return json.load(f)


@st.cache_data
def build_cloud_to_replica_index(replica_map):
    """Invert Replica -> [schemas] into schema -> Replica, for the
    Cloud-first lookup the requirements doc asks for."""
    index = {}
    for replica, info in replica_map.items():
        for schema in info["schemas"]:
            index[schema] = {"replica": replica, "type": info["type"]}
    return index


REPLICA_MAP = load_replica_map()
CLOUD_INDEX = build_cloud_to_replica_index(REPLICA_MAP)
ALL_CLOUDS = sorted(CLOUD_INDEX.keys())

st.title("Phir-Dash")
st.caption("Ask a business question. No SQL, no Redash knowledge needed.")

# ---------------------------------------------------------------------------
# 1. Cloud -> Replica (reversed lookup, per requirements doc section 1)
# ---------------------------------------------------------------------------
st.subheader("1. Which Cloud/client is this about?")
cloud = st.selectbox("Search for a Cloud or client schema", [""] + ALL_CLOUDS, index=0)

if cloud:
    info = CLOUD_INDEX[cloud]
    c1, c2 = st.columns(2)
    c1.metric("Cloud", cloud)
    c2.metric("Replica", info["replica"])
    schema_name = cloud
else:
    st.info("Pick a Cloud or client above to continue.")
    schema_name = None

st.divider()

# ---------------------------------------------------------------------------
# 2. Category -> Question (fixed patterns only - no free combination, so no
#    baseless queries are possible, per requirements doc section 5)
# ---------------------------------------------------------------------------
if schema_name:
    st.subheader("2. What do you want to find?")
    category = st.radio("Category", CATEGORIES, label_visibility="collapsed")

    if category == "Advanced Investigation":
        st.info(
            "For open-ended investigation across any table - picking your own tables, "
            "fields, and filters - use the full query builder (`app.py`) instead. "
            "This simple mode only covers the fixed, verified questions below."
        )
    else:
        cat_patterns = [p for p in PATTERNS if p["category"] == category]
        if not cat_patterns:
            st.warning(
                f"No verified question pattern for **{category}** yet - rather than guess "
                f"one, this stays empty until a real query confirms how to answer it "
                f"correctly. Share an example query for this category and it'll be added."
            )
        else:
            question = st.radio(
                "Question",
                [p["question"] for p in cat_patterns],
                label_visibility="collapsed",
            )
            pattern = next(p for p in cat_patterns if p["question"] == question)

            st.caption(pattern["explanation"])

            with st.form(key=f"form_{pattern['id']}"):
                values = {}
                for inp in pattern["inputs"]:
                    values[inp["key"]] = st.text_input(inp["label"], key=f"{pattern['id']}_{inp['key']}")
                submitted = st.form_submit_button("Find it")

            if submitted:
                missing = [inp["label"] for inp in pattern["inputs"] if not values.get(inp["key"], "").strip()]
                if missing:
                    st.error(f"Please fill in: {', '.join(missing)}")
                else:
                    sql = pattern["sql_template"].format(schema=schema_name, **values).strip()
                    st.success("Here's what this looks for:")
                    st.write(pattern["explanation"])
                    st.markdown(
                        f"**Source:** *{pattern['source']}*  \n"
                        "No live database connection is configured here, so this generates "
                        "the ready-to-run query - copy it into Redash (targeting the "
                        f"**{schema_name}** schema on **{CLOUD_INDEX[schema_name]['replica']}**) "
                        "to get the actual result."
                    )
                    with st.expander("Advanced: view the generated SQL"):
                        st.code(sql, language="sql")
