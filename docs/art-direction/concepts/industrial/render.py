#!/usr/bin/env python3
"""Original Quiet Industrial concept drawings; writes editable SVG + Cairo PNG.

No game assets, state, screenshots, fonts or remote images are imported.
Run: PYTHONPATH=build/art-tools python3 docs/art-direction/concepts/industrial/render.py
"""
from pathlib import Path
from html import escape
import json
import math

ROOT = Path(__file__).resolve().parent
C = dict(void="#101a20", wall="#24343b", edge="#41545a", floor="#657572",
         pale="#eae4cf", text="#f0eddf", muted="#a9b9b5", amber="#e1b16c",
         teal="#8dbdb3", blue="#8daebe", coral="#d68f7f", panel="#18292f")
PAGES = {}


def rect(x, y, w, h, fill, r=0, stroke="none", sw=1, opacity=1):
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}" opacity="{opacity}"/>'


def line(x1, y1, x2, y2, stroke, sw=1, opacity=1):
    return f'<path d="M{x1} {y1}L{x2} {y2}" fill="none" stroke="{stroke}" stroke-width="{sw}" stroke-linecap="round" opacity="{opacity}"/>'


def circle(x, y, r, fill, stroke="none", sw=1):
    return f'<circle cx="{x}" cy="{y}" r="{r}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>'


def ellipse(x, y, rx, ry, fill, opacity=1):
    return f'<ellipse cx="{x}" cy="{y}" rx="{rx}" ry="{ry}" fill="{fill}" opacity="{opacity}"/>'


def poly(points, fill, stroke="none", sw=1):
    return f'<polygon points="{" ".join(f"{x},{y}" for x,y in points)}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}" stroke-linejoin="round"/>'


def text(x, y, value, size=16, fill=None, weight=400, anchor="start", spacing=0, mono=False):
    size = max(12, size)
    font = "Courier New,monospace" if mono else "Arial,sans-serif"
    return f'<text x="{x}" y="{y}" fill="{fill or C["text"]}" font-family="{font}" font-size="{size}" font-weight="{weight}" text-anchor="{anchor}" letter-spacing="{spacing}">{escape(str(value))}</text>'


def lines(x, y, values, size=16, fill=None, leading=24, **kwargs):
    return "".join(text(x, y+i*leading, v, size, fill, **kwargs) for i,v in enumerate(values))


def panel(x, y, w, h, r=12, fill=None):
    return (rect(x, y+5, w, h, "#071116", r, opacity=.3)
            +rect(x, y, w, h, fill or "url(#glass)", r, "#758e90", .8)
            +line(x+r, y+1, x+w-r, y+1, "#d6e2d9", .7, .28))


def button(x,y,w,label,primary=False,disabled=False):
    fill = C['amber'] if primary else '#2a3e45'
    if disabled: fill = '#26373d'
    return (rect(x,y,w,40,fill,8, '#eed0a0' if primary else '#5c7379',.8, .5 if disabled else 1)
            +text(x+w/2,y+25,label,15,C['void'] if primary else C['muted'] if disabled else C['text'],600,'middle'))


def pill(x,y,label,fill=None,w=None):
    w = w or len(label)*7+24
    return rect(x,y,w,26,fill or '#2e4549',6)+text(x+12,y+18,label,12,C['pale'],600,spacing=.4)


def screw(x,y):
    return circle(x,y,2.5,'#85928a')+line(x-1,y,x+1,y,'#35474a',.8)


