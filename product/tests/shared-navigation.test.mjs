import {test} from 'node:test'
import assert from 'node:assert/strict'
import {navigationGroups} from '../../packages/alcor-secretary/src/shared-navigation.js'

test('shared navigation keeps business apps separate and uses native plugin destinations', () => {
  const applications=[{id:'alcor-tasks',label:'事务'}]
  const entries=[{options:{id:'plugins',label:()=> '插件'}},{options:{id:'aurabot',label:()=> 'AuraBot'}},{options:{id:'alcor-tasks',label:'事务'}}]
  const groups=navigationGroups(applications,entries)
  assert.equal(groups[0].label,'应用')
  assert.strictEqual(groups[0].items,applications)
  assert.deepEqual(groups[1],{label:'系统',items:[{id:'plugins',label:'插件'},{id:'aurabot',label:'AuraBot'}]})
  assert.equal(groups[0].items.some(row=>row.id==='aurabot'),false)
  assert.deepEqual(navigationGroups(applications,entries.slice(0,1))[1].items,[{id:'plugins',label:'插件'}])
})
