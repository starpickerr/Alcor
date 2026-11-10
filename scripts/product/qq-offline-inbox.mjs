// NapCat pages include their anchor. real_seq orders equal-second messages; message_seq is the paging cursor.
async function missedMessages(call, kind, peer, since, active) {
  const rows = new Map(), cursors = new Set()
  let cursor
  while (active()) {
    const result = await call(kind === 'group' ? 'get_group_msg_history' : 'get_friend_msg_history', {
      [kind === 'group' ? 'group_id' : 'user_id']: Number(peer), count:80, reverseOrder:true,
      ...(cursor == null ? {} : {message_seq:cursor}),
    })
    if (!Array.isArray(result?.messages)) throw Error('平台未返回历史记录')
    const page = result.messages.slice().sort((a,b) => a.time-b.time || Number(a.real_seq ?? 0)-Number(b.real_seq ?? 0))
    for (const row of page) {
      if (!Number.isFinite(Number(row.time)) || row.message_id == null) throw Error('平台记录缺少时间或消息编号')
      if (row.time >= since) rows.set(String(row.message_id),row)
    }
    if (page.length < 80 || page[0].time < since) return [...rows.values()].sort((a,b) => a.time-b.time || Number(a.real_seq ?? 0)-Number(b.real_seq ?? 0))
    cursor = page[0].message_seq ?? page[0].message_id
    if (cursors.has(cursor)) throw Error('平台历史翻页停滞')
    cursors.add(cursor)
  }
  throw Error('通道已断开')
}

export function createOfflineInbox({account, table, peers, online, call, receive, log, now = Date.now}) {
  const seconds = () => Math.floor(now()/1000)
  const pending = new Map(), dirty = new Map()
  const key = chat => account+'/'+chat
  const started = seconds()
  let stopped = false, scan, generation = 0
  function update(chat, change) {
    const task = (dirty.get(chat) ?? Promise.resolve()).catch(()=>{}).then(async () => {
      const old = table.get(key(chat)) ?? {since:started,seen:{}}
      await table.put(key(chat),change(old))
    })
    dirty.set(chat,task)
    return task
  }
  const ready = (async () => {
    const reset = !!table.get(key('workbench'))
    const allowed = new Set(peers().map(key))
    for (const [entry] of table.entries()) {
      if (entry.startsWith(account+'/') && (reset || !allowed.has(entry)) && entry!==key('workbench')) await table.delete(entry)
    }
    await Promise.all(peers().map(chat=>update(chat,old=>old)))
    if (reset) await table.delete(key('workbench'))
  })()
  async function deliver(event, run) {
    const chat = event.message_type+':'+(event.message_type==='group' ? event.group_id : event.user_id)
    if (!peers().includes(chat) || String(event.user_id)===account || event.message_id==null) return run()
    await ready
    const id = String(event.message_id), ticket = chat+'/'+id
    if (pending.has(ticket)) return pending.get(ticket)
    const state = table.get(key(chat))
    if (Object.hasOwn(state?.seen ?? {},id)) return
    const task = Promise.resolve().then(run).then(() => {
      if (!stopped && online()) return update(chat,old=>({...old,seen:{...old.seen,[id]:Number(event.time) || seconds()}}))
    }).finally(()=>pending.delete(ticket))
    pending.set(ticket,task)
    return task
  }
  async function recover() {
    const turn = generation
    const active = () => !stopped && online() && generation===turn
    await ready
    if (!active()) return
    const who = await call('get_login_info',{})
    if (String(who?.user_id)!==account || !active()) return
    for (const chat of peers()) {
      if (!active()) return
      const [kind,peer] = chat.split(':')
      const start = seconds(), state = table.get(key(chat))
      try {
        const rows = await missedMessages(call,kind,peer,state?.since ?? started,active)
        let batch = [], lastSender, lastTime = 0
        const flush = async () => {
          const group = batch
          batch = []
          await Promise.all(group.map(event=>receive(event)))
        }
        for (const row of rows) {
          if (!active() || !peers().includes(chat)) return
          const sender = String(row.sender?.user_id ?? row.user_id)
          if (sender===account) { await flush(); continue }
          if (!/^\d+$/.test(sender) || (row.self_id != null && String(row.self_id)!==account) ||
              (row.message_type && row.message_type!==kind) || (kind==='private' ? sender!==peer : String(row.group_id)!==peer)) continue
          const text = Array.isArray(row.message) ? row.message.filter(s=>s.type==='text').map(s=>s.data?.text).join('') : String(row.raw_message ?? row.message ?? '')
          // Administrative commands are live actions. Do not run old resets or mute commands after restart.
          if (/^\s*\//.test(text)) continue
          const plain = Array.isArray(row.message) && row.message.every(s=>s.type==='text' || s.type==='at')
          if (!plain || lastSender!==sender || row.time-lastTime>8 || batch.length>=12) await flush()
          batch.push({...row,post_type:'message',message_type:kind,self_id:account,user_id:sender})
          lastSender=sender
          lastTime=row.time
          if (!plain) await flush()
        }
        await flush()
        if (active() && rows.length) await update(chat,old=>{
          const since = Math.max(old.since,Math.min(start,Number(rows.at(-1).time))-30)
          return {since,seen:Object.fromEntries(Object.entries(old.seen).filter(([,at])=>at>=since))}
        })
      } catch (error) { if (active()) log('warn','QQ 补读未完成，将重试：'+error.message) }
    }
  }
  function sync() {
    if (stopped || !online()) return
    if (!scan) scan = recover().catch(error=>log('warn','QQ 历史暂不可用：'+error.message)).finally(()=>{scan=undefined})
    return scan
  }
  return {deliver, sync, disconnected(){generation++}, async stop(){stopped=true;generation++;await Promise.allSettled([ready,scan,...pending.values(),...dirty.values()])}, ready}
}
