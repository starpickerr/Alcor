"""Read payment exports as data; no file paths, formulas or macros are executed."""
import base64
import binascii
import csv
import hashlib
import io
import json
import re
import zipfile
from datetime import datetime
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

MAX_FILE = 5 * 1024 * 1024
NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'


def _xml(archive, name):
    data = archive.read(name)
    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise ValueError('不支持含外部实体的 Excel 文件。')
    return ET.fromstring(data)


def _xlsx(data):
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        infos = archive.infolist()
        if len(infos) > 300 or sum(item.file_size for item in infos) > 30 * 1024 * 1024:
            raise ValueError('Excel 解压后过大。')
        if 'xl/workbook.xml' in archive.namelist():
            properties = _xml(archive, 'xl/workbook.xml').find(NS + 'workbookPr')
            if properties is not None and properties.get('date1904') in ('1', 'true'):
                raise ValueError('不支持 1904 日期系统，请使用支付平台原始导出文件。')
        strings = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            strings = [''.join(node.itertext()) for node in _xml(archive, 'xl/sharedStrings.xml').findall(NS + 'si')]
        sheets = sorted(item.filename for item in infos if re.fullmatch(r'xl/worksheets/sheet\d+\.xml', item.filename))
        for sheet in sheets:
            rows = []
            for row in _xml(archive, sheet).iter(NS + 'row'):
                cells = {}
                for cell in row.findall(NS + 'c'):
                    ref = re.match(r'([A-Z]+)', cell.get('r', ''))
                    if not ref: continue
                    index = 0
                    for char in ref[1]: index = index * 26 + ord(char) - 64
                    if index > 100: raise ValueError('Excel 列数过多。')
                    if cell.find(NS + 'f') is not None:
                        raise ValueError('账单不能包含公式，请使用支付平台原始导出文件。')
                    value = cell.findtext(NS + 'v', '')
                    kind = cell.get('t')
                    if kind == 's': value = strings[int(value)]
                    elif kind == 'inlineStr': value = ''.join(cell.find(NS + 'is').itertext())
                    elif re.fullmatch(r'\d{16,}', value):
                        raise ValueError('Excel 中的长交易号为数字格式，可能丢失精度，请重新导出。')
                    cells[index - 1] = value
                rows.append([cells.get(i, '') for i in range(max(cells, default=-1) + 1)])
                if len(rows) > 11000: raise ValueError('单个文件最多支持 10000 笔交易。')
            if _header(rows) is not None: return rows
    except (zipfile.BadZipFile, ET.ParseError, KeyError, IndexError, TypeError, binascii.Error) as error:
        raise ValueError('Excel 文件损坏或格式不受支持。') from error
    raise ValueError('没有找到微信或支付宝账单表头。')


def _clean(value):
    return str(value).replace('\ufeff', '').strip().strip('\t').strip()


def _header(rows):
    for index, row in enumerate(rows[:150]):
        names = [_clean(value).replace('（', '(').replace('）', ')') for value in row]
        if ('交易时间' in names or '交易创建时间' in names) and '收/支' in names and any(name in names for name in ('金额', '金额(元)')):
            return index, names
    return None


def _decode(data):
    for encoding in ('utf-8-sig', 'gb18030'):
        try: return data.decode(encoding)
        except UnicodeDecodeError: pass
    raise ValueError('CSV 编码不受支持，请使用 UTF-8 或 GB18030。')


