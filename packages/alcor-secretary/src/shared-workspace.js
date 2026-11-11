// Alcor retains separate navigation/list scroll regions; the native list scrolls itself.
const sectionScroller={bindScrollerRef(){}}
export const sharedWorkspaceContext={useSectionScroller:()=>sectionScroller}
export const sharedWorkspaceSlot={kind:'single',scope:'root',inject:{hooks:{
  sectionScroller:(_standard,context)=>context?.useSectionScroller ?? sharedWorkspaceContext.useSectionScroller,
}}}
