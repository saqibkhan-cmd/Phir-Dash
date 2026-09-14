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

# Tables confirmed (via real query testing during this project) or strongly
# expected to be very large and/or span many tenants/facilities within a
# single shared schema. A search against these with no facility/tenant/SKU
# scoping at all risks an unintentionally broad, expensive query - flagged
# so the UI can require at least one scoping filter before running.
HIGH_RISK_TABLES = {
    "item_type_inventory", "item_type_inventory_allocation",
    "inventory_adjustment", "inventory_ledger", "notification",
}


def humanize(col_name):
    return " ".join(w.capitalize() for w in col_name.split("_"))


def make_description(major, sub, anchor_table, quick_labels):
    hint = ", ".join(quick_labels[:3]) if quick_labels else "key details"
    return f"Look up {sub} records - includes things like {hint}."


def from_export(export_name, label=None):
    cfg = bv_by_name.get(export_name)
    if not cfg or not cfg.get("anchor_table") or not cfg.get("from_join_clause"):
        return None
    all_fields = [{"label": c["business_name"], "expr": c["sql_expression"]}
                  for c in cfg["columns"][:MAX_FIELDS_PER_SUBCAT]]
    filters = [{"label": f["business_name"], "condition": f.get("condition"), "type": f.get("type")}
               for f in cfg["filters"] if f.get("business_name")]
    quick_labels = pick_quick_fields(all_fields)
    quick_filter_labels = pick_quick_filters(filters)
    disp_label = label or export_name
    return {
        "label": disp_label,
        "description": make_description("", disp_label, cfg["anchor_table"], quick_labels),
        "anchor_table": cfg["anchor_table"],
        "anchor_alias": cfg["anchor_alias"],
        "from_join_clause": cfg["from_join_clause"],
        "source": "export",
        "export_name": export_name,
        "fields": all_fields,
        "quick_fields": quick_labels,
        "filters": filters,
        "quick_filters": quick_filter_labels,
        "requires_scope": cfg["anchor_table"] in HIGH_RISK_TABLES,
    }


# Fields commonly worth defaulting to: short, identifying, direct column
# references (not big CASE expressions) whose label suggests it's a core
# identifying/status/date field - a business person scanning a result
# wants "what is this row, what state is it in, when did it happen", not
# every one of the 25 columns a report happens to expose.
QUICK_KEYWORDS = ["code", "status", "name", "date", "created", "updated", "qty", "quantity"]


def pick_quick_fields(fields, min_quick=3, max_quick=8):
    """Prefer the report's own column order - real report authors already
    put the most identifying fields first. Count is NOT fixed - it's
    however many identifying fields naturally exist (bounded), so a report
    with 4 obvious identifying fields shows 4, not padded/truncated to a
    round number that has nothing to do with the actual report."""
    matches = [f["label"] for f in fields
               if any(k in f["label"].lower() for k in QUICK_KEYWORDS)]
    quick = matches[:max_quick]
    if len(quick) < min_quick:
        # not enough keyword matches - fall back to the report's own lead columns
        extra = [f["label"] for f in fields if f["label"] not in quick]
        quick = (quick + extra)[:min_quick]
    return quick


# What someone searches BY first is different from what they see: an
# identifying code/number is almost always the starting point ("I have
# this one order/SKU/GRN, tell me about it"), then a date range. Things
# like "Updated Since (Hours) - Deprecated" are not a newcomer's starting
# point even though they're valid filters.
FILTER_PRIORITY_KEYWORDS = ["code", "no.", "number", "#", "id is", " id", "date range"]


def pick_quick_filters(filters, max_quick=3):
    scored = []
    for f in filters:
        label_lower = f["label"].lower()
        rank = next((i for i, k in enumerate(FILTER_PRIORITY_KEYWORDS) if k in label_lower), None)
        if rank is not None:
            scored.append((rank, f["label"]))
    scored.sort(key=lambda x: x[0])
    quick = [label for rank, label in scored[:max_quick]]
    if not quick and filters:
        quick = [filters[0]["label"]]
    return quick


