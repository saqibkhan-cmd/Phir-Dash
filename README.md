# Phir-Dash

One unified app - a newcomer CSM/TAM analyst and an expert Redash/SQL user
both work from the same screen:

1. Pick a **Cloud/client** - the Replica is looked up automatically.
2. Pick a **Major category → Sub-category** (13 business categories built
   from real production reports), or **Advanced / Custom Table** to pick
   any of the 377 tables directly - the expert path, same screen.
3. Check the fields/filters you want. The query **previews live** as you
   check boxes - no submit button.
4. **Add this selection** locks it into your running list at the top
   (editable/removable). Add as many categories as you need.
5. **Generate Final Query** stitches everything into one query - but only
   across categories with a *verified* relationship between them. If two
   selections don't have one, they come back as separate queries instead
   of being forced together into something misleading.

No LLM, no paid APIs - pure Python/logic, same as the other internal tools.

See **[QUERY_CONVENTIONS.md](QUERY_CONVENTIONS.md)** for the underlying
schema patterns (schema qualification, code-based joins, composite joins,
shared-PK subtypes, polymorphic references).

## Project layout

```
redash_query_builder/
├── app.py                    # the one app - both simple and advanced modes
├── query_engine.py           # core query-assembly logic (tested independently)
├── build_schema_data.py      # offline: CSV export -> schema_data.json
├── build_replica_map.py      # offline: replica/schema hierarchy -> replica_map.json
├── build_taxonomy.py         # offline: business_vocabulary.json -> taxonomy.json
├── requirements.txt
├── QUERY_CONVENTIONS.md
├── README.md
└── data/
    ├── schema_data.json          # tables, relationships, composite/polymorphic refs
    ├── replica_map.json          # Replica -> Cloud/client schema list
    ├── business_vocabulary.json  # raw extraction: business-labeled columns/filters
    │                              # + each export's verbatim join chain, from 197 real
    │                              # production export configs
    └── taxonomy.json             # the 13 major / 68 sub-category structure app.py
                                   # actually reads, built from business_vocabulary.json
```

## How the multi-category stitching works

Each category (e.g. "Sale Order → Sale Orders") carries its own **verbatim,
already-correct join chain** copied from a real export config - table
aliases and all. Combining two categories doesn't try to merge those join
chains (too easy to get wrong); instead, each selected category becomes its
own self-contained subquery, and subqueries are joined to each other at the
*anchor-table* level using the same verified relationship graph the
Advanced/Custom Table mode uses. If no verified relationship connects two
categories' anchor tables, `app.py` refuses to force a join - it shows each
as a separate, independently-correct query instead. This is the mechanism
behind requirement "prevent baseless queries": there's no free-form
table/field combination possible, only pre-validated categories linked
through evidence-backed relationships.

`query_engine.py` has no Streamlit dependency and is tested independently -
see the test transcripts in this project's history for coverage (single
block, linked multi-block, refused unlinked multi-block, composite-join
stitching, filter token substitution, the eCloud `uniware` schema override,
and the Advanced/Custom Table path).

## Run it locally

```bash
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

## Push to GitHub / deploy on Streamlit Cloud

Same repo, same steps as before - see git/GitHub/Streamlit Cloud
instructions from earlier in this project. Just replace `app.py`, add
`query_engine.py` and `build_taxonomy.py`, update the `data/` files, and
**delete `business_ui.py` and `patterns.py`** if they're still in the repo
- everything now lives in the one `app.py`.

## Known limitations / next steps

1. **68 sub-categories, 13 majors** covering Sale Order, Inventory, PO,
   Inventory Syncing, Facility, SKU & Category, Users, Putaway, Returns,
   Work Order/Kitting/Roll-up SKU, Shipping Package, Reports, Cycle Count.
   62 of 68 pull real business-labeled fields from a matching export
   config; 6 (Channel Sync Status, Role, Access Resource, Role→Access
   Resource Mapping, Work Order, Kit Composition, Bundle, Bundle Items) had
   no matching export config and fall back to raw table columns - flagged
   with a warning in the UI.
2. **No live database connection** - generates the ready-to-run query
   rather than executing it (no Redash API/DB credentials configured here).
3. **Filters are AND-only** within a category; cross-category stitching is
   a simple JOIN chain (no OR logic, no exclusions).
4. **Linking is linear** (block 1 ↔ block 2 ↔ block 3 in the order added) -
   if block 3 only relates to block 1, not block 2, it won't find that path.
   Uncommon in practice but worth knowing.
5. Categories without a real export config (marked "raw" in `taxonomy.json`)
   haven't been business-validated - worth sending a real query for any of
   them, the same way the rest of this project's data was built.
6. `customer.id = party.id` and a handful of "low confidence" fuzzy-matched
   relationships in `schema_data.json` are still worth spot-checking against
   real data over time.
