const sentences = new Intl.Segmenter('zh', {granularity:'sentence'})

export function splitQQReply(text) {
  const blocks=[]
  let lines=[],fence=''
  for(const line of text.trim().split(/\r?\n/)) {
    const marker=/^ {0,3}(`{3,}|~{3,})/.exec(line)?.[1]
    if(marker) {
      if(!fence)fence=marker
      else if(marker[0]===fence[0] && marker.length>=fence.length)fence=''
    }
    if(!line.trim() && !fence) {
      if(lines.length)blocks.push(lines.join('\n').trim())
      lines=[]
    } else lines.push(line)
  }
  if(lines.length)blocks.push(lines.join('\n').trim())
  const parts=[]
  for(const block of blocks.filter(Boolean)) {
    // Keep structured explanations and URL/inline-code paragraphs intact.
    if(block.length<=120 || /https?:\/\/|`[^`\n]+`/.test(block) ||
       /^ {0,3}(?:`{3,}|~{3,}|[-*+]\s|\d+[.)、]\s|#{1,6}\s|>|\|)/m.test(block)) {
      parts.push(block)
      continue
    }
    let part=''
    for(const {segment} of sentences.segment(block)) {
      if(part && part.length+segment.length>120) {
        parts.push(part.trim())
        part=''
      }
      part+=segment
    }
    if(part.trim())parts.push(part.trim())
  }
  // Bound a single turn's message count without dropping the remainder.
  return parts.length>6 ? [...parts.slice(0,5),parts.slice(5).join('\n\n')] : parts
}

export function replyDelay(text) {
  return Math.min(1600, Math.max(450, Array.from(text).length * 18))
}