def parse_files(files):
    if not isinstance(files, list) or not 1 <= len(files) <= 5:
        raise ValueError('每次请选择 1 至 5 个账单文件。')
    parsed, issues = [], []
    total_bytes = 0
    for file in files:
        if not isinstance(file, dict) or set(file) != {'name', 'content'}:
            raise ValueError('账单文件参数无效。')
        name, content = file['name'], file['content']
        if not isinstance(name, str) or not 1 <= len(name) <= 200 or not name.lower().endswith(('.csv', '.xlsx')) or '/' in name or '\\' in name:
            raise ValueError('仅支持 CSV 和 XLSX 账单文件。')
        if not isinstance(content, str) or len(content) > (MAX_FILE + 2) // 3 * 4:
            raise ValueError('每个账单文件不能超过 5 MB。')
        try: data = base64.b64decode(content, validate=True)
        except (ValueError, binascii.Error) as error: raise ValueError('文件编码无效。') from error
        total_bytes += len(data)
        if len(data) > MAX_FILE or total_bytes > 20 * 1024 * 1024:
            raise ValueError('账单文件大小超限。')
        rows = _xlsx(data) if name.lower().endswith('.xlsx') else list(csv.reader(io.StringIO(_decode(data))))
        found = _header(rows)
        if found is None: raise ValueError('没有找到微信或支付宝账单表头。')
        start, names = found
        nonempty = [name for name in names if name]
        if len(set(nonempty)) != len(nonempty): raise ValueError('账单表头存在重复列。')
        provider = 'wechat' if '交易单号' in names else 'alipay' if any(key in names for key in ('交易订单号', '交易号')) else None
        if not provider: raise ValueError('缺少交易单号，无法安全去重。')
        if len(rows) - start - 1 > 10000: raise ValueError('单个文件最多支持 10000 笔交易。')
        for number, values in enumerate(rows[start + 1:], start + 2):
            if not any(_clean(value) for value in values): continue
            if len(values) == 1 and re.match(r'^[-=]{3,}', _clean(values[0])): break
            row = dict(zip(names, map(_clean, values)))
            def get(*keys): return next((row[key] for key in keys if row.get(key)), '')
            try:
                direction = get('收/支')
                if direction not in ('收入', '支出'): raise ValueError('不计收支或非交易记录')
                status = get('当前状态', '交易状态')
                if '退款' in status or '退还' in status or Decimal(get('成功退款(元)') or '0') != 0:
                    raise ValueError('退款需核对原交易，未自动入账')
                if status not in ('交易成功', '支付成功', '已收钱', '对方已收钱', '已转账', '转账成功', '已存入零钱', '已到账', '收款成功'):
                    raise ValueError('非成功交易，未入账')
                if get('币种', '货币') not in ('', 'CNY', '人民币', '元'):
                    raise ValueError('暂只支持人民币账单，不自动换算外币')
                raw = get('金额(元)', '金额').replace('¥', '').replace('￥', '').replace(',', '')
                amount = Decimal(raw)
                if not amount.is_finite() or amount <= 0 or amount * 100 != (amount * 100).to_integral_value() or amount * 100 > 9223372036854775807:
                    raise ValueError('金额必须为正数且最多两位小数')
                when = get('交易时间', '交易创建时间')
                try: stamp = datetime.strptime(when, '%Y-%m-%d %H:%M:%S').replace(tzinfo=ZoneInfo('Asia/Shanghai')).isoformat()
                except ValueError:
                    try:
                        serial = Decimal(when)
                        if not name.lower().endswith('.xlsx') or not serial.is_finite() or not 20000 < serial < 100000: raise ValueError()
                        from datetime import timedelta
                        stamp = (datetime(1899, 12, 30) + timedelta(days=float(serial))).replace(tzinfo=ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds')
                    except (InvalidOperation, ValueError): raise ValueError('交易时间无效')
                transaction = get('交易单号', '交易订单号', '交易号')
                if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', transaction): raise ValueError('交易单号缺失或格式无效')
                counterparty = get('交易对方')
                payload = {'name': get('商品说明', '商品', '商品名称', '交易类型') or counterparty or '交易', 'amount_minor': int(amount * 100), 'currency': 'CNY', 'entry_type': 'income' if direction == '收入' else 'expense', 'occurred_at': stamp, 'status': 'posted', 'category': get('交易分类', '交易类型'), 'counterparty': counterparty, 'txn_type': provider, 'note': ' · '.join(value for value in [('微信' if provider == 'wechat' else '支付宝') + '订单 ' + transaction, get('支付方式', '收/付款方式'), get('备注')] if value)}
                key = hashlib.sha256((provider + ':' + transaction).encode()).hexdigest()
                digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                parsed.append({'key': key, 'digest': digest, 'payload': payload, 'file': name, 'row': number})
            except (ValueError, InvalidOperation) as error:
                issues.append({'file': name, 'row': number, 'reason': str(error) if not isinstance(error, InvalidOperation) else '金额格式无效'})
    return parsed, issues


def import_files(service, files, dry_run=False):
    if type(dry_run) is not bool: raise ValueError('dry_run 必须为布尔值。')
    parsed, issues = parse_files(files)
    with service.store.transaction() as db:
        unique, duplicates = {}, 0
        for row in parsed:
            previous = db.execute('SELECT digest FROM bill_import_sources WHERE external_key=?', (row['key'],)).fetchone()
            digest = previous['digest'] if previous else unique.get(row['key'], {}).get('digest')
            if digest:
                if digest != row['digest']: raise ValueError('同一交易单号的内容已变化，已停止导入，请核对原记录。')
                duplicates += 1
            else: unique[row['key']] = row
        rows = list(unique.values())
        totals = {kind: sum(row['payload']['amount_minor'] for row in rows if row['payload']['entry_type'] == kind) for kind in ('income', 'expense')}
        if not dry_run:
            for row in rows:
                entry = service._build_ledger(row['payload'], existing=None)
                service.store.insert_ledger(entry)
                db.execute('INSERT INTO bill_import_sources VALUES (?,?,?)', (row['key'], row['digest'], entry['id']))
            for stamp, currency in {(row['payload']['occurred_at'][:7], row['payload']['currency']) for row in rows}:
                service._refresh_periods(stamp + '-01T00:00:00+08:00', currency)
        return {'dry_run': dry_run, 'imported': 0 if dry_run else len(rows), 'ready': len(rows), 'duplicates': duplicates, 'skipped': len(issues), 'issues': issues[:50], 'more_issues': max(0, len(issues) - 50), 'totals_minor': {key: str(value) for key, value in totals.items()}, 'currency': 'CNY'}
