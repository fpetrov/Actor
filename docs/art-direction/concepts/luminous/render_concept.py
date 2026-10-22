#!/usr/bin/env python3
"""Original, editable Luminous Station concept boards. No game assets modified.

Render: DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib PYTHONPATH=build/art-tools
        python3 docs/art-direction/concepts/luminous/render_concept.py
Only CairoSVG is optional; all artwork is authored SVG, not downloaded imagery.
"""
from pathlib import Path
from html import escape
import json
import math

BASE = Path(__file__).resolve().parent
C = dict(void="#09151e", ink="#142937", panel="#203947", floor="#4c6876",
         slate="#718f9e", frost="#d4e4e9", white="#f2f5ed", teal="#9be3d8",
         amber="#e6ba7e", lilac="#b7b9e8", coral="#e39a98", muted="#a8bec9")


class Board:
    def __init__(self, width, height, prefix):
        self.w, self.h, self.p, self.a = width, height, prefix, []
        self.defs = f'''<defs>
        <linearGradient id="{prefix}-bg" x2="1" y2="1"><stop stop-color="#193341"/><stop offset="1" stop-color="#08131c"/></linearGradient>
        <linearGradient id="{prefix}-floor" x2="0.3" y2="1"><stop stop-color="#698591"/><stop offset="1" stop-color="#334d5c"/></linearGradient>
        <linearGradient id="{prefix}-metal" x2="0.4" y2="1"><stop stop-color="#e4eef0"/><stop offset=".5" stop-color="#b2cbd4"/><stop offset="1" stop-color="#7896a6"/></linearGradient>
        <linearGradient id="{prefix}-glass" x2=".3" y2="1"><stop stop-color="#304f60" stop-opacity=".97"/><stop offset="1" stop-color="#152a38" stop-opacity=".98"/></linearGradient>
        <linearGradient id="{prefix}-window" x2=".9" y2="1"><stop stop-color="#133b4c"/><stop offset="1" stop-color="#081b2a"/></linearGradient>
        <radialGradient id="{prefix}-glow"><stop stop-color="#a7f0df" stop-opacity=".27"/><stop offset="1" stop-color="#8adbd1" stop-opacity="0"/></radialGradient>
        <radialGradient id="{prefix}-fog" gradientUnits="userSpaceOnUse" cx="410" cy="355" r="480"><stop offset=".32" stop-color="#08131d" stop-opacity="0"/><stop offset=".64" stop-color="#08131d" stop-opacity=".10"/><stop offset="1" stop-color="#08131d" stop-opacity=".92"/></radialGradient>
        </defs>'''

    def add(self, s): self.a.append(s)
    def url(self, name): return f"url(#{self.p}-{name})"
    def rect(self, x, y, w, h, fill, radius=0, stroke="none", sw=1, opacity=1):
        self.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}" opacity="{opacity}"/>')
    def ellipse(self, x, y, rx, ry, fill, stroke="none", sw=1, opacity=1):
        self.add(f'<ellipse cx="{x}" cy="{y}" rx="{rx}" ry="{ry}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}" opacity="{opacity}"/>')
    def poly(self, pts, fill, stroke="none", sw=1, opacity=1):
        points=" ".join(f"{x},{y}" for x,y in pts)
        self.add(f'<polygon points="{points}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}" stroke-linejoin="round" opacity="{opacity}"/>')
    def line(self, pts, color=C['slate'], width=1, opacity=1):
        points=" ".join(f"{x},{y}" for x,y in pts)
        self.add(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round" opacity="{opacity}"/>')
    def path(self, d, fill="none", stroke=C['slate'], sw=1, opacity=1):
        self.add(f'<path d="{d}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}" stroke-linecap="round" stroke-linejoin="round" opacity="{opacity}"/>')
    def text(self, x, y, text, size=14, color=C['frost'], weight=400, spacing=0, anchor="start"):
        self.add(f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" font-weight="{weight}" letter-spacing="{spacing}" text-anchor="{anchor}">{escape(str(text))}</text>')
    def group(self, x=0,y=0,s=1): self.add(f'<g transform="translate({x} {y}) scale({s})">')
    def end(self): self.add('</g>')
    def panel(self,x,y,w,h):
        self.rect(x,y+7,w,h,C['void'],14,opacity=.35)
        self.rect(x,y,w,h,self.url('glass'),14,"#668a9b",.8)
        self.line([(x+16,y+1),(x+w-16,y+1)],C['frost'],1,.23)
    def tag(self,x,y,label,color=C['teal'],width=None):
        w=width or 14+len(label)*6.7
        self.rect(x,y,w,23,C['ink'],7,color,.8)
        self.text(x+8,y+16,label,11,color,600)
    def button(self,x,y,w,label,primary=False,selected=False):
        fill=C['teal'] if primary else '#375363' if selected else '#1b3444'
        self.rect(x,y,w,40,fill,10,C['teal'] if selected else '#628494',.9)
        self.text(x+w/2,y+25,label,14,C['ink'] if primary else C['white'],600,anchor='middle')
    def svg(self):
        return f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" viewBox="0 0 {self.w} {self.h}" font-family="Arial, sans-serif">{self.defs}<g stroke-linejoin="round">'+''.join(self.a)+'</g></svg>'


def prism(b,x,y,s=1):
    b.group(x,y,s)
    b.poly([(0,-26),(21,-1),(0,25),(-21,0)],C['teal'])
    b.poly([(0,-26),(0,25),(-21,0)],'#eef9ed',opacity=.88)
    b.poly([(0,-26),(21,-1),(0,3)],'#b5e1de')
    b.line([(0,-26),(0,25),(21,-1)],'#f1fff5',1.1)
    b.end()


def character(b,x,y,s=.8,skin=None,direction='front',step=0,lean=0):
    skin=skin or C['teal']; b.group(x,y,s)
    b.ellipse(0,3,24,7,'#091724',opacity=.48)
    if lean:b.add(f'<g transform="rotate({lean})">')
    b.poly([(-15,-22),(-3,-22),(-4,step),( -15,step)],'#132934',C['void'],2)
    b.poly([(3,-22),(15,-22),(15,-step),(4,-step)],'#132934',C['void'],2)
    b.poly([(-22,-43),(-29,-27),(-25,-15),(-17,-21),(-13,-39)],'#6d8b9b',C['void'],2)
    b.poly([(21,-43),(29,-27),(25,-15),(17,-21),(13,-39)],'#6d8b9b',C['void'],2)
    b.poly([(-19,-45),(18,-45),(25,-17),(17,-12),(-18,-12),(-25,-18)],skin,C['void'],2.4)
    b.poly([(-19,-45),(-10,-46),(-5,-16),(-18,-12),(-25,-18)],'#eff3e8',opacity=.66)
    b.line([(2,-40),(5,-20)],'#183341',2)
    b.rect(-20,-78,40,43,'#142b37',19,C['void'],2.5)
    b.poly([(-19,-60),(-15,-75),(0,-82),(15,-74),(19,-60),(13,-68),(0,-73),(-13,-68)],'#35515e',C['void'],1.4)
    b.line([(-17,-64),(-14,-71),(-8,-74)],skin,2.5,.7)
    if direction=='back':
        b.line([(0,-72),(0,-47)],'#4c6875',1)
        b.line([(-8,-45),(0,-41),(9,-45)],'#73919d',1.5)
    elif direction=='side':
        b.ellipse(12,-58,3,6,C['white'])
    else:
        b.ellipse(-7,-58,3,6,C['white'])
        b.ellipse(7,-58,3,6,C['white'])
    b.rect(11,-39,5,8,C['white'],1)
    if lean:b.end()
    b.end()


def corpse(b,x,y,s=1,skin=None):
    skin=skin or C['lilac'];b.group(x,y,s)
    b.ellipse(0,9,44,12,'#081522',opacity=.5)
    b.poly([(-38,0),(-20,-13),(5,-10),(16,6),(-3,20),(-30,17)],skin,C['void'],2.5)
    b.poly([(-32,1),(-13,-4),(-7,15),(-30,17)],'#d8e2e1',opacity=.65)
    b.poly([(-35,5),(-43,7),(-40,17),(-30,16)],'#172f3b',C['void'],2)
    b.poly([(-20,15),(-18,25),(-7,23),(-8,14)],'#172f3b',C['void'],2)
    b.rect(6,-17,37,29,'#142b37',12,C['void'],2.5)
    b.line([(18,-6),(23,-5)],'#8faeb8',1.3)
    b.line([(30,-5),(34,-4)],'#8faeb8',1.3)
    b.poly([(6,-17),(18,-21),(38,-14),(43,-5),(29,-11),(14,-11)],'#35515e',C['void'],1.2)
    b.end()


def terminal(b,x,y,kind='task',accent=None):
    accent=accent or C['teal']; b.group(x,y)
    b.ellipse(0,21,27,9,'#122a38',opacity=.7)
    b.poly([(-24,4),(23,4),(28,16),(20,23),(-21,23),(-28,16)],'#6e8996',C['ink'],2)
    b.rect(-24,-24,48,33,b.url('metal'),7,C['ink'],2)
    b.rect(-17,-17,34,18,'#17384a',4)
    if kind=='meeting':
        b.ellipse(0,-8,7,7,'none',accent,2)
        b.line([(-10,-8),(10,-8)],accent,1.4)
    elif kind=='security':
        for dx in (-9,0,9):b.line([(dx,-13),(dx,-3)],accent,2)
    else:
        b.poly([(0,-15),(8,-10),(8,-4),(0,1),(-8,-4),(-8,-10)],'none',accent,1.7)
    b.rect(-5,13,10,3,accent,1)
    b.end()


def hud(b,room='Main Hall',role='Crew · Engineer',task='Validate pressure',progress='2 / 7'):
    b.panel(18,16,224,49); b.ellipse(34,32,3,3,C['teal'])
    b.text(45,36,room,16,C['white'],600);b.text(32,55,'EXPLORATION',10,C['muted'],600,1.6)
    b.panel(302,16,196,49);b.text(318,36,'Crew progress',12,C['muted'])
    b.text(480,36,progress,13,C['frost'],600,anchor='end');b.rect(318,47,164,4,'#142c3a',2);b.rect(318,47,49,4,C['teal'],2)
    b.panel(594,16,188,49);b.text(610,36,'YOU',10,C['muted'],600,1.3);b.text(610,55,role,14,C['white'],600)
    b.panel(18,520,204,60);b.text(33,541,'Current task',11,C['muted']);b.text(33,562,task,14,C['white'],600)
    b.button(611,540,171,'Messages  ·  1')
    b.text(22,503,'WASD move  ·  T messages',11,C['muted'])


def room_floor(b,kind):
    b.rect(0,0,800,600,b.url('bg'))
    b.ellipse(400,268,380,240,b.url('glow'))
    if kind=='hall':
        floor=[(81,171),(201,114),(318,102),(505,102),(624,132),(701,198),(704,252),(800,252),(800,349),(684,349),(655,408),(565,447),(473,447),(473,600),(336,600),(336,447),(226,447),(131,409),(81,340)]
    else:
        floor=[(128,142),(272,100),(523,100),(674,159),(707,283),(666,415),(535,459),(489,459),(489,600),(345,600),(345,459),(222,449),(119,379),(96,263)]
    b.poly([(x,y+24) for x,y in floor], '#0b202e','#06111b',8)
    b.poly(floor,b.url('floor'),'#142f3e',16)
    b.poly(floor,'none','#a4c2cd',2)
    # Large quiet plate seams follow the floor; details stay near boundaries.
    b.line([(220,155),(270,230),(273,395)],'#93adb7',1,.3)
    b.line([(555,154),(525,230),(525,410)],'#93adb7',1,.3)
    b.line([(135,323),(665,323)],'#92adb8',1,.3)
    b.line([(336,480),(472,480)],'#9bb5bd',1,.4)
    b.line([(336,496),(472,496)],'#9bb5bd',1,.2)
    b.poly([(133,151),(219,110),(584,110),(660,162),(649,185),(569,140),(224,139),(143,180)],b.url('metal'),'#234553',1.5)
    b.poly([(145,146),(221,119),(575,119),(642,153),(635,166),(570,133),(226,132),(151,160)],b.url('window'))
    for x in (222,310,405,499,574):b.line([(x,119),(x,137)],'#96b5c4',3)
    # Light rails communicate architecture, not remote dynamic activity.
    b.line([(115,216),(115,299)],C['teal'],4,.7)
    b.line([(676,224),(676,248)],C['teal'],4,.7)
    b.line([(354,462),(354,508)],C['teal'],3,.6)
    b.line([(454,462),(454,508)],C['teal'],3,.6)


def hall():
    b=Board(800,600,'hall');room_floor(b,'hall')
    b.ellipse(403,302,132,80,'none','#8da6b2',2,.6)
    b.ellipse(403,302,108,64,'none','#8da6b2',1,.3)
    b.line([(403,220),(403,382)],'#b6c9ce',1,.22)
    b.line([(266,302),(540,302)],'#b6c9ce',1,.22)
    b.ellipse(403,185,95,61,b.url('glow'))
    prism(b,403,161,1.0)
    b.line([(386,193),(418,193)],C['teal'],2,.5)
    terminal(b,177,221,'meeting')
    terminal(b,620,212)
    b.text(403,435,'CONCOURSE  /  01',13,'#c3d4d9',600,2,anchor='middle')
    b.rect(81,246,18,57,'#173341',3)
    for dy in (254,270,286):b.line([(83,dy),(97,dy+8)],C['amber'],3,.85)
    character(b,309,327,.67,C['amber'])
    character(b,568,290,.67,C['lilac'],'side')
    character(b,421,388,.78,C['teal'])
    b.ellipse(421,392,25,8,'none',C['teal'],1.5,.9)
    b.text(421,415,'YOU',10,C['white'],600,1.4,anchor='middle')
    b.rect(0,70,800,450,b.url('fog'))
    hud(b)
    b.panel(277,544,296,36);b.tag(288,551,'E',C['teal'],24);b.text(322,568,'Inspect meeting console',13,C['frost'])
    return b


def engineering():
    b=Board(800,600,'eng');room_floor(b,'engineering')
    for x in (175,212,249):
        b.rect(x,185,23,123,'#233f4e',5,'#7d9daa',1)
        b.rect(x+6,192,5,105,C['amber'],2,opacity=.75)
        for yy in (212,239,266):b.line([(x+13,yy),(x+21,yy)],'#bad2d9',2,.7)
    b.ellipse(427,340,108,69,'#193746','#89aab9',3)
    b.ellipse(427,328,94,62,b.url('metal'),'#213d4c',3)
    b.ellipse(427,318,66,43,'#17394b','#cee7e6',2)
    b.ellipse(427,317,91,64,b.url('glow'))
    b.poly([(406,294),(438,289),(453,316),(432,339),(405,331),(397,314)],C['teal'],C['white'],1)
    b.poly([(406,294),(432,313),(432,339),(405,331),(397,314)],'#5b9d9e')
    b.line([(347,348),(363,373),(385,384)],C['amber'],3)
    b.line([(507,347),(490,372),(467,382)],C['amber'],3)
    terminal(b,593,235,'task',C['amber'])
    b.text(430,436,'THERMAL SYSTEMS  /  02',12,C['frost'],600,1.7,anchor='middle')
    character(b,570,360,.75,C['teal'],'side')
    b.rect(0,70,800,450,b.url('fog'));hud(b,'Engineering','Crew · Engineer','Balance the flow')
    b.panel(277,544,296,36);b.tag(288,551,'E',C['amber'],24);b.text(322,568,'Balance thermal flow',13,C['frost'])
    return b


def security():
    b=Board(800,600,'sec');room_floor(b,'security')
    for x in (170,550):
        b.poly([(x,181),(x+60,168),(x+60,296),(x,309)],'#9ab7c3','#d5e9e7',1.5)
        b.poly([(x+8,188),(x+52,179),(x+52,285),(x+8,298)],'#1d3b50')
        for yy in range(197,278,16):b.line([(x+14,yy),(x+43,yy-5)],C['lilac'],2,.8)
        b.poly([(x,309),(x+60,296),(x+70,312),(x+8,327)],'#385666','#668d9e',1)
    b.rect(320,195,159,74,b.url('metal'),16,C['ink'],2)
    b.rect(332,203,135,41,'#243e52',8)
    for i,hh in enumerate((12,22,15,29,19,26,13)):b.rect(346+i*15,237-hh,7,hh,C['lilac'],2,opacity=.8)
    b.line([(342,249),(455,249)],C['teal'],2)
    terminal(b,401,287,'security',C['lilac'])
    character(b,403,389,.75,C['amber'])
    b.text(402,437,'ARCHIVE  /  03',12,C['frost'],600,2,anchor='middle')
    b.rect(0,70,800,450,b.url('fog'));hud(b,'Security','Crew · Sheriff','Check the records')
    b.panel(277,544,296,36);b.tag(288,551,'E',C['lilac'],24);b.text(322,568,'Check archive terminal',13,C['frost'])
    return b


def lobby():
    b=Board(800,600,'lobby');b.rect(0,0,800,600,b.url('bg'))
    b.ellipse(133,185,240,205,b.url('glow'))
    for x,y,w,h in ((-60,180,270,180),(130,99,160,123),(95,340,240,160)):
        b.poly([(x,y+20),(x+60,y),(x+w,y+20),(x+w,y+h-20),(x+w-55,y+h),(x,y+h-20)],'none','#446d7e',1,opacity=.4)
    prism(b,48,40,.65);b.text(72,45,'ACTOR',17,C['white'],600,3)
    b.text(752,45,'Settings',13,C['muted'],anchor='end')
    b.text(30,116,'AGENTS',45,C['white'],600,1.4);b.text(30,163,'ARENA',45,C['frost'],600,1.4)
    b.text(32,195,'A quiet station. An uncertain crew.',14,C['muted'])
    b.panel(28,225,344,316);b.text(47,255,'Available rooms',19,C['white'],600);b.text(352,255,'Refresh',12,C['teal'],anchor='end')
    for i,(name,mode,players) in enumerate((('The long watch','SOCIAL','4 / 6'),('Night shift','SOCIAL','3 / 4'),('Station workshop','SANDBOX','1 / 8'))):
        yy=279+i*77;b.rect(43,yy,314,65,'#203b4b',9,'#526f80',.5)
        b.text(57,yy+23,name,15,C['white'],600);b.text(57,yy+44,mode,10,C['teal'] if mode=='SOCIAL' else C['amber'],600,1.2)
        b.text(291,yy+23,players,12,C['muted'],anchor='end');b.text(341,yy+42,'Join',12,C['teal'],600,anchor='end')
    b.panel(392,91,380,450)
    b.text(414,123,'Enter the station',24,C['white'],600)
    b.text(414,153,'Your name',12,C['muted']);b.rect(414,165,336,41,'#102a3a',9,'#608292',.8);b.text(428,191,'Iris',16,C['white'])
    b.text(414,232,'Play as',12,C['muted']);b.button(414,244,164,'Human',selected=True);b.button(586,244,164,'Agent')
    b.text(414,313,'Game mode',12,C['muted']);b.button(414,325,204,'Social Deduction',selected=True);b.button(626,325,124,'Sandbox')
    b.text(414,393,'Join by code',12,C['muted']);b.rect(414,404,215,41,'#102a3a',9,'#608292',.8);b.text(430,431,'A B C 1 2 3',16,C['muted'],600,2);b.button(639,404,111,'Join',True)
    b.button(414,466,336,'Create a new room',True)
    b.text(28,578,'Quit',13,C['muted']);b.ellipse(619,574,3,3,C['teal']);b.text(632,578,'Connected to station',12,C['muted'])
    return b


PEOPLE=[('Iris',C['teal']),('Milo',C['amber']),('Jun',C['lilac']),('Sera',C['coral']),('Noa','#8ba9d2'),('Ari','#c7ceaa')]


def meeting_header(b,phase,time):
    b.rect(0,0,800,600,b.url('bg'))
    for x in (72,160,640,728):b.line([(x,90),(400,300),(x,510)],'#648595',1,.10)
    b.text(24,32,'MEETING  01',10,C['teal'],600,2.2)
    b.text(24,65,phase,28,C['white'],600)
    b.tag(675,28,time,C['teal'],99)
    b.text(775,69,'Movement paused',11,C['muted'],anchor='end')


def discussion():
    b=Board(800,600,'discussion');meeting_header(b,'Speak. Compare. Question.','00:42')
    b.panel(20,90,180,488);b.text(36,119,'Participants',14,C['white'],600)
    for i,(name,skin) in enumerate(PEOPLE):
        yy=151+i*65
        character(b,54,yy+27,.35,skin)
        b.text(80,yy+1,name,14,C['frost'],600)
        b.text(80,yy+20,'Out' if i==4 else 'Alive · you' if i==0 else 'Alive',11,C['muted'])
        if i==4:b.line([(32,yy+10),(70,yy+10)],C['slate'],1.5)
    b.panel(214,90,566,488);b.text(232,119,'Discussion',14,C['white'],600);b.tag(649,103,'LOCAL CLAIMS',C['muted'],112)
    messages=[('Milo','00:18','I found a body in Storage.',C['amber'],False),('Jun','00:22','Who used the east passage?',C['lilac'],False),('You','00:26','Я был в восточном коридоре.',C['teal'],True),('Sera','00:31','I did not see the attack.',C['coral'],False)]
    for i,(name,time,text,skin,own) in enumerate(messages):
        yy=143+i*82;xx=265 if own else 232;ww=492 if own else 480
        b.rect(xx,yy,ww,65,'#365866' if own else '#294857',10,'#638696' if own else '#3f6274',.6)
        b.ellipse(xx+15,yy+17,3,3,skin);b.text(xx+26,yy+21,name,12,skin,600);b.text(xx+ww-14,yy+21,time,10,C['muted'],anchor='end')
        b.text(xx+14,yy+47,text,15,C['white'])
    b.text(233,490,'Messages are player statements, not verified facts.',11,C['muted'])
    b.rect(232,510,435,46,'#102a3a',10,C['teal'],.8);b.text(246,539,'Ask about what others saw…',14,C['muted']);b.button(681,513,81,'Send',True)
    return b


def voting():
    b=Board(800,600,'vote');meeting_header(b,'Choose carefully. Vote once.','00:27')
    for i,(name,skin) in enumerate(PEOPLE):
        col,row=i%3,i//3;xx=24+col*256;yy=100+row*151;selected=i==2;out=i==4
        b.panel(xx,yy,240,135)
        if selected:b.rect(xx,yy,240,135,'none',14,C['teal'],2.2)
        character(b,xx+48,yy+105,.64,skin)
        b.text(xx+88,yy+46,name,20,C['white'] if not out else C['slate'],600)
        b.text(xx+88,yy+70,'Out of play' if out else 'Your candidate' if selected else 'Eligible',12,C['teal'] if selected else C['muted'])
        b.ellipse(xx+214,yy+23,7,7,C['teal'] if selected else 'none',C['teal'] if selected else C['slate'],1.2)
        if out:b.line([(xx+23,yy+55),(xx+72,yy+98)],C['slate'],2)
    b.panel(24,421,752,148)
    b.text(44,451,'Selected: Jun',18,C['white'],600)
    b.text(44,475,'Your ballot stays private until the server resolves voting.',13,C['muted'])
    b.button(44,505,180,'Skip this vote');b.button(492,505,264,'Confirm vote for Jun',True)
    b.text(24,591,'Submitted state: replace these controls with “Ballot locked”. No edit, no accidental second vote.',11,C['muted'])
    return b


def waiting_room():
    b=Board(800,600,'waiting');b.rect(0,0,800,600,b.url('bg'))
    prism(b,38,38,.55);b.text(59,44,'ACTOR / AGENTS ARENA',13,C['white'],600,1.5)
    b.text(24,95,'The long watch',30,C['white'],600);b.tag(619,76,'SOCIAL DEDUCTION',C['teal'],157)
    b.panel(24,120,468,382);b.text(44,151,'Crew manifest',18,C['white'],600);b.text(472,151,'4 / 6 players',12,C['muted'],anchor='end')
    for i in range(6):
        col,row=i%2,i//2;xx=42+col*223;yy=174+row*101
        b.rect(xx,yy,207,86,'#233e4e' if i<4 else '#172f3f',10,'#55798a',.6)
        if i<4:
            name,skin=PEOPLE[i];character(b,xx+36,yy+68,.56,skin);b.text(xx+72,yy+33,name+(' · you' if i==0 else ''),14,C['white'],600)
            b.text(xx+72,yy+57,'Ready' if i in (1,2,3) else 'Not ready',12,C['teal'] if i else C['amber'])
        else:b.text(xx+103,yy+49,'Open place',13,C['slate'],anchor='middle')
    b.panel(508,120,268,382);b.text(528,151,'Room details',18,C['white'],600)
    b.text(528,189,'ROOM CODE',10,C['muted'],600,1.6);b.text(528,224,'A B C 1 2 3',23,C['white'],600,2)
    b.button(528,241,228,'Copy room code')
    b.text(528,318,'Start condition',13,C['white'],600)
    b.text(528,344,'At least 4 players ready.',13,C['muted'])
    b.text(528,381,'Assignments are made',13,C['muted']);b.text(528,402,'when the match starts.',13,C['muted'])
    b.tag(528,444,'CONNECTED',C['teal'],115)
    b.button(24,528,188,'Leave room');b.button(508,528,268,'Ready',True)
    b.text(238,553,'Your name does not reveal your side.',12,C['muted'])
    return b


def death():
    b=Board(800,600,'death');b.rect(0,0,800,600,b.url('bg'))
    b.ellipse(400,355,290,250,b.url('glow'));corpse(b,400,185,1.8,C['teal'])
    b.text(400,271,'You are out of play.',34,C['white'],600,anchor='middle')
    b.text(400,309,'Your team can still win.',19,C['muted'],anchor='middle')
    b.panel(168,352,464,149)
    b.text(400,387,'Waiting for the match result',18,C['white'],600,anchor='middle')
    b.text(400,421,'Movement, chat and voting are unavailable.',14,C['muted'],anchor='middle')
    b.text(400,450,'Other assignments remain hidden.',14,C['muted'],anchor='middle')
    b.tag(346,469,'OUT OF PLAY',C['muted'],109)
    b.text(400,556,'Local feedback only · no distant kill sound or remote role reveal',12,C['slate'],anchor='middle')
    return b


def results():
    b=Board(800,600,'results');b.rect(0,0,800,600,b.url('bg'))
    prism(b,61,58,.75);b.text(94,46,'MATCH COMPLETE',11,C['teal'],600,1.8)
    b.text(94,79,'Crew prevailed.',33,C['white'],600)
    b.text(25,126,'Tasks restored the station.',16,C['muted']);b.tag(521,104,'YOU · TEAM WON / DID NOT SURVIVE',C['amber'],255)
    b.panel(24,150,752,345)
    b.text(44,180,'Player',12,C['muted']);b.text(310,180,'Assignment',12,C['muted']);b.text(622,180,'Outcome',12,C['muted'])
    for i,(name,skin) in enumerate(PEOPLE):
        yy=207+i*45
        b.line([(43,yy+28),(756,yy+28)],'#496a7a',.5,.5)
        b.ellipse(52,yy+4,4,4,skin);b.text(70,yy+9,name+(' · you' if i==0 else ''),15,C['white'],600)
        b.text(310,yy+9,'Impostor / Crew duty' if i==3 else 'Crew / Engineer' if i==0 else 'Crew / Station duty',14,C['frost'])
        b.text(622,yy+9,'Survived' if i not in (0,4) else 'Dead',14,C['muted'])
    b.button(24,529,240,'Return to lobby');b.button(474,529,302,'Play another match',True)
    b.text(400,588,'Only the terminal server summary reveals assignments. A rematch creates a fresh identity.',11,C['muted'],anchor='middle')
    return b


def characters():
    b=Board(1600,1050,'motion');b.rect(0,0,1600,1050,b.url('bg'))
    b.text(40,48,'THE SURVEYORS',32,C['white'],600,1.5);b.text(40,77,'A dark hood, twin ivory eyes and a folded mantle. Appearance never encodes Alignment.',16,C['muted'])
    b.panel(34,104,558,297);character(b,166,337,2.35,C['teal']);character(b,341,315,1.7,C['amber'],'side');corpse(b,458,329,1.55,C['lilac'])
    b.text(69,378,'01 / STANDING',12,C['teal'],600,1);b.text(273,378,'02 / SIDE',12,C['amber'],600,1);b.text(426,378,'03 / CORPSE',12,C['lilac'],600,1)
    b.panel(610,104,954,297);b.text(634,138,'Eight directions · one rig · recolourable mantle',20,C['white'],600)
    for i,label in enumerate(('N','NE','E','SE','S','SW','W','NW')):
        xx=671+i*113;character(b,xx,270,.95,PEOPLE[i%6][1],'back' if i in (0,1,7) else 'side' if i in (2,6) else 'front',step=3 if i%2 else 0)
        b.text(xx,303,label,12,C['muted'],600,anchor='middle')
    b.text(634,339,'6 source directions + mirrored left diagonals; rooted feet, stable pivot.',15,C['muted'])
    b.text(634,366,'Corpse is a separate folded pose. Eyes close; profile colour survives.',15,C['muted'])
    b.text(40,451,'0.60 SECOND KILL  /  PRESENTATION AFTER SERVER ACCEPTANCE',21,C['white'],600,1)
    times=['0 ms','80 ms','160 ms','260 ms','420 ms','600 ms']
    stages=['Settle','Anticipation','Contact pulse','Fold','Weight settles','Separate corpse']
    for i in range(6):
        xx=34+i*258;yy=478;b.panel(xx,yy,244,278)
        b.text(xx+15,yy+27,times[i],17,C['teal'],600)
        if i<3:
            character(b,xx+69+(9 if i==2 else 0),yy+193,.9,C['teal'],'side',step=-2 if i==1 else 0,lean=-7 if i==1 else 10 if i==2 else 0)
            character(b,xx+163,yy+193,.9,C['lilac'])
        elif i==3:
            character(b,xx+65,yy+193,.9,C['teal'],'side');corpse(b,xx+164,yy+181,.8,C['lilac'])
        else:
            character(b,xx+58,yy+193,.9,C['teal'],'back' if i==5 else 'side');corpse(b,xx+163,yy+191,.95,C['lilac'])
        if i==1:b.line([(xx+100,yy+129),(xx+113,yy+142)],C['teal'],2)
        if i==2:
            b.poly([(xx+106,yy+122),(xx+134,yy+139),(xx+111,yy+151),(xx+122,yy+135)],'#e4fff2',opacity=.8)
            b.line([(xx+105,yy+112),(xx+122,yy+107),(xx+137,yy+118)],C['teal'],2)
        b.text(xx+15,yy+239,stages[i],14,C['white'],600)
        b.text(xx+15,yy+261,'No blood · no global flash',11,C['muted'])
    b.panel(34,784,748,222);b.text(58,819,'Motion grammar',22,C['white'],600)
    for i,t in enumerate(('Walk: 8 frames, 9–11 fps, 1.5 px mantle lag. No foot sliding.',
                          'UI: 140 ms fade / 180 ms position. No spring overshoot.',
                          'Work & fake work use the same 700 ms hand/light loop.',
                          'Reduced motion removes travel and shake; keeps state cues.')):b.text(58,854+i*35,t,15,C['muted'])
    b.panel(802,784,762,222);b.text(826,819,'Visibility is the effect budget',22,C['white'],600)
    for i,t in enumerate(('Use one server action identity; replay is visually idempotent.',
                          'Only permitted witnesses see local impact / hear local sound.',
                          'Own death: 180 ms local iris fade → “You are out of play”.',
                          'Corpse glow never reveals a body through walls or fog.')):b.text(826,854+i*35,t,15,C['muted'])
    return b


def system():
    b=Board(1600,1050,'system');b.rect(0,0,1600,1050,b.url('bg'))
    b.text(40,49,'LUMINOUS STATION',34,C['white'],600,1);b.text(40,80,'Ceramic ribs / blue mineral floors / light folded into architecture',17,C['muted'])
    swatches=[('Void','void'),('Ink','ink'),('Hull','panel'),('Slate','slate'),('Frost','frost'),('Signal','teal'),('Warmth','amber'),('Archive','lilac'),('Warning','coral')]
    for i,(name,key) in enumerate(swatches):
        xx=40+i*171;b.rect(xx,113,149,65,C[key],9,'#7897a6',.5);b.text(xx,203,name,15,C['frost'],600);b.text(xx,225,C[key].upper(),13,C['muted'])
    b.panel(34,255,748,350);b.text(58,293,'Typography / humanist, not ornamental',24,C['white'],600)
    b.text(58,344,'Meet the crew.',36,C['white'],600)
    b.text(58,382,'01  Exploration / Main Hall',22,C['frost'],600)
    b.text(58,417,'A clear line is stronger than another glowing panel.',16,C['muted'])
    b.text(58,450,'Русский текст: наблюдение ≠ утверждение',16,C['frost'])
    b.text(58,484,'UI body 16 / labels 13 / status 12 / title 32–40',14,C['teal'],600)
    b.text(58,521,'Prototype: installed Arial, no font binaries redistributed.',13,C['muted'])
    b.text(58,547,'Production candidate: Noto Sans + Noto Sans Mono, OFL audit',13,C['muted'])
    b.text(58,570,'and glyph/render checks required before shipping.',13,C['muted'])
    b.panel(802,255,762,350);b.text(826,293,'Components / glass with a firm edge',24,C['white'],600)
    b.button(826,320,208,'Primary action',True);b.button(1048,320,208,'Secondary');b.button(1270,320,264,'Selected',selected=True)
    b.rect(826,382,370,45,'#102a3a',10,C['teal'],1.5);b.text(843,410,'Focused input |',16,C['white'])
    b.rect(1215,382,319,45,'#1d3544',10,'#4d6b7d',1);b.text(1374,410,'Unavailable',15,C['slate'],600,anchor='middle')
    b.tag(826,450,'DELIVERED',C['teal']);b.tag(947,450,'COOLDOWN',C['amber']);b.tag(1080,450,'OUT OF PLAY',C['muted']);b.tag(1223,450,'BALLOT LOCKED',C['frost'])
    b.text(826,510,'Spacing 8 / 12 / 20 / 32 · radius 10 / 14 · controls ≥40 px',15,C['muted'])
    b.text(826,543,'76–96% tinted fill, 1 px rim, restrained top reflection.',15,C['muted'])
    b.text(826,576,'Blur is optional. High-contrast fallback is the baseline.',15,C['teal'],600)
    b.panel(34,632,748,365);b.text(58,672,'One station / four silhouettes',24,C['white'],600)
    rooms=[('MAIN HALL','Light well / radial inlay',C['teal']),('ENGINEERING','Thermal ribs / solid reactor',C['amber']),('SECURITY','Vertical archives / glass',C['lilac']),('STORAGE','Low cassettes / warm straps',C['frost'])]
    for i,(name,note,color) in enumerate(rooms):
        xx=61+(i%2)*358;yy=709+(i//2)*130
        b.rect(xx,yy,326,103,'#2d4a5b',10,'#7e9aa7',1)
        if i==0:prism(b,xx+39,yy+45,.7)
        elif i==1:
            b.ellipse(xx+39,yy+46,24,18,'none',color,5)
            b.line([(xx+23,yy+30),(xx+55,yy+62)],color,2)
        elif i==2:
            for dx in (20,35,50):b.rect(xx+dx,yy+24,9,43,color,2,opacity=.7)
        else:
            for dx,dy in ((15,38),(39,38),(27,18)):b.rect(xx+dx,yy+dy,23,20,color,3,'#183644',2)
        b.text(xx+82,yy+39,name,13,C['white'],600,1)
        b.text(xx+82,yy+65,note,12,C['muted'])
    b.panel(802,632,762,365);b.text(826,672,'Unity production contract',24,C['white'],600)
    lines=['Original vector master → deterministic 2× PNG atlas.',
           'Shared 50 PPU world art; 8-direction pivots stay fixed.',
           'SpriteRenderer / URP 2D; wall, floor and emissive layers separate.',
           'No new collider inferred from a decorative pixel or glow.',
           'UI Toolkit views consume existing permitted ViewModels.',
           'Blur spike must pass 800×600 rendered FPS + fallback tests.',
           'No remote event can light a lamp, animate or sound outside sight.',
           'Sandbox original art remains isolated from Social redesign.']
    for i,t in enumerate(lines):b.text(826,712+i*32,t,15,C['muted'])
    return b


def pair(name,left,right):
    b=Board(1600,600,name)
    b.add(left.svg().replace('<svg ', '<svg x="0" y="0" ',1))
    b.add(right.svg().replace('<svg ', '<svg x="800" y="0" ',1))
    return b


def contact(boards):
    b=Board(1600,1730,'contact');b.rect(0,0,1600,1730,b.url('bg'))
    b.text(40,49,'LUMINOUS STATION',33,C['white'],600,1.4);b.text(40,82,'Independent visual direction / ACTOR Agents Arena / original vector concept',16,C['muted'])
    layout=[('main-hall',40,132,720,540,'01 / MAIN HALL'),('lobby',840,132,720,540,'02 / LOBBY'),
            ('engineering-security',40,739,720,270,'03 / ENGINEERING + SECURITY'),('discussion-vote',840,739,720,270,'04 / DISCUSSION + VOTING'),
            ('character-motion',40,1080,720,472.5,'05 / CHARACTER + CORPSE + 0.60 s KILL'),('style-system',840,1080,720,472.5,'06 / SYSTEM + PRODUCTION')]
    for key,x,y,w,h,title in layout:
        b.text(x,y-16,title,13,C['teal'],600,1.4)
        svg=boards[key].svg().replace('<svg ',f'<svg x="{x}" y="{y}" ',1)
        svg=svg.replace(f'width="{boards[key].w}" height="{boards[key].h}"',f'width="{w}" height="{h}"',1)
        b.add(svg)
    b.text(40,1630,'Signature: folded light / ceramic ribs / surveying mantles / smoked-glass controls.',19,C['white'],600)
    b.text(40,1663,'All scenes are art-direction mockups. No existing source images copied, no live gameplay or Unity rendering claimed.',14,C['muted'])
    b.text(40,1692,'Native 800×600 screen files accompany this editable vector contact sheet.',14,C['muted'])
    return b


def main():
    boards={'main-hall':hall(),'engineering-security':pair('rooms',engineering(),security()),
            'lobby':lobby(),'waiting-room':waiting_room(),'discussion-vote':pair('meetings',discussion(),voting()),
            'death-results':pair('endstates',death(),results()),
            'character-motion':characters(),'style-system':system()}
    boards['contact-sheet']=contact(boards)
    try: import cairosvg
    except (ImportError,OSError):cairosvg=None
    metadata=[]
    for name,board in boards.items():
        data=board.svg();(BASE/(name+'.svg')).write_text(data)
        if cairosvg:cairosvg.svg2png(bytestring=data.encode(),write_to=str(BASE/(name+'.png')))
        metadata.append({'file':name,'width':board.w,'height':board.h,'svg':True,'png_rendered':bool(cairosvg)})
    (BASE/'artifacts.json').write_text(json.dumps({'concept':'Luminous Station','original_art':True,'source_images_copied':False,'game_assets_modified':False,'boards':metadata},indent=2)+'\n')
    print(json.dumps({'boards':len(boards),'png_renderer':bool(cairosvg)}))


if __name__=='__main__':main()
