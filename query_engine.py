"""
Core SQL-assembly engine for the unified app. Framework-agnostic (no
Streamlit imports) so it can be tested and reasoned about on its own.

Model: a "block" is one category selection (e.g. "Sale Order > Sale Orders"
with some fields/filters checked). Each block carries its own verbatim,
already-correct FROM/JOIN chain (from a real export config, or a single
table for raw/advanced picks) with its OWN table aliases untouched.

To combine multiple blocks into one query, each block is wrapped as an
independent derived-table subquery (so its internal aliases can never
collide with another block's), and blocks are stitched together at the
*anchor-table* level using the verified relationship graph - the same
engine the advanced table-by-table builder uses. If no verified
relationship connects two blocks' anchor tables, they are NOT forced
together (that would risk a baseless/fan-out query) - they're returned as
separate, independently-correct queries instead.
"""
import html
import re


def unescape(s):
    return html.unescape(s) if isinstance(s, str) else s


def qualify_from_join_clause(clause, schema):
    """Schema-qualify every table name immediately after FROM/JOIN in a
    verbatim FROM..JOIN clause, without touching aliases or ON conditions."""
    return re.sub(
        r'\b(FROM|JOIN)\s+`?(\w+)`?',
        lambda m: f"{m.group(1)} {schema}.{m.group(2)}",
        clause,
        flags=re.IGNORECASE,
    )


CONFIDENCE_RANK = {"verified": 0, "high": 1, "low": 2}


def find_relationship(table_a, table_b, relationships):
    candidates = []
    for r in relationships:
        if r["from_table"] == table_a and r["to_table"] == table_b:
            candidates.append((r, "a_to_b"))
        elif r["from_table"] == table_b and r["to_table"] == table_a:
            candidates.append((r, "b_to_a"))
    if not candidates:
        return None, None
    candidates.sort(key=lambda x: CONFIDENCE_RANK.get(x[0]["confidence"], 9))
    return candidates[0]


def find_composite(table_a, table_b, composite_relationships):
    for cr in composite_relationships:
        if set(cr["tables"]) == {table_a, table_b}:
            return cr
    return None


def substitute_filter_tokens(condition, token_values):
    """Replace :tokenName placeholders in a filter condition with quoted
    literal values. token_values: {token_name: sql_literal_string}."""
    def repl(m):
        token = m.group(1)
        return token_values.get(token, m.group(0))
    return re.sub(r':(\w+)', repl, condition)


def extract_tokens(condition):
    return re.findall(r':(\w+)', condition)


def build_block_subquery(block, schema, alias, extra_hidden_cols=None):
    """
    block: {anchor_table, anchor_alias, from_join_clause, fields:[{label,expr}],
            selected_filters:[{condition_filled}]}
    Returns (subquery_sql, output_field_labels) where output_field_labels
    maps each selected field's label to its column name inside the subquery.
    """
    from_join = qualify_from_join_clause(unescape(block["from_join_clause"]), schema)

    select_parts = []
    output_labels = {}
    for i, f in enumerate(block["fields"]):
        col_alias = f"f{i}"
        select_parts.append(f"{unescape(f['expr'])} AS {col_alias}")
        output_labels[f["label"]] = col_alias

    for i, col in enumerate(extra_hidden_cols or []):
        select_parts.append(f"{col} AS __join_{i}")

    where_parts = [unescape(w) for w in block.get("filled_filter_conditions", [])]

    sql = "SELECT\n    " + ",\n    ".join(select_parts) + "\n" + from_join
    if where_parts:
        sql += "\nWHERE " + "\n  AND ".join(where_parts)

    return f"(\n{sql}\n) AS {alias}", output_labels


