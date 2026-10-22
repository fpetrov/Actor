#!/usr/bin/env python3
"""Original Atmospheric Minimalism assets. SVG masters remain editable.
Run: DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib PYTHONPATH=build/art-tools python3 art/atmospheric/generate.py
No game geometry, source images, fonts or third-party audio are modified.
"""
from __future__ import annotations
import hashlib, io, json, math, struct, wave
from pathlib import Path
import cairosvg
ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT/'art/atmospheric/source'
OUT = ROOT/'Agents Arena/Assets/Art/Atmospheric/Generated'
TOKENS = dict(void='#171F29', ink='#26343D', panel='#24313C', paper='#EEE9DC', floor='#D9D8C9', sage='#95B6A4', clay='#D9947E', lilac='#A7ACC9', gold='#E9C781', muted='#B5C0C2', white='#FAF7F0', edge='#5B6A72', shadow='#52615F')
DIRECTIONS = ('Down','Up','Left','Right','DownRight','UpRight')
CUES = ('Hover','Click','Message','TaskAccepted','TaskCompleted','Meeting','VoteSubmitted','Emergency','Death','KillImpact','GameOver')

def svg(w,h,body):
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">{body}</svg>'
def path(d,fill,stroke='none',sw=1):
    return f'<path d="{d}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}" stroke-linejoin="round" stroke-linecap="round"/>'
def ellipse(x,y,rx,ry,fill):
    return f'<ellipse cx="{x}" cy="{y}" rx="{rx}" ry="{ry}" fill="{fill}"/>'
def rect(x,y,w,h,fill,r=0):
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{fill}"/>'
def export(name,text):
    SOURCE.mkdir(parents=True,exist_ok=True); OUT.mkdir(parents=True,exist_ok=True)
    (SOURCE/f'{name}.svg').write_text(text)
    cairosvg.svg2png(bytestring=text.encode(),write_to=str(OUT/f'{name}.png'))

def surveyor(direction,frame=0,skin='#E000E0'):
    # Mask R=B, G=0 identifies only the fabric in the palette shader.
    phase=frame*math.tau/8; stride=math.sin(phase); lift=abs(math.sin(phase))*3
    x=120; side=direction in ('Left','Right'); back=direction in ('Up','UpRight')
    lean=4 if direction in ('Right','DownRight','UpRight') else -4 if direction=='Left' else 0
    b=ellipse(120,338,65,9,'#171F2938')
    for foot,step in ((96,stride),(144,-stride)):
        b+=rect(foot-17,310+step*7,34,25,TOKENS['ink'],11)
        b+=rect(foot-14,325+step*7,29,7,'#43535A',3)
    b+=f'<g transform="translate({lean} {-lift})">'
    b+=path('M78 172 Q120 147 162 172 L199 307 Q164 331 120 319 Q75 330 41 307 Z',skin,TOKENS['ink'],6)
    b+=path('M85 177 L68 294 Q89 305 109 301 L110 187 Z','#A000A0')
    b+=path('M136 184 L154 300 L180 302 L156 179 Z','#C000C0')
    b+=path('M120 186 L120 310','none','#FF00FF',3)
    b+=path('M73 192 L55 261 L69 274 L88 208 Z','#26343D')
    b+=path('M167 192 L185 261 L171 274 L152 208 Z','#26343D')
    b+=ellipse(120,143,73,80,TOKENS['ink'])
    b+=path('M58 148 Q58 71 121 63 Q181 72 183 148 L162 186 Q124 211 76 182 Z','#34444B')
    b+=path('M68 141 Q70 87 121 82 Q169 86 172 145 L156 175 Q123 196 83 174 Z',TOKENS['void'])
    if back:
        b+=path('M120 83 Q104 126 114 170','none','#43535A',4)
        b+=path('M78 181 Q119 204 163 182','none',TOKENS['edge'],3)
    else:
        eye_shift=20 if side and direction=='Right' else -20 if side else 9 if direction=='DownRight' else 0
        # Always two individually authored ivory eyes, even at the side view.
        left=102+eye_shift; right=137+eye_shift
        if direction=='Left': left,right=81,108
        if direction=='Right': left,right=132,158
        b+=ellipse(left,144,7 if side else 9,13,TOKENS['paper'])
        b+=ellipse(right,144,5 if side else 9,13,TOKENS['paper'])
        b+=path('M79 181 Q121 201 162 181','none',TOKENS['edge'],3)
    b+=path('M54 305 Q77 315 101 311 M140 312 Q164 317 187 306','none','#FF00FF',3)
    return b+'</g>'

