import csv, json, re
from collections import defaultdict

SRC = '/mnt/user-data/uploads/New_Query_2026_09_07__1_.csv'
with open(SRC) as f:
    rows = list(csv.DictReader(f))

tables = defaultdict(list)
for row in rows:
    tables[row['TABLE_NAME']].append(row)

table_names_lower = {t.lower(): t for t in tables}

SYNONYMS = {
    'party': 'party', 'shipping_address':'address_detail', 'billing_address':'address_detail',
    'address':'address_detail',
}
PREFIXES = ['parent_','from_','to_','default_','associated_','source_','dispatch_','return_',
            'prev_','cur_','current_','writeoff_','approved_by_','created_by_','updated_by_',
            'count_task_','request_','uc_','qr_code_','channel_product_','snapshot_reference_',
            'catalog_sync_','client_','package_type_','priority_','shipping_','billing_','old_','new_',
            'altered_','credit_','delivery_challan_']

def strip_role_words(stem):
    variants = {stem}
    cur = stem
    changed = True
    while changed:
        changed = False
        for p in PREFIXES:
            if cur.startswith(p) and cur != p.rstrip('_'):
                cur2 = cur[len(p):]
                if cur2 and cur2 not in variants:
                    variants.add(cur2); cur = cur2; changed = True; break
    return variants

def candidate_names(stem):
    cands = [stem]
    if stem in SYNONYMS:
        cands.append(SYNONYMS[stem])
    if stem.endswith('ies'):
        cands.append(stem[:-3]+'y')
    if stem.endswith('s') and not stem.endswith('ss'):
        cands.append(stem[:-1])
    return list(dict.fromkeys(cands))

relationships = []
ambiguous = []

# manual overrides learned from review (fixes known-bad fuzzy matches / confirmed via real queries)
MANUAL_OVERRIDES = {
    ('sale_order_item','altered_sale_order_item_id'): 'sale_order_item',
    ('deleted_sale_order_item','parent_sale_order_item_id'): 'sale_order_item',
    ('sale_order_item','parent_sale_order_item_id'): 'sale_order_item',
    ('sale_order_item','return_invoice_item_id'): 'invoice_item',
    ('return_item','return_invoice_item_id'): 'invoice_item',
    ('outbound_gate_pass_item','return_invoice_item_id'): 'invoice_item',
    ('vendor_invoice','credit_vendor_invoice_id'): 'vendor_invoice',
    # verified from real Redash query (name match is misleading - this is NOT `picklist`)
    ('picklist_item_detail','picklist_id'): 'picklist_detail',
    # verified from real Redash query (kit_item_type.component_sku_id -> item_type,
    # describing a kit's component SKU, not a table named "component_sku")
    ('kit_item_type','component_sku_id'): 'item_type',
}

for table, cols in tables.items():
    for c in cols:
        col = c['COLUMN_NAME']
        if col == 'id':
            continue
        m = re.match(r'^(.*)_id$', col, re.IGNORECASE)
        if not m:
            continue
        stem = m.group(1).lower()
        if not stem:
            continue

        key = (table, col)
        if key in MANUAL_OVERRIDES:
            target = MANUAL_OVERRIDES[key]
            relationships.append({'from_table':table,'from_column':col,'to_table':target,
                                   'to_column':'id','confidence':'verified','method':'manual'})
            continue

        found = list(dict.fromkeys([table_names_lower[c2] for c2 in candidate_names(stem) if c2 in table_names_lower]))
        if len(found) == 1:
            relationships.append({'from_table':table,'from_column':col,'to_table':found[0],
                                   'to_column':'id','confidence':'high','method':'direct'})
            continue
        elif len(found) > 1:
            ambiguous.append({'from_table':table,'from_column':col,'candidates':found,'reason':'multiple direct matches'})
            continue

        variants = strip_role_words(stem)
        fuzzy = set()
        for v in variants:
            for c2 in candidate_names(v):
                if c2 in table_names_lower:
                    fuzzy.add(table_names_lower[c2])
        fuzzy = list(fuzzy)
        if len(fuzzy) == 1:
            relationships.append({'from_table':table,'from_column':col,'to_table':fuzzy[0],
                                   'to_column':'id','confidence':'low','method':'fuzzy-role-strip'})
        elif len(fuzzy) > 1:
            ambiguous.append({'from_table':table,'from_column':col,'candidates':fuzzy,'reason':'multiple fuzzy matches'})
        else:
            ambiguous.append({'from_table':table,'from_column':col,'candidates':[],'reason':f'no matching table for stem "{stem}"'})

