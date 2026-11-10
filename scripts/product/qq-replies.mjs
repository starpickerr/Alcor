// Aurabot's explicit pause selects the bubbles; paragraphs and literal code keep their formatting.
export function splitQQReply(text) {
  const parts = []
  let buffer = '', code = ''
  for (const token of text.split(/(`+|~{3,}|\|\|\|)/)) {
    if (/^(?:`+|~{3,})$/.test(token)) {
      if (!code) code = token
      else if (code[0] === token[0] && code.length === token.length) code = ''
    }
    if (token === '|||' && !code) {
      if (buffer.trim()) parts.push(buffer.trim())
      buffer = ''
    } else buffer += token
  }
  if (buffer.trim()) parts.push(buffer.trim())
  return parts
}

export function replyDelay(text) {
  return Math.min(1600, Math.max(450, Array.from(text).length * 18))
}