def worker(x,y,color=None,scale=1,pose='front',name=None):
    """Distinct separated helmet/neck/body/arms/boots, root at feet."""
    col=color or C['amber']
    a=[ellipse(0,-1,16,5,'#0a151b',.45)]
    if pose=='back':
        a += [rect(-11,-39,22,29,col,5,'#17272c',2),rect(-14,-62,28,27,C['pale'],7,'#1d2c32',2),rect(-10,-58,20,10,'#80918d',3),rect(-7,-39,14,20,'#34484c',3)]
    elif pose=='side':
        a += [rect(-9,-38,20,28,col,4,'#17272c',2),rect(-10,-63,26,27,C['pale'],6,'#1d2c32',2),rect(5,-57,12,13,'#2d454e',3),rect(-14,-35,8,21,col,3,'#17272c',2),rect(1,-35,8,20,col,3,'#17272c',2)]
    else:
        a += [rect(-14,-39,28,29,col,5,'#17272c',2),rect(-20,-37,8,22,col,3,'#17272c',2),rect(12,-37,8,22,col,3,'#17272c',2),rect(-14,-64,28,28,C['pale'],7,'#1d2c32',2),rect(-11,-58,22,12,'#2d454e',3),line(-8,-56,6,-56,'#a4c7c2',2),rect(-9,-42,18,6,'#334b50',2),rect(-4,-34,8,10,'#f0dfb8',1),line(-10,-18,10,-18,'#34484c',3)]
    a += [rect(-11,-12,9,12,'#26393f',2),rect(2,-12,9,12,'#26393f',2),rect(-12,-3,11,5,'#a9b6ac',2),rect(2,-3,11,5,'#a9b6ac',2)]
    out=f'<g transform="translate({x},{y}) scale({scale})">'+''.join(a)+'</g>'
    if name: out+=text(x,y+19,name,12,C['pale'],600,'middle')
    return out


def corpse(x,y,color=None,scale=1):
    col=color or C['amber']
    s=ellipse(0,1,34,9,'#08141a',.5)
    s+=poly([(-11,-17),(8,-21),(19,-12),(15,1),(-7,0),(-16,-8)],col,'#182a31',2)
    s+=rect(-31,-22,24,23,C['pale'],6,'#182a31',2)+rect(-29,-17,16,8,'#2d454e',2)
    s+=poly([(13,-13),(27,-6),(33,-11),(38,-5),(27,4),(11,-3)],'#31464c','#182a31',2)
    s+=poly([(9,-1),(20,8),(30,5),(32,12),(17,15),(3,5)],'#31464c','#182a31',2)
    s+=line(-8,-4,4,1,'#f5e9cc',3)+rect(29,-10,9,6,'#a9b6ac',2)+rect(25,8,9,6,'#a9b6ac',2)
    return f'<g transform="translate({x},{y}) scale({scale})">{s}</g>'


def console(x,y,accent=None,kind='task'):
    accent=accent or C['teal']
    out=ellipse(x,y+25,40,9,'#15262b',.35)+rect(x-29,y-6,58,33,'#22343a',4,'#91a29a',1)
    out+=poly([(x-34,y-34),(x+32,y-34),(x+39,y-4),(x-36,y-4)],'#b4bdb0','#22343a',2)
    out+=poly([(x-26,y-29),(x+24,y-29),(x+28,y-12),(x-28,y-12)],'#182c34')
    out+=line(x-19,y-24,x+11,y-24,accent,2)+line(x-19,y-19,x+2,y-19,'#6d8b8f',2)
    out+=rect(x-22,y+2,34,7,'#617775',2)+circle(x+22,y+6,3,accent)+screw(x-24,y+20)+screw(x+24,y+20)
    if kind=='meeting':out+=circle(x,y-22,6,'none',accent,1.8)+line(x,y-31,x,y-14,accent,1.5)
    return out


DEFS='''<defs>
<linearGradient id="glass" x2="0" y2="1"><stop stop-color="#2c4249" stop-opacity=".97"/><stop offset="1" stop-color="#14272e" stop-opacity=".96"/></linearGradient>
<linearGradient id="floor" x2="1" y2="1"><stop stop-color="#73817a"/><stop offset="1" stop-color="#4f6464"/></linearGradient>
<radialGradient id="pool"><stop stop-color="#e3c38b" stop-opacity=".20"/><stop offset="1" stop-color="#e3c38b" stop-opacity="0"/></radialGradient>
<pattern id="grate" width="8" height="8" patternUnits="userSpaceOnUse"><rect width="8" height="8" fill="#24383d"/><path d="M0 4H8" stroke="#506369" stroke-width="1.4"/></pattern>
<pattern id="floorgrid" width="88" height="74" patternUnits="userSpaceOnUse"><rect width="88" height="74" fill="url(#floor)"/><path d="M0 0H88V74" fill="none" stroke="#9aa69b" stroke-opacity=".23"/><path d="M2 73H86" stroke="#233e43" stroke-opacity=".25"/></pattern>
</defs>'''


def save(name,content,w=800,h=600):
    inner=DEFS+content
    svg=f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-label="Quiet Industrial concept: {name}">{inner}</svg>'
    (ROOT/f'{name}.svg').write_text(svg)
    PAGES[name]=(inner,w,h)


