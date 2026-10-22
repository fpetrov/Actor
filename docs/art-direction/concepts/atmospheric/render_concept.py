"""Original SVG design studies. Run from any cwd; no Unity or external assets.

PNG rendering: PYTHONPATH=build/art-tools python3.12 -m cairosvg ...
All illustration, layout, icon and character geometry below is original.
"""
from pathlib import Path
from html import escape
import json

OUT = Path(__file__).resolve().parent
P = dict(void="#171F29", ink="#26343D", panel="#24313C", paper="#EEE9DC",
         floor="#D9D8C9", quiet="#A5ADA5", sage="#95B6A4", clay="#D9947E",
         lilac="#A7ACC9", gold="#E9C781", muted="#B5C0C2", white="#FAF7F0",
         edge="#5B6A72", shadow="#52615F", darksage="#576E68", danger="#F0B98A")

def attrs(**kw):
    return " ".join(f'{k.replace("_", "-")}="{escape(str(v), quote=True)}"' for k,v in kw.items() if v is not None)

def tag(kind, content="", **kw): return f"<{kind} {attrs(**kw)}>{content}</{kind}>"
def rect(x,y,w,h,fill,rx=0,**kw): return tag("rect",x=x,y=y,width=w,height=h,fill=fill,rx=rx,**kw)
def line(x1,y1,x2,y2,color,width=1,**kw): return tag("line",x1=x1,y1=y1,x2=x2,y2=y2,stroke=color,stroke_width=width,**kw)
def path(d,fill,**kw): return tag("path",d=d,fill=fill,**kw)
def poly(points,fill,**kw): return tag("polygon",points=" ".join(f"{x},{y}" for x,y in points),fill=fill,**kw)
def ellipse(x,y,rx,ry,fill,**kw): return tag("ellipse",cx=x,cy=y,rx=rx,ry=ry,fill=fill,**kw)
def circle(x,y,r,fill,**kw): return ellipse(x,y,r,r,fill,**kw)
def text(x,y,label,size=15,fill=None,weight=400,**kw):
    return tag("text",escape(str(label)),x=x,y=y,font_size=size,fill=fill or P["paper"],font_weight=weight,**kw)
def group(content, transform=None, **kw): return tag("g",content,transform=transform,**kw)
def lines(x,y,labels,size=15,fill=None,leading=22,**kw): return "".join(text(x,y+i*leading,s,size,fill,**kw) for i,s in enumerate(labels))

def icon(x,y,name,color=None,s=1):
    c=color or P["paper"]
    data={
        "check":path("M2 8 6 12 14 3","none",stroke=c,stroke_width=2,stroke_linecap="round",stroke_linejoin="round"),
        "chat":path("M2 2H15V11H8L3 15V11H2Z","none",stroke=c,stroke_width=1.5,stroke_linejoin="round"),
        "task":rect(2,1,12,14,"none",2,stroke=c,stroke_width=1.5)+line(5,5,11,5,c)+line(5,9,11,9,c),
        "report":path("M8 1 16 15H0Z","none",stroke=c,stroke_width=1.5)+line(8,5,8,10,c,2)+circle(8,12,1,c),
        "arrow":path("M1 8H15M10 3 15 8 10 13","none",stroke=c,stroke_width=1.6,stroke_linecap="round",stroke_linejoin="round"),
        "eye":path("M0 8Q8 -1 16 8Q8 17 0 8Z","none",stroke=c,stroke_width=1.5)+circle(8,8,2,c),
        "pause":rect(3,2,3,12,c,1)+rect(10,2,3,12,c,1),
        "sound":path("M0 6H4L9 2V14L4 10H0Z",c)+path("M12 4Q18 8 12 12","none",stroke=c,stroke_width=1.5),
        "cross":line(3,3,13,13,c,1.5)+line(13,3,3,13,c,1.5),
        "dot":circle(8,8,3,c),
        "diamond":poly([(8,0),(16,8),(8,16),(0,8)],c),
        "square":rect(2,2,12,12,c,1),
    }
    return group(data[name],f"translate({x} {y}) scale({s})")

def glass(x,y,w,h,rx=16,opacity=.95):
    return (rect(x,y+7,w,h,"#101A22",rx,opacity=.18)
            +rect(x,y,w,h,P["panel"],rx,fill_opacity=opacity,stroke="#66757D",stroke_opacity=.48)
            +line(x+rx,y+1,x+w-rx,y+1,"#B7C4C8",1,opacity=.25))

