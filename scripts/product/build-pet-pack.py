"""Render the existing Alcor vector mascot as a standard, independent AuraBot pet pack."""
import argparse
import base64
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
DEST = ROOT / 'assets/desktop-pet'
STATES = ['idle', 'speak', 'click', 'thinking', 'working', 'result', 'waiting', 'success', 'error']
FPS, FRAMES = 24, 48


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--ffmpeg', default=shutil.which('ffmpeg'))
    args = parser.parse_args()
    if not args.ffmpeg:
        parser.error('Provide an FFmpeg executable with libvpx-vp9 support using --ffmpeg')
    original = (ROOT / 'packages/alcor-secretary/src/assets/alcor-mascot.svg').read_text()
    outline = re.search(r' d="([^"]+)"', original).group(1)
    outer = outline.split('Z', 1)[0] + 'Z'
    scarf = re.search(r'M301\.0 377\.1.*?Z', outline).group(0)
    star = '420,322 423,329 431,330 425,335 426,343 420,339 414,343 415,335 409,330 417,329'
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="83 77 465 453">
<title>Alcor bear with round glasses, a blue scarf and a gold star</title>
<path fill="#fff7e9" d="M265 345 Q233 395 238 425 L259 437 L256 493 Q252 520 284 516 L304 510 L306 492 Q345 508 387 492 L388 514 Q411 527 435 505 L435 448 L456 431 L448 379 L490 366 L495 336 L480 331 L437 348 Z"/>
<path fill="#fff7e9" d="{outer}"/>
<path fill="#91acb0" d="M388 356 Q418 348 426 359 L445 383 L461 435 L393 440 Q378 409 388 356Z"/>
<path fill="#91acb0" d="{scarf}"/>
<path fill="#303332" fill-rule="evenodd" d="{outline}"/>
<polygon points="{star}" fill="#e3b75c" stroke="#6d6249" stroke-width="2" stroke-linejoin="round"/>
</svg>'''
    DEST.mkdir(parents=True, exist_ok=True)
    (DEST / 'alcor-bear.svg').write_text(svg)
    animations = DEST / 'alcor-bear-animation'
    animations.mkdir(exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width':512,'height':512})
        page.set_content('<style>html,body{margin:0;background:transparent}canvas{display:block}</style><canvas width="512" height="512"></canvas>')
        page.evaluate('''async src => {window.bear=new Image();bear.src=src;await bear.decode()}''', 'data:image/svg+xml;base64,' + base64.b64encode(svg.encode()).decode())
        for state in STATES:
            command = [args.ffmpeg,'-hide_banner','-loglevel','error','-y','-f','image2pipe','-vcodec','png','-framerate',str(FPS),'-i','-',
                       '-an','-c:v','libvpx-vp9','-pix_fmt','yuva420p','-b:v','0','-crf','28','-cpu-used','4','-auto-alt-ref','0',str(animations / (state+'.webm'))]
            process = subprocess.Popen(command, stdin=subprocess.PIPE)
            first = None
            for frame in range(FRAMES):
                data = page.evaluate('''({state,frame,frames}) => {
                  const c=document.querySelector('canvas'),x=c.getContext('2d'),t=frame/frames*Math.PI*2;
                  x.clearRect(0,0,512,512);let dx=0,dy=0,angle=0,scale=1;
                  if(state==='idle'){dy=1.5*Math.sin(t);scale=1+.006*Math.sin(t)}
                  if(state==='speak'){dy=2*Math.sin(3*t);scale=1+.012*Math.sin(3*t)}
                  if(state==='click'){angle=3*Math.sin(t);dy=-8*Math.pow(Math.sin(t/2),2)}
                  if(state==='thinking'){angle=-1.5+Math.sin(t)}
                  if(state==='working'){angle=1.5*Math.sin(2*t);dy=2*Math.sin(2*t)}
                  if(state==='result'){scale=1+.015*Math.sin(t)}
                  if(state==='waiting'){dx=3*Math.sin(t);angle=1.3*Math.sin(t)}
                  if(state==='success'){dy=-18*Math.pow(Math.sin(t/2),2);angle=2*Math.sin(t)}
                  if(state==='error'){angle=-3*Math.sin(t/2)}
                  x.save();x.translate(256+dx,450+dy);x.rotate(angle*Math.PI/180);x.scale(1,scale);x.drawImage(bear,-214,-418,428,418);x.restore();
                  const dot=(a,b,r,color)=>{x.fillStyle=color;x.beginPath();x.arc(a,b,r,0,Math.PI*2);x.fill()};
                  if(state==='thinking')for(let i=0;i<3;i++)dot(369+i*16,94-i*12,3+i,'rgba(108,136,143,'+(.4+.5*(1+Math.sin(t-i))/2)+')');
                  if(state==='success'){x.strokeStyle='#d8ad54';x.lineWidth=3;for(const [a,b] of [[93,175],[423,198]]){let r=6+3*Math.sin(t);x.beginPath();x.moveTo(a-r,b);x.lineTo(a+r,b);x.moveTo(a,b-r);x.lineTo(a,b+r);x.stroke()}}
                  if(state==='result'){x.strokeStyle='#7c9e88';x.lineWidth=5;x.lineCap='round';x.beginPath();x.moveTo(385,110);x.lineTo(394,120);x.lineTo(412,99);x.stroke()}
                  if(state==='error'){x.fillStyle='#90b4c5';x.beginPath();x.moveTo(391,106);x.quadraticCurveTo(379,124,391,128);x.quadraticCurveTo(403,124,391,106);x.fill()}
                  return c.toDataURL('image/png').split(',')[1]
                }''', {'state':state,'frame':frame,'frames':FRAMES})
                png = base64.b64decode(data)
                if frame == 0:
                    first = data
                    alpha = Image.open(io.BytesIO(png)).getchannel('A')
                    assert alpha.getextrema() == (0,255), 'Pet frame must contain transparency and an opaque body'
                    if state == 'idle': (DEST / 'preview.png').write_bytes(png)
                if frame == FRAMES//7: assert data != first, 'An animation must have visible movement'
                process.stdin.write(png)
            process.stdin.close()
            assert process.wait() == 0, 'FFmpeg failed'
            print('Rendered',state,flush=True)
        browser.close()
    config = {'name':'alcor-bear','source':'Alcor existing vector mascot; ivory body, blue scarf, gold star.',
              'animations':{'idle':['idle','waiting'],'speak':['speak'],'click':['click']},
              'weights':{'idle':80,'speak':15,'click':5},
              'events':{'proactive':'speak','whisper':['speak'],'workStatus':{s:s for s in STATES[3:]}},
              'physics':{'gravity':1000,'restitution':0.65,'groundFriction':2.5,'ceilingBounce':True,'throwPower':0.8}}
    (DEST / 'alcor-bear-config.json').write_text(json.dumps(config,ensure_ascii=False,indent=2)+'\n')
    labels = ['安静陪伴','轻声说话','轻轻招手','认真思考','忙碌记录','整理完成','耐心等待','开心庆祝','遇到困难']
    (DEST / 'alcor-bear-manifest.json').write_text(json.dumps(dict(zip(labels,STATES)),ensure_ascii=False,indent=2)+'\n')


if __name__ == '__main__':
    main()