def room_base(label='MAIN HALL',index='01'):
    pts=[(70,172),(162,84),(624,84),(736,174),(736,456),(645,554),(161,554),(70,462)]
    outer=[(x,y-18) for x,y in pts]
    out=rect(0,0,800,600,C['void'])+poly(outer,'#35474c','#0c171d',12)+poly(pts,'url(#floorgrid)','#182c32',18)
    out+=poly([(x+(400-x)*.025,y+(310-y)*.025) for x,y in pts],'none','#91a49a',2)
    out+=ellipse(400,310,330,245,'url(#pool)')
    for x in (202,596):
        out+=rect(x-60,91,120,26,'#273b41',6,'#9ba79a',1)+rect(x-43,94,86,4,C['amber'],2)
        for dx in (-50,50):out+=screw(x+dx,103)
    for x in (81,716):
        out+=rect(x,225,10,95,'url(#grate)',3,'#71877f',1)
        out+=rect(x+2,235,6,34,C['amber'],1)
    out+=rect(353,534,94,65,'url(#grate)',2)+line(354,534,446,534,C['amber'],5)
    out+=text(122,162,index,42,'#344f52',600,spacing=3)+text(122,187,label,14,'#c4c9b7',600,spacing=3)
    out+=line(125,198,226,198,C['amber'],3)
    return out


def hud(room='MAIN HALL'):
    s=panel(18,16,764,44,9)+text(34,44,'A /',21,C['amber'],700)+text(94,36,room,12,C['pale'],600,spacing=1.7)
    s+=text(94,51,'EXPLORATION',10,C['muted'],spacing=1.2)+text(400,44,'07:42',18,C['pale'],500,'middle',mono=True)
    s+=text(514,43,'STATION WORK',10,C['muted'],600,spacing=1)+rect(651,31,80,9,'#15272d',4)
    s+=rect(651,31,26,9,C['teal'],4)+text(764,44,'2/6',13,C['pale'],500,'end',mono=True)
    return s


main=room_base()
main+=rect(282,256,233,93,'#24373d',15,'#9ca79b',2)+rect(288,245,221,89,'#b4bbaa',13,'#283e43',3)
main+=rect(307,260,182,55,'#4b6667',9)+line(327,279,469,279,'#9db8af',2)+circle(399,287,20,'none','#c6d0bb',2)
main+=console(616,225,C['amber'],'meeting')+text(616,272,'MEETING',10,'#dce0ca',600,'middle',1)
main+=rect(138,369,73,68,'#334b51',7,'#aab5a5',2)+rect(145,375,59,13,C['teal'],3)+line(151,398,194,398,'#7c9691',3)+line(151,409,194,409,'#7c9691',3)
hall_static=main
main+=worker(393,432,C['amber'],.82,name='YOU')+worker(568,361,C['blue'],.78,name='SERA')
hall_world=main
main+=hud()+panel(18,76,202,82,10)+text(34,102,'YOUR NEXT TASK',10,C['muted'],600,spacing=1.4)+text(34,126,'Check power relay',15,C['text'],600)+text(34,146,'Engineering · 1 of 3',12,C['muted'])
main+=pill(620,76,'CREW  /  ALIVE',w=162)+pill(307,473,'E   Inspect relay',w=187)
main+=panel(18,486,264,96,10)+text(34,508,'NEARBY',10,C['teal'],600,spacing=1.4)+text(34,530,'Sera',13,C['blue'],600)+text(79,530,'Heading to the archive.',13,C['text'])+text(34,554,'T  Write a message',13,C['muted'])+text(265,570,'1',11,C['amber'],600,'end')
main+=panel(614,510,168,72,10)+text(630,534,'HUMAN CONTROL',10,C['muted'],600,spacing=1)+text(630,558,'WASD  ·  E  ·  T',14,C['pale'],500,mono=True)
save('main-hall',main)

fog=hall_static+rect(0,0,800,600,'#0e1c22',opacity=.87)
fog+='<defs><clipPath id="permitted-vision"><path d="M182 430 Q176 306 264 243 Q333 187 427 206 L482 231 L482 284 L548 284 Q649 324 650 427 Q641 529 551 569 L297 585 Q195 540 182 430Z"/></clipPath></defs>'
fog+=f'<g clip-path="url(#permitted-vision)">{hall_world}</g>'
fog+=hud()+pill(18,76,'CURRENT SIGHT',w=160)+panel(18,482,330,100,10)+text(34,508,'THE ROOM REMAINS LEGIBLE.',12,C['pale'],600,spacing=.6)
fog+=lines(34,533,['People, bodies and effects exist only', 'inside permitted sight. No remote markers.'],14,C['muted'],22)
save('fog-of-war',fog)

