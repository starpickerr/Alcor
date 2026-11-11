const original='productTop:not(:empty)'
const adapted="productTop>[data-slot='shell.top']:not(:empty)"

export function adaptLayoutClient(client,reverse=false){
  const [from,to]=reverse?[adapted,original]:[original,adapted]
  if(client.split(from).length!==7 || client.includes(to))throw Error('Unexpected DSH product top selectors')
  return client.replaceAll(from,to)
}
