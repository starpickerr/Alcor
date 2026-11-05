"""Synthetic platform exports for native tool/ledger tests; no personal data."""
import base64
import csv
import io
import json
import zipfile
from xml.sax.saxutils import escape


def files():
    header = ['交易时间', '交易类型', '交易对方', '商品', '收/支', '金额(元)', '支付方式', '当前状态', '交易单号', '商户单号', '备注']
    rows = [['微信支付账单明细'], header,
            ['2026-11-03 12:00:00', '商户消费', '合成商户', '午餐', '支出', '25.01', '零钱', '支付成功', '900719925474099312345', '', ''],
            ['2026-11-03 14:00:00', '转账', '合成用户', '转账', '收入', '10.00', '零钱', '已收钱', 'wx-income', '', '']]
    xml = ['<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>']
    for number, row in enumerate(rows, 1):
        xml.append(f'<row r="{number}">')
        for index, value in enumerate(row):
            col = chr(65 + index)
            xml.append(f'<c r="{col}{number}" t="inlineStr"><is><t>{escape(value)}</t></is></c>')
        xml.append('</row>')
    xml.append('</sheetData></worksheet>')
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('xl/worksheets/sheet1.xml', ''.join(xml))
    csv_text = io.StringIO(newline='')
    writer = csv.writer(csv_text)
    writer.writerow(['支付宝交易明细'])
    writer.writerow(['交易时间', '交易分类', '交易对方', '商品说明', '收/支', '金额', '交易状态', '交易订单号', '备注'])
    writer.writerow(['2026-11-02 18:00:00', '交通', '合成商户', '车票,返程', '支出', '8.80', '交易成功', 'ali-one', '合成备注'])
    return [{'name': '微信支付.xlsx', 'content': base64.b64encode(out.getvalue()).decode()},
            {'name': '支付宝明细.csv', 'content': base64.b64encode(csv_text.getvalue().encode('gb18030')).decode()}]


if __name__ == '__main__':
    print(json.dumps(files()))