eng=room_base('ENGINEERING','02')
eng+=poly([(278,203),(507,203),(554,254),(554,417),(497,466),(286,466),(243,418),(243,255)],'#344c50','#182b31',8)
eng+=ellipse(400,350,112,79,'#162a30')+ellipse(400,323,112,79,'#9ca99d')+ellipse(400,314,94,64,'#3e595d')+ellipse(400,303,62,43,'#a4b9aa')+ellipse(400,299,45,30,'#314b51')
for x in (277,508):eng+=rect(x,266,19,122,'url(#grate)',4,'#99aa9c',2)+rect(x+4,284,11,37,C['amber'],2)
eng+=line(401,254,401,340,C['teal'],8)+ellipse(401,296,21,17,'#b6d4bd')+ellipse(401,294,12,10,'#779d91')
eng+=console(601,204,C['teal'])+rect(136,248,48,162,'url(#grate)',6,'#8fa496',2)
for y in (269,305,341,377):eng+=rect(143,y,34,10,'#c3b595',2)
eng+=worker(591,334,C['amber'],.85)+hud('ENGINEERING')+pill(18,76,'02  /  SYSTEMS & AIR',w=208)
eng+=panel(218,501,364,80,10)+text(237,526,'E   Service coolant regulator',16,C['text'],600)+text(237,552,'Hold interaction · 4 seconds',13,C['muted'])+rect(237,565,326,3,C['teal'],1)
save('engineering',eng)

security=room_base('SECURITY','03')
for x in (170,565):
    security+=rect(x,205,70,230,'#253c42',7,'#8faaa0',2)
    for y in (218,269,320,371):
        security+=rect(x+8,y,54,40,'#aab7a8',4)+rect(x+13,y+6,44,17,'#39545a',2)+line(x+18,y+13,x+49,y+13,C['blue'],2)+circle(x+49,y+31,2,C['amber'])
security+=rect(308,235,184,84,'#344e54',8,'#a2b3a6',2)+rect(319,224,164,58,'#bbc2ad',7)+rect(328,230,146,42,'#263f48',4)
for x in (343,383,423):security+=rect(x,240,29,20,'#769a9c',2)+line(x+4,245,x+21,245,'#c3d5c0',2)
security+=text(400,344,'STATION ARCHIVE',12,'#dce1ce',600,'middle',3)+worker(397,414,C['blue'],.86)
security+=hud('SECURITY')+pill(18,76,'03  /  RECORDS & ACCESS',w=233)+panel(224,510,352,70,10)+text(242,539,'E   Verify archive checksum',16,C['text'],600)+text(242,562,'Local terminal · no remote player feed',12,C['muted'])
save('security',security)


def lobby_base(title='BOARDING'):
    s=rect(0,0,800,600,C['void'])+rect(0,0,285,600,'#203239')
    for y in range(0,600,60):s+=line(0,y,285,y,'#31464c',1)
    s+=poly([(0,240),(250,240),(285,275),(285,520),(0,520)],'#334950')+poly([(0,264),(225,264),(263,300),(263,496),(0,496)],'#718178')
    s+=console(150,356,C['amber'],'meeting')+worker(93,444,C['teal'],1.15)+rect(0,512,285,88,'#182930')
    s+=text(28,55,'A / ACTOR',22,C['amber'],700,spacing=3)+text(28,98,'AGENTS',29,C['pale'],700,spacing=1)+text(28,132,'ARENA',40,C['pale'],700,spacing=1)
    s+=text(28,166,'TRUST IS PART OF THE WORK.',10,C['muted'],600,spacing=1.2)
    s+=pill(28,197,'PLAY',C['edge'],w=123)+text(28,245,'Settings',15,C['muted'])
    s+=text(28,553,'Social Deduction',14,C['pale'],600)+text(28,576,'A shared station. Private intentions.',11,C['muted'])
    s+=text(318,50,title,12,C['muted'],600,spacing=2)+text(772,50,'ESC  Exit',12,C['muted'],anchor='end')
    return s


