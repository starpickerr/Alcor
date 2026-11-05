import test from 'node:test'
import assert from 'node:assert/strict'
import { readBillFiles } from '../../packages/stella-secretary/src/bill-files.js'

test('bill reader uses the DSH filesystem, honors its denials and caps reads before returning bytes', async () => {
  let reads = 0
  const fs = { resolve: async path => path, stat: async () => ({ type: 'file', size: 5 }), readBytes: async (_target, _signal, max) => { assert.equal(max, 5 * 1024 * 1024); reads++; return Buffer.from('bill') } }
  assert.equal((await readBillFiles(fs, ['/test/支付宝.csv']))[0].content, Buffer.from('bill').toString('base64'))
  await assert.rejects(readBillFiles(fs, ['/test/secret.txt']), /CSV/)
  assert.equal(reads, 1)
  fs.resolve = async () => { throw new Error('DSH denied') }
  await assert.rejects(readBillFiles(fs, ['/test/微信.xlsx']), /DSH denied/)
  fs.resolve = async path => path
  fs.stat = async () => ({ type: 'file', size: 6 * 1024 * 1024 })
  await assert.rejects(readBillFiles(fs, ['/test/微信.xlsx']), /5 MB/)
  assert.equal(reads, 1)
})
