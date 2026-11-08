"""Read-only views of the ledger and its existing period snapshots."""
from collections import defaultdict


def totals(rows):
    groups = {}
    for row in rows:
        group = groups.setdefault(row['currency'], {'income': 0, 'expense': 0})
        if row['status'] == 'posted':
            group[row['entry_type']] += row['amount_minor']
    return [{'currency': currency, 'income_minor': str(value['income']),
             'expense_minor': str(value['expense']), 'net_minor': str(value['income'] - value['expense'])}
            for currency, value in sorted(groups.items())]


def ledger_report(service, start, end):
    rows = service.list_ledger_between(start, end)
    days = defaultdict(list)
    for row in rows:
        days[row['occurred_at'][:10]].append(row)
    return {'totals': totals(rows), 'days': [
        {'date': day, 'totals': totals(items),
         'entry_ids': [row['id'] for row in sorted(items, key=lambda row: (row['occurred_at'][11:19], row['created_at'], row['id']), reverse=True)]}
        for day, items in sorted(days.items(), reverse=True)]}


def statement_reports(service, period_type, year):
    reports = []
    for statement in service.list_statements(period_type, year):
        items = service.store.list_statement_items(statement['id'])
        report = {**statement, **{key: str(statement[key]) for key in ('income_minor', 'expense_minor', 'net_minor')},
                  'categories': {}, 'rankings': {}}
        for direction in ('income', 'expense'):
            total = statement[direction + '_minor']
            share = lambda amount: (amount * 10000 + total // 2) // total if total else 0
            groups = {}
            ranking = []
            for item in items:
                if item['entry_type'] != direction:
                    continue
                category = item['category'].strip() or '未分类'
                group = groups.setdefault(category, {'category': category, 'amount_minor': 0, 'entry_count': 0})
                group['amount_minor'] += item['amount_minor']
                group['entry_count'] += item['entry_count']
                ranking.append({**item, 'category': category, 'share_basis_points': share(item['amount_minor'])})
            categories = sorted(groups.values(), key=lambda row: (-row['amount_minor'], row['category']))
            for row in categories:
                row['share_basis_points'] = share(row['amount_minor'])
            ranking.sort(key=lambda row: (-row['amount_minor'], row['name'], row['category']))
            for group in (categories, ranking):
                for row in group:
                    row['amount_minor'] = str(row['amount_minor'])
            report['categories'][direction] = categories
            report['rankings'][direction] = ranking
        reports.append(report)
    return reports