def from_raw_table(table, label, field_names=None, description=None):
    if table not in TABLES:
        return None
    cols = TABLES[table]["columns"]
    if field_names:
        cols = [c for c in cols if c["name"] in field_names]
    else:
        cols = cols[:MAX_FIELDS_PER_SUBCAT]
    alias = table  # single-table block - alias with its own name, no collision risk in isolation
    fields = [{"label": humanize(c["name"]), "expr": f"{alias}.{c['name']}"} for c in cols]
    quick_labels = pick_quick_fields(fields)

    # raw tables (no matching export config) get no filters otherwise - add
    # a simple "contains" search on code/name if those columns exist, so a
    # lookup like "find the role with PII in its code" is possible without
    # dropping into Advanced mode
    all_col_names = {c["name"] for c in TABLES[table]["columns"]}
    filters = []
    for search_col in ("code", "name", "sku_code", "facility_code", "shelf_code", "batch_code", "status_code"):
        if search_col in all_col_names:
            filters.append({
                "label": f"{humanize(search_col)} contains",
                "condition": f"{alias}.{search_col} LIKE CONCAT('%', :{search_col}Contains, '%')",
                "type": "text",
            })

    return {
        "label": label,
        "description": description or make_description("", label, table, quick_labels),
        "anchor_table": table,
        "anchor_alias": alias,
        "from_join_clause": f"FROM {table} {alias}",
        "source": "raw",
        "export_name": None,
        "fields": fields,
        "quick_fields": quick_labels,
        "filters": filters,
        "quick_filters": [f["label"] for f in filters],
        "requires_scope": table in HIGH_RISK_TABLES,
    }