# build table metadata dict
table_meta = {}
for t, cols in tables.items():
    table_meta[t] = {
        'columns': [
            {
                'name': c['COLUMN_NAME'],
                'type': c['COLUMN_TYPE'],
                'nullable': c['IS_NULLABLE'] == 'YES',
                'key': c['COLUMN_KEY'],
                'default': c['COLUMN_DEFAULT'],
            } for c in cols
        ],
        'primary_key': [c['COLUMN_NAME'] for c in cols if c['COLUMN_KEY']=='PRI'],
    }

# ---------------------------------------------------------------------------
# Pass 2: code-based joins. Confirmed to exist in real Redash queries
# alongside _id joins (e.g. picklist_item.sale_order_item_code -> sale_order_item.code).
# Same naming heuristic as the _id pass, but matches `<stem>_code` columns to
# a `code` column on the candidate table. Tagged with join_column='code' so
# the app knows this isn't a plain id equality.
# ---------------------------------------------------------------------------
tables_with_code_col = {t for t, meta in table_meta.items()
                         if any(c['name'] == 'code' for c in meta['columns'])}

code_relationships = []
for table, cols in tables.items():
    for c in cols:
        col = c['COLUMN_NAME']
        if col in ('code',):
            continue
        m = re.match(r'^(.*)_code$', col, re.IGNORECASE)
        if not m:
            continue
        stem = m.group(1).lower()
        if not stem:
            continue
        found = list(dict.fromkeys([table_names_lower[c2] for c2 in candidate_names(stem)
                                     if c2 in table_names_lower and table_names_lower[c2] in tables_with_code_col]))
        if len(found) == 1 and found[0] != table:
            code_relationships.append({'from_table': table, 'from_column': col,
                                        'to_table': found[0], 'to_column': 'code',
                                        'confidence': 'high', 'method': 'code-direct'})

# manually verified from your sample query (not auto-inferred - "sale_order_item_code"
# and "sale_order_code" both live on picklist_item, confirming the pattern above)
CODE_OVERRIDES_VERIFIED = {
    ('picklist_item', 'sale_order_item_code'): ('sale_order_item', 'code'),
    ('picklist_item', 'sale_order_code'): ('sale_order', 'code'),
}
for (t, c), (tt, tc) in CODE_OVERRIDES_VERIFIED.items():
    for r in code_relationships:
        if r['from_table'] == t and r['from_column'] == c:
            r['confidence'] = 'verified'
            r['method'] = 'manual (confirmed via sample query)'

relationships.extend(code_relationships)
print(f"Code-based relationships inferred: {len(code_relationships)}")

# ---------------------------------------------------------------------------
# Composite / multi-column relationships: joins with no single `_id` or
# `_code` column, discovered from real queries only. Not auto-inferable.
# ---------------------------------------------------------------------------
composite_relationships = [
    {
        'tables': ['item_type_inventory', 'picklist_item_detail'],
        'conditions': [
            {'left_col': 'item_type_id', 'op': '=', 'right_col': 'item_type_id'},
            {'left_col': 'shelf_id', 'op': '=', 'right_col': 'shelf_id'},
            {'left_col': 'batch_id', 'op': '<=>', 'right_col': 'batch_id'},
        ],
        'confidence': 'verified',
        'method': 'manual (confirmed via sample query)',
        'note': 'batch_id uses MySQL null-safe equals (<=>) since batch_id can be NULL',
    },
]