lobby=lobby_base('FIND YOUR NEXT SHIFT')+panel(310,75,464,459,14)
lobby+=text(330,111,'Join the station',26,C['pale'],600)+text(330,137,'Play with people and autonomous agents.',13,C['muted'])
lobby+=text(330,170,'PLAYER NAME',10,C['muted'],600,spacing=1.2)+rect(330,180,424,40,'#172b32',7,'#687f81',1)+text(344,206,'Mara',16,C['text'])
lobby+=text(330,246,'CONTROL',10,C['muted'],600,spacing=1.2)+rect(416,230,338,34,'#12272e',7)+rect(419,233,166,28,C['edge'],5)+text(502,253,'Human',14,C['pale'],600,'middle')+text(665,253,'Agent',14,C['muted'],500,'middle')
lobby+=text(330,299,'AVAILABLE ROOMS',10,C['muted'],600,spacing=1.2)+text(751,299,'Refresh',12,C['teal'],anchor='end')
for i,(name,players,mode) in enumerate([('Evening watch','3 / 6','Social deduction'),('Station 12','2 / 6','Social deduction'),('Workshop','1 / 4','Sandbox')]):
    y=311+i*48
    lobby+=rect(330,y,424,44,'#31484e' if i==0 else '#21383f',6,'#a4b4a2' if i==0 else '#405b61',1)
    lobby+=circle(344,y+16,3,C['teal'])+text(356,y+19,name,14,C['pale'],600)+text(356,y+35,mode,10,C['muted'])+text(738,y+26,players,13,C['pale'],anchor='end',mono=True)
lobby+=text(330,479,'Connection ready',11,C['teal'])+button(579,461,175,'Join selected',True)
lobby+=text(330,518,'Have a code?',12,C['muted'])+text(420,518,'Join by code',12,C['pale'],600)+text(754,518,'Create room  +',12,C['pale'],600,'end')
lobby+=text(312,568,'No account or agent software needed for Human play.',11,C['muted'])
save('lobby',lobby)

boarding=lobby_base('EVENING WATCH')+panel(310,75,464,459,14)+text(330,112,'Waiting for the crew',25,C['pale'],600)
boarding+=pill(330,132,'SOCIAL DEDUCTION',w=192)+text(752,151,'4 / 6',14,C['muted'],anchor='end',mono=True)
for i,(name,col,state) in enumerate([('Mara · you',C['amber'],'READY'),('Sera',C['blue'],'READY'),('Olin',C['teal'],'READY'),('Rune',C['coral'],'NOT READY')]):
    y=176+i*58;boarding+=rect(330,y,424,52,'#263e45',7)+worker(356,y+42,col,.47)+text(383,y+31,name,15,C['pale'],600)+text(736,y+31,state,11,C['teal'] if i<3 else C['muted'],600,'end')
boarding+=text(330,431,'Room code',11,C['muted'])+text(330,458,'AB12CD',22,C['pale'],600,spacing=3)+text(495,455,'Copy',13,C['teal'])
boarding+=button(578,423,176,'Ready',True)+text(330,503,'Starts when at least 4 players are ready.',12,C['muted'])
boarding+=text(312,565,'No role assignments are visible in the lobby.',11,C['muted'])
save('boarding',boarding)


def meeting_base(phase,timer):
    s=rect(0,0,800,600,C['void'])+rect(0,0,800,70,'#23373e')+line(24,69,776,69,'#56706f',1)
    s+=text(24,35,'A /',20,C['amber'],700)+text(79,34,phase,19,C['pale'],600,spacing=2)+text(79,55,'MEETING 02  /  BODY REPORTED',10,C['muted'],600,spacing=1)
    s+=text(771,39,timer,26,C['pale'],500,'end',mono=True)+text(771,57,'TIME REMAINING',9,C['muted'],500,'end',1)
    return s


discussion=meeting_base('DISCUSSION','01:26')+panel(24,88,170,430,10)
for i,(name,col,state) in enumerate([('Mara · you',C['amber'],'Alive'),('Sera',C['blue'],'Alive'),('Olin',C['teal'],'Alive'),('Rune',C['coral'],'Out of play')]):
    y=106+i*91
    discussion+=worker(51,y+47,col,.55)+text(76,y+21,name,13,C['pale'],600)+text(76,y+42,state,10,C['muted'])
    if i<3:discussion+=line(40,y+66,179,y+66,'#3b5358',1)
