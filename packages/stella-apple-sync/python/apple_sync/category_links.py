"""Stable category-link checks shared by calendar and reminder sync."""

DIRECTIONS = frozenset({"import", "export", "bidirectional"})


def bidirectional_links(selections, sources, categories):
    if not isinstance(selections, list) or not selections:
        raise ValueError("请至少选择一个要同步的项目")
    available = {row["id"]: row for row in sources}
    local = {row["id"]: row for row in categories}
    seen = set()
    used = set()
    for row in selections:
        if not isinstance(row, dict) or set(row) - {"apple_id", "stella_id"}:
            raise ValueError("同步项目格式无效")
        identity = row.get("apple_id")
        if not isinstance(identity, str) or identity in seen or identity not in available:
            raise ValueError("同步项目重复或已不可用，请刷新后重新选择")
        if not available[identity].get("writable"):
            raise ValueError("双向同步需要可写的日历或清单")
        target = row.get("stella_id")
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
        target = selection.get("stella_id") or ""
        title = str(available[selection["apple_id"]].get("title") or "Apple")[:200].strip() or "Apple"
        if not target:
            existing = next((row for row in categories if row["name"] == title and row["id"] not in used), None)
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
        links.append({"apple_id": selection["apple_id"], "stella_id": target, "create_name": name,
                      "direction": "bidirectional", "enabled": True})
    return links


def reconcile_names(stored_apple, stored_stella, apple_now, stella_now):
    """Decide a name change from the last synced pair, not from content direction.

    Returns ``(action, name)``. ``name`` is the string the other side should
    adopt, or ``None`` when the two sides disagree.
    """
    apple_changed = apple_now != stored_apple
    stella_changed = stella_now != stored_stella
    if apple_changed and stella_changed:
        if apple_now == stella_now:
            return "aligned", apple_now
        return "conflict", None
    if apple_changed:
        return "apple_to_stella", apple_now
    if stella_changed:
        return "stella_to_apple", stella_now
    return "unchanged", stella_now


def validate_links(rows):
    """Reject links that are not a one-to-one enabled mapping."""
    seen_apple = set()
    seen_stella = set()
    for row in rows:
        apple_id = str(row.get("apple_id") or "").strip()
        stella_id = str(row.get("stella_id") or "").strip()
        direction = str(row.get("direction") or "").strip()
        if not apple_id:
            raise ValueError("apple_id is required")
        if not stella_id:
            raise ValueError("stella_id is required")
        if direction not in DIRECTIONS:
            raise ValueError(f"direction must be one of {', '.join(sorted(DIRECTIONS))}")
        if not isinstance(row.get("enabled"), bool):
            raise ValueError("enabled must be a boolean")
        if apple_id in seen_apple or stella_id in seen_stella:
            raise ValueError("category links must be one-to-one")
        seen_apple.add(apple_id)
        seen_stella.add(stella_id)


def all_source_links(sources, categories, stored):
    """Keep stable mappings while discovering new calendars and reminder lists."""
    previous = {row["apple_id"]: row for row in stored}
    selections = [{"apple_id": row["id"], "stella_id": previous.get(row["id"], {}).get("stella_id")}
                  for row in sources]
    if not selections:
        return [{**row, "enabled": bool(row["enabled"])} for row in stored]
    reserved = {row['stella_id'] for row in stored if row['apple_id'] not in {source['id'] for source in sources}}
    categories = [row for row in categories if row['id'] not in reserved]
    links = bidirectional_links(selections, [{**row, "writable": True} for row in sources], categories)
    writable = {row["id"]: row.get("writable", False) for row in sources}
    for link in links:
        link["direction"] = "bidirectional" if writable[link["apple_id"]] else "import"
    return links + [{**row,"enabled":bool(row["enabled"])} for row in stored if row["apple_id"] not in writable]
