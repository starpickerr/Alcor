import test from 'node:test'
import assert from 'node:assert/strict'
import {pluginScope} from '../../packages/alcor-plugin-info/src/scope.js'

test('product scope follows actual integration and preserves unknown variants', () => {
  assert.equal(pluginScope('@alcor/secretary')[0],'Alcor')
  assert.equal(pluginScope('@alcor/agent-hub')[0],'DSH 工作台')
  assert.equal(pluginScope('@alcor/mail')[0],'两边共用')
  assert.match(pluginScope('dsh-onebot-qq',{version:'0.9.19-alcor.18'})[1],/已连接/)
  assert.equal(pluginScope('dsh-onebot-qq',{version:'0.9.19'})[0],'范围未声明')
  assert.equal(pluginScope('@alcor/unknown')[0],'范围未声明')
  assert.equal(pluginScope('shell')[0],'范围未声明')
  assert.equal(pluginScope('shell',{item:true})[0],'两边共用')
})
