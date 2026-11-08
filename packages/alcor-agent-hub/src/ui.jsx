import React from 'react'
export const showTime = value => new Date(value).toLocaleString('zh-CN')
export function Field({label,children}) { return <label className="alcor-field"><span>{label}</span>{['select','textarea'].includes(children?.type) ? React.cloneElement(children,{'aria-label':label}) : children}</label> }
export const markdownLabels = {code:{copyLabel:'复制',copiedLabel:'已复制',toolbarLabels:{codeLabel:'代码',wrapLabel:'自动换行',unwrapLabel:'取消换行'}},footnotes:'脚注'}
