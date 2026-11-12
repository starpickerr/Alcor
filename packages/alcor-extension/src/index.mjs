export function apply() {
  if(process.env.ALCOR_CLIENT_SHELL!=='1' || process.env.ALCOR_APPLICATION!=='dsh')throw Error('请在 Alcor 客户端的 DSH 插件页管理 Alcor。')
  // The client follows the native bundle selection, including when Host HMR is unavailable.
}
