// MIT derivative of DeepSeek Harness Desktop web-protocol.ts (0.2.2-rc.1).
const withheld=['set-cookie','content-encoding','content-length','transfer-encoding','connection','keep-alive','te','trailer','upgrade','proxy-authenticate','proxy-authorization']
export async function authenticate(url){
  const response=await fetch(url,{redirect:'manual'})
  const cookie=response.headers.get('set-cookie');await response.body?.cancel()
  if(response.status!==303 || !cookie)throw Error('本地运行环境认证失败。')
  return cookie.split(';',1)[0]
}
export async function forward(request,host,cookie){
  const source=new URL(request.url),origin=request.headers.get('origin')
  if(origin!==null && origin!=='dsh-app://app')return new Response(null,{status:403})
  const target=new URL(host);target.pathname=source.pathname;target.search=source.search
  const headers=new Headers(request.headers)
  for(const name of ['host','origin','cookie','sec-fetch-site'])headers.delete(name)
  headers.set('cookie',cookie)
  const response=await fetch(target,{method:request.method,headers,body:request.body,signal:request.signal,duplex:'half',redirect:'manual'})
  const outgoing=new Headers(response.headers);for(const name of withheld)outgoing.delete(name)
  if(source.pathname.startsWith('/plugins/'))outgoing.set('cache-control','no-store')
  return new Response(response.body,{status:response.status,headers:outgoing})
}
