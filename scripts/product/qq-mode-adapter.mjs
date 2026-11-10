// Apply after the existing Alcor QQ integration, against the pinned gateway source.
export function adaptQQOwner(file,text) {
  function replace(from,to){
    if(text.split(from).length!==2)throw Error('Unexpected QQ owner adapter input: '+file+' '+from.slice(0,48))
    text=text.replace(from,to)
  }
  if(file==='lib/index.js'){
    replace('    alcorAutoQzone: z.boolean()', "    productScope: z.union([z.const('alcor'),z.const('dsh')]).default('alcor').description('QQ 唯一接管方'),\n    alcorAutoQzone: z.boolean()")
    replace("const mediaDir = config.mediaDir !== '' ? config.mediaDir : defaultMediaDir();","const baseDir = config.mediaDir !== '' ? config.mediaDir : defaultMediaDir();\n    const mediaDir = config.productScope==='dsh' ? join(baseDir,'workbench') : baseDir;")
    replace('            botQQ: config.botQQ,','            productScope: config.productScope,\n            botQQ: config.botQQ,')
    replace('workspacePath: config.workspacePath,',"workspacePath: config.productScope==='dsh' ? join(mediaDir,'workspace') : config.workspacePath,")
    replace('void bridge.handleInbound(event);',"void bridge.handleInbound(event).catch(()=>log('warn','QQ 入站未确认，将在同步历史时重试。'));")
    replace('            await bridge.start();',"            if (config.productScope === 'dsh') await ctx.alcorSecretary.qqCheckpoints.put(String(config.botQQ)+'/workbench',{since:Math.floor(Date.now()/1000),seen:{}});\n            await bridge.start();")
    replace('ctx.effect(() => installConversationInfo','if(config.productScope!==\'dsh\') ctx.effect(() => installConversationInfo')
  }
  if(file==='lib/settings-remote.js'){
    replace('const config = effectiveConfig(raw);',"const config = {...effectiveConfig(raw),productScope:raw.productScope ?? 'alcor'};")
  }
  if(file==='lib/client.js'){
    replace('mode: vEnum(["reverse", "forward"]),','productScope: vEnum(["alcor","dsh"]), mode: vEnum(["reverse", "forward"]),')
    replace('function OnebotSettingsPanel(props) {',`function AssignedSettings(props) {
      const [owner,setOwner]=React.useState(null);
      const mode=React.useSyncExternalStore(fn=>{
        window.addEventListener('alcor-mode-changed',fn);
        return ()=>window.removeEventListener('alcor-mode-changed',fn);
      },()=>globalThis[Symbol.for('@alcor/secretary.workspace-navigation')]?.mode?.getSnapshot() ?? 'dsh');
      React.useEffect(()=>{
        let active=true, generation=0;
        const read=async()=>{
          const current=++generation;
          try{const r=await props.remote.getSettings();
            if(active && current===generation)setOwner(unwrap(r).config.productScope);}
          catch{if(active && current===generation)setOwner(null)}
        };
        void read();window.addEventListener('alcor-channel-catalog-changed',read);
        return ()=>{active=false;window.removeEventListener('alcor-channel-catalog-changed',read)};
      },[props.remote]);
      if(!owner)return null;
      return mode===owner ? React.createElement(OnebotSettingsPanel,props) : React.createElement('p',{},'当前由 '+(owner==='alcor'?'Alcor':'DSH 工作台')+' 接管 QQ。');
    }
    function OnebotSettingsPanel(props) {`)
    replace('React.createElement(OnebotSettingsPanel, Object.assign','React.createElement(AssignedSettings, Object.assign')
  }
  if(file==='lib/registry.js'){
    text=text.replaceAll('sessionIdForChat(chatId)', 'this.sessionBase(chatId)')
    replace('    mappingPath() {',`    sessionBase(chatId) {
        const id=sessionIdForChat(chatId);
        return this.deps.config.productScope==='dsh' ? 'workbench-'+id : id;
    }
    mappingPath() {`)
    replace('    async resolvePresetId(chatId) {',"    async resolvePresetId(chatId) {\n        if(this.deps.config.productScope==='dsh')return 'qq-dsh-chat';")
    replace("if (strict && preferred !== GROUP_PRESET_ID)", "if (this.deps.config.productScope==='dsh') preferred='qq-dsh-chat';\n        if (strict && preferred !== (this.deps.config.productScope==='dsh'?'qq-dsh-chat':GROUP_PRESET_ID))")
    replace('        if (isGroupChat && sessionId !== groupSessionId', "        if (this.deps.config.productScope==='dsh' && sessionId !== groupSessionId && !sessionId.startsWith(groupSessionId+'-')) throw Error('QQ 会话归属不匹配');\n        if (isGroupChat && sessionId !== groupSessionId")
    replace('        if (chatId.startsWith(\'group:\'))\n            await mkdir(cwd', "        if (this.deps.config.productScope==='dsh' || chatId.startsWith('group:'))\n            await mkdir(cwd")
  }
  if(file==='lib/bridge.js'){
    replace("if (connected) { this.participation.reconnected();", "if (connected) { this.syncOffline?.(); this.participation.reconnected();")
    replace("        this.participation.stop();","        this.participation.stop();\n        const inboxStop = this.offlineInbox?.stop();")
    replace("        const failures = [];","        await inboxStop;\n        const failures = [];")
    replace("else this.inbound.bursts.clear();","else { this.offlineInbox?.disconnected(); this.inbound.bursts.clear(); }")
    replace("const run = () => this.handleInboundOnce(event, opts.injected === true);", "const handle = () => this.handleInboundOnce(event, opts.injected === true, opts.recovered === true);\n        const run = () => !opts.injected && this.offlineInbox ? this.offlineInbox.deliver(event,handle) : handle();")
    replace("async handleInboundOnce(event, injected)","async handleInboundOnce(event, injected, recovered)")
    replace("this.deps.log('error', 'inbound handling failed: ' + describeError(error) + errorStack(error));","this.deps.log('error', 'inbound handling failed: ' + describeError(error) + errorStack(error));\n            if (this.offlineInbox && !injected) {\n                this.inbound.seenMessages.delete((event.message_type === 'group' ? 'group:'+event.group_id : 'private:'+event.user_id)+'#'+event.message_id);\n                throw error;\n            }")

    replace("const memory=deps.ctx.get('alcorSecretary')?.memory;", "if(deps.config.productScope==='dsh')return 0;\n                    const memory=deps.ctx.get('alcorSecretary')?.memory;")
    replace('chooseGroupReply(deps.ctx,deps.defaultModel?.(),lines,signal,candidates,groupId,mode)',"chooseGroupReply(deps.ctx,deps.defaultModel?.(),lines,signal,candidates,groupId,mode,deps.config.productScope==='dsh')")
    replace('msg=>currentInjectRound() ? false', "msg=>deps.config.productScope==='dsh' || currentInjectRound() ? false")
    replace("personaName: () => this.deps.ctx.get", "personaName: () => deps.config.productScope==='dsh' ? 'DSH' : this.deps.ctx.get")
    replace("if (ids.length) {\n            const sessionId", "if (ids.length && this.deps.config.productScope!=='dsh') {\n            const sessionId")
    replace("source?.kind === 'plugin:dsh-onebot' && !chat.alcorLabelPending", "this.deps.config.productScope!=='dsh' && source?.kind === 'plugin:dsh-onebot' && !chat.alcorLabelPending")
    replace("reason.kind === 'blocked' && !this.deps.ctx", "reason.kind === 'blocked' && this.deps.config.productScope!=='dsh' && !this.deps.ctx")
    replace('    installChannelScope(agentCtx, chatId) {',`    installChannelScope(agentCtx, chatId) {
        if(this.deps.config.productScope==='dsh'){
            agentCtx.tools.restrict({allow:[]});
            agentCtx.tools.guard(()=> 'QQ 工作台通道仅接收文字对话。');
            agentCtx.on('agent/pre-step',({messages},next)=>messages.some(m=>m.source.kind!=='plugin:dsh-onebot') ? Promise.resolve({kind:'reject'}) : next(),{prepend:true});
            return;
        }`)
  }
  return text
}