def button(x,y,w,label,primary=False,icon_name=None,h=42):
    c=P["clay"] if primary else P["panel"]
    t=P["void"] if primary else P["paper"]
    out=rect(x,y,w,h,c,10,stroke=None if primary else P["edge"],stroke_opacity=.65)
    if icon_name: out+=icon(x+14,y+(h-16)/2,icon_name,t)
    return out+text(x+(40 if icon_name else w/2),y+h/2+5,label,14,t,600,text_anchor="start" if icon_name else "middle")

def pill(x,y,w,label,fill=None,color=None):
    return rect(x,y,w,27,fill or "#35434C",13)+text(x+w/2,y+18,label,12,color or P["paper"],600,text_anchor="middle")

def badge(x,y,color,shape="diamond",s=1):
    return icon(x,y,shape,color,s)

def character(x,y,color=None,s=1,direction="front",pose="idle",mark="diamond",alpha=1):
    """Foot pivot (0,0), two separate boots, narrow face aperture, coat seam.
    Back and diagonals are authored variants; no alignment-dependent geometry.
    """
    c=color or P["clay"]
    mirrored=direction in ("left", "diagonal-left", "back-diagonal-left")
    if direction.endswith("-left"): direction=direction.removesuffix("-left")
    side=direction in ("left","right")
    back=direction in ("back","back-diagonal")
    diagonal=direction in ("diagonal","back-diagonal")
    walk=pose=="walk"
    lean=-5 if pose=="anticipate" else (7 if pose=="impact" else 0)
    boot1=(-12,-13,11,13) if not walk else (-15,-10,11,10)
    boot2=(3,-13,11,13) if not walk else (7,-16,11,16)
    out=ellipse(0,-1,24,7,P["void"],opacity=.17)
    out+=rect(*boot1,P["ink"],3)+rect(*boot2,P["ink"],3)
    body=path("M-17 -46Q-26 -39 -25 -23L-19 -10Q0 -5 20 -10L25 -23Q24 -39 16 -46Z",c)
    body+=path("M2 -42H16L22 -12Q14 -8 3 -9Z",P["ink"],opacity=.12)
    body+=path("M-23 -37 -28 -20 -21 -17 -14 -36Z",c)+path("M18 -38 27 -23 22 -18 12 -35Z",c)
    body+=rect(-29,-22,8,7,P["ink"],3)+rect(21,-23,8,7,P["ink"],3)
    body+=path("M-13 -46 -17 -39 0 -32 17 -39 12 -46Z",P["ink"])
    body+=path("M-17 -67Q-19 -80 -7 -83H8Q21 -79 17 -65L14 -48Q0 -42 -14 -48Z",c)
    body+=path("M4 -80Q19 -78 17 -65L14 -48 5 -46Z",P["ink"],opacity=.16)
    if not back:
        if side:
            body+=path("M8 -73 19 -70 17 -57 8 -55Z",P["ink"])+line(13,-68,13,-60,P["paper"],2)
        elif diagonal:
            body+=path("M-2 -74H13L14 -57 -1 -55Z",P["ink"])+rect(6,-69,3,8,P["paper"],1)
        else:
            body+=rect(-8,-74,16,20,P["ink"],5)+rect(-2,-70,4,10,P["paper"],2)
        body+=line(0,-30,0,-13,P["ink"],1.2,opacity=.4)
        body+=badge(-5,-29,P["paper"],mark,.62)
    else:
        body+=path("M-8 -73H8V-56H-8Z",P["ink"],opacity=.16)
        body+=rect(-11,-33,22,17,P["ink"],4,opacity=.22)+line(-7,-28,7,-28,P["paper"],1.4,opacity=.7)
    out+=group(body,f"rotate({lean} 0 -22)")
    if mirrored: out=group(out,"scale(-1 1)")
    return group(out,f"translate({x} {y}) scale({s})",opacity=alpha)

def corpse(x,y,color=None,s=1,fold=.0,mark="square"):
    c=color or P["lilac"]
    out=ellipse(0,2,44,12,P["void"],opacity=.18)
    out+=path("M-22 -21Q-7 -32 14 -18L29 -8 22 4Q7 10 -13 4L-29 -3Z",c)
    out+=path("M-11 -24 3 -20 13 2 -5 5Z",P["ink"],opacity=.14)
    out+=path("M-36 -27Q-30 -37 -18 -31L-9 -21 -18 -7 -35 -11Z",c)
    out+=path("M-35 -26 -24 -28 -18 -20 -23 -13 -34 -15Z",P["ink"])
    out+=rect(-30,-25,3,7,P["paper"],1,transform="rotate(-24 -30 -25)")
    out+=path("M12 -8 27 -20 35 -16 29 -5 37 0 34 7 22 5Z",P["ink"])
    out+=path("M4 -5 12 -17 19 -13 15 0 22 5 16 11 6 6Z",P["ink"])
    out+=path("M-6 -23 -1 -35 7 -31 5 -17Z",c)
    out+=badge(-7,-10,P["paper"],mark,.65)
    return group(out,f"translate({x} {y}) scale({s})")

