"""
Builds data/taxonomy.json - the major/sub-category structure the unified
app.py uses for its business-question mode.

Every sub-category is grounded in one of two verified sources:
  - "export": pulls real business-labeled columns/filters straight from a
    real production Redash export config (data/business_vocabulary.json).
    This is the strong case - the fields shown are exactly what a real
    report already exposes to end users.
  - "raw": no matching export config exists, so falls back to the anchor
    table's own columns from data/schema_data.json, with the column name
    turned into a readable label. Weaker case - flagged as such in the UI
    so it's clear these haven't been business-validated yet.

Run with: python3 build_taxonomy.py
"""
import json
import re

schema = json.load(open("redash_query_builder/data/schema_data.json"))
bv = json.load(open("redash_query_builder/data/business_vocabulary.json"))
bv_by_name = {c["export_name"]: c for c in bv}
TABLES = schema["tables"]

MAX_FIELDS_PER_SUBCAT = 25


def humanize(col_name):
    return " ".join(w.capitalize() for w in col_name.split("_"))


def from_export(export_name, label=None):
    cfg = bv_by_name.get(export_name)
    if not cfg or not cfg.get("anchor_table") or not cfg.get("from_join_clause"):
        return None
    fields = [{"label": c["business_name"], "expr": c["sql_expression"]}
              for c in cfg["columns"][:MAX_FIELDS_PER_SUBCAT]]
    filters = [{"label": f["business_name"], "condition": f.get("condition"), "type": f.get("type")}
               for f in cfg["filters"] if f.get("business_name")]
    return {
        "label": label or export_name,
        "anchor_table": cfg["anchor_table"],
        "anchor_alias": cfg["anchor_alias"],
        "from_join_clause": cfg["from_join_clause"],
        "source": "export",
        "export_name": export_name,
        "fields": fields,
        "filters": filters,
    }


def from_raw_table(table, label, field_names=None):
    if table not in TABLES:
        return None
    cols = TABLES[table]["columns"]
    if field_names:
        cols = [c for c in cols if c["name"] in field_names]
    else:
        cols = cols[:MAX_FIELDS_PER_SUBCAT]
    alias = table  # single-table block - alias with its own name, no collision risk in isolation
    fields = [{"label": humanize(c["name"]), "expr": f"{alias}.{c['name']}"} for c in cols]
    return {
        "label": label,
        "anchor_table": table,
        "anchor_alias": alias,
        "from_join_clause": f"FROM {table} {alias}",
        "source": "raw",
        "export_name": None,
        "fields": fields,
        "filters": [],
    }


