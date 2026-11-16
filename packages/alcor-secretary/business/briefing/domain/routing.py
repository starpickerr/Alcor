"""Resolve supported facts before opening a model session."""
import re
from dataclasses import replace
from zoneinfo import ZoneInfo

from .generator import (NeedModel, SelectedCandidate, _sort_key, briefing_title, decision_schema,
                        report_body, rules_document, source_entry, validate_decisions)
from .instructions import rule_document
from .window import instant

ASSIGNMENT_ONLY_RULE = (
    '## eLearning 作业入选条件\n'
    '只收录平台列为作业（assignment）、尚未提交且仍在有效截止时间内的事项。遇到延期，以该作业最新有效的截止时间为准。'
    '已交、已过有效截止时间的作业，以及公告、成绩和独立课程文件，均不入选。提交状态或截止时间不明时，不推定为符合条件。\n'
    '每份入选作业单列一张卡。同一作业的发布、催交和延期合在该卡。不同作业分别写。写清课程、作业名、提交状态和截止时间。'
    '若尚未开放，写明何时可以开始。指定了提交入口、必交材料或特殊提交方式时，写出会影响提交的细节。'
)


def routing_rules(rules):
    return rules_document(rules).replace("- 合并同一邮件线程中的重复信息，保留关键变化。",
                                         "- 每条只依据自身原文独立解读。", 1)


def local_record_text(item):
    record = item.payload.get('record') or {}
    if item.source == 'calendar_agenda' and item.payload.get('start_at'):
        fields = [('开始', item.payload['start_at']), ('结束', item.payload.get('end_at')),
                  ('全天', '是' if item.payload.get('all_day') else '否'), ('日程', item.snippet)]
    elif item.source == 'alcor_tasks' and record:
        fields = [(name, record.get(key)) for name, key in (
            ('状态', 'status'), ('截止日期', 'due_on'), ('截止时间', 'due_time'),
            ('优先级', 'priority'), ('重复', 'repeat_frequency'))]
    elif item.source == 'alcor_bills' and record:
        from decimal import Decimal
        amount = record.get('amount_minor')
        currency = record.get('currency')
        money = Decimal(amount).scaleb(0 if currency == 'JPY' else -2) if amount is not None else None
        fields = [('金额', f'{money} {currency}' if money is not None and currency else None),
                  ('类型', record.get('entry_type')), ('时间', record.get('occurred_at')), ('状态', record.get('status'))]
    else:
        return ''
    parts = [f'{label}：{value}' for label, value in fields if value is not None and value != '']
    if item.payload.get('body'):
        parts.append(item.payload['body'])
    return '\n'.join(parts)


def assignment_only_policy(rules):
    """Recognize the saved explicit eLearning rule; edits fall back to model review."""
    return any(rule.enabled and rule.source == 'elearning' and
               rule.text.strip() == ASSIGNMENT_ONLY_RULE for rule in rules)


def local_result(item, email, rules, now):
    """Return a final (decision, content), or None when interpretation is needed."""
    data = item.payload
    body = str(data.get('body') or item.snippet).strip()
    if item.source == 'elearning' and assignment_only_policy(rules):
        if data.get('resource_type') != 'assignment' or data.get('submission_status') != 'unsubmitted':
            return {'id': item.id, 'selected': False, 'reason': '不符合 eLearning 作业入选条件'}, ''
        try:
            due = instant(data.get('due_at'))
        except (TypeError, ValueError):
            return {'id': item.id, 'selected': False, 'reason': '截止时间不明确'}, ''
        if due <= instant(now):
            return {'id': item.id, 'selected': False, 'reason': '有效截止时间已过'}, ''
        if not body or not data.get('course_id'):
            return None
        deadline = due.astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M')
        summary = f'尚未提交，截止北京时间 {deadline}。'
        return {'id': item.id, 'selected': True, 'section': 'action', 'reason': '未提交且未过截止时间',
                'score': 80, 'category': 'needs_action', 'information_type': 'assignment',
                'summary': summary}, body
    include = exclude = False
    for rule in rules:
        if not rule.enabled or rule.source not in ('*', item.source):
            continue
        if rule.note or rule.text and rule.text != rule_document(replace(rule, text='')):
            return None  # Free-form instructions may change selection or presentation.
        address = email.sender_email if email else ''
        fields = {'sender': item.sender, 'title': item.title, 'sender_email': address,
                  'sender_domain': address.rpartition('@')[2], 'list_id': email.list_id if email else ''}
        value = fields[rule.match_field].casefold()
        needle = rule.match_value.casefold()
        if value == needle or (rule.match_op == 'contains' and needle in value):
            if rule.action == 'include':
                include = True
            elif rule.action == 'exclude':
                exclude = True
    if exclude and not include:
        return {'id': item.id, 'selected': False, 'reason': '符合排除规则'}, ''

    kind = data.get('resource_type')
    action = False
    if item.source in ('calendar_agenda', 'alcor_tasks', 'alcor_bills') and item.account_id == 'local':
        body = local_record_text(item)
        if not body:
            return None
        kind = 'information'
    elif item.source == 'elearning' and data.get('course_id') and body:
        if kind not in ('file', 'assignment', 'quiz', 'grade', 'course_grade'):
            return None
        if kind == 'grade' and data.get('submission_status') == 'pending_review' and 'points_possible' not in data:
            body = '已提交，成绩待复核。'  # Earlier connectors included provisional scores.
        action = kind in ('assignment', 'quiz') and data.get('submission_status') == 'unsubmitted'
    elif item.source.startswith('plugin:') and isinstance(data.get('briefing_fact'), str) and data['briefing_fact'].strip():
        body = data['briefing_fact'].strip()
        kind = 'information'
    elif item.source == 'email' and email and email.sender_email.lower() == 'noreply@github.com':
        oauth = re.search(r'A third-party OAuth application \(([^()\n]+)\) with ([\w:, -]+) scopes was recently authorized', body)
        device = re.search(r'You just added a passkey to your account from your "([^"\n]+)" device', body)
        if item.title.split() == '[GitHub] A third-party OAuth application has been added to your account'.split() and oauth:
            body = f'GitHub 授权提醒：{oauth[1]} 已获 {oauth[2]} 权限。请核对是否为本人操作。'
            body += '\n[查看授权](https://github.com/settings/applications) · [安全日志](https://github.com/settings/security-log)'
        elif item.title.strip() == '[GitHub] A passkey was added to your account' and device:
            body = f'GitHub 已从 {device[1]} 添加通行密钥。若为本人操作，请保存恢复码；若非本人操作，请检查活跃会话及所有通行密钥。失去凭据且没有恢复码时，GitHub 支持无法恢复账户访问。'
            body += '\n[恢复码](https://github.com/settings/auth/recovery-codes) · [安全设置](https://github.com/settings/security)'
        else:
            return None
        kind = 'information'
        action = True
    else:
        return None
    return {'id': item.id, 'selected': True, 'section': 'action' if action else 'fyi',
            'reason': '根据来源事实整理', 'score': 80 if action else 50,
            'category': 'needs_action' if action else 'status_update', 'information_type': kind,
            'summary': body.splitlines()[0]}, body