def characters():
    for direction in DIRECTIONS:
        body=''.join(f'<g transform="translate({i*240} 0)">{surveyor(direction,i)}</g>' for i in range(8))
        export('surveyor-'+direction.lower(),svg(1920,350,body))
    # A whole-body fold has authored intermediate shapes; it is never a rotated standing sprite.
    for index in range(3):
        b=ellipse(120,122,99,15,'#171F2938')
        if index==0:
            b+=path('M47 120 Q52 55 101 35 Q143 32 164 70 L205 125 Q162 143 116 131 Z','#E000E0',TOKENS['ink'],5)
            b+=ellipse(112,56,42,44,TOKENS['ink'])+ellipse(105,58,7,9,TOKENS['paper'])+ellipse(127,58,7,9,TOKENS['paper'])
        elif index==1:
            b+=path('M30 123 Q64 61 122 78 L201 116 L212 131 Q143 143 94 130 Z','#E000E0',TOKENS['ink'],5)
            b+=ellipse(78,88,42,33,TOKENS['ink'])+path('M58 91 L70 93 M79 94 L91 95','none',TOKENS['paper'],3)
        else:
            b+=path('M26 121 Q59 91 104 91 L194 108 L217 127 Q168 144 116 130 Q68 143 26 121 Z','#E000E0',TOKENS['ink'],5)
            b+=path('M111 98 L136 127 M142 105 L171 130','none','#B000B0',7)
            b+=ellipse(63,107,39,26,TOKENS['ink'])+path('M44 108 L54 110 M66 112 L76 112','none',TOKENS['paper'],3)
            b+=rect(183,117,32,15,TOKENS['ink'],6)
        export(f'fold-{index}',svg(240,150,b))
    for i in range(6):
        # Small visible cosmetic stitches, unrelated to permission, duty or alignment.
        paths=('M8 16 L16 4 L24 16 L16 28 Z','M8 8 L24 8 L24 24 L8 24 Z','M16 5 L26 25 L6 25 Z','M6 16 L26 16 M16 6 L16 26','M6 10 L16 25 L26 10','M7 7 L25 25 M25 7 L7 25')
        export(f'mark-{i}',svg(32,32,path(paths[i],'none',TOKENS['paper'],3)))
    def colored(direction,index):
        color=TOKENS[('sage','clay','lilac','gold','muted','edge')[index]]
        rgb=tuple(int(color[n:n+2],16) for n in (1,3,5))
        text=surveyor(direction)
        for mask,gain in (('#E000E0',224/255),('#A000A0',160/255),('#C000C0',192/255),('#FF00FF',1)):
            text=text.replace(mask,'#'+''.join(f'{round(c*gain):02x}' for c in rgb))
        return text
    preview=''.join(f'<g transform="translate({(i%3)*260} {(i//3)*380})">{colored(d,i)}</g>' for i,d in enumerate(DIRECTIONS))
    export('character-review',svg(780,760,rect(0,0,780,760,TOKENS['floor'])+preview))

