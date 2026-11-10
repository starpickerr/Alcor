"""Stable category-link checks shared by calendar and reminder sync."""

DIRECTIONS = frozenset({"import", "export", "bidirectional"})


def bidirectional_links(selections, sources, categories, *, unavailable_ids=(), existing_links=()):
    if not isinstance(selections, list) or not selections:
        raise ValueError("请至少选择一个要同步的项目")
    available = {row["id"]: row for row in sources}
    local = {row["id"]: row for row in categories}
    previous = {(row["apple_id"],row["alcor_id"]):row for row in existing_links}
    seen = set()
    used = set()
    for row in selections:
        if not isinstance(row, dict) or set(row) - {"apple_id", "alcor_id"}:
            raise ValueError("同步项目格式无效")
        identity = row.get("apple_id")
        if not isinstance(identity, str) or identity in seen or identity not in available and not (previous.get((identity,row.get('alcor_id')),{}).get('enabled') and row.get('alcor_id') in local):
            raise ValueError("同步项目重复或已不可用，请刷新后重新选择")
        old = previous.get((identity,row.get("alcor_id")), {})
        if identity in available and not available[identity].get("writable") and old.get("direction") != "import":
            raise ValueError("双向同步需要可写的日历或清单")
        target = row.get("alcor_id")
        if target is not None and not isinstance(target, str):
            raise ValueError("分类或清单格式无效")
        if target and (target not in local or target in used):
            raise ValueError("请选择不同的有效分类或清单")
        seen.add(identity)
        if target:
            used.add(target)
    names = {row["name"] for row in categories}
    links = []
    for selection in selections:
        target = selection.get("alcor_id") or ""
        old = previous.get((selection["apple_id"], target), {})
        title = str(available.get(selection["apple_id"], {}).get("title") or old.get("apple_name") or "Apple")[:200].strip() or "Apple"
        if not target:
            existing = next((row for row in categories if row["name"] == title and row["id"] not in used and row["id"] not in unavailable_ids), None)
            if existing:
                target = existing["id"]
                used.add(target)
        name = title
        number = 2
        while not target and name in names:
            suffix = f" ({number})"
            name = title[:200 - len(suffix)] + suffix
            number += 1
        names.add(name)
        links.append({"apple_id": selection["apple_id"], "alcor_id": target, "create_name": name,
                      "direction": previous.get((selection["apple_id"],target), {}).get("direction", "bidirectional"), "enabled": True})
    return links


def reconcile_names(stored_apple, stored_alcor, apple_now, alcor_now):
    """Decide a name change from the last synced pair, not from content direction.

    Returns ``(action, name)``. ``name`` is the string the other side should
    adopt, or ``None`` when the two sides disagree.
    """
    apple_changed = apple_now != stored_apple
    alcor_changed = alcor_now != stored_alcor
    if apple_changed and alcor_changed:
        if apple_now == alcor_now:
            return "aligned", apple_now
        return "conflict", None
    if apple_changed:
        return "apple_to_alcor", apple_now
    if alcor_changed:
        return "alcor_to_apple", alcor_now
    return "unchanged", alcor_now


def validate_links(rows):
    """Reject links that are not a one-to-one enabled mapping."""
    seen_apple = set()
    seen_alcor = set()
    for row in rows:
        apple_id = str(row.get("apple_id") or "").strip()
        alcor_id = str(row.get("alcor_id") or "").strip()
        direction = str(row.get("direction") or "").strip()
        if not apple_id:
            raise ValueError("apple_id is required")
        if not alcor_id:
            raise ValueError("alcor_id is required")
        if direction not in DIRECTIONS:
            raise ValueError(f"direction must be one of {', '.join(sorted(DIRECTIONS))}")
        if not isinstance(row.get("enabled"), bool):
            raise ValueError("enabled must be a boolean")
        if apple_id in seen_apple or alcor_id in seen_alcor:
            raise ValueError("category links must be one-to-one")
        seen_apple.add(apple_id)
        seen_alcor.add(alcor_id)


def all_source_links(sources, categories, stored, *, unavailable_ids=()):
    """Keep stable mappings while discovering new calendars and reminder lists."""
    previous = {row["apple_id"]: row for row in stored}
    sources = [row for row in sources if previous.get(row["id"], {}).get("enabled", True)]
    selections = [{"apple_id": row["id"], "alcor_id": previous.get(row["id"], {}).get("alcor_id")}
                  for row in sources]
    if not selections:
        return [{**row, "enabled": bool(row["enabled"])} for row in stored]
    reserved = {row['alcor_id'] for row in stored if row['apple_id'] not in {source['id'] for source in sources}}
    categories = [row for row in categories if row['id'] not in reserved]
    links = bidirectional_links(selections, [{**row, "writable": True} for row in sources], categories, unavailable_ids=unavailable_ids)
    writable = {row["id"]: row.get("writable", False) for row in sources}
    for link in links:
        old = previous.get(link["apple_id"], {})
        link["direction"] = old.get("direction", "bidirectional") if writable[link["apple_id"]] else "import"
        link["enabled"] = bool(old.get("enabled", True))
    return links + [{**row,"enabled":bool(row["enabled"])} for row in stored if row["apple_id"] not in writable]


def changed_link_groups(sync, links, previous, available):
    """Claim only links whose persisted relationship changes; retain untouched holds."""
    if any(not isinstance(row,dict) for row in links):
        raise ValueError('link must be an object')
    requested = {str(row.get('apple_id') or '').strip():row for row in links}
    if len(requested) != len(links) or '' in requested:
        raise ValueError('category links need distinct Apple identities')
    changed = {key for key in set(previous)|set(requested)
               if key not in previous or key not in requested or
               any(requested[key].get(field) != (bool(previous[key][field]) if field=='enabled' else previous[key][field])
                   for field in ('alcor_id','direction','enabled'))}
    groups = {}
    for key in changed:
        native = available.get(key) or sync._get('category-snapshot:'+key) or {}
        groups[key] = native.get('source_id') or sync._get('category-source:'+key)
    return groups


def restore_link_selection(sync, previous, selected_key):
    """Reconnecting identical links changes selection state, never ownership claims."""
    import json
    with sync._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        current = {row['apple_id']:dict(row) for row in db.execute('SELECT * FROM category_links ORDER BY apple_id')}
        if current != previous:
            raise ValueError('category links changed before reconnecting')
        enabled = [row for row in current.values() if row['enabled']]
        state = {selected_key:[row['apple_id'] for row in enabled], 'connected':bool(enabled)}
        directions = {row['direction'] for row in enabled}
        if len(directions) == 1:
            state['direction'] = directions.pop()
        db.executemany('INSERT OR REPLACE INTO settings VALUES (?,?)', [(key,json.dumps(value)) for key,value in state.items()])
