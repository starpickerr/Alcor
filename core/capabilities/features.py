"""Read-only projection of declarations, published contributions, and adapters."""
from core.capabilities.descriptions import clean_copy


def _schemas(source):
    return {key: source[key] for key in ('inputSchema', 'outputSchema') if isinstance(source.get(key), dict)}


def mcp_features(row):
    if not row.get('enabled') or row.get('state') != 'ready':
        return []
    return [dict(id=tool['name'], type='tools', source='mcp', available=True,
                 name=clean_copy(tool.get('title')) or tool['name'],
                 description=clean_copy(tool.get('description')), **_schemas(tool))
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
            name = clean_copy(item.get('title')) or clean_copy(item.get('name')) or identifier
            feature = dict(id=identifier, name=name, description=clean_copy(item.get('description')),
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
        features[('tools', tool['id'])] = dict(
            id=tool['id'], type='tools', source='plugin', name=tool['title'], description=tool['intro'],
            detailDescription=clean_copy(tool.get('toolDescription')) or clean_copy(tool.get('detail')) or tool['intro'],
            available=available and tool.get('enabled', False) and tool.get('available', False), **_schemas(tool))
    return list(features.values())