def environment():
    layout=json.loads((ROOT/'Agents Arena/Assets/World/SocialLayout.json').read_text())
    x0,ymax=-4.5,6.4; scale=100
    def pt(p): return f'{(p["x"]-x0)*scale:.3f},{(ymax-p["y"])*scale:.3f}'
    b=''
    for f in sorted(layout['floors'],key=lambda f:f['room_id']!='east-corridor'):
        color={'main-hall':TOKENS['floor'],'engineering':'#CDD8C9','security':'#D3D2DC','storage':'#DED0C4'}.get(f['room_id'],'#C7CCBF')
        points=' '.join(pt(p) for p in f['physical_polygon'])
        b+=f'<polygon points="{points}" fill="{color}"/>'
    # The clip prevents a decorative inlay from inventing a floor beyond geometry.
    clip=''.join(f'<polygon points="{" ".join(pt(p) for p in f["physical_polygon"])}"/>' for f in layout['floors'])
    b+='<defs><clipPath id="floor">'+clip+'</clipPath></defs><g clip-path="url(#floor)">'
    # Flat markings only: geometry is still exactly the exported floor union.
    def worldpath(coords,color,width):
        return path('M'+' L'.join(pt(dict(x=x,y=y)) for x,y in coords),'none',color,width)
    b+=worldpath([(-2.3,-.7),(1.8,-.7),(2.5,-.1)],'#B8C4B7',3)
    b+=worldpath([(-2.3,-2.75),(1.8,-2.75)],'#C0C8B8',2)
    b+=worldpath([(0,-3.1),(0,-5.8)],'#B2C2B2',3)
    for x,y,w,h,c in [(6.44,3.77,2.1,1.45,'lilac'),(5.34,-6.83,1.8,1.4,'clay'),(0,-8.8,2.3,1.5,'sage')]:
        px=(x-x0)*scale; py=(ymax-y)*scale
        b+=f'<rect x="{px-w*50}" y="{py-h*50}" width="{w*100}" height="{h*100}" rx="15" fill="none" stroke="{TOKENS[c]}" stroke-width="3" opacity=".65"/>'
    export('station-floor',svg(1410,1865,b+'</g>'))
    # Strip can be stretched along the true union boundary, never across doorways.
    export('wall',svg(160,48,rect(0,4,160,40,'#171F292C',4)+rect(0,3,160,31,TOKENS['ink'],4)+rect(0,3,160,20,TOKENS['edge'],4)+rect(0,3,160,8,TOKENS['muted'],3)+rect(8,27,144,3,'#405159',1)))
    for kind,accent in [('meeting','gold'),('task','sage'),('records','lilac'),('storage','clay'),('life-support','sage')]:
        b=ellipse(48,59,36,9,'#171F2938')
        b+=path('M17 24 L27 12 L69 12 L79 24 L79 55 Q48 67 17 55 Z',TOKENS['ink'])
        b+=path('M17 24 L27 12 L69 12 L79 24 L69 43 L27 43 Z',TOKENS['edge'])
        b+=path('M26 24 L32 18 L65 18 L71 24 L64 35 L32 35 Z',TOKENS['void'])
        b+=rect(34,22,27,8,TOKENS[accent],2)
        b+=rect(31,44,11,4,TOKENS['paper'],1)+rect(53,44,11,4,TOKENS[accent],1)
        if kind=='meeting': b+=ellipse(48,25,7,4,TOKENS['gold'])
        if kind=='records': b+=path('M37 22 L57 22 M37 26 L53 26','none',TOKENS['paper'],1)
        if kind=='life-support': b+=path('M36 26 L42 26 L46 20 L50 30 L54 25 L61 25','none',TOKENS['paper'],1.5)
        export('terminal-'+kind,svg(96,72,b))
    b=ellipse(80,165,70,20,'#171F2940')
    b+=path('M18 67 L38 46 L122 46 L142 67 L142 148 L121 172 L39 172 L18 148 Z',TOKENS['ink'])
    b+=path('M18 67 L38 46 L122 46 L142 67 L120 89 L40 89 Z',TOKENS['edge'])
    b+=path('M40 89 L120 89 L120 151 Q80 172 40 151 Z',TOKENS['panel'])
    b+=ellipse(80,73,43,24,TOKENS['sage'])+ellipse(80,73,31,16,TOKENS['ink'])+ellipse(80,73,23,11,TOKENS['gold'])
    b+=path('M30 100 L30 141 M130 100 L130 141','none',TOKENS['sage'],7)
    b+=path('M55 116 L105 116 M55 126 L105 126','none',TOKENS['edge'],3)
    export('reactor',svg(160,200,b))
    export('service-light',svg(64,16,rect(0,0,64,16,TOKENS['ink'],5)+rect(7,5,50,6,TOKENS['gold'],3)))
    return hashlib.sha256(json.dumps(layout,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def landmark_art():
    # Mounted entirely beyond the existing collision contour. Abstract diagrams
    # are pictograms; player-readable names remain retained UI labels.
    common=rect(2,8,236,98,'#171F2930',9)+path('M10 8 L230 8 L238 16 L238 95 L230 103 L10 103 L2 95 L2 16 Z',TOKENS['ink'])
    board=common+rect(10,14,220,77,TOKENS['edge'],5)+rect(17,20,206,64,TOKENS['panel'],4)
    board+=path('M69 40 L69 69 L157 69 L181 48 L181 35','none',TOKENS['sage'],3)
    board+=path('M69 51 L149 51 L149 29','none',TOKENS['sage'],3)
    for x,y,w,h,c in [(38,31,63,30,'gold'),(45,66,47,12,'sage'),(135,24,32,20,'lilac'),(166,57,34,20,'clay')]:
        board+=rect(x,y,w,h,TOKENS[c],4)
    board+=rect(24,94,32,3,TOKENS['muted'],1)+rect(183,94,32,3,TOKENS['muted'],1)
    records=common+rect(10,14,220,77,TOKENS['edge'],5)
    for x in (17,87,157):
        records+=rect(x,21,65,63,TOKENS['panel'],4)
        for y in (28,46,64):
            records+=rect(x+6,y,53,13,TOKENS['muted'],2)+rect(x+10,y+3,28,7,TOKENS['lilac'],1)+rect(x+44,y+5,9,3,TOKENS['ink'],1)
    records+=rect(27,94,185,3,TOKENS['lilac'],1)
    cargo=rect(8,2,98,236,'#171F2930',9)+path('M8 10 L16 2 L95 2 L103 10 L103 230 L95 238 L16 238 L8 230 Z',TOKENS['ink'])
    cargo+=rect(15,10,79,220,TOKENS['edge'],5)
    for index,y in enumerate((17,69,121,173)):
        cargo+=rect(23,y,62,46,TOKENS['panel'],4)
        cargo+=rect(29,y+4,50,34,TOKENS['clay'] if index%2==0 else '#BEBDB0',3)
        cargo+=rect(47,y+4,11,34,'#62746C',1)+rect(44,y+15,17,9,TOKENS['ink'],2)+rect(48,y+18,9,3,TOKENS['gold'],1)
        cargo+=rect(21,y+41,66,4,TOKENS['muted'],1)
    return {'landmark-hall-directory':svg(240,110,board),'landmark-security-records':svg(240,110,records),'landmark-storage-cargo':svg(110,240,cargo)}

def audio():
    for index,name in enumerate(CUES):
        duration=(.045,.065,.16,.14,.28,.32,.15,.36,.24,.16,.36)[index]
        freq=(680,480,620,430,520,340,560,190,150,95,420)[index]
        rate=48000; samples=[]
        for n in range(round(duration*rate)):
            t=n/rate; u=t/duration
            envelope=min(t/.006,1)*(1-u)**2
            f=freq*(1+.12*math.sin(math.pi*u))
            signal=math.sin(math.tau*f*t)+.22*math.sin(math.tau*f*2*t)
            if name in ('TaskCompleted','GameOver'): signal+=.38*math.sin(math.tau*freq*1.5*t)
            if name=='KillImpact': signal+=.18*math.sin(math.tau*71*t)*math.sin(math.tau*1139*t)
            samples.append(round(max(-1,min(1,signal*.18*envelope))*32767))
        with wave.open(str(OUT/f'{name}.wav'),'wb') as out:
            out.setnchannels(1); out.setsampwidth(2); out.setframerate(rate)
            out.writeframes(struct.pack('<'+'h'*len(samples),*samples))

def main():
    characters(); geometry=environment();
    for name,master in landmark_art().items(): export(name,master)
    audio()
    placements=json.loads((SOURCE.parent/'placements.json').read_text())
    (OUT/'landmarks.json').write_text(json.dumps(placements,indent=2)+'\n')
    paths=sorted(p for p in OUT.iterdir() if p.suffix in ('.png','.wav'))
    manifest={'style':'Atmospheric Minimalism','original':True,'logical_world_ppu':50,'world_ppu':100,'character_ppu':320,'character_frame':[240,350],'character_pivot':[.5,8/350],'directions':DIRECTIONS,'frames_per_direction':8,'kill_duration_ms':640,'layout_semantic_sha256':geometry,'palette':TOKENS,'landmarks':placements['landmarks'],'files':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}}
    (SOURCE.parent/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps({'assets':len(paths),'svg_sources':len(list(SOURCE.glob('*.svg'))),'layout_sha256':geometry}))
if __name__=='__main__': main()
