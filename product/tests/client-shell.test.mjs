import test from 'node:test'
import assert from 'node:assert/strict'
import {hostUrl} from '../client-shell/host.mjs'

test('the shell accepts only an authenticated local Host origin',()=>{
  assert.equal(hostUrl('http://127.0.0.1:12345/?token=synthetic'),'http://127.0.0.1:12345/?token=synthetic')
  for(const url of ['http://evil.invalid:12345/?token=x','file:///etc/passwd','http://127.0.0.1:12345/','http://user:pass@127.0.0.1:12345/?token=x','https://127.0.0.1:12345/?token=x','http://127.0.0.1:12345/api?token=x'])assert.throws(()=>hostUrl(url))
})
