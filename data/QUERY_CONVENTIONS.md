# How queries are actually written against this schema

Notes distilled from real Redash/DB queries, kept here so the patterns aren't
lost and so the query builder's assumptions are traceable back to evidence.

## 1. Schema qualification

Every table reference is schema-qualified: `<Schema>.<table>`, e.g.
`Cloud1.tenant`, `Cloud29.sale_order_item`, `HealthyHeyFoods.item`.
`uniware.` and `public.` show up in some hand-written examples — these are
generic placeholders people use when writing docs/examples, not real schema
names. The real prefixes are the `Cloud<N>` names or dedicated client names
from `data/replica_map.json`.

## 2. Three relationship shapes, not just `_id`

1. **`_id` → `id`** (the common case): `sale_order_item.sale_order_id → sale_order.id`.
2. **`_code` → `code`**: seen in `picklist_item.sale_order_item_code → sale_order_item.code`
   and `picklist_item.sale_order_code → sale_order.code`. Business-key joins,
   used interchangeably with id-based joins in real queries.
3. **Composite (multi-column) joins**: no single FK column at all. Example:
   `item_type_inventory` ↔ `picklist_item_detail` joins on
   `item_type_id = item_type_id AND shelf_id = shelf_id AND batch_id <=> batch_id`
   (note the null-safe `<=>` for `batch_id`, since it can be NULL). These only
   surface by reading real queries — no naming convention points to them.

## 3. Shared-PK subtype tables

`facility` and `vendor` do **not** have a `party_id` column — they reuse
`party.id` as their own `id`. Confirmed via real queries:
```sql
JOIN party p ON p.id = f.id      -- facility
JOIN party p ON v.id = p.id      -- vendor
```
`customer` is assumed to follow the same pattern (not yet confirmed by a
query) — treat as lower confidence until verified.

`party` looks like a base/generic entity table (tenant, code, name, address
fields) with `facility`/`vendor`/`customer` as specialized subtypes sharing
its id space — a classic supertype/subtype (joined-table inheritance) design.

## 4. Polymorphic references

Several tables have an `_id`-like column whose target table isn't fixed —
it's decided by a separate discriminator column at query time:

| Table | Discriminator | Id column | Naming style | Confidence |
|---|---|---|---|---|
| `notification` | `entity` | `identifier` | short class name (`'ShippingPackage'`) | verified |
| `custom_field_value` | `entity` | `identifier` | full Java class (`'com.uniware.core.entity.InflowReceiptItem'`) | verified |
| `entity_source_reference` | `entity` | `entity_identifier` | short class name | verified |
| `handling_unit_metadata` | `entity_type` | `entity_id` | unknown | inferred (same shape, unconfirmed) |
| `shelf_activity_audit` | `entity_type` | `entity_id` | unknown | inferred |
| `iti_snapshot_debugging` | `entity_name` | `entity_id` | unknown | inferred |
| `box_item_metadata` | *(none found)* | `entity_id` | unknown | unresolved |
| `picklist_detail_metadata` | *(none found)* | `entity_id` | unknown | unresolved — has a `type` column that may serve this role |

These can't be joined directly in the query builder — add the target table
manually and filter on the discriminator value yourself.

## 5. Common query patterns worth building into the tool eventually

- **`LEFT JOIN ... WHERE (x.id IS NULL OR x.status_code IN (...))`** — very
  common "not yet at this stage, or at one of these stages" pattern (e.g.
  open sale-order-items not yet packaged, or packaged-but-not-shipped).
- **`status_code IN (...)` filtering** is the default way state is filtered,
  not a single `=`. Known status values seen so far (not exhaustive):
  - `sale_order_item.status_code`: `FULFILLABLE`, `PICKING_FOR_STAGING`,
    `PICKING_FOR_INVOICING`, `STAGED`, `CANCELLED`, `CREATED`, `UNFULFILLABLE`
  - `shipping_package.status_code`: `CREATED`, `PICKING`, `PICKED`,
    `LOCATION_NOT_SERVICEABLE`, `PENDING_CUSTOMIZATION`, `CUSTOMIZATION_COMPLETE`
  - `item_type_inventory_allocation.status_code`: `ALLOCATED`,
    `ADDED_IN_PICKLIST`, `PICKLIST_SCAN_COMPLETE`
  - `picklist_item.status_code`: includes `PUTBACK_PENDING`
  - `picklist_detail.status`: commonly filtered as `<> 'CLOSED'`
- **Filtering by business code, not id**: `tenant.code = 'yesmadam'`,
  `sale_order.code = 'SO18392'` are the normal way to scope a query, not raw
  numeric ids.
- **Cross-schema reporting**: a `PREPARE`/`EXECUTE` pattern using
  `INFORMATION_SCHEMA` + `GROUP_CONCAT(... SEPARATOR ' UNION ALL ')` runs one
  query across every schema that has certain columns, substituting
  `SCHEMA_NAME` per iteration. Out of scope for v1 (single-schema queries),
  but worth a "run across all schemas" mode later.
- **`FORCE INDEX (created)`** shows up on large, frequently-filtered tables
  like `sale_order` for performance — not something the builder needs to
  replicate, just don't be surprised to see it in hand-written queries.

## 6. Kit/bundle structure

`kit_item_type.item_type_id` and `kit_item_type.component_sku_id` **both**
point to `item_type` — a kit's own SKU and its component SKUs are the same
table, self-referenced twice in one row.
