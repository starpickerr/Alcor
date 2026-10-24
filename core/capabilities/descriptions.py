"""Display-only fallbacks for shipped capabilities whose older packages lack prose.

Package/configuration descriptions take precedence. These labels confer no trust
or execution permissions and do not require updating installed package digests.
"""

SHIPPED_DESCRIPTIONS = {
    ('plugins', 'stella.bills'): {
        'zh': '记录和查询个人收支，维护账单分类并查看月度、年度汇总。可在账单页面管理记录，在配置中调整插件选项。',
        'en': 'Record and search personal income and expenses, manage categories, and review monthly or yearly totals. Manage records on the Bills page and plugin options under Configuration.',
    },
    ('plugins', 'stella.calendar'): {
        'zh': '创建、查询和维护日程、分类及重复事件。可在日程页面安排活动，在配置中管理 Apple 日历连接与同步。',
        'en': 'Create and manage events, categories, and recurring schedules. Plan activities on the Calendar page and manage Apple Calendar connections and sync under Configuration.',
    },
    ('plugins', 'stella.tasks'): {
        'zh': '管理事务、子事务、排序和完成状态。可在事务页面整理待办，在配置中管理 Apple 提醒事项连接与同步。',
        'en': 'Manage tasks, subtasks, ordering, and completion status. Organize work on the Tasks page and manage Apple Reminders connections and sync under Configuration.',
    },
    ('plugins', 'stella.briefing'): {
        'zh': '汇总已配置来源的信息，生成和查看每日简报，维护简报条目与规则。生成结果取决于可用来源及当前授权范围。',
        'en': 'Collect information from configured sources, generate and review daily briefings, and manage items and rules. Results depend on available sources and current access permissions.',
    },
    ('plugins', 'stella.automation'): {
        'zh': '创建和管理定时任务、提醒与周期性自动化规则。后台执行沿用任务的权限模式，通知使用已配置的渠道。',
        'en': 'Create and manage scheduled tasks, reminders, and recurring automation rules. Background runs use the task permission mode and notifications use configured channels.',
    },
    ('plugins', 'stella.agent-hub'): {
        'zh': '连接已配置的外部智能体，提交任务、查看执行进度或请求取消。使用前需要配置接入方式，外部操作仍受连接和 Agent 权限约束。',
        'en': 'Connect configured external agents, submit tasks, inspect progress, or request cancellation. Configure access before use; external operations remain subject to connection and Agent permissions.',
    },
    ('plugins', 'stella.knowledge'): {
        'zh': '保存、检索和维护知识文档，为对话提供可引用的资料。可在知识页面整理内容，Agent 通过知识工具读取或更新授权范围内的文档。',
        'en': 'Store, search, and maintain knowledge documents for use as conversation references. Organize content on the Knowledge page; Agent knowledge tools read or update documents within their permitted scope.',
    },
    ('plugins', 'stella.memory'): {
        'zh': '保存、检索和维护长期记忆，为启用记忆的对话提供上下文。记忆与知识文档分别管理，读取和更新遵循当前权限。',
        'en': 'Store, search, and maintain long-term memory for conversations with memory enabled. Memory is managed separately from knowledge documents; reads and updates follow current permissions.',
    },
    ('mcp', 'email'): {
        'zh': '通过独立的邮件 MCP 服务管理邮箱连接、查询邮件来源并采集邮件。在配置中添加账号、选择采集范围和保存方式；使用前需要有效的邮箱连接。',
        'en': 'Use the independent Email MCP service to manage mailbox connections, inspect sources, and collect messages. Add accounts and choose collection scope and storage under Configuration; a valid mailbox connection is required.',
    },
    ('mcp', 'elearning'): {
        'zh': '通过独立的 eLearning MCP 服务查询课程、采集课程信息并同步课程文件。在配置中设置账号、课程范围和文件保存目录，使用前需要有效的课程平台连接。',
        'en': 'Use the independent eLearning MCP service to query courses, collect course information, and sync course files. Configure the account, course scope, and file destination under Configuration; a valid learning-platform connection is required.',
    },
}


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
    identifier = row.get('pluginId') if kind == 'plugins' else row.get('name', row.get('id'))
    if known := SHIPPED_DESCRIPTIONS.get((kind, identifier)):
        return dict(known)
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
