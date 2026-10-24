"""Read-only projection of declarations, published contributions, and adapters."""
from core.capabilities.descriptions import clean_copy
from core.capabilities.tool_copy import user_tool_copy


def _schemas(source):
    return {key: source[key] for key in ('inputSchema', 'outputSchema') if isinstance(source.get(key), dict)}


def mcp_features(row):
    from agent.tools.mcp.mcp_tool import McpTool
    if not row.get('enabled') or row.get('state') != 'ready':
        return []
    return [dict(id=tool['name'], type='tools', source='mcp', available=tool.get('enabled', True),
                 enabled=tool.get('enabled', True), toggleId=McpTool.name_for(row['name'], tool['name']),
                 name=clean_copy(tool.get('title')) or tool['name'],
                 description=clean_copy(tool.get('description')), **_schemas(tool),
                 **user_tool_copy(tool.get('description')))
            for tool in row.get('tools', [])]


def plugin_features(row, adapters):
    available = row['intent'] == 'enabled' and row['state'] not in {'failed', 'maintenance', 'removed', 'draining'}
    features = {}
    declared = row.get('manifest', {}).get('contributes', {})
    published = row.get('contributes', {}) if available else {}
    for category in ('tools', 'pages', 'skills', 'mcp', 'services'):
        for item in [*declared.get(category, []), *published.get(category, [])]:
            item = {'id': item} if isinstance(item, str) else item
            identifier = item.get('id') or item.get('name')
            if not identifier:
                continue
            previous = features.get((category, identifier), {})
            name = clean_copy(item.get('title')) or clean_copy(item.get('name')) or previous.get('name') or identifier
            feature = dict(previous, id=identifier, name=name,
                           description=clean_copy(item.get('description')) or previous.get('description', ''),
                           type=category, source='plugin', available=available, **_schemas(item))
            if category == 'tools' and available:
                published_ids = [entry if isinstance(entry, str) else entry.get('id') or entry.get('name')
                                 for entry in published.get('tools', [])]
                feature['available'] = True if identifier in published_ids else None
            if category == 'pages':
                feature['pageId'] = identifier
                if name == identifier and len(declared.get('pages', [])) == 1:
                    feature['name'] = row['name']
            features[(category, identifier)] = feature
    for tool in adapters:
        source = tool.get('source', {})
        if source.get('type') != 'plugin' or row['pluginId'] not in source.get('plugin_ids', []):
            continue
        previous = features.get(('tools', tool['id']), {})
        features[('tools', tool['id'])] = dict(previous,
            id=tool['id'], type='tools', source='plugin', enabled=tool.get('enabled', True), toggleId=tool['id'], name=clean_copy(tool.get('title')) or previous.get('name') or tool['id'],
            description=clean_copy(tool.get('intro')) or previous.get('description', ''),
            detailDescription=clean_copy(tool.get('toolDescription')) or clean_copy(tool.get('detail')) or previous.get('detailDescription') or clean_copy(tool.get('intro')) or previous.get('description', ''),
            available=available and tool.get('enabled', False) and tool.get('available', False), **_schemas(tool))
        features[('tools', tool['id'])].update(user_tool_copy(
            features[('tools', tool['id'])]['description'], tool.get('detail'), tool.get('toolDescription')))
    for feature in features.values():
        if feature['type'] == 'tools' and 'userSummary' not in feature:
            feature.update(user_tool_copy(feature['description'], feature.get('detailDescription')))
    return list(features.values())