discussion+=text(40,494,'Roles stay private.',11,C['muted'])+panel(210,88,566,352,10)
discussion+=text(230,114,'DISCUSSION CHANNEL',10,C['teal'],600,spacing=1.2)+text(754,114,'All living participants',10,C['muted'],anchor='end')
for y,name,col,words in [(146,'Mara',C['amber'],['I found a body by the archive door.']), (218,'Sera',C['blue'],['I crossed the lower passage.', 'Did anyone see the east hall?']), (313,'Olin',C['teal'],['I saw a figure, but not clearly enough', 'to identify them.'])]:
    discussion+=rect(230,y,521,70 if len(words)>1 else 53,'#2c454c',8)+rect(230,y,3,70 if len(words)>1 else 53,col,1)
    discussion+=text(246,y+20,name,12,col,600)+lines(246,y+42,words,15,C['text'],22)
discussion+=panel(210,456,566,92,10)+text(231,482,'Message the discussion…',15,C['muted'])+text(231,526,'Enter to send · movement paused while typing',11,C['muted'])+button(660,497,96,'Send',True)
discussion+=text(24,576,'CREW · ALIVE',11,C['muted'],600,spacing=.6)+text(776,576,'Voting begins when discussion ends.',11,C['muted'],anchor='end')
save('discussion',discussion)

