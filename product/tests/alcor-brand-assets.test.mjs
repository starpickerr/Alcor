import { fileURLToPath } from 'node:url'
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { build } from 'esbuild'
const root=new URL('../../',import.meta.url)
const react={createElement:(type,props,...children)=>({type,props:props??{},children})}
async function load(file) {
  const result=await build({entryPoints:[fileURLToPath(new URL(file,root))],bundle:true,packages:'external',format:'cjs',platform:'node',loader:{'.svg':'dataurl'},write:false})
  const module={exports:{}};new Function('module','exports','require',result.outputFiles[0].text)(module,module.exports,name=>name==='react'?react:{})
  return module.exports
}
const asset=async name=>readFile(new URL(`packages/alcor-secretary/src/assets/${name}.svg`,root),'utf8')
function content(src) { const [header,body]=src.split(/,(.*)/s);assert.match(header,/^data:image\/svg\+xml/);return header.includes(';base64')?Buffer.from(body,'base64').toString():decodeURIComponent(body) }
test('shared avatar embeds only the tightly cropped transparent vector and preserves its requested size', async()=>{
  const svg=await asset('alcor-avatar');assert.match(svg,/<path /);assert.doesNotMatch(svg,/<(?:rect|image|script|foreignObject)\b|#fff\b|#ffffff\b/i)
  const {BearLogo}=await load('packages/alcor-secretary/src/bear-logo.jsx');for(const size of [20,22,30,32]){const logo=BearLogo({size});assert.equal(content(logo.props.src),svg);assert.equal(logo.props.width,size);assert.equal(logo.props.height,size);assert.equal(logo.props.alt,'');assert.equal(logo.props['aria-hidden'],'true')}
})
test('full mascot is decorative in empty states and loading or filtered views retain their original icon', async()=>{
  const {Empty}=await load('packages/alcor-secretary/src/business-pages.jsx');const svg=await asset('alcor-mascot');assert.doesNotMatch(svg,/<(?:rect|image|script|foreignObject)\b|#fff\b|#ffffff\b/i);const image=Empty({title:'还没有事务',mascot:true}).children[0];assert.equal(content(image.props.src),svg);assert.equal(image.props.alt,'');assert.equal(image.props.width,'124');const icon={type:'svg'};assert.equal(Empty({title:'筛选为空',icon}).children[0].children[0],icon);assert.equal(Empty({title:'加载'}).children[0],undefined)
})