TAXONOMY = {
    "Sale Order": {
        "Sale Orders": from_export("Sale Orders"),
        "Sale Order Margins": from_export("Sale Order Margins"),
        "Back Orders": from_export("Back Orders"),
        "Hopped Orders": from_export("Hopped orders"),
    },
    "Inventory": {
        "Shelfwise Inventory": from_export("Shelfwise Inventory"),
        "Inventory Worth": from_export("Inventory Worth"),
        "Inventory Worth By Category": from_export("Inventory Worth By Category"),
        "Inventory Aging": from_export("Inventory Aging"),
        "Inventory Ledger": from_export("Inventory Ledger"),
        "Inventory Adjustment": from_export("Inventory Adjustment"),
        "Reorder Report": from_export("Reorder Report"),
        "Fast Moving SKU": from_export("Fast Moving SKU"),
        "Expiring/Expired Inventory": from_export("Batching Expired Inventory"),
    },
    "PO": {
        "Purchase Orders": from_export("Purchase Orders"),
        "GRN": from_export("GRN"),
        "GRN-PO Mapping": from_export("GRN-PO Mapping"),
        "Vendors": from_export("Vendors"),
        "Vendor Item Master": from_export("Vendor Item Master"),
        "Vendor Catalog": from_export("Vendor Catalog"),
        "Unwanted Purchase Orders": from_export("Unwanted Purchase Orders"),
        "ASN": from_export("ASN report"),
    },
    "Inventory Syncing": {
        "Channel Item Type Sync": from_export("Channel Item Type Report"),
        "Channel Sync Status": from_raw_table(
            "channel", "Channel Sync Status",
            {"code", "source_code", "inventory_sync_status", "catalog_sync_status",
             "order_sync_status", "pricing_sync_status", "reconciliation_sync_status", "enabled"}),
    },
    "Facility": {
        "Facility": from_export("Facility"),
        "Facility Alias": from_export("Facility Alias"),
        "Facility Time Slot": from_export("Facility Time Slot"),
        "Facility Allocation Rules": from_export("Facility Allocation Rules"),
        "Facility Enable": from_export("Facility Enable"),
    },
    "SKU & Category": {
        "Item Master": from_export("Item Master"),
        "Item Barcodes": from_export("Item Barcodes"),
        "Category": from_export("Category"),
        "Category Section Mapping": from_export("Category Section Mapping"),
        "Dropship Facility Item Master": from_export("Dropship Facility Item Master"),
        "Tax Type Configuration": from_export("Tax Type Configuration"),
    },
    "Users": {
        "Users": from_export("Users"),
        "Users Detailed View": from_export("Users detailed view"),
        "User Comments": from_export("User Comments"),
        "Role": from_raw_table("role", "Role", None),
        "Access Resource": from_raw_table("access_resource", "Access Resource", None),
        "Role -> Access Resource Mapping": from_raw_table(
            "role_access_resource", "Role Access Resource Mapping", None),
    },
    "Putaway": {
        "Putaway": from_export("Putaway"),
        "GRN/Gatepass to Putaway": from_export("GRN/Gatepass to Putaway"),
        "Pending Putaways": from_export("Pending Putaways New"),
    },
    "Returns": {
        "Reverse Pickup": from_export("Reverse Pickup"),
        "Reverse Pickup Payments": from_export("Reverse Pickup Payments"),
        "Return Manifest": from_export("Return Manifest"),
        "Return Invoices": from_export("Return Invoices"),
        "Courier Returns": from_export("Courier Returns"),
        "Putback Pending": from_export("Putback Pending All Facility"),
        "Not Found Audit": from_export("NotFoundAuditExport"),
    },
    "Work Order / Kitting / Roll-up SKU": {
        "Work Order": from_raw_table("work_order", "Work Order", None),
        "Kit Composition": from_raw_table("kit_item_type", "Kit Composition", None),
        "Bundle": from_raw_table("bundle", "Bundle", None),
        "Bundle Items (Roll-up SKU)": from_raw_table("bundle_item_type", "Bundle Items", None),
    },
    "Shipping Package": {
        "Shipping Package": from_export("Shipping Package"),
        "Picklist": from_export("Picklist"),
        "Invoice": from_export("Invoice"),
        "Shipping Manifest": from_export("Shipping Manifest"),
        "Box / Packslip": from_export("Box Packslip Report"),
        "Gatepass": from_export("Gatepass"),
        "Inbound Gatepass": from_export("Inbound GatePass"),
    },
    "Reports": {
        "Transaction Ledger": from_export("Transaction Ledger"),
        "HSN Summary Report": from_export("HSN Summary Report"),
        "Sales Forecast Report": from_export("Sales Forecast Report"),
        "Aggregate Sales Report": from_export("Aggregate Sales Report"),
    },
    "Cycle Count": {
        "Cycle Count Overall Data": from_export("Cycle Count Overall Data"),
        "Cycle Count Report": from_export("CYCLE_COUNT_REPORT"),
        "Shelf Report": from_export("Shelf Report"),
    },
}

# drop any sub-category that failed to resolve (export not found / no anchor table)
# rather than silently shipping a broken entry
resolved, dropped = {}, []
for major, subs in TAXONOMY.items():
    resolved[major] = {}
    for sub, data in subs.items():
        if data is None:
            dropped.append(f"{major} -> {sub}")
            continue
        resolved[major][sub] = data
    if not resolved[major]:
        del resolved[major]

print("Majors:", len(resolved))
print("Sub-categories resolved:", sum(len(v) for v in resolved.values()))
if dropped:
    print("Dropped (no verified source found):", dropped)

json.dump(resolved, open("redash_query_builder/data/taxonomy.json", "w"), indent=1)
import os
print("taxonomy.json size KB:", os.path.getsize("redash_query_builder/data/taxonomy.json") / 1024)