def name_tag(x,y,label,color=None,shape="diamond",own=False):
    w=len(label)*8+30
    return rect(x-w/2,y-18,w,26,P["void"],9,opacity=.9)+badge(x-w/2+7,y-10,color or P["paper"],shape,.55)+text(x-w/2+23,y,label,13,P["paper"],600)

def interaction(x,y,label,key="E"):
    w=len(label)*7+58
    return glass(x-w/2,y,w,31,9)+rect(x-w/2+6,y+5,22,21,P["paper"],5)+text(x-w/2+17,y+20,key,12,P["ink"],700,text_anchor="middle")+text(x-w/2+37,y+20,label,13,P["paper"],500)

HALL=[(83,147),(575,147),(575,222),(721,222),(721,385),(575,385),(575,467),(176,467),(176,416),(69,416),(69,254),(83,254)]

def hall_world(dim=False):
    floor="#3F4B50" if dim else P["floor"]
    wall="#27363F" if dim else "#87958E"
    out=poly([(x,y+13) for x,y in HALL],"#101922")+poly(HALL,wall,stroke=wall,stroke_width=19,stroke_linejoin="round")
    out+=poly(HALL,floor)
    # Large quiet planes, not a grid texture.
    out+=path("M83 254H225V416H176V467H575V385H721V222H575V147H83Z",floor)
    if not dim:
        out+=rect(107,170,438,14,"#C5CCBE",4)+rect(110,177,430,3,P["paper"],1,opacity=.75)
        out+=path("M108 276H213V387H108Z","#C5CDBF")+line(250,171,250,442,"#C7CBBC",1)
        out+=line(283,172,283,443,"#C7CBBC",1)
        out+=path("M579 246H701M579 361H701","none",stroke="#BEC6B8",stroke_width=3)
        out+=text(652,310,"EAST LOOP",10,P["darksage"],600,text_anchor="middle",letter_spacing=2)
        # Meeting sculpture: broad asymmetry, quiet clay, interaction plinth.
        out+=ellipse(399,281,59,27,P["shadow"],opacity=.12)
        out+=path("M355 258Q399 237 443 258L443 276Q399 298 355 276Z",P["darksage"])
        out+=ellipse(399,257,44,20,P["sage"])
        out+=path("M377 249 386 211 406 201 424 233 416 258Z",P["clay"])
        out+=path("M386 211 406 201 406 244 377 249Z","#E4AB94")
        out+=line(401,216,401,241,P["paper"],2,opacity=.7)
        out+=rect(164,211,65,16,P["darksage"],5)+rect(170,206,53,12,P["sage"],5)
        out+=rect(100,330,19,55,P["darksage"],5)+rect(104,336,11,43,P["sage"],3)
        out+=rect(490,419,55,16,P["darksage"],5)+rect(496,414,43,12,P["sage"],5)
        out+=rect(488,191,48,37,P["ink"],8)+rect(497,198,30,18,P["sage"],4)+line(503,207,520,207,P["paper"],2)
        out+=circle(512,232,3,P["gold"])
    return out

def top_hud(room="Main Hall",phase="Exploration",time="02:34",life="You are alive"):
    out=glass(20,18,254,56,13)+badge(36,38,P["clay"],"diamond",.9)
    out+=text(60,42,room,17,P["paper"],600)+text(60,61,phase+"  /  "+life,12,P["muted"])
    out+=glass(640,18,140,56,13)+text(659,40,"MATCH",10,P["muted"],600,letter_spacing=1.2)+text(659,63,time,21,P["paper"],600)
    out+=icon(749,36,"pause",P["paper"])
    return out

def bottom_hud(room="hall",warning=False):
    out=glass(20,476,228,104,14)+icon(36,493,"task",P["sage"])+text(61,507,"Your tasks",15,P["paper"],600)
    out+=text(36,533,"Restore systems",13,P["muted"])+text(227,533,"2 / 5",13,P["paper"],600,text_anchor="end")
    out+=rect(36,546,195,4,"#43515A",2)+rect(36,546,78,4,P["sage"],2)+text(36,568,"M  Map      Tab  Details",11,P["muted"])
    out+=glass(548,528,232,52,13)+icon(563,545,"chat",P["paper"])+text(591,550,"Proximity",14,P["paper"],600)+text(591,568,"Enter to talk nearby",11,P["muted"])
    if warning:
        out+=button(572,466,208,"Report body",True,"report")
    return out

