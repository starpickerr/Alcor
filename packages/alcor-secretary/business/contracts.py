from __future__ import annotations

import inspect


PARAMETERS = {
    'calendar': {
        'cancel_apple_operation':('operation_id',), 'categories': (), 'get_category': ('id',), 'get_event': ('id',),
        'list_events': ('start', 'end', 'category_id'),
        'create_category': ('name', 'color','source_id'), 'update_category': ('id', 'patch'),
        'delete_category': ('id',), 'set_event_category': ('id', 'category_id'),
        'create_event': ('title', 'description', 'location', 'category_id', 'start_at', 'end_at', 'all_day', 'repeat_frequency', 'repeat_days', 'repeat_until', 'repeat_timezone','url','alarms','floating'),
        'update_event': ('id', 'patch'), 'delete_event': ('id','scope','occurrence_on'),
        'upsert_external_event': ('provider', 'source_key', 'payload', 'event_id', 'reconciled'),
        'exclude_occurrence': ('series_id', 'occurrence_on'), 'import_china_holidays': ('year',),
        'create_special_date': ('title', 'date', 'timezone', 'annual', 'description'),
        'import_special_dates': ('content', 'format', 'timezone'),
        'ingest_briefing_revision': ('briefing_id', 'revision'),
        'find_briefing_sources': ('source_keys',), 'has_source_tombstone': ('source_keys',),
        'sync_briefing_facts': ('id', 'changes', 'baseline', 'force'),
    },
    'bills': {
        'import_files': ('files', 'dry_run'),
        'list_ledger': ('year', 'month'), 'list_ledger_between': ('start', 'end'), 'get_ledger': ('id',),
        'ledger_report': ('start', 'end'), 'statement_reports': ('period_type', 'year'),
        'create_ledger': ('name', 'amount_minor', 'currency', 'entry_type', 'occurred_at', 'category', 'note', 'status'),
        'update_ledger': ('id', 'patch', 'expectedRevision'), 'delete_ledger': ('id', 'expectedRevision'),
        'list_statements': ('period_type', 'year'), 'get_statement': ('id',),
        'export_ledger_csv': ('start', 'end'), 'export_ledger': ('start', 'end', 'lang'), 'get_operation': ('operationId',),
    },
}

EXAMPLES = {
    ('tasks', 'create_todo'): {'payload': {'title': '提交报告', 'status': 'todo', 'due_on': '2026-11-06', 'due_time': '09:00', 'timezone': 'Asia/Shanghai', 'priority': 2, 'planned_on': '2026-11-05', 'list_name': '工作', 'tags': ['报告'], 'flagged': True, 'checklist': [{'title': '核对数据', 'completed': False}]}},
    ('tasks', 'update_todo'): {'todo_id': 'existing-id', 'payload': {'notes': '修改备注', 'remind_at': '2026-11-06T08:00:00+08:00'}},
    ('calendar', 'create_event'): {'title': '例会', 'start_at': '2026-11-06T09:00:00+08:00', 'end_at': '2026-11-06T10:00:00+08:00', 'repeat_frequency': 'weekly', 'repeat_days': [4], 'repeat_timezone': 'Asia/Shanghai'},
    ('bills', 'create_ledger'): {'name': '午餐', 'amount_minor': 2500, 'currency': 'CNY', 'entry_type': 'expense', 'occurred_at': '2026-11-05T12:30:00+08:00', 'category': '餐饮', 'status': 'posted'},
    ('briefing', 'run_pipeline'): {'briefing_date': '2026-11-05', 'sources': [{'source': 'email', 'account_id': 'configured-account', 'items': [{'source_id': 'stable-source-id', 'title': '邮件标题', 'snippet': '内容摘要', 'occurred_at': '2026-11-05T09:00:00Z', 'payload': {'body': '原文'}}], 'cursors': {'sequence': '1'}, 'errors': []}]},
}


def catalog(domain, entry, service):
    methods = []
    for descriptor in entry.SERVICE_DESCRIPTORS:
        for original in descriptor['methods']:
            method = dict(original)
            name = method['name']
            if domain in PARAMETERS:
                method['inputSchema'] = {'type': 'object', 'properties': {key: {} for key in PARAMETERS[domain][name]}, 'additionalProperties': False}
            target = getattr(service, name, None)
            if target:
                method['signature'] = str(inspect.signature(target))
            if (domain, name) in EXAMPLES:
                method['example'] = EXAMPLES[domain, name]
            methods.append(method)
    if domain == 'briefing':
        methods.append({'name': 'configure_sources', 'description': '由简报页面显式保存信息来源。Agent 无权调用此配置入口；凭据由 DSH 管理。', 'example': {'sources': [{'source': 'elearning', 'accountId': 'configured-account', 'tool': 'mcp__elearning__fetch', 'method': 'fetch', 'arguments': {}}]}, 'inputSchema': {'type': 'object', 'properties': {'sources': {'type': 'array'}}}})
        methods.append({'name': 'available_sources', 'inputSchema': {'type': 'object'}, 'description': '查询 DSH 已连接的简报采集工具。'})
        methods.append({'name': 'generate', 'inputSchema': {'type': 'object', 'properties': {key: {} for key in ('date', 'notes', 'requestId', 'parentSessionId', 'sources')}}, 'description': '通过 DSH 当前模型批量筛选、撰写并保存简报。date 为 YYYY-MM-DD，notes 可为空，requestId 使用 UUID；模型失败不前移游标。未提供 sources 时使用 configure_sources 的配置。'})
    return methods