def routed_draft(rows, rules, briefing_date, submissions, preserved_decisions, now):
    """One local pass, up to one model submission, one source-bound result per ID."""
    rules_text = routing_rules(rules)
    results = {}
    entries = []
    pending = []
    for item, email in rows:
        entry = source_entry(item, email)
        entries.append(entry)
        local = local_result(item, email, rules, now)
        if local is not None:
            results[item.id] = local
            continue
        prior = preserved_decisions.get(item.id)
        if prior and prior.get('_individual_report'):
            results[item.id] = prior, prior.get('summary', '')
            continue
        # Only this item's own message fields may enter the model turn.
        material = {key: entry[key] for key in
                    ('id', 'source', 'sender', 'title', 'occurred_at', 'url', 'body')}
        if item.account_id == 'local':
            material['body'] = local_record_text(item) or material['body']
        if not material['body'] and isinstance(item.payload.get('briefing_fact'), str):
            material['body'] = item.payload['briefing_fact']
        if email:
            material.update(sender_email=email.sender_email, list_id=email.list_id)
        pending.append(material)

    if pending:
        ids = [row['id'] for row in pending]
        if not submissions:
            task = ('一次提交本批全部条目的 decisions，每个 id 恰好一次。对每条仅依据它自己的来源、'
                    '发布时间和正文独立判断。不得关联其他条目、个人记录或历史简报。'
                    '每项必填 id、布尔值 selected、非空 reason。保留时还必须填 section（action 或 fyi）、'
                    '整数 score（0 至 100）及非空 summary，将该项完整可展示的解读写入 summary，保留行动、条件、期限和原文例外；'
                    '未注明的信息不得推断。不要再撰写整份简报。')
            request = NeedModel(task, rules_text, decision_schema(), pending)
            request.request.update(singleBatch=True, submitSchema=request.request['schema'])
            raise request
        if len(submissions) != 1:
            raise ValueError('本次简报只接受一份模型结果。')
        result = submissions[0]
        validate_decisions(result, ids)
        for decision in result['decisions']:
            results[decision['id']] = decision, decision.get('summary', '')

    cards, mail = [], []
    ranked = []
    for (item, _), entry in zip(rows, entries):
        decision, content = results[item.id]
        decision = {**decision, '_individual_report': True}
        mail.append({**entry, 'decision': decision, 'filter_reason': decision['reason'],
                     'category': 'selected' if decision['selected'] else 'filtered'})
        if decision['selected']:
            ranked.append(SelectedCandidate(item, decision['section'], decision['score']))
    for index, candidate in enumerate(sorted(ranked, key=_sort_key)):
        item = candidate.item
        decision, content = results[item.id]
        cards.append({**{key: getattr(item, key) for key in
                         ('source', 'account_id', 'source_id', 'thread_id', 'sender', 'title', 'occurred_at', 'url')},
                      'source_item_id': item.id, 'sort_order': index, 'section': candidate.section,
                      'score': candidate.score, 'snippet': decision['summary'],
                      'payload': {**item.payload, '_briefing_reason': decision['reason'], '_briefing_report': content,
                                  '_briefing_members': [{'id': item.id, 'title': item.title, 'summary': decision['summary']}]}})
    return {'title': briefing_title(briefing_date), 'body': report_body(cards), 'items': cards,
            'mail': mail, 'rule_text': rules_text}
