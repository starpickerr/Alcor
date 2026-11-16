// Extend the native sidebar without disabling its module or redeclaring its children.
export function sidebarOwner(slots, Actions, BrandMark) {
  let releases=[],closed=false
  const clear=()=>{for(const release of releases.splice(0).reverse())release()}
  return {
    async select(alcor){
      if(!alcor || closed){clear();return}
      if(releases.length)return
      try{
        releases.push(slots.register({name:'sidebar.brand.mark',priority:-10},BrandMark))
        releases.push(slots.register({name:'sidebar.brand.name',priority:-10},()=> 'Alcor'))
        releases.push(slots.register({name:'sidebar.session.action',id:'alcor-navigation',priority:-10,locale:'sidebar'},Actions))
      }catch(error){clear();throw error}
    },
    async dispose(){closed=true;clear()},
  }
}
