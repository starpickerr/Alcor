// Display transport envelopes as ordinary messages; this never establishes identity or permission.
export function historyMessages(records, privateNumber) {
  return records.flatMap(record => {
    const event = record.type === 'event' ? record.event : record
    if (!['user/message', 'assistant/message'].includes(event.type)) return []
    const incoming = event.type === 'user/message'
    const message = incoming ? event.data : event.data.message
    if (incoming && message.source?.kind !== 'plugin:dsh-onebot') return []
    let text = (Array.isArray(message.content) ? message.content : []).filter(part => part.type === 'text').map(part => part.text).join('\n')
    const senderId=incoming ? message.source.qqUserId || privateNumber : undefined
    let nickname
    if (incoming) {
      const envelope = /<user_message\b[^>]*>\s*\n?([\s\S]*)\n?\s*<\/user_message>\s*$/.exec(text)
      if (envelope) {
        const header=/^<user_message qq="([1-9]\d{0,19})" nickname="([^"<>]{0,128})">/.exec(envelope[0])
        if (header && header[1]===senderId) nickname=header[2]
        text = envelope[1].trim()
        if ((message.source.qqGroupContext || message.source.qqAmbient) && text.startsWith('近期本群发言（背景资料）：')) {
          const boundary=text.indexOf('\n当前发言：')
          if (boundary>=0) text=text.slice(boundary+'\n当前发言：'.length)
        }
      }
    }
    const role = incoming ? senderId ? 'QQ ' + senderId : 'QQ' : 'Alcor'
    return text ? [{ seq: event.seq, role, text, ...(nickname ? {nickname} : {}), ...(message.source?.qqAmbient ? {ambient:true} : {}), ...(Number.isFinite(event.time) ? {time:event.time} : {}) }] : []
  })
}
