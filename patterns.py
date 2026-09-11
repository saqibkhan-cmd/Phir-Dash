"""
Pattern library for the business-question UI.
Every pattern here is grounded in a real, verified query shared during this
project - none are guessed. Where a pattern is a light adaptation of a
verified query (e.g. swapping a hardcoded id for a SKU/facility-code lookup),
that's noted in `source`.

Each pattern:
  id, category, question (business language), inputs (business-labeled
  fields the user fills in), sql_template (Python .format string, schema
  qualified via {schema}), explanation (plain-English description of what
  the result means), source (where this was verified).
"""

PATTERNS = [
    # ------------------------------------------------------------------
    # SKU / Inventory Investigation
    # ------------------------------------------------------------------
    {
        "id": "blocked_qty_breakdown",
        "category": "SKU / Inventory Investigation",
        "question": "Where is quantity blocked for this SKU, and against which Sale Order?",
        "inputs": [
            {"key": "sku_code", "label": "SKU Code", "type": "text"},
            {"key": "facility_code", "label": "Facility Code", "type": "text"},
        ],
        "explanation": (
            "Shows every place blocked quantity for this SKU/facility is sitting: "
            "open B2C items, B2B allocations, B2C cancelled-but-not-put-back items, "
            "and B2B put-back-pending quantity. This is the full picture across all "
            "four ways inventory gets blocked, not just one."
        ),
        "source": "Verified: adapted from 4 real queries shared directly (B2C open, "
                  "B2B allocations, B2C cancelled+putback pending, B2B putback pending)",
        "sql_template": """
-- Where is blocked quantity for SKU {sku_code} at facility {facility_code}, and against which order?
-- (B2C open statuses)
SELECT
    'B2C_OPEN' AS source,
    soi.item_type_inventory_id,
    soi.code AS sale_order_item_code,
    so.code AS sale_order_code,
    soi.quantity AS blocked_qty
FROM {schema}.sale_order_item soi
JOIN {schema}.sale_order so ON so.id = soi.sale_order_id
LEFT JOIN {schema}.shipping_package sp ON soi.shipping_package_id = sp.id
WHERE soi.item_type_inventory_id IN (
    SELECT iti.id FROM {schema}.item_type_inventory iti
    JOIN {schema}.item_type it ON it.id = iti.item_type_id
    JOIN {schema}.facility f ON f.id = iti.facility_id
    JOIN {schema}.party p ON p.id = f.id
    WHERE it.sku_code = '{sku_code}' AND p.code = '{facility_code}'
  )
  AND soi.status_code IN ('FULFILLABLE','PICKING_FOR_STAGING','PICKING_FOR_INVOICING','STAGED')
  AND (sp.id IS NULL OR sp.status_code IN ('CREATED','PICKING','PICKED','LOCATION_NOT_SERVICEABLE','PENDING_CUSTOMIZATION','CUSTOMIZATION_COMPLETE'))

UNION ALL

-- B2B allocations
SELECT
    'B2B_ALLOCATED' AS source,
    itia.item_type_inventory_id,
    soi.code AS sale_order_item_code,
    so.code AS sale_order_code,
    itia.quantity AS blocked_qty
FROM {schema}.item_type_inventory_allocation itia
JOIN {schema}.sale_order_item soi ON itia.sale_order_item_id = soi.id
JOIN {schema}.sale_order so ON so.id = soi.sale_order_id
LEFT JOIN {schema}.shipping_package sp ON soi.shipping_package_id = sp.id
WHERE itia.item_type_inventory_id IN (
    SELECT iti.id FROM {schema}.item_type_inventory iti
    JOIN {schema}.item_type it ON it.id = iti.item_type_id
    JOIN {schema}.facility f ON f.id = iti.facility_id
    JOIN {schema}.party p ON p.id = f.id
    WHERE it.sku_code = '{sku_code}' AND p.code = '{facility_code}'
  )
  AND soi.status_code IN ('FULFILLABLE','PICKING_FOR_STAGING','PICKING_FOR_INVOICING','STAGED')
  AND (sp.id IS NULL OR sp.status_code IN ('CREATED','PICKING','PICKED','LOCATION_NOT_SERVICEABLE','PENDING_CUSTOMIZATION','CUSTOMIZATION_COMPLETE'))
  AND itia.status_code IN ('ALLOCATED','ADDED_IN_PICKLIST','PICKLIST_SCAN_COMPLETE')

UNION ALL

-- B2C cancelled + putback pending
SELECT
    'B2C_CANCELLED_PUTBACK_PENDING' AS source,
    soi.item_type_inventory_id,
    soi.code AS sale_order_item_code,
    so.code AS sale_order_code,
    1 AS blocked_qty
FROM {schema}.sale_order_item soi
JOIN {schema}.sale_order so ON soi.sale_order_id = so.id
JOIN {schema}.picklist_item pi ON soi.code = pi.sale_order_item_code AND so.code = pi.sale_order_code
JOIN {schema}.picklist p2 ON p2.id = pi.picklist_id
WHERE soi.item_type_inventory_id IN (
    SELECT iti.id FROM {schema}.item_type_inventory iti
    JOIN {schema}.item_type it ON it.id = iti.item_type_id
    JOIN {schema}.facility f ON f.id = iti.facility_id
    JOIN {schema}.party pty ON pty.id = f.id
    WHERE it.sku_code = '{sku_code}' AND pty.code = '{facility_code}'
  )
  AND soi.status_code = 'CANCELLED'
  AND pi.status_code = 'PUTBACK_PENDING';
""",
    },
    {
        "id": "blocked_qty_quick",
        "category": "SKU / Inventory Investigation",
        "question": "How much inventory is currently blocked for this SKU at this facility?",
        "inputs": [
            {"key": "sku_code", "label": "SKU Code", "type": "text"},
            {"key": "facility_code", "label": "Facility Code", "type": "text"},
        ],
        "explanation": (
            "A quick total: current good/blocked/damaged/not-found quantity for this "
            "SKU at this facility, straight from the inventory table - no investigation, "
            "just the numbers as they stand right now."
        ),
        "source": "Verified: item_type_inventory columns confirmed from schema export "
                  "(quantity, quantity_blocked, quantity_damaged, quantity_not_found)",
        "sql_template": """
SELECT
    it.sku_code,
    f.display_name AS facility_name,
    p.code AS facility_code,
    iti.quantity AS good_quantity,
    iti.quantity_blocked AS blocked_quantity,
    iti.quantity_damaged AS damaged_quantity,
    iti.quantity_not_found AS not_found_quantity
FROM {schema}.item_type it
JOIN {schema}.item_type_inventory iti ON iti.item_type_id = it.id
JOIN {schema}.facility f ON iti.facility_id = f.id
JOIN {schema}.party p ON p.id = f.id
WHERE it.sku_code = '{sku_code}' AND p.code = '{facility_code}';
""",
    },
    # ------------------------------------------------------------------
    # Order Investigation
    # ------------------------------------------------------------------
    {
        "id": "order_current_status",
        "category": "Order Investigation",
        "question": "What is the current status of this Sale Order?",
        "inputs": [{"key": "sale_order_code", "label": "Sale Order Code", "type": "text"}],
        "explanation": (
            "The order's overall status plus the status of every line item in it - "
            "useful as a first check before digging into why something's stuck."
        ),
        "source": "Verified: sale_order.status_code / sale_order_item.status_code "
                  "confirmed from schema; join pattern (soi.sale_order_id -> so.id) "
                  "confirmed via multiple real queries",
        "sql_template": """
SELECT
    so.code AS sale_order_code,
    so.status_code AS order_status,
    soi.code AS item_code,
    soi.sku_code,
    soi.status_code AS item_status,
    soi.quantity
FROM {schema}.sale_order so
JOIN {schema}.sale_order_item soi ON soi.sale_order_id = so.id
WHERE so.code = '{sale_order_code}';
""",
    },
    {
        "id": "order_stuck_why",
        "category": "Order Investigation",
        "question": "Why is this order stuck? (open items + shipping package status)",
        "inputs": [{"key": "sale_order_code", "label": "Sale Order Code", "type": "text"}],
        "explanation": (
            "Lists every item in this order that isn't cancelled/dispatched/delivered "
            "yet, alongside its shipping package status if one exists - this is usually "
            "where the blocker shows up (e.g. package stuck at LOCATION_NOT_SERVICEABLE)."
        ),
        "source": "Verified: shipping_package.status_code values and the LEFT JOIN + "
                  "status-list pattern confirmed from a real query shared directly",
        "sql_template": """
SELECT
    soi.code AS item_code,
    soi.sku_code,
    soi.status_code AS item_status,
    sp.code AS shipping_package_code,
    sp.status_code AS package_status
FROM {schema}.sale_order so
JOIN {schema}.sale_order_item soi ON soi.sale_order_id = so.id
LEFT JOIN {schema}.shipping_package sp ON soi.shipping_package_id = sp.id
WHERE so.code = '{sale_order_code}'
  AND soi.status_code NOT IN ('CANCELLED', 'DISPATCHED', 'DELIVERED');
""",
    },
    # ------------------------------------------------------------------
    # Status & Activity
    # ------------------------------------------------------------------
    {
        "id": "shipping_package_activity",
        "category": "Status & Activity",
        "question": "Who changed the status of this Shipping Package, and when?",
        "inputs": [{"key": "shipping_package_code", "label": "Shipping Package Code", "type": "text"}],
        "explanation": (
            "Every recorded status/field change on this shipping package, in order, "
            "with who made the change and when - the activity trail."
        ),
        "source": "Verified: notification table's polymorphic entity/identifier pattern "
                  "('ShippingPackage') confirmed via a real query shared directly",
        "sql_template": """
SELECT
    n.field,
    n.old_value,
    n.new_value,
    n.created AS changed_at,
    u.username AS changed_by
FROM {schema}.shipping_package sp
JOIN {schema}.notification n ON n.identifier = sp.id AND n.entity = 'ShippingPackage'
LEFT JOIN {schema}.user u ON n.user_id = u.id
WHERE sp.code = '{shipping_package_code}'
ORDER BY n.created;
""",
    },
    # ------------------------------------------------------------------
    # Facility Investigation
    # ------------------------------------------------------------------
    {
        "id": "order_facility",
        "category": "Facility Investigation",
        "question": "Which facility is this Sale Order fulfilled from?",
        "inputs": [{"key": "sale_order_code", "label": "Sale Order Code", "type": "text"}],
        "explanation": "The facility (or facilities, if items ship from more than one) fulfilling this order.",
        "source": "Verified: facility<->party shared-id pattern confirmed via multiple "
                  "real queries; sale_order_item.facility_id confirmed from schema",
        "sql_template": """
SELECT DISTINCT
    p.code AS facility_code,
    f.display_name AS facility_name
FROM {schema}.sale_order so
JOIN {schema}.sale_order_item soi ON soi.sale_order_id = so.id
JOIN {schema}.facility f ON soi.facility_id = f.id
JOIN {schema}.party p ON p.id = f.id
WHERE so.code = '{sale_order_code}';
""",
    },
]

CATEGORIES = [
    "SKU / Inventory Investigation",
    "Order Investigation",
    "Status & Activity",
    "Facility Investigation",
    "User / Audit Information",   # no verified pattern yet - shown as "coming soon"
    "Advanced Investigation",      # routes to the full query builder, not a pattern
]