def hall_screen():
    out=rect(0,0,800,600,P["void"])+hall_world(True)
    out+=tag("g",hall_world(),clip_path="url(#hall-sight)")
    out+=character(390,393,P["clay"],.72)+name_tag(390,317,"MIRA · YOU",P["clay"])
    out+=character(490,335,P["sage"],.64,direction="diagonal",pose="walk",mark="square")+name_tag(490,268,"ROWAN",P["sage"],"square")
    out+=interaction(399,422,"Call meeting")+top_hud()+bottom_hud()
    out+=text(314,562,"Quiet spaces. Readable company.",11,P["muted"],letter_spacing=.8)
    return out

def room_shell(base,accent):
    out=rect(0,0,800,600,P["void"])
    out+=rect(65,133,670,334,"#101922",28)+rect(65,117,670,334,accent,25)+rect(80,135,640,301,base,18)
    out+=rect(300,429,200,81,base,5)+rect(285,449,15,64,accent)+rect(500,449,15,64,accent)
    out+=rect(97,153,607,10,P["paper"],3,opacity=.22)+line(104,405,696,405,P["ink"],1,opacity=.12)
    return out

def engineering_screen():
    out=room_shell("#B4C8B8",P["darksage"])
    out+=path("M106 185H240V365H106Z","#8DA99A")+rect(119,200,107,145,"#6B887D",11)
    for y in (216,248,280): out+=rect(133,y,79,20,"#BACBBC",6)+circle(200,y+10,3,P["gold"])
    out+=ellipse(434,323,93,43,P["shadow"],opacity=.17)
    out+=path("M365 244Q435 205 505 244V305Q435 349 365 305Z",P["darksage"])
    out+=ellipse(435,244,70,36,"#D6DCC9")+ellipse(435,240,50,26,P["sage"])
    out+=path("M402 235Q435 212 468 235L455 253H416Z",P["paper"])+ellipse(435,235,15,8,P["gold"])
    out+=rect(592,182,83,118,P["ink"],13)+rect(604,195,58,62,P["sage"],6)
    out+=path("M612 228H626L633 209 641 245 647 225H654","none",stroke=P["paper"],stroke_width=2)
    out+=circle(633,277,6,P["gold"])+text(592,329,"LIFE SUPPORT",11,P["darksage"],600,letter_spacing=1)
    out+=character(526,392,P["clay"],.73,direction="diagonal")+name_tag(526,316,"MIRA · YOU",P["clay"])
    out+=interaction(583,417,"Service life support")+top_hud("Engineering",time="02:12")+bottom_hud()
    return out

def security_screen():
    out=room_shell("#C5C6D8","#767D98")
    out+=rect(103,175,125,203,"#ABB0C8",10)+rect(118,191,95,13,"#7F8AA8",3)
    for y in (225,269,313):
        out+=rect(118,y,95,30,"#DFDDE1",6)+line(129,y+11,181,y+11,"#8C97AA",2)+line(129,y+19,164,y+19,"#A0AABD",2)
    out+=path("M346 205H613L645 268V298H327V268Z","#7F88A0")
    out+=path("M346 205H613L631 259H336Z",P["ink"])
    for x in (358,442,526):
        out+=rect(x,218,68,27,P["lilac"],5)+line(x+10,228,x+50,228,P["paper"],2)+line(x+10,237,x+39,237,P["paper"],1)
    out+=rect(373,278,223,5,"#626E88",2)+circle(610,280,4,P["gold"])
    out+=character(457,383,P["lilac"],.73,direction="back",mark="square")+name_tag(457,308,"VALE · YOU",P["lilac"],"square")
    out+=interaction(461,413,"Inspect records")+top_hud("Security",time="01:48")+bottom_hud()
    return out

