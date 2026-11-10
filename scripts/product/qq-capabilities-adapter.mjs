// Channel transports consume the same profile-owned selections.
export function adaptQQCapabilities(file,text) {
  function replace(from,to) {
    if(text.split(from).length!==2)throw Error('Unexpected shared channel input in '+file+': '+from)
    text=text.replace(from,to)
  }
  if(file==='lib/index.js') {
    replace('return ctx.agentDefaultModel.currentSelection();','return ctx.alcorSecretary.channels.capabilities.main();')
    replace('const transcriber = new Transcriber({','const transcriber = new Transcriber({\n        shared: ctx.alcorSecretary.channels.capabilities,')
  }
  if(file==='lib/bridge.js') {
    replace("ref.kind === 'image' && !ref.file?.startsWith('file://')","(ref.kind === 'image' || ref.kind === 'voice') && !ref.file?.startsWith('file://')")
    replace('    ensureChat(chatId, nickname) {\n        return this.registry.ensureChat(chatId, nickname);',`    async ensureChat(chatId, nickname) {
        const chat=await this.registry.ensureChat(chatId,nickname);
        const selection=this.deps.ctx.get('alcorSecretary')?.channels.capabilities.main();
        if(selection)chat.agent.model.select(selection);
        return chat;`)
    replace("deps.ctx.get('alcorSecretary')?.createQQImageReader?.(question, admitted)",
      "deps.ctx.get('alcorSecretary')?.channels.capabilities.createImageReader(question,admitted)")
  }
  if(file==='lib/stt.js') {
    replace('return this.config.enabled;', 'return this.config.shared ? !!this.config.shared.selection().transcription : this.config.enabled;')
    replace('const ready = isSilkData(raw.subarray(0, 16)) ? await writeSilkWav(raw, wavPath) : false;', `let ready = false;
            if (raw.subarray(0,4).toString() === 'RIFF' && raw.subarray(8,12).toString() === 'WAVE') {
                await writeFile(wavPath,raw);
                ready = true;
            } else if (isSilkData(raw.subarray(0,16))) ready = await writeSilkWav(raw,wavPath);`)
    replace('            const engine = await this.resolveEngine();',`            if (this.config.shared) {
                return await this.config.shared.transcribe(await readFile(wavPath),AbortSignal.timeout(60000));
            }
            const engine = await this.resolveEngine();`)
  }
  if(file==='lib/inbound.js') {
    replace(`                if (this.ctx.transcriber.enabled) {
                    void this.transcribeLater(resolved.path, chatId);
                }
                return '[语音]';`, `                if (this.ctx.transcriber.enabled) {
                    try {
                        const text=await this.ctx.transcriber.transcribe(resolved.path);
                        return text ? '[语音转写：'+text.replaceAll('<','＜').replaceAll('>','＞')+']' : '[语音未识别出文字]';
                    } catch { return '[语音识别失败，无法确认内容]'; }
                }
                return '[语音未识别，尚未选择语音识别服务]';`)
  }
  return text
}