TAXONOMY = {
    "Orders": {
        "Sale Orders": from_export("Sale Orders"),
        "Sale Order Margins": from_export("Sale Order Margins"),
        "Back Orders": from_export("Back Orders"),
        "Hopped Orders": from_export("Hopped orders"),
        "Customer Details": from_export("Copy of Customer Report", label="Customer Details"),
        "Sales Forecast Report": from_export("Sales Forecast Report"),
        "Aggregate Sales Report": from_export("Aggregate Sales Report"),
    },
    "Inventory": {
        "Shelfwise Inventory": from_export("Shelfwise Inventory"),
        "Inventory Blocked Against Orders": from_raw_table(
            "item_type_inventory_allocation", "Inventory Blocked Against Orders", None,
            description="Find inventory reserved/blocked for specific sale orders - search by "
                        "SKU or facility to see how much is allocated, and to which order, "
                        "broken down by allocation status (ALLOCATED, ADDED_IN_PICKLIST, "
                        "PICKLIST_SCAN_COMPLETE)."),
        "Inventory Worth": from_export("Inventory Worth"),
        "Inventory Worth By Category": from_export("Inventory Worth By Category"),
        "Inventory Aging": from_export("Inventory Aging"),
        "Inventory Ledger": from_export("Inventory Ledger"),
        "Inventory Adjustment": from_export("Inventory Adjustment"),
        "Reorder Report": from_export("Reorder Report"),
        "Fast Moving SKU": from_export("Fast Moving SKU"),
        "Expiring/Expired Inventory": from_export("Batching Expired Inventory"),
    },
    "Purchasing": {
        "Purchase Orders": from_export("Purchase Orders"),
        "GRN": from_export("GRN"),
        "GRN-PO Mapping": from_export("GRN-PO Mapping"),
        "Vendors": from_export("Vendors"),
        "Vendor Item Master": from_export("Vendor Item Master"),
        "Vendor Catalog": from_export("Vendor Catalog"),
        "Unwanted Purchase Orders": from_export("Unwanted Purchase Orders"),
        "ASN": from_export("ASN report"),
    },
    "Channels": {
        "Channel Item Type Sync": from_export("Channel Item Type Report"),
        "Channel Sync Status": from_raw_table(
            "channel", "Channel Sync Status",
            {"code", "source_code", "inventory_sync_status", "catalog_sync_status",
             "order_sync_status", "pricing_sync_status", "reconciliation_sync_status", "enabled"}),
        "Channel Prices Report": from_export("Prices Report", label="Channel Prices Report"),
    },
    "Facility": {
        "Facility": from_export("Facility"),
        "Facility Alias": from_export("Facility Alias"),
        "Facility Time Slot": from_export("Facility Time Slot"),
        "Facility Allocation Rules": from_export("Facility Allocation Rules"),
        "Facility Enable": from_export("Facility Enable"),
    },
    "Catalog": {
        "Item Master": from_export("Item Master"),
        "Item Barcodes": from_export("Item Barcodes"),
        "Category": from_export("Category"),
        "Category Section Mapping": from_export("Category Section Mapping"),
        "Dropship Facility Item Master": from_export("Dropship Facility Item Master"),
        "Tax Type Configuration": from_export("Tax Type Configuration"),
    },
    "Access / User": {
        "Users": from_export("Users"),
        "Users Detailed View": from_export("Users detailed view"),
        "User Comments": from_export("User Comments"),
        "Role": from_raw_table("role", "Role", None,
            description="Look up roles and their access levels - includes PII access roles "
                        "(e.g. codes like PII_ADMIN), permission levels, and role descriptions."),
        "Access Resource": from_raw_table("access_resource", "Access Resource", None,
            description="Look up individual access resources/permissions (URLs, APIs, UI tabs) "
                        "that can be granted to a role."),
        "Role -> Access Resource Mapping": from_raw_table(
            "role_access_resource", "Role Access Resource Mapping", None,
            description="See which access resources/permissions are granted to which role - "
                        "use this to check what a role (including PII-related roles) can access."),
    },
    "Operations": {
        "Putaway": from_export("Putaway"),
        "GRN/Gatepass to Putaway": from_export("GRN/Gatepass to Putaway"),
        "Pending Putaways": from_export("Pending Putaways New"),
        "Work Order": from_raw_table("work_order", "Work Order", None),
        "Kit Composition": from_raw_table("kit_item_type", "Kit Composition", None),
        "Bundle": from_raw_table("bundle", "Bundle", None),
        "Bundle Items (Roll-up SKU)": from_raw_table("bundle_item_type", "Bundle Items", None),
        "Cycle Count Overall Data": from_export("Cycle Count Overall Data"),
        "Cycle Count Report": from_export("CYCLE_COUNT_REPORT"),
        "Cycle Count Non-Barcoded Items Report": from_export("CYCLE_COUNT_NONBARCODED_ITEMS_REPORT",
                                                               label="Cycle Count Non-Barcoded Items Report"),
        "Shelf Report": from_export("Shelf Report"),
        "Warehouse Productivity Tracker": from_export("PRODUCTIVITY TRACKER",
                                                        label="Warehouse Productivity Tracker"),
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
    "Shipping": {
        "Shipping Package": from_export("Shipping Package"),
        "Picklist": from_export("Picklist"),
        "Invoice": from_export("Invoice"),
        "Shipping Manifest": from_export("Shipping Manifest"),
        "Box / Packslip": from_export("Box Packslip Report"),
        "Gatepass": from_export("Gatepass"),
        "Inbound Gatepass": from_export("Inbound GatePass"),
    },
    "Finance / Tax": {
        "Transaction Ledger": from_export("Transaction Ledger"),
        "Facility Transaction Ledger": from_export("Facility Transaction Ledger"),
        "HSN Summary Report": from_export("HSN Summary Report"),
        "GST E-Invoice": from_export("GST Einvoice"),
        "Clear Tax Sale Report": from_export("Clear Tax Sale Report"),
        "Clear Tax Credit Note": from_export("Clear Tax Credit Note"),
        "Tally ERP9": from_export("Tally ERP9"),
        "Tally GST Report": from_export("Tally GST Report"),
        "Tally Return GST Report": from_export("Tally Return GST Report"),
    },
    "Audit / History": {
        # Hand-built, not from an export config - closes a real gap found
        # during this project: investigating "who changed/disabled X" via
        # the `notification` table (~193M rows) took several minutes and
        # multiple round trips when done ad hoc, because `identifier`/
        # `entity` aren't indexed - only (tenant_id, created) is. These
        # reports tie tenant scoping directly into the indexed join column
        # and require both Tenant Code and a date range before they can be
        # added, so the generated query is fast and correct on the first
        # try - no separate "fetch the internal id first" step needed,
        # since the join resolves codes to ids internally.
        "Shipping Provider Change History": {
            "label": "Shipping Provider Change History",
            "description": "See who enabled/disabled a shipping provider, and when. "
                           "Requires Tenant Code, Shipping Provider Code, and a date "
                           "range - notification history is a very large table, and "
                           "these three together keep the search fast and precise.",
            "anchor_table": "notification",
            "anchor_alias": "n",
            "from_join_clause": (
                "FROM tenant t\n"
                "JOIN shipping_provider sp ON sp.tenant_id = t.id\n"
                "JOIN notification n ON n.identifier = sp.id AND n.entity = 'ShippingProvider' "
                "AND n.tenant_id = t.id\n"
                "LEFT JOIN user u ON n.user_id = u.id"
            ),
            "source": "raw",
            "export_name": None,
            "fields": [
                {"label": "Shipping Provider Code", "expr": "sp.code"},
                {"label": "Field Changed", "expr": "n.field"},
                {"label": "Old Value", "expr": "n.old_value"},
                {"label": "New Value", "expr": "n.new_value"},
                {"label": "Changed At", "expr": "n.created"},
                {"label": "Changed By", "expr": "u.username"},
            ],
            "quick_fields": ["Field Changed", "Old Value", "New Value", "Changed At", "Changed By"],
            "filters": [
                {"label": "Tenant Code (required)", "condition": "t.code = :tenantCode", "type": "text"},
                {"label": "Shipping Provider Code (required)", "condition": "sp.code = :providerCode", "type": "text"},
                {"label": "Date Range (required)",
                 "condition": "n.created >= :dateStart and n.created <= :dateEnd", "type": "daterange"},
            ],
            "quick_filters": ["Tenant Code (required)", "Shipping Provider Code (required)", "Date Range (required)"],
            "requires_scope": True,
            "required_filter_labels": ["Tenant Code (required)", "Shipping Provider Code (required)",
                                        "Date Range (required)"],
        },
        "Shipping Package Status History": {
            "label": "Shipping Package Status History",
            "description": "See the status/field change history for a shipping package - "
                           "who did what, and when. Requires Tenant Code, Shipping Package "
                           "Code, and a date range for the same reason as above.",
            "anchor_table": "notification",
            "anchor_alias": "n",
            "from_join_clause": (
                "FROM tenant t\n"
                "JOIN shipping_package sp ON sp.tenant_id = t.id\n"
                "JOIN notification n ON n.identifier = sp.id AND n.entity = 'ShippingPackage' "
                "AND n.tenant_id = t.id\n"
                "LEFT JOIN user u ON n.user_id = u.id"
            ),
            "source": "raw",
            "export_name": None,
            "fields": [
                {"label": "Shipping Package Code", "expr": "sp.code"},
                {"label": "Field Changed", "expr": "n.field"},
                {"label": "Old Value", "expr": "n.old_value"},
                {"label": "New Value", "expr": "n.new_value"},
                {"label": "Changed At", "expr": "n.created"},
                {"label": "Changed By", "expr": "u.username"},
            ],
            "quick_fields": ["Field Changed", "Old Value", "New Value", "Changed At", "Changed By"],
            "filters": [
                {"label": "Tenant Code (required)", "condition": "t.code = :tenantCode", "type": "text"},
                {"label": "Shipping Package Code (required)", "condition": "sp.code = :packageCode", "type": "text"},
                {"label": "Date Range (required)",
                 "condition": "n.created >= :dateStart and n.created <= :dateEnd", "type": "daterange"},
            ],
            "quick_filters": ["Tenant Code (required)", "Shipping Package Code (required)", "Date Range (required)"],
            "requires_scope": True,
            "required_filter_labels": ["Tenant Code (required)", "Shipping Package Code (required)",
                                        "Date Range (required)"],
        },
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

# Two high-risk categories (large/wide tables) were confirmed to have no
# facility-scoping filter at all in their real export config, even though
# their own join chain already has a usable alias for it - inject one
# rather than leave a broad-search risk with no way to narrow it in the
# guided UI.
SCOPE_FILTER_PATCHES = {
    ("Inventory", "Shelfwise Inventory"): {
        "label": "Facility Name contains",
        "condition": "f.display_name LIKE CONCAT('%', :facilityContains, '%')",
        "type": "text",
    },
    ("Inventory", "Inventory Adjustment"): {
        "label": "Facility Name contains",
        "condition": "f.display_name LIKE CONCAT('%', :facilityContains, '%')",
        "type": "text",
    },
    ("Inventory", "Inventory Ledger"): {
        "label": "Facility Id equals",
        "condition": "il.facility_id = :facilityId",
        "type": "number",
    },
    ("Facility", "Facility Allocation Rules"): {
        "label": "Tenant Code contains",
        "condition": "t.code LIKE CONCAT('%', :tenantContains, '%')",
        "type": "text",
    },
    ("Access / User", "Users Detailed View"): {
        "label": "Tenant Code contains",
        "condition": "t.code LIKE CONCAT('%', :tenantContains, '%')",
        "type": "text",
    },
    ("Finance / Tax", "Transaction Ledger"): {
        "label": "Tenant Code contains",
        "condition": "t.code LIKE CONCAT('%', :tenantContains, '%')",
        "type": "text",
    },
    ("Finance / Tax", "Facility Transaction Ledger"): {
        "label": "Tenant Code contains",
        "condition": "t.code LIKE CONCAT('%', :tenantContains, '%')",
        "type": "text",
    },
}
for (maj, sub), patch in SCOPE_FILTER_PATCHES.items():
    if maj in resolved and sub in resolved[maj]:
        resolved[maj][sub]["filters"].insert(0, patch)
        resolved[maj][sub]["quick_filters"] = [patch["label"]] + resolved[maj][sub]["quick_filters"]
        resolved[maj][sub]["requires_scope"] = True
        resolved[maj][sub]["required_filter_label"] = patch["label"]

# Two categories were found (via testing) to have a Redash parameter
# (:tenantId) baked directly into their JOIN chain itself, not just in a
# filter condition - meaning the generated SQL was literally broken
# (contained an unfilled ":tenantId" with no way to fill it). These need a
# REQUIRED tenant-code filter whose value substitutes into the join chain
# itself, not just the WHERE clause - handled specially in app.py via the
# "resolves_join_token" marker.
JOIN_TOKEN_PATCHES = {
    ("Orders", "Sales Forecast Report"): "tenantId",
    ("Shipping", "Picklist"): "tenantId",
}
for (maj, sub), token in JOIN_TOKEN_PATCHES.items():
    if maj in resolved and sub in resolved[maj]:
        resolved[maj][sub]["filters"].insert(0, {
            "label": "Tenant Code (required)",
            "condition": None,
            "type": "text",
            "resolves_join_token": token,
        })
        resolved[maj][sub]["quick_filters"] = ["Tenant Code (required)"] + resolved[maj][sub]["quick_filters"]
        resolved[maj][sub]["requires_scope"] = True
        resolved[maj][sub]["required_filter_labels"] = ["Tenant Code (required)"]

output = {"categories": resolved}

json.dump(output, open("redash_query_builder/data/taxonomy.json", "w"), indent=1)
import os
print("taxonomy.json size KB:", os.path.getsize("redash_query_builder/data/taxonomy.json") / 1024)