def lobby_screen():
    out=rect(0,0,800,600,P["void"])
    # Quiet station illustration shares actual geometry language and palette.
    out+=path("M0 216 257 132 408 223V600H0Z","#394B50")
    out+=path("M0 380 204 282 350 362V600H0Z",P["sage"])
    out+=path("M0 485 173 394 350 485V600H0Z",P["floor"])
    out+=ellipse(172,478,70,22,P["shadow"],opacity=.16)
    out+=character(173,466,P["clay"],1.45)+text(36,42,"ACTOR  /  AGENTS ARENA",11,P["muted"],600,letter_spacing=2)
    out+=text(34,116,"Quiet station.",34,P["paper"],500)+text(34,156,"Uneasy company.",34,P["paper"],500)
    out+=lines(36,196,["A social deduction game.","Human and agent. Equal rules."],14,P["muted"],22)
    out+=text(36,549,"MIRA",18,P["ink"],600)+text(36,571,"Identity and colour are yours.",12,P["ink"])
    for i,c in enumerate((P["clay"],P["sage"],P["lilac"],P["gold"])):
        out+=circle(228+i*28,548,10,c,stroke=P["ink"] if i==0 else "none",stroke_width=2)
    out+=glass(366,77,412,501,20)
    out+=text(390,112,"Find your company",25,P["paper"],500)+pill(660,91,92,"ONLINE",fill="#3C554C",color="#C6DFC9")
    out+=text(390,142,"Name",12,P["muted"])+rect(390,152,226,41,"#182630",8,stroke=P["edge"])+text(405,179,"Mira",15)
    out+=button(627,152,126,"Human",False,h=41)
    out+=rect(390,213,364,40,"#17232E",10)+rect(394,217,207,32,"#46535A",8)
    out+=text(497,238,"Social deduction",13,P["paper"],600,text_anchor="middle")+text(676,238,"Sandbox",13,P["muted"],500,text_anchor="middle")
    out+=text(390,285,"PUBLIC ROOMS",11,P["muted"],600,letter_spacing=1)+text(753,285,"Refresh ↻",12,P["paper"],text_anchor="end")
    out+=rect(390,300,364,94,"#36464D",12,stroke=P["sage"],stroke_width=1)
    out+=badge(407,319,P["sage"],"diamond",.7)+text(430,331,"The late shift",17,P["paper"],600)+text(406,358,"4–6 players  ·  3 waiting",13,P["muted"])
    out+=text(406,381,"Ready when everyone is here",12,P["sage"])+button(651,325,88,"Join",True,h=42)
    out+=rect(390,406,364,60,"#2B3A44",11)+text(406,432,"Quiet orbit",16,P["paper"],500)+text(406,452,"In progress  ·  4 players",12,P["muted"])+pill(667,421,70,"Playing")
    out+=button(390,484,174,"Create room",True)+button(579,484,175,"Join by code")
    out+=text(390,556,"Settings",13,P["muted"])+icon(491,543,"sound",P["muted"],.9)+text(753,556,"Connection healthy",12,P["sage"],text_anchor="end")
    return out

ROSTER=[("Mira",P["clay"],"diamond"),("Rowan",P["sage"],"square"),("Vale",P["lilac"],"dot"),("Ivo",P["gold"],"diamond")]

def meeting_back():
    out=rect(0,0,800,600,P["void"])+group(hall_world(True),"translate(0 20)",opacity=.55)
    out+=glass(20,20,760,560,20)
    return out

def discussion_screen():
    out=meeting_back()+text(44,61,"A body was reported",26,P["paper"],500)+pill(617,38,139,"DISCUSSION · 0:42",fill="#4B4B45",color=P["gold"])
    out+=text(44,85,"Share what you observed. Claims belong to their speaker.",13,P["muted"])
    out+=line(44,104,756,104,"#53636D",1,opacity=.5)
    out+=text(44,133,"THIS MEETING",11,P["muted"],600,letter_spacing=1.4)
    for i,(n,c,sh) in enumerate(ROSTER):
        y=160+i*73
        out+=badge(44,y,c,sh,.75)+text(68,y+12,n+(" · you" if i==0 else ""),15,P["paper"],600)
        out+=text(68,y+32,"Reported body" if i==0 else ("Eliminated" if i==2 else "Alive"),12,P["muted"])
        if i==2: out+=line(44,y+7,56,y+7,P["muted"],1.5)
    out+=rect(254,126,1,345,"#52616B",opacity=.55)
    # Fictional UI copy; deliberate distinction between author and assertion.
    msgs=[(142,"MIRA",P["clay"],["I found Vale in the east corridor.","I did not see who was there before."],"Your message · accepted by server"),
          (264,"ROWAN",P["sage"],["I passed Storage. Did anyone see", "the corridor during that time?"],"Player statement"),
          (386,"IVO",P["gold"],["I was in Engineering. That is all", "I can confirm."],"Player statement")]
    for y,n,c,body,foot in msgs:
        out+=badge(279,y,c,"diamond",.55)+text(302,y+10,n,11,c,600,letter_spacing=1)
        out+=lines(279,y+37,body,15,P["paper"],22)+text(279,y+86,foot,12,P["muted"])
    out+=rect(44,497,583,57,"#17252F",11,stroke=P["edge"])+text(62,522,"Discussion · Everyone in this meeting",11,P["muted"])+text(62,544,"Write a message…",15,P["paper"])
    out+=button(640,497,116,"Send",True,"arrow",h=57)
    return out