voting=meeting_base('VOTING','00:39')+text(24,96,'Choose one person or skip. Your ballot is final after confirmation.',13,C['muted'])
for i,(name,col,state) in enumerate([('Mara · you',C['amber'],'Alive'),('Sera',C['blue'],'SELECTED'),('Olin',C['teal'],'Alive'),('Rune',C['coral'],'Out of play')]):
    x=24+(i%3)*256;y=112+(i//3)*171
    voting+=panel(x,y,240,151,10, '#405b5e' if i==1 else '#263c43')
    if i==1:voting+=rect(x+1,y+1,238,149,'none',10,C['amber'],2)+circle(x+213,y+24,10,C['amber'])+line(x+209,y+24,x+212,y+27,C['void'],2)+line(x+212,y+27,x+217,y+20,C['void'],2)
    voting+=worker(x+50,y+94,col,.95)+text(x+91,y+57,name,16,C['pale'],600)+text(x+91,y+82,state,11,C['amber'] if i==1 else C['muted'],600)
    if i==3:voting+=rect(x,y,240,151,C['void'],10,opacity=.3)
voting+=line(24,475,776,475,'#4b656b',1)+text(24,504,'YOUR SELECTION',10,C['muted'],600,spacing=1.4)+text(24,531,'Sera',20,C['pale'],600)
voting+=button(370,518,128,'Skip')+button(514,518,262,'Confirm ballot',True)+text(776,583,'Other ballots remain private until resolution.',11,C['muted'],anchor='end')
save('voting',voting)

death=room_base()+rect(0,0,800,600,'#0e1e25',opacity=.8)+panel(176,126,448,350,16)
death+=text(400,176,'OUT OF PLAY',11,C['muted'],600,'middle',3)+text(400,224,'Your shift has ended.',28,C['pale'],600,'middle')
death+=corpse(400,290,C['amber'],1.3)+lines(400,340,['Your team is still playing.', 'Wait for the station result.'],16,C['muted'],25,anchor='middle')
death+=button(257,407,286,'Watch the station')+text(400,457,'No hidden roles or remote events are revealed.',11,C['muted'],anchor='middle')
save('death',death)

gameover=rect(0,0,800,600,C['void'])+text(24,38,'A /  END OF SHIFT',13,C['muted'],600,spacing=1.5)
gameover+=text(400,107,'STATION SECURED',31,C['pale'],600,'middle',2)+text(400,139,'CREW WON  /  IMPOSTOR ELIMINATED',12,C['teal'],600,'middle',1.1)
gameover+=panel(24,166,752,78,10)+text(44,195,'YOUR TEAM',10,C['muted'],600,spacing=1)+text(44,222,'Victory',22,C['teal'],600)+text(295,195,'YOUR SURVIVAL',10,C['muted'],600,spacing=1)+text(295,222,'Out of play',22,C['pale'],600)+text(564,195,'YOUR ASSIGNMENT',10,C['muted'],600,spacing=1)+text(564,222,'Crew · Engineering',16,C['pale'],600)
for i,(name,role,status) in enumerate([('Mara · you','Crew','Eliminated'),('Sera','Crew','Survived'),('Olin','Crew','Survived'),('Rune','Impostor','Ejected')]):
    y=263+i*49;gameover+=rect(24,y,752,43,'#243a42',6)+text(44,y+27,name,15,C['pale'],600)+text(305,y+27,role,14,C['muted'])+text(752,y+27,status,14,C['muted'],anchor='end')
gameover+=button(24,516,207,'Return to lobby')+button(514,516,262,'Play again',True)+text(400,582,'A new match starts with fresh private assignments.',11,C['muted'],anchor='middle')
save('game-over',gameover)

motion=rect(0,0,1600,900,C['void'])+text(38,46,'QUIET INDUSTRIAL / SHIFT WORKERS',22,C['pale'],600,spacing=2)+text(38,75,'Separated helmet, shoulders and boots. Identity is color + callsign; appearance never encodes alignment.',15,C['muted'])
for i,(col,name,pose) in enumerate([(C['amber'],'WHEAT','front'),(C['blue'],'SLATE','front'),(C['teal'],'SAGE','back'),(C['coral'],'CLAY','side')]):
    x=105+i*188;motion+=panel(x-65,103,160,235,12)+worker(x+15,277,col,2.1,pose)+text(x+15,320,name,12,C['muted'],600,'middle',2)
motion+=text(848,128,'DEDICATED BODY ART',13,C['amber'],600,spacing=2)+corpse(1015,243,C['amber'],3)+text(848,315,'Folded limbs + retained helmet / chest colors.',14,C['muted'])
motion+=text(1240,127,'GAME-SIZE CHECK',12,C['muted'],600,spacing=1)+worker(1303,216,C['amber'],.7)+worker(1370,216,C['blue'],.7)+corpse(1450,204,C['teal'],.7)+text(1240,251,'45 px live / 48 px body',13,C['pale'])+text(1240,280,'No role-coded outline.',13,C['muted'])
motion+=line(38,371,1562,371,C['edge'],1)+text(38,412,'0.64s / SERVER-CONFIRMED KILL PRESENTATION',20,C['pale'],600,spacing=1)
frames=[('0 ms','Anticipation', 'weight shift'),('90 ms','Action', 'short arm impulse'),('160 ms','Impact', 'four quiet sparks'),('360 ms','Fall', 'jointed collapse'),('640 ms','Rest', 'corpse replaces pose')]
for i,(time,title,caption) in enumerate(frames):
    x=38+i*309;motion+=panel(x,439,289,277,10)+text(x+20,470,time,14,C['amber'],600,mono=True)
    if i<3:
        motion+=worker(x+91,615,C['blue'],1.08)+worker(x+198,615,C['amber'],1.08)
        if i in (1,2):motion+=line(x+107,579,x+165,579,C['blue'],9)+line(x+161,579,x+172,581,C['pale'],7)
        if i==2:
            for a in (0,90,180,270):
                vx=math.cos(math.radians(a));vy=math.sin(math.radians(a))
                motion+=line(x+179+vx*11,570+vy*11,x+179+vx*20,570+vy*20,C['pale'],3)
    elif i==3:
        motion+=worker(x+76,615,C['blue'],1.08)+f'<g transform="translate({x+199},615) rotate(54)">{worker(0,0,C["amber"],.98)}</g>'
    else:motion+=worker(x+76,615,C['blue'],1.08)+corpse(x+194,608,C['amber'],1.1)
    motion+=text(x+20,660,title,18,C['pale'],600)+text(x+20,687,caption,13,C['muted'])
motion+=lines(38,762,['Authoritative life changes at the confirmed event. Only the visual child eases; the motor and server clock do not wait.', '0–90 ms anticipation · 90–160 ms impulse · 160–360 ms fall · 360–640 ms settle. No camera takeover; reduced motion = impact + body.', 'Witness-scoped sound and VFX only. Event identity deduplicates playback. Death never reveals foreign Alignment.'],15,C['muted'],30)
save('character-motion',motion,1600,900)

system=rect(0,0,1200,900,C['void'])+text(32,47,'QUIET INDUSTRIAL / MATERIAL & INTERFACE SYSTEM',21,C['pale'],600,spacing=1.5)
palette=[('VOID','void'),('GRAPHITE','wall'),('FLOOR','floor'),('PAPER','pale'),('AMBER','amber'),('SEA GLASS','teal'),('SLATE','blue'),('ALERT','coral')]
for i,(name,key) in enumerate(palette):
    x=32+i*146;system+=rect(x,77,128,65,C[key],8,'#6b7e7d',.5)+text(x,164,name,10,C['muted'],600,spacing=.8)+text(x,186,C[key],13,C['pale'],mono=True)
system+=line(32,211,1168,211,C['edge'],1)+text(32,247,'01 / TYPOGRAPHY',13,C['amber'],600,spacing=1.5)
system+=text(32,295,'A quiet working station.',32,C['pale'],600)+text(32,328,'Readable language before technical decoration.',16,C['pale'])+text(32,356,'Main Hall / Главный зал / Shift 07',14,C['muted'])+text(32,388,'00:39  /  4 OF 6 READY',13,C['teal'],mono=True)
system+=lines(648,264,['Production target: Noto Sans + Noto Sans Mono, OFL.', 'Concept previews use portable Arial / Courier fallbacks.', '32 title · 22 section · 16 body · 14 secondary · 12 labels.', 'Cyrillic/Latin/IME must be tested with real font assets.', 'Avoid labels below 12 px in the shipped 800×600 UI.'],14,C['muted'],27)
system+=line(32,419,1168,419,C['edge'],1)+text(32,452,'02 / COMPONENTS',13,C['amber'],600,spacing=1.5)
system+=button(32,478,173,'Continue',True)+button(221,478,173,'Secondary')+button(410,478,173,'Unavailable',disabled=True)+rect(32,542,551,48,'#192d34',8,'#e1b16c',2)+text(48,572,'Message the discussion…',16,C['muted'])
system+=panel(648,465,520,140,12)+text(668,493,'SMOKED GLASS',12,C['amber'],600,spacing=1)+text(668,523,'94% dark body + restrained top highlight',15,C['pale'])+text(668,549,'8–12 px blur only behind large stationary surfaces.',14,C['muted'])+text(668,578,'Low quality: opaque tint; text contrast unchanged.',14,C['muted'])
system+=line(32,637,1168,637,C['edge'],1)+text(32,674,'03 / SHAPE & MOTION',13,C['amber'],600,spacing=1.5)
system+=lines(32,713,['8 px control radius / 12 px panels / 16 px large dialogs.', '8 px spacing unit · 24 px screen safe margin · 40 px buttons.', 'World edges: chamfered steel, 2–3 layered bevel bands.', 'UI hover 80 ms · press 60 ms · panel 160 ms · phase 240 ms.'],15,C['muted'],30)
system+=lines(648,713,['Not every surface glows. Amber is a signal, not a room fill.', 'Character and corpse keep the brightest local silhouettes.', 'UI never reads server authority or hidden remote state.', 'Reduced motion removes travel/overshoot, keeps status text.'],15,C['muted'],30)
save('design-system',system,1200,900)

contact=rect(0,0,1648,2014,C['void'])+text(32,46,'ACTOR / QUIET INDUSTRIAL',27,C['pale'],600,spacing=2)+text(32,76,'Original independent direction · grounded station / smoked-glass interface / readable workers',15,C['muted'])
for i,name in enumerate(('main-hall','lobby','engineering','security','discussion','voting')):
    x=24+(i%2)*812;y=110+(i//2)*628
    content,w,h=PAGES[name]
    contact+=text(x,y+18,name.upper().replace('-',' / '),12,C['amber'],600,spacing=2)
    contact+=f'<svg x="{x}" y="{y+30}" width="800" height="600" viewBox="0 0 {w} {h}">{content}</svg>'
save('contact-sheet',contact,1648,2014)

if __name__=='__main__':
    import cairosvg
    results=[]
    for name,(_,w,h) in PAGES.items():
        cairosvg.svg2png(url=str(ROOT/f'{name}.svg'),write_to=str(ROOT/f'{name}.png'),output_width=w,output_height=h)
        results.append({'file':name,'width':w,'height':h})
    (ROOT/'manifest.json').write_text(json.dumps({'schema':'actor.art-concept.v1','direction':'Quiet Industrial','original_vector_art':True,'production_assets_modified':False,'render':'CairoSVG 2.8.2','pages':results},indent=2)+'\n')
    print(json.dumps({'pages':len(PAGES),'rendered':True}))
