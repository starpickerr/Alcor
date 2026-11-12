import test from 'node:test'
import assert from 'node:assert/strict'
import {createServer} from 'node:http'
import {once} from 'node:events'
import {authenticate,forward} from '../client-shell/transport.mjs'

test('native transport binds each view to its own Host and withholds credentials from the page',async t=>{
  const server=createServer((req,res)=>{
    if(req.url==='/?token=synthetic'){res.writeHead(303,{'set-cookie':'owned-cookie=synthetic; HttpOnly; SameSite=Strict','location':'/'});res.end();return}
    res.writeHead(200,{'Content-Type':'application/json','Set-Cookie':'never=expose; HttpOnly','Cache-Control':'immutable'})
    res.end(JSON.stringify({path:req.url,cookie:req.headers.cookie,origin:req.headers.origin,host:req.headers.host}))
  }).listen(0,'127.0.0.1');await once(server,'listening');t.after(()=>new Promise(done=>server.close(done)))
  const host='http://127.0.0.1:'+server.address().port
  const cookie=await authenticate(host+'/?token=synthetic');assert.equal(cookie,'owned-cookie=synthetic')
  const response=await forward(new Request('dsh-app://app/plugins/??unchanged/client.js&rev=test',{headers:{Origin:'dsh-app://app',Cookie:'foreign=blocked'}}),host,cookie)
  assert.equal(response.headers.get('set-cookie'),null);assert.equal(response.headers.get('cache-control'),'no-store')
  const body=await response.json();assert.equal(body.cookie,cookie);assert.equal(body.path,'/plugins/??unchanged/client.js&rev=test');assert.equal(body.origin,undefined);assert.equal(body.host,'127.0.0.1:'+server.address().port)
  const denied=await forward(new Request('dsh-app://app/api',{headers:{Origin:'https://untrusted.invalid'}}),host,cookie);assert.equal(denied.status,403)
})
