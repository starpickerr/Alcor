export function sidebarOwner(loader, register) {
  const entry=[...loader.entries()].find(row=>row.options.name==='@deepseek-ai/dsh-client-ui-sidebar')
  if(!entry || entry.options.disabled)throw Error('Official sidebar is not available for a reversible mode transition')
  let release,queue=Promise.resolve(),closed=false
  const select=alcor=>{
    const work=queue.catch(()=>{}).then(async()=>{
      if(alcor && !closed && !release){
        await loader.update(entry.id,{disabled:true})
        try{release=register()}
        catch(error){await loader.update(entry.id,{disabled:false});throw error}
      }else if((!alcor || closed) && release){
        release();release=undefined
        await loader.update(entry.id,{disabled:false})
      }
    })
    queue=work
    return work
  }
  return {select,dispose(){closed=true;return select(false)}}
}
