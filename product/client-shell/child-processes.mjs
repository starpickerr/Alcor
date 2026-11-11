import {execFile} from 'node:child_process'
import {promisify} from 'node:util'
const execute=promisify(execFile)

async function processes(){
  const {stdout}=await execute('/bin/ps',['-axo','pid=,ppid=,lstart=,stat=,command='])
  return stdout.split('\n').flatMap(line=>{
    const match=line.match(/^\s*(\d+)\s+(\d+)\s+((?:\S+\s+){4}\S+)\s+(\S+)\s+(.*)$/)
    return match && !match[4].startsWith('Z')?[{pid:Number(match[1]),parent:Number(match[2]),identity:match[3]+' '+match[5]}]:[]
  })
}

export async function ownedChildren(parent){
  // shortcut: reparented daemons need explicit supervision when a plugin starts using them.
  const rows=await processes(),parents=new Set([parent]),children=[]
  for(let changed=true;changed;){
    changed=false
    for(const row of rows)if(parents.has(row.parent) && !parents.has(row.pid)){parents.add(row.pid);children.push(row);changed=true}
  }
  return children
}

export async function stopChildren(children){
  if(!children.length)return
  const remaining=async()=>{
    const live=new Map((await processes()).map(row=>[row.pid,row.identity]))
    return children.filter(row=>live.get(row.pid)===row.identity)
  }
  const signal=async name=>{for(const row of await remaining())try{process.kill(row.pid,name)}catch(error){if(error.code!=='ESRCH')throw error}}
  await signal('SIGTERM')
  for(let i=0;i<20 && (await remaining()).length;i++)await new Promise(done=>setTimeout(done,100))
  await signal('SIGKILL')
  for(let i=0;i<20 && (await remaining()).length;i++)await new Promise(done=>setTimeout(done,100))
  if((await remaining()).length)throw Error('Alcor 的后台进程尚未停止，请重试关闭。')
}
