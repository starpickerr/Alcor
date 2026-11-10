"""Normalize public descriptions supplied by capability packages and configs."""


def clean_copy(value):
    """Only public text/localizations belong in the inventory, never extra keys."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return {lang: text.strip() for lang in ('zh', 'en')
                if isinstance(text := value.get(lang), str) and text.strip()}
    return ''


def text_for(value, lang):
    value = clean_copy(value)
    return value if isinstance(value, str) else value.get(lang) or next(iter(value.values()), '')


def description_for(kind, row):
    field = row.get('manifest', {}).get('description') if kind == 'plugins' else row.get('intro' if kind == 'tools' else 'description')
    if description := clean_copy(field):
        return description
    if kind == 'mcp' and row.get('tools'):
        names = ', '.join(tool['name'] for tool in row['tools'][:3])
        return {'zh': f'通过 MCP 提供工具：{names}。完整工具说明可在功能标签中查看。',
                'en': f'Provides MCP tools: {names}. See Features for the complete tool descriptions.'}
    return {
        'plugins': {'zh': '此插件尚未提供介绍，可在功能中查看已登记的工具、服务和页面。',
                    'en': 'This plugin has not supplied an introduction. See Features for its registered tools, services, and pages.'},
        'mcp': {'zh': '此 MCP 服务尚未提供介绍。连接成功后可在功能中查看已发现的工具及用途。',
                'en': 'This MCP service has not supplied an introduction. After connecting, see Features for discovered tools and their purposes.'},
        'skills': {'zh': '此技能尚未提供简介，可在内容中阅读任务流程和使用指令。',
                   'en': 'This skill has not supplied a summary. See Content for its workflow and instructions.'},
        'tools': {'zh': '此工具尚未提供详细说明，调用仍受当前 Agent 权限模式限制。',
                  'en': 'This tool has not supplied a description. Calls remain subject to the current Agent permission mode.'},
    }[kind]