def voting_screen():
    out=meeting_back()+text(44,61,"Choose a ballot",26,P["paper"],500)+pill(636,38,120,"VOTING · 0:26",fill="#4B4B45",color=P["gold"])
    out+=text(44,85,"Select, then confirm. Your final ballot cannot be changed.",13,P["muted"])
    out+=line(44,104,756,104,"#53636D",1,opacity=.5)
    for i,(n,c,sh) in enumerate(ROSTER):
        x=44+(i%2)*245; y=126+(i//2)*151
        selected=i==1; unavailable=i==2
        out+=rect(x,y,227,133,"#3D4E53" if selected else "#2C3C47",13,stroke=P["sage"] if selected else "#4D5E69",stroke_width=2 if selected else 1)
        out+=character(x+49,y+100,c,.75,mark=sh,alpha=.32 if unavailable else 1)
        out+=text(x+94,y+45,n,18,P["muted"] if unavailable else P["paper"],600)
        out+=text(x+94,y+68,"Eliminated" if unavailable else ("Selected" if selected else "Alive"),12,P["sage"] if selected else P["muted"])
        out+=icon(x+181,y+101,"check" if selected else ("cross" if unavailable else "dot"),P["sage"] if selected else P["muted"],.9)
    out+=rect(551,126,205,284,"#1B2A35",13)+text(570,154,"Before you vote",15,P["paper"],600)
    out+=lines(570,184,["A statement is", "not a fact.","","No public tally", "until voting ends."],14,P["muted"],22)
    out+=icon(570,345,"eye",P["muted"])+text(595,358,"Your ballot is private",11,P["muted"])
    out+=button(44,453,227,"Skip this vote")+text(289,479,"Selected: Rowan",14,P["sage"],600)
    out+=button(552,453,204,"Confirm ballot",True,"check")
    out+=text(44,540,"Only eligible living participants can vote.",12,P["muted"])+text(756,540,"Discussion closed",12,P["muted"],text_anchor="end")
    return out

def character_board():
    out=rect(0,0,1600,1040,P["void"])+text(38,51,"FIELDKEEPERS",28,P["paper"],500)+text(38,80,"Character, corpse and a 0.64-second gesture",15,P["muted"])
    out+=pill(1215,31,347,"APPEARANCE NEVER REVEALS ALIGNMENT")
    out+=rect(38,106,1524,315,"#D5D6C7",19)
    out+=character(158,371,P["clay"],2.5)+character(322,371,P["sage"],2.5,direction="back",mark="square")
    out+=character(486,371,P["lilac"],2.5,direction="right",mark="dot")+character(650,371,P["gold"],2.5,direction="diagonal",pose="walk")
    out+=text(158,402,"FRONT",12,P["ink"],600,text_anchor="middle",letter_spacing=1)+text(322,402,"BACK",12,P["ink"],600,text_anchor="middle",letter_spacing=1)
    out+=text(486,402,"SIDE",12,P["ink"],600,text_anchor="middle",letter_spacing=1)+text(650,402,"DIAGONAL",12,P["ink"],600,text_anchor="middle",letter_spacing=1)
    out+=line(760,137,760,391,"#A5B0A3",1)
    out+=corpse(953,324,P["lilac"],2.6)+text(805,365,"Dedicated folded silhouette",18,P["ink"],600)
    out+=lines(805,389,["Face aperture and colour remain identifiable."],12,P["ink"])
    out+=lines(1182,157,["A narrow face aperture.","A broad coat hem.","Two separate boots.","A centred identity mark."],17,P["ink"],29)
    out+=lines(1182,306,["Soft mass, no armour noise.","No sinister skin or outline.","Body art is never a rotated idle."],13,P["darksage"],23)
    out+=text(38,465,"DIRECTION & WALK RHYTHM",12,P["sage"],600,letter_spacing=1.5)
    dirs=["front","diagonal","right","back-diagonal","back","back-diagonal-left","left","diagonal-left"]
    labels=["S","SE","E","NE","N","NW ↔","W","SW ↔"]
    for i,(d,l) in enumerate(zip(dirs,labels)):
        x=79+i*116
        out+=character(x,573,P["clay"],.95,direction=d,pose="walk" if i%2 else "idle")+text(x,599,l,12,P["muted"],600,text_anchor="middle")
    out+=lines(1050,492,["Six authored directions + mirrored diagonals.","Idle breath 1.8s; 8-frame step at 0.64s.","Foot pivot fixed. Preserve phase on a turn.","Only visual pose moves; physics root stays put."],14,P["muted"],26)
    out+=text(38,659,"KILL / 0.64s / AUTHORITATIVE OBSERVERS ONLY",12,P["sage"],600,letter_spacing=1.5)
    times=["0.00", "0.08", "0.16", "0.28", "0.44", "0.64"]
    phases=["Notice", "Gather", "Contact", "Fold", "Settle", "Recover"]
    for i,(t,p) in enumerate(zip(times,phases)):
        x=38+i*258
        out+=rect(x,682,238,274,"#D5D6C7",13)+text(x+17,710,t+"s",16,P["ink"],600)+text(x+17,933,p.upper(),11,P["darksage"],600,letter_spacing=1)
        if i<3:
            out+=character(x+74+(9 if i==2 else 0),882,P["clay"],1.2,direction="right",pose="anticipate" if i==1 else ("impact" if i==2 else "idle"))
            out+=character(x+162,882,P["lilac"],1.2,direction="front",mark="square")
            if i==2: out+=path(f"M{x+120} 804l10 18 18 -7 -7 18 13 10 -22 1 -7 18 -6 -20 -18 -1 16 -14Z",P["paper"])
        else:
            out+=character(x+73,882,P["clay"],1.2,direction="right",pose="impact" if i==3 else "idle")
            out+=corpse(x+171,879,P["lilac"],.95 if i==3 else 1.15)
            if i==3: out+=path(f"M{x+137} 822Q{x+185} 824 {x+193} 855","none",stroke=P["darksage"],stroke_width=2,stroke_dasharray="3 4")
    out+=text(38,998,"No gore · One quiet impact · No camera shake by default · Reduced motion keeps the same authoritative outcome",15,P["muted"])
    return out

def system_board():
    out=rect(0,0,1600,800,P["void"])+text(38,51,"ATMOSPHERIC MINIMALISM",28,P["paper"],500)+text(38,81,"A quiet station that makes people conspicuous",15,P["muted"])
    colors=[("VOID",P["void"]),("INK",P["ink"]),("PAPER",P["paper"]),("SAGE",P["sage"]),("CLAY",P["clay"]),("LILAC",P["lilac"]),("SIGNAL",P["gold"])]
    for i,(n,c) in enumerate(colors):
        x=38+i*220
        out+=rect(x,114,204,103,c,14,stroke="#65727A",stroke_opacity=.6)+text(x+14,244,n,12,P["paper"],600,letter_spacing=1)+text(x+14,267,c,13,P["muted"])
    out+=text(38,325,"01  TYPOGRAPHY",12,P["sage"],600,letter_spacing=1.5)
    out+=text(38,373,"Quiet is not empty.",34,P["paper"],500)+text(38,407,"Readable at 800 × 600",22,P["paper"],500)
    out+=text(38,441,"15px body / 22px line height / 13px essential labels",15,P["muted"])
    out+=text(38,471,"Noto Sans proposed for Latin + Cyrillic. Mockups use system sans.",13,P["muted"])
    out+=text(38,494,"No font binary bundled; glyph and licence QA precede integration.",13,P["muted"])
    out+=text(810,325,"02  VELLUM COMPONENTS",12,P["sage"],600,letter_spacing=1.5)
    out+=glass(810,348,355,148)+text(832,381,"Connection restored",18,P["paper"],600)+text(832,409,"You can return to your own actor.",14,P["muted"])
    out+=button(831,431,155,"Resume",True,"arrow")+button(1000,431,143,"Leave")
    out+=button(1190,348,337,"Report body",True,"report")+button(1190,405,337,"Ballot submitted",False,"check")
    out+=pill(1190,469,158,"RECONNECTING",fill="#5D5140",color=P["gold"])+pill(1360,469,167,"YOU ARE DEAD")
    out+=text(38,559,"03  MOTION & SOUND",12,P["sage"],600,letter_spacing=1.5)
    for i,(title,desc) in enumerate([("120ms","Hover / focus"),("180ms","Panel entrance"),("240ms","Meeting dim"),("640ms","Kill gesture")]):
        x=38+i*200
        out+=line(x,584,x+170,584,P["edge"],2)+circle(x+10,584,4,P["clay"])+text(x,619,title,26,P["paper"],500)+text(x,646,desc,14,P["muted"])
    out+=lines(883,568,["Dry felt steps. Muted ceramic impacts.","Three soft tones for a phase transition.","No off-screen event sound or role cue.","Reduced motion: opacity only, no shake."],15,P["muted"],26)
    out+=line(38,697,1562,697,P["edge"],1)+text(38,734,"UNITY PLAN",12,P["sage"],600,letter_spacing=1.5)
    out+=text(190,734,"Sprite atlases + existing motor / UI Toolkit candidate / tinted panels work without blur / no runtime SVG dependency",15,P["paper"])
    out+=text(38,771,"Concept renders only. No Unity integration, rendered gameplay, platform performance or interaction acceptance is claimed.",13,P["muted"])
    return out

def outcome_screens():
    dead=rect(0,0,800,600,P["void"])+hall_world(True)
    dead+=tag("g",hall_world(),clip_path="url(#hall-sight)")
    dead+=corpse(397,372,P["clay"],.95,mark="diamond")+top_hud(life="You are dead")
    dead+=glass(156,447,488,132,16)+pill(178,463,112,"YOU ARE DEAD")
    dead+=text(178,512,"Your part in this round is over.",21,P["paper"],500)
    dead+=text(178,537,"You cannot move, act, vote or talk to the living.",14,P["muted"])
    dead+=text(178,561,"Wait for the result. No new hidden information is shown.",12,P["muted"])
    end=rect(0,0,800,600,P["void"])+hall_world(True)
    end+=glass(112,54,576,496,20)+text(400,101,"ROUND COMPLETE",11,P["sage"],600,text_anchor="middle",letter_spacing=2)
    end+=text(400,150,"Crew restored the station",30,P["paper"],500,text_anchor="middle")
    end+=text(400,179,"Your team won. You were eliminated.",15,P["muted"],text_anchor="middle")
    for i,(n,c,sh) in enumerate(ROSTER):
        x=194+i*137
        end+=character(x,312,c,1.05,mark=sh)+text(x,340,n,15,P["paper"],600,text_anchor="middle")
        end+=text(x,362,"Impostor" if i==1 else "Crew",13,P["clay"] if i==1 else P["sage"],text_anchor="middle")
    end+=text(400,403,"Roles are revealed only after the server ends the round.",12,P["muted"],text_anchor="middle")
    end+=button(149,437,240,"Play another round",True,"arrow")+button(408,437,242,"Back to lobby")
    end+=text(400,514,"A new round starts with new identities and private state.",12,P["muted"],text_anchor="middle")
    return dead+group(end,"translate(800 0)")

DEFS='''<defs>
<clipPath id="hall-sight"><circle cx="386" cy="349" r="247"/></clipPath>
</defs>'''

def svg(w,h,content,title):
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-labelledby="title desc">
<title id="title">{escape(title)}</title><desc id="desc">Original Atmospheric Minimalism concept for ACTOR Agents Arena. Fictional UI copy. Vector mockup, not a Unity screenshot.</desc>
{DEFS}<g font-family="Arial, Helvetica, sans-serif" font-feature-settings="tnum" stroke-linejoin="round">{content}</g></svg>'''

def write(name,w,h,content,title):
    (OUT/f"{name}.svg").write_text(svg(w,h,content,title),encoding="utf-8")

SCREENS=[("main-hall",hall_screen,"Main Hall"),("engineering",engineering_screen,"Engineering"),("security",security_screen,"Security"),("lobby",lobby_screen,"Lobby"),("discussion",discussion_screen,"Discussion"),("voting",voting_screen,"Voting")]
for name,fn,title in SCREENS: write(name,800,600,fn(),f"Atmospheric Minimalism — {title}")
write("character-motion",1600,1040,character_board(),"Fieldkeepers: eight directions, dedicated body, kill storyboard")
write("design-system",1600,800,system_board(),"Atmospheric Minimalism: palette, type, components, motion")
write("death-gameover",1600,600,outcome_screens(),"Passive death and authoritative round result")
write("corpse-source",240,160,corpse(120,91,P["lilac"],2.2),"Original dedicated folded-coat corpse, transparent source")

# Complete editable contact sheet. Geometry remains vector, no raster embeds.
sheet=rect(0,0,1680,2570,"#101922")+text(40,54,"AGENTS ARENA  /  ATMOSPHERIC MINIMALISM",27,P["paper"],500)+text(40,84,"Independent whole-game direction · Original editable vector studies · 800 × 600 screen layouts",14,P["muted"])
for i,(name,fn,title) in enumerate(SCREENS):
    x=30+(i%2)*830; y=117+(i//2)*648
    sheet+=group(fn(),f"translate({x} {y})")+text(x+8,y+624,f"0{i+1}  {title.upper()}",12,P["muted"],600,letter_spacing=1)
sheet+=group(character_board(),"translate(30 2080) scale(.38)")
sheet+=group(system_board(),"translate(680 2080) scale(.60)")
write("contact-sheet",1680,2570,sheet,"Atmospheric Minimalism — complete independent direction")
(OUT/"tokens.json").write_text(json.dumps({"concept":"Atmospheric Minimalism","palette":P,"viewport":[800,600],"spacing":[4,8,12,16,24,32],"typography":{"body":15,"essential_label":13,"title":26,"line_height":22},"motion_ms":{"focus":120,"panel":180,"meeting":240,"kill":640},"status":"concept_only"},indent=2)+"\n")
print(f"Wrote {len(SCREENS)+5} SVG originals and tokens in {OUT.name}")
