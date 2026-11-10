// NapCat's message_seq is an opaque ID; real_seq determines chronological order.
export function platformHistory(data, before, account) {
  const rows=Array.isArray(data?.messages)?data.messages:[]
  return rows.flatMap(message=>{
    const cursor=Number(message.message_seq),seq=Number(message.real_seq ?? message.message_seq)
    if(!Number.isSafeInteger(seq) || !Number.isSafeInteger(cursor) || cursor===before)return []
    const segments=Array.isArray(message.message)?message.message:[]
    const text=segments.map(segment=>segment.type==='text'?String(segment.data?.text ?? ''):segment.type==='at'?'@'+String(segment.data?.qq ?? ''):({image:'[图片]',face:'[表情]',record:'[语音]',file:'[文件]',video:'[视频]',reply:'[引用]'}[segment.type] ?? '[消息]')).join('')
    if(!text)return []
    return [{seq,cursor,role:String(message.user_id)===account?'Alcor':'QQ '+String(message.user_id),text,
      time:Number(message.time)*1000,...(message.sender?.card || message.sender?.nickname?{nickname:String(message.sender.card || message.sender.nickname).slice(0,128)}:{})}]
  }).sort((a,b)=>a.seq-b.seq)
}
