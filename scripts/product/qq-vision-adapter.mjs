// Extend the pinned inbound bridge after its normal QQ admission and command handling.
export function adaptQQVision(file, text) {
  function replace(from, to) {
    if (text.split(from).length !== 2) throw Error('Unexpected QQ vision input in '+file+': '+from)
    text = text.replace(from,to)
  }
  if (file === 'lib/bridge.js') {
    replace('media: [], forwardId: undefined', "media: this.deps.config.productScope === 'dsh' ? [] : inbound.media.filter(ref => ref.kind === 'image' && !ref.file?.startsWith('file://')), forwardId: undefined")
    replace('            buildBody: (text, media, chatId) => this.buildBody(text, media, chatId),', `            createImageReader: (question, admitted) => deps.config.productScope === 'dsh' ? undefined :
                deps.ctx.get('alcorSecretary')?.createQQImageReader?.(question, admitted),
            buildBody: (...args) => this.buildBody(...args),`)
    for (const method of ['buildBody','expandQuote']) {
      const params = method === 'buildBody' ? 'text, media, chatId' : 'messageId, verifiedData'
      replace(method+'('+params+') {\n        return this.inbound.'+method+'('+params+');',
        method+'(...args) {\n        return this.inbound.'+method+'(...args);')
    }
    replace('expandQuote: (messageId, verifiedData) => this.expandQuote(messageId, verifiedData)', 'expandQuote: (...args) => this.expandQuote(...args)')
  }
  if (file === 'lib/inbound.js') {
    replace('        if (this.rateLimited(chatId))\n            return;','')
    replace('        const body = await this.ctx.buildBody(inbound.text, inbound.media, chatId);', `        if (this.rateLimited(chatId)) return;
        const admitted = () => !silent() && !this.ctx.unavailable?.() &&
            (kind === 'private' ? dmAllowed(userId,this.ctx.policy) : groupAllowed(groupId,this.ctx.policy));
        const readImage = this.ctx.createImageReader?.(inbound.text, admitted);
        const body = await this.ctx.buildBody(inbound.text, inbound.media, chatId, readImage);`)
    replace('this.ctx.buildBody(expansion.text, expansion.media, chatId)', 'this.ctx.buildBody(expansion.text, expansion.media, chatId, readImage)')
    replace('this.ctx.expandQuote(inbound.replyId, verifiedQuote)', 'this.ctx.expandQuote(inbound.replyId, verifiedQuote, chatId, readImage)')
    replace('async expandQuote(messageId, verifiedData)', 'async expandQuote(messageId, verifiedData, chatId, readImage)')
    replace('const text = cqUnescape(segmentText(segments, raw));', `let text = cqUnescape(segmentText(segments, raw));
            const sameChat = data.message_type === 'group' ? chatId === 'group:'+data.group_id :
                data.message_type === 'private' && chatId === 'private:'+data.user_id;
            if (readImage && sameChat) {
                const quoted = parseMessage(segments, raw);
                if (quoted.media.some(ref => ref.kind === 'image')) {
                    text = await this.buildBody(quoted.text, quoted.media.filter(ref => ref.kind === 'image'),chatId,readImage,true);
                }
            }`)
    replace('async buildBody(text, media, chatId)', 'async buildBody(text, media, chatId, readImage, quoted = false)')
    replace('let out = text;','let out = text;\n        let cursor = 0;')
    replace('const idx = out.indexOf(placeholder);','const idx = out.indexOf(placeholder,cursor);')
    replace('const annotation = await this.resolveMediaRef(ref, chatId);','const annotation = await this.resolveMediaRef(ref, chatId, readImage, quoted);')
    replace('out = out.slice(0, idx) + annotation + out.slice(idx + placeholder.length);',
      'out = out.slice(0, idx) + annotation + out.slice(idx + placeholder.length);\n                cursor = idx + annotation.length;')
    replace('async resolveMediaRef(ref, chatId)', 'async resolveMediaRef(ref, chatId, readImage, quoted = false)')
    replace('return { url: data.url, file: data.file };', "return data.base64 ? {file:'base64://'+data.base64} : { url: data.url, file: data.file };")
    replace("if (resolved === undefined)\n            return '';","if (resolved === undefined)\n            return readImage && ref.kind === 'image' ? '[图片未能读取]' : '';")
    replace('settings.lastImagePath = resolved.path;\n                settings.pendingImageRef = undefined;', `if (!quoted) {
                    settings.lastImagePath = resolved.path;
                    settings.pendingImageRef = undefined;
                }
                if (readImage) return (ref.stickerLabel ? '[表情包识别：' : '[图片识别：') + await readImage(resolved.path) + ']';`)
  }
  if (file === 'lib/prompt.js') {
    replace('如需查看图片内容，请使用当前可用的图像查看/描述工具', '若消息已附图片识别结果，结合结果和对方的问题自然回复；结果为未识别或失败时不要猜测图中内容。如需查看仅有路径的图片，请使用当前可用的图像查看/描述工具')
  }
  return text
}