# ---------------------------------------------------------------------------
# Shared-PK / subtype relationships: confirmed from real queries that
# `facility`/`vendor` (and by the same pattern, `customer`) reuse `party.id`
# as their own id, rather than holding a separate `party_id` column. Modeled
# as ordinary id->id relationships so the existing join engine handles them
# with no special-casing.
# ---------------------------------------------------------------------------
SHARED_PK_RELATIONSHIPS = [
    {'from_table': 'facility', 'confidence': 'verified',
     'note': 'confirmed via real query: JOIN party p ON p.id = f.id'},
    {'from_table': 'vendor', 'confidence': 'verified',
     'note': 'confirmed via real query: JOIN party p ON v.id = p.id'},
    {'from_table': 'customer', 'confidence': 'low',
     'note': 'inferred from the same pattern as facility/vendor - not yet confirmed by a real query'},
]
for spk in SHARED_PK_RELATIONSHIPS:
    if spk['from_table'] in tables:
        relationships.append({
            'from_table': spk['from_table'], 'from_column': 'id',
            'to_table': 'party', 'to_column': 'id',
            'confidence': spk['confidence'], 'method': 'shared_pk_inheritance',
            'note': spk['note'],
        })

# ---------------------------------------------------------------------------
# Polymorphic relationships: a (discriminator_column, id_column) pair where
# the target table is decided at query time by the discriminator's value,
# not by column naming. NOT added to `relationships` (target isn't fixed) -
# tracked separately so the app can surface them distinctly instead of
# dumping them in "ambiguous".
# ---------------------------------------------------------------------------
polymorphic_relationships = [
    {'table': 'notification', 'discriminator_column': 'entity', 'id_column': 'identifier',
     'naming_style': 'short_class_name', 'confidence': 'verified',
     'example': "entity='ShippingPackage' -> identifier references shipping_package.id"},
    {'table': 'custom_field_value', 'discriminator_column': 'entity', 'id_column': 'identifier',
     'naming_style': 'full_java_class', 'confidence': 'verified',
     'example': "entity='com.uniware.core.entity.InflowReceiptItem' -> identifier references inflow_receipt_item.id"},
    {'table': 'entity_source_reference', 'discriminator_column': 'entity', 'id_column': 'entity_identifier',
     'naming_style': 'short_class_name', 'confidence': 'verified',
     'example': "entity='ShippingPackage' -> entity_identifier references shipping_package.id"},
    {'table': 'handling_unit_metadata', 'discriminator_column': 'entity_type', 'id_column': 'entity_id',
     'naming_style': 'unknown', 'confidence': 'inferred',
     'example': 'same shape as notification/entity_source_reference, not directly confirmed by a query yet'},
    {'table': 'shelf_activity_audit', 'discriminator_column': 'entity_type', 'id_column': 'entity_id',
     'naming_style': 'unknown', 'confidence': 'inferred',
     'example': 'same shape as notification/entity_source_reference, not directly confirmed by a query yet'},
    {'table': 'iti_snapshot_debugging', 'discriminator_column': 'entity_name', 'id_column': 'entity_id',
     'naming_style': 'unknown', 'confidence': 'inferred',
     'example': 'same shape as notification/entity_source_reference, not directly confirmed by a query yet'},
    {'table': 'box_item_metadata', 'discriminator_column': None, 'id_column': 'entity_id',
     'naming_style': 'unknown', 'confidence': 'unresolved',
     'example': 'entity_id present but no discriminator column found in this table'},
    {'table': 'picklist_detail_metadata', 'discriminator_column': None, 'id_column': 'entity_id',
     'naming_style': 'unknown', 'confidence': 'unresolved',
     'example': 'entity_id present but no discriminator column found in this table (has a `type` column that may serve this role)'},
]

# these columns are now explained (not just "unresolved") - drop them from
# the plain ambiguous list so the app doesn't show them twice
poly_keys = {(p['table'], p['id_column']) for p in polymorphic_relationships if p['id_column']}
ambiguous = [a for a in ambiguous if (a['from_table'], a['from_column']) not in poly_keys]

out = {
    'tables': table_meta,
    'relationships': relationships,
    'composite_relationships': composite_relationships,
    'polymorphic_relationships': polymorphic_relationships,
    'ambiguous': ambiguous,
    'starter_tables': ['sale_order', 'sale_order_item', 'item', 'item_type'],
}

with open('redash_query_builder/data/schema_data.json','w') as f:
    json.dump(out, f, indent=1)

print('tables:', len(table_meta))
print('relationships:', len(relationships))
print('ambiguous:', len(ambiguous))
import os
print('file size KB:', os.path.getsize('redash_query_builder/data/schema_data.json')/1024)
