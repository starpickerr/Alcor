import test from 'node:test'
import assert from 'node:assert/strict'
import { money, periodRange, periodStatus, billValues, billPayload } from '../../packages/stella-secretary/src/bill-view.js'

test('bill display keeps exact large amounts, currency scales, periods and historical time precision', () => {
  assert.equal(money('9007199254741091', 'CNY'), '¥90,071,992,547,410.91')
  assert.equal(money('250', 'JPY'), 'JP¥250')
  assert.equal(money('-30', 'CNY'), '-¥0.30')
  assert.deepEqual(periodRange('2024', '02'), { start: '2024-02-01', end: '2024-02-29' })
  assert.equal(periodRange('2100', '02').end, '2100-02-28')
  assert.equal(periodStatus({ year: 2026, month: 9 }, '2026-11-06'), '已结束')
  assert.equal(periodStatus({ year: 2026, month: 11 }, '2026-11-06'), '进行中')
  const record = { title: '午餐', amount: '12.34', currency: 'CNY', direction: 'expense', details: '备注', business: { occurred_at: '2026-09-27T17:46:12.123+08:00', status: 'posted', category: '餐饮' } }
  const value = billValues(record)
  assert.equal(billPayload({ ...value, note: '新备注' }, record).occurred_at, record.business.occurred_at)
  assert.equal(billPayload({ ...value, occurred_at: '2026-09-28T00:01:00' }, record).occurred_at, '2026-09-28T00:01:00+08:00')
  assert.equal(billPayload({ ...value, amount: '28.01' }, record).amount_minor, 2801)
  assert.equal(billPayload({ ...value, currency: 'JPY', amount: '250' }, record).amount_minor, 250)
  assert.throws(() => billPayload({ ...value, amount: '0.001' }, record))
  assert.throws(() => billPayload({ ...value, amount: '900719925474109.12' }, record))
  const large = { ...record, amount: '900719925474109.12' }
  assert.equal('amount_minor' in billPayload(billValues(large), large), false)
})
