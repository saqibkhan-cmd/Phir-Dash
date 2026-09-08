# Redash Query Builder

Pick a Replica/schema → a starting table → see what's related to it → choose
fields and filters → get a working, schema-qualified SQL query. No LLM, no
paid APIs — the relationship map is inferred once, offline, from a Redash
schema export plus real queries, and the app itself is plain Python/Streamlit
logic from there.

See **[QUERY_CONVENTIONS.md](QUERY_CONVENTIONS.md)** for the patterns this
tool's data model is built on (schema qualification, code-based joins,
composite joins, shared-PK subtypes, polymorphic references) — read that
before extending the inference logic.

## Project layout

```
redash_query_builder/
├── app.py                    # the Streamlit app
├── build_schema_data.py      # offline inference: CSV export -> schema_data.json
├── build_replica_map.py      # offline: replica/schema hierarchy -> replica_map.json
├── requirements.txt
├── QUERY_CONVENTIONS.md      # how queries are actually written here (read first)
├── README.md
└── data/
    ├── schema_data.json      # tables, relationships, composite/polymorphic refs
    └── replica_map.json      # Replica -> Cloud/client schema list
```

## Run it locally

```bash
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

Opens at `http://localhost:8501`.

## Push to GitHub

```bash
cd redash_query_builder
git init
git add .
git commit -m "Redash query builder v1"
git branch -M main
git remote add origin https://github.com/<your-username>/<repo-name>.git
git push -u origin main
```

If `data/schema_data.json` ever contains anything you don't want public
(it currently doesn't — no credentials, no row data, just table/column/
relationship metadata and the client-name list from the dedicated replicas),
make the repo **private** in GitHub's repo creation step, or add the file to
`.gitignore` and distribute it separately.

## Deploy with Streamlit Community Cloud (easiest hosted option)

1. Push the repo to GitHub (above).
2. Go to [share.streamlit.io](https://share.streamlit.io), sign in with
   GitHub.
3. **New app** → pick your repo, branch `main`, main file path `app.py`.
4. Deploy. Streamlit installs `requirements.txt` and runs `streamlit run app.py`
   automatically — no extra config needed since this app has no secrets or
   external API calls.
5. Every push to `main` auto-redeploys.

If the client-name list in `replica_map.json` shouldn't be public, keep the
GitHub repo private — Streamlit Community Cloud can deploy from private repos
once you grant it access during setup.

## Regenerating the data (new schema export, more queries, mapping changes)

```bash
# Table/column/relationship data - re-run whenever the schema CSV changes
python3 build_schema_data.py

# Replica -> schema hierarchy - re-run when a new dedicated client CSV
# arrives, or the ReplicaECloud1 range gets confirmed
python3 build_replica_map.py
```

Both scripts read from paths currently hardcoded to this session's uploaded
files (`/mnt/user-data/uploads/...`) — update those paths at the top of each
script to point at wherever you keep the source CSVs going forward.

## How the relationship inference works

- **`tables`**: every table's columns, types, nullability, keys — straight
  from the Redash "schema browser" CSV export.
- **`relationships`**: `_id`/`_code` columns matched to the table/column they
  likely reference, each tagged with a confidence level:
  - `verified` — manually confirmed against a real query
  - `high` — direct name match (column stem == an existing table/column name)
  - `low` — matched after stripping a role prefix like `parent_`, `return_`,
    `approved_by_` — **double-check these** before trusting them.
- **`composite_relationships`**: multi-column joins with no single FK column
  (only discoverable from real queries — see QUERY_CONVENTIONS.md §2).
- **`polymorphic_relationships`**: columns whose target table depends on a
  separate discriminator column's value, not fixed by naming — shown
  separately in the app's sidebar rather than offered as a normal "Add"
  button (see QUERY_CONVENTIONS.md §4).
- **`ambiguous`**: `_id` columns that couldn't be confidently resolved
  (multiple candidate tables, or no match at all). Visible in the app
  sidebar for review.

The app auto-derives JOINs by walking `relationships` + `composite_relationships`
whenever you add a table linked to one already in your query. If no link is
found, it adds a flagged placeholder `JOIN ... ON 1=1` so you know to fix it
manually or add a bridging table. Each join can be toggled between `JOIN`
and `LEFT JOIN` in the sidebar.

## Known limitations / next steps

1. **Multiple relationships between two tables**: when two tables have more
   than one plausible FK path (e.g. `sale_order_item.item_id` *and*
   `item.sale_order_item_id` both exist), the app picks whichever it finds
   first. Worth adding a picker when this happens.
2. **The 64 remaining "unresolved" columns** haven't been individually
   chased down — mostly generic-purpose columns (`thread_id`, `request_id`,
   `token_id`) that may not be real FKs at all. Worth a pass together.
3. **Filters are AND-only** — no OR groups or nested conditions yet.
4. **No self-join support** — hierarchical FKs (e.g. `parent_sale_order_item_id`)
   aren't yet offered as "add this table again with an alias."
5. **No cross-schema ("run against every Cloud schema") mode** — real queries
   use a dynamic `PREPARE`/`EXECUTE` pattern for this (QUERY_CONVENTIONS.md §5);
   the app currently targets one schema at a time.
6. Only `sale_order`, `sale_order_item`, `item`, `item_type` are pinned as
   "starter" quick-add buttons — grow this list as more corners of the
   schema get validated.
7. **`customer.id = party.id`** is assumed (same pattern as `facility`/
   `vendor`) but not yet confirmed by a real query — verify when convenient.
