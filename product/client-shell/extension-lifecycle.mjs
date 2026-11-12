export function extensionSelected(manifest){
  const bundles=manifest.dsh?.profile?.bundles??[]
  if(!Array.isArray(bundles) || bundles.some(name=>typeof name!=='string'))throw Error('DSH 插件配置无效。')
  return typeof manifest.dependencies?.['@alcor/extension']==='string' && bundles.includes('@alcor/extension')
}

export function extensionLifecycle({start,stop,selectWorkbench,changed=()=>{}}) {
  let desired=false,enabled=false,phase='disabled',queue=Promise.resolve(),closed=false,version=0,interruption=0,interrupt=Promise.resolve()
  const snapshot=()=>({enabled,desired,phase})
  const setEnabled=value=>{
    if(typeof value!=='boolean')throw Error('Alcor 启停状态无效。')
    if(closed)return Promise.resolve(snapshot())
    desired=value;version++
    if(!value && phase==='starting'){interruption++;interrupt=Promise.resolve().then(stop);interrupt.catch(()=>{})}
    const update=queue.then(async()=>{
      while(!closed && (desired!==enabled || phase==='failed')){
        const next=desired,attempt=version,epoch=interruption
        phase=next?'starting':'stopping';changed(snapshot())
        try{
          if(next){
            await start();await interrupt
            if(epoch!==interruption || closed){if(!desired && !closed)await selectWorkbench();await stop();enabled=false;phase='disabled';changed(snapshot());continue}
            enabled=true;phase='ready'
          }
          else{await selectWorkbench();await stop();enabled=false;phase='disabled'}
        }catch(error){
          if(next && (attempt!==version || closed)){
            try{if(!closed)await selectWorkbench();await interrupt;await stop();enabled=false;phase='disabled';changed(snapshot());continue}
            catch(cleanup){phase='failed';changed(snapshot());throw cleanup}
          }
          phase='failed';changed(snapshot());throw error
        }
        changed(snapshot())
      }
      return snapshot()
    })
    queue=update.catch(()=>{})
    return update
  }
  return {snapshot,setEnabled,invalidate(){if(!closed){phase='failed';changed(snapshot())}},async close(){closed=true;desired=false;phase='stopping';changed(snapshot());await stop();await queue;await stop();enabled=false;phase='disabled';changed(snapshot())}}
}
