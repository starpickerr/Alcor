import {build} from 'esbuild'
import {createHash} from 'node:crypto'
import {mkdir,readFile,writeFile,copyFile,rm,rename,access,readdir} from 'node:fs/promises'
import {spawn,execFileSync} from 'node:child_process'
import {join,resolve,dirname} from 'node:path'
import {fileURLToPath} from 'node:url'
import {parseArgs} from 'node:util'
import {officialDesktop,run} from './environments.mjs'
const repository=fileURLToPath(new URL('../../',import.meta.url))
const {values}=parseArgs({options:{out:{type:'string'},config:{type:'string'},app:{type:'string'}}})
if(!values.out || !values.config)throw Error('请指定 --out 和 --config。')
const out=resolve(values.out),stage=out+'.building',source=join(repository,'product/client-shell')
try{await access(out);throw Error('目标客户端已存在，原文件已保留。')}catch(error){if(error.code!=='ENOENT')throw error}
const config=JSON.parse(await readFile(values.config,'utf8')),desktop=await officialDesktop(values.app??config.officialApp)
await mkdir(dirname(out),{recursive:true});await mkdir(stage,{mode:0o700})
const contents=join(stage,'Contents'),resources=join(contents,'Resources'),bundle=out+'.bundle'
await mkdir(resources,{recursive:true});await mkdir(bundle,{mode:0o700})
for(const part of ['MacOS','Frameworks'])execFileSync('/bin/cp',['-RX',join(desktop.app,'Contents',part),join(contents,part)])
const metadata=JSON.parse(execFileSync('/usr/bin/plutil',['-convert','json','-o','-',join(desktop.app,'Contents/Info.plist')],{encoding:'utf8'}))
Object.assign(metadata,{CFBundleIdentifier:'com.alcor.client',CFBundleName:'Alcor',CFBundleDisplayName:'Alcor',CFBundleShortVersionString:'0.1.0',CFBundleVersion:'1',CFBundleIconFile:'Alcor.icns'})
await rename(join(contents,'MacOS',metadata.CFBundleExecutable),join(contents,'MacOS','Alcor'))
metadata.CFBundleExecutable='Alcor'
for(const file of await readdir(join(contents,'Frameworks'))){
  if(!file.startsWith('DeepSeek Harness Helper') || !file.endsWith('.app'))continue
  const old=join(contents,'Frameworks',file),target=join(contents,'Frameworks',file.replace('DeepSeek Harness','Alcor'))
  const info=JSON.parse(execFileSync('/usr/bin/plutil',['-convert','json','-o','-',join(old,'Contents/Info.plist')],{encoding:'utf8'}))
  const executable=info.CFBundleExecutable
  info.CFBundleExecutable=executable.replace('DeepSeek Harness','Alcor');info.CFBundleName=info.CFBundleExecutable;info.CFBundleDisplayName=info.CFBundleExecutable
  info.CFBundleIdentifier=info.CFBundleIdentifier.replace('com.deepseek.dsh','com.alcor.client')
  await rename(join(old,'Contents/MacOS',executable),join(old,'Contents/MacOS',info.CFBundleExecutable))
  await writeFile(join(old,'Contents/Info.plist'),JSON.stringify(info));execFileSync('/usr/bin/plutil',['-convert','xml1',join(old,'Contents/Info.plist')])
  await rename(old,target);execFileSync('/usr/bin/xattr',['-cr',target]);execFileSync('/usr/bin/codesign',['--force','--sign','-',target],{stdio:'pipe'})
}
delete metadata.ElectronAsarIntegrity;delete metadata.LSUIElement;delete metadata.CFBundleURLTypes
await writeFile(join(contents,'Info.plist'),JSON.stringify(metadata));execFileSync('/usr/bin/plutil',['-convert','xml1',join(contents,'Info.plist')])
await copyFile(join(source,'icon.icns'),join(resources,'Alcor.icns'))
await build({entryPoints:[join(source,'main.mjs')],outfile:join(bundle,'main.mjs'),bundle:true,platform:'node',format:'esm',target:'node24',external:['electron'],banner:{js:"import {createRequire as __createRequire} from 'node:module';const require=__createRequire(import.meta.url);"}})
for(const file of ['preload.cjs','content-preload.cjs','index.html','shell.css','shell.js'])await copyFile(join(source,file),join(bundle,file))
await copyFile(join(repository,'packages/alcor-secretary/src/assets/alcor-avatar.svg'),join(bundle,'bear.svg'))
execFileSync(desktop.binary,['-e',`require('fs').copyFileSync(${JSON.stringify(join(desktop.app,'Contents/Resources/app.asar/dsh/node_modules/@deepseek-ai/dsh-web-frontend/dist/favicon.svg'))},${JSON.stringify(join(bundle,'fish.svg'))})`],{env:{...process.env,ELECTRON_RUN_AS_NODE:'1'}})
await copyFile(join(repository,'packages/alcor-secretary/DSH-LICENSE.txt'),join(bundle,'DSH-LICENSE.txt'))
const pkg=JSON.parse(await readFile(join(repository,'packages/alcor-secretary/package.json')))
await copyFile(join(repository,`.local/artifacts/alcor-secretary-${pkg.version}.tgz`),join(bundle,'alcor-secretary.tgz'))
await writeFile(join(bundle,'config.json'),JSON.stringify(config,null,2)+'\n',{mode:0o600})
await writeFile(join(bundle,'package.json'),JSON.stringify({name:'alcor-client',version:'0.1.0',private:true,type:'module',main:'main.mjs',license:'MIT'}))
await run('npm',['exec','--yes','--package=@electron/asar@4.0.1','--','asar','pack',bundle,join(resources,'app.asar')],{cwd:repository,env:process.env})
const archive=await readFile(join(resources,'app.asar'))
metadata.ElectronAsarIntegrity={'Resources/app.asar':{algorithm:'SHA256',hash:createHash('sha256').update(archive.subarray(16,16+archive.readUInt32LE(12))).digest('hex')}}
await writeFile(join(contents,'Info.plist'),JSON.stringify(metadata));execFileSync('/usr/bin/plutil',['-convert','xml1',join(contents,'Info.plist')])
// This is an Alcor local build. It never impersonates the official DSH signature.
execFileSync('/usr/bin/xattr',['-cr',stage])
execFileSync('/usr/bin/codesign',['--force','--sign','-',stage],{stdio:'pipe'})
execFileSync('/usr/bin/codesign',['--verify','--deep','--strict',stage],{stdio:'pipe'})
await rename(stage,out);await rm(bundle,{recursive:true})
console.log('Built Alcor client:',out)