def build_combined_query(blocks, schema, relationships, composite_relationships):
    """
    blocks: list of block dicts (see build_block_subquery).
    Returns: {
      'joined_sql': str or None,   # single combined query, if all blocks link up
      'unlinked_blocks': [...],     # blocks that couldn't be linked (standalone SQL each)
      'standalone_sqls': [(label, sql), ...],  # used when joined_sql is None
      'link_notes': [str, ...],     # what was linked on, for transparency
    }
    """
    if len(blocks) == 1:
        sq, labels = build_block_subquery(blocks[0], schema, "b0")
        # for a single block, just select straight from its own from_join (no need to nest)
        from_join = qualify_from_join_clause(unescape(blocks[0]["from_join_clause"]), schema)
        select_parts = [f"{unescape(f['expr'])} AS `{f['label']}`" for f in blocks[0]["fields"]]
        where_parts = [unescape(w) for w in blocks[0].get("filled_filter_conditions", [])]
        sql = "SELECT\n    " + ",\n    ".join(select_parts) + "\n" + from_join
        if where_parts:
            sql += "\nWHERE " + "\n  AND ".join(where_parts)
        sql += "\nLIMIT 500;"
        return {"joined_sql": sql, "unlinked_blocks": [], "standalone_sqls": [], "link_notes": []}

    # find a link for each consecutive pair (chain: block0-block1-block2-...)
    links = []  # (from_col, to_col, confidence, note) per consecutive pair, or None
    for i in range(len(blocks) - 1):
        a, b = blocks[i]["anchor_table"], blocks[i + 1]["anchor_table"]
        rel, direction = find_relationship(a, b, relationships)
        if rel:
            if direction == "a_to_b":
                links.append({"left_idx": i, "right_idx": i + 1,
                              "left_col": rel["from_column"], "right_col": rel["to_column"],
                              "confidence": rel["confidence"], "composite": False})
            else:
                links.append({"left_idx": i, "right_idx": i + 1,
                              "left_col": rel["to_column"], "right_col": rel["from_column"],
                              "confidence": rel["confidence"], "composite": False})
            continue
        cr = find_composite(a, b, composite_relationships)
        if cr:
            links.append({"left_idx": i, "right_idx": i + 1, "conditions": cr["conditions"],
                          "confidence": cr["confidence"], "composite": True})
            continue
        links.append(None)  # no verified link between these two blocks

    if any(l is None for l in links):
        # can't safely join everything - return each block as its own query
        standalone = []
        for blk in blocks:
            r = build_combined_query([blk], schema, relationships, composite_relationships)
            standalone.append((blk.get("label", blk["anchor_table"]), r["joined_sql"]))
        unlinked_pairs = [(blocks[i]["label"], blocks[i+1]["label"])
                           for i, l in enumerate(links) if l is None]
        return {"joined_sql": None, "unlinked_blocks": unlinked_pairs,
                "standalone_sqls": standalone, "link_notes": []}

    # build a subquery per block, exposing any columns needed for links
    hidden_cols_per_block = {i: [] for i in range(len(blocks))}
    for l in links:
        if l["composite"]:
            for c in l["conditions"]:
                hidden_cols_per_block[l["left_idx"]].append(
                    f"{blocks[l['left_idx']]['anchor_alias']}.{c['left_col']}")
                hidden_cols_per_block[l["right_idx"]].append(
                    f"{blocks[l['right_idx']]['anchor_alias']}.{c['right_col']}")
        else:
            hidden_cols_per_block[l["left_idx"]].append(
                f"{blocks[l['left_idx']]['anchor_alias']}.{l['left_col']}")
            hidden_cols_per_block[l["right_idx"]].append(
                f"{blocks[l['right_idx']]['anchor_alias']}.{l['right_col']}")

    subqueries, all_labels, link_notes = [], [], []
    for i, blk in enumerate(blocks):
        alias = f"b{i}"
        sq, labels = build_block_subquery(blk, schema, alias, hidden_cols_per_block[i])
        subqueries.append((alias, sq))
        all_labels.append((alias, blk.get("label", blk["anchor_table"]), labels))

    select_parts = []
    for alias, label, labels in all_labels:
        for field_label, col in labels.items():
            select_parts.append(f"{alias}.{col} AS `{label}: {field_label}`")

    sql_lines = ["SELECT", "    " + ",\n    ".join(select_parts), f"FROM {subqueries[0][1]}"]
    hidden_idx_counter = {i: 0 for i in range(len(blocks))}
    for l in links:
        li, ri = l["left_idx"], l["right_idx"]
        ralias, rsq = subqueries[ri]
        lalias = subqueries[li][0]
        if l["composite"]:
            on_parts = []
            for _ in l["conditions"]:
                lh = f"__join_{hidden_idx_counter[li]}"; hidden_idx_counter[li] += 1
                rh = f"__join_{hidden_idx_counter[ri]}"; hidden_idx_counter[ri] += 1
                on_parts.append(f"{lalias}.{lh} = {ralias}.{rh}")
            sql_lines.append(f"JOIN {rsq} ON " + " AND ".join(on_parts))
            link_notes.append(f"{blocks[li]['label']} <-> {blocks[ri]['label']}: composite join "
                              f"({l['confidence']})")
        else:
            lh = f"__join_{hidden_idx_counter[li]}"; hidden_idx_counter[li] += 1
            rh = f"__join_{hidden_idx_counter[ri]}"; hidden_idx_counter[ri] += 1
            sql_lines.append(f"JOIN {rsq} ON {lalias}.{lh} = {ralias}.{rh}")
            link_notes.append(f"{blocks[li]['label']} <-> {blocks[ri]['label']}: "
                              f"{l['left_col']} = {l['right_col']} ({l['confidence']})")

    sql_lines.append("LIMIT 500;")
    return {"joined_sql": "\n".join(sql_lines), "unlinked_blocks": [],
            "standalone_sqls": [], "link_notes": link_notes}
