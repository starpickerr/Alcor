import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync, readdirSync} from 'node:fs'

const root = new URL('../../assets/desktop-pet/', import.meta.url)
const read = name => JSON.parse(readFileSync(new URL(name,root),'utf8'))
test('Independent pet pack resolves every animation and work state', () => {
  const config=read('alcor-bear-config.json'), manifest=read('alcor-bear-manifest.json')
  const names=new Set(readdirSync(new URL('alcor-bear-animation/',root)).map(file=>file.replace(/\.webm$/,'')))
  assert.equal(names.size,9)
  for (const name of [...Object.values(manifest), ...Object.values(config.animations).flat(), ...Object.values(config.events.workStatus)]) {
    assert.ok(names.has(name),name)
    assert.equal(readFileSync(new URL(`alcor-bear-animation/${name}.webm`,root)).subarray(0,4).toString('hex'),'1a45dfa3')
  }
  assert.deepEqual(Object.keys(config.events.workStatus).sort(),['error','result','success','thinking','waiting','working'])
})
