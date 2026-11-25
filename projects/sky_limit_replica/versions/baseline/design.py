"""Visual reconstruction of 夜言-碎雨's BV11U4y127EJ, from cover and tutorial frames.

Minecraft coordinates; Y=45 is the lower orbital promenade. Original vanilla
palette intentionally retained in BMC4. No terrain or unrelated additions.
"""
from math import hypot, atan2, pi, cos, sin
from src.core.building import Building

Q = 'minecraft:smooth_quartz'
P = 'minecraft:quartz_pillar'
C = 'minecraft:chiseled_quartz_block'
S = 'minecraft:quartz_slab'
T = 'minecraft:quartz_stairs'
I = 'minecraft:packed_ice'
G = 'minecraft:prismarine'
D = 'minecraft:dark_prismarine'
L = 'minecraft:sea_lantern'
R = 'minecraft:end_rod'


def build():
    b = Building('天空即为极限 · 视频视觉复刻')
    groups = {}
    def put(part, x, y, z, block=Q, **states):
        p = (int(x), int(y), int(z))
        b.place(p, block, **states)
        groups[p] = part
    def slab(part, x, y, z, top=False):
        put(part,x,y,z,S,type='top' if top else 'bottom',waterlogged='false')
    def rod(part,x,y,z):
        put(part,x,y,z,R,facing='up')
    def disc(part,cx,cz,y,r,block=Q,inside=0):
        for x in range(-r-1,r+2):
            for z in range(-r-1,r+2):
                if inside <= hypot(x,z) <= r+.25:
                    put(part,cx+x,y,cz+z,block)
    def line(part,a,c,block=Q):
        n=max(abs(c[i]-a[i]) for i in range(3))
        for k in range(n+1):
            xyz=[round(a[i]+(c[i]-a[i])*k/max(1,n)) for i in range(3)]
            put(part,*xyz,block)
    def tier(part,cx,cz,y,r):
        disc(part,cx,cz,y,r)
        for x in range(-r-1,r+2):
            for z in range(-r-1,r+2):
                if r-.7 < hypot(x,z) <= r+.65:
                    slab(part,cx+x,y,cz+z)
    def orbit(radius,y,label):
        # A narrow inhabitable ribbon, with alternating luminous inlay and
        # blue upper edge. Void is preserved both inside and outside the ring.
        for x in range(-radius-3,radius+4):
            for z in range(-radius-3,radius+4):
                r=hypot(x,z)
                if radius-2.1 <= r <= radius+1.25:
                    put(label+'/Walkway',x,y,z,Q)
                    if r>radius+.1 or r<radius-1.35:
                        put(label+'/Underside',x,y-1,z,G)
                        slab(label+'/Cornice',x,y-2,z)
                    if radius+.3 < r <= radius+1.25:
                        a=atan2(z,x)
                        put(label+'/Ice frieze',x,y+1,z,I)
                        if int(round(a*radius))%9==0:
                            put(label+'/Light inlay',x,y+1,z,L)
                        slab(label+'/White coping',x,y+2,z)
                if radius-2.7 < r < radius-2.0:
                    slab(label+'/Inner lip',x,y,z,True)
    def buttress(cx,cz,y,part):
        # Small lantern pavilions: fluted stem, flared corbels, flower bowl,
        # mint hanging ornaments and a little blue-white beacon crown.
        for j in range(-14,0):
            put(part+'/Pendant',cx,y+j,cz,L if j in (-11,-4) else P,**({} if j in (-11,-4) else {'axis':'y'}))
        rod(part+'/Pendant',cx,y-16,cz)
        put(part+'/Pendant',cx,y-15,cz,G)
        for dx,dz in ((1,0),(-1,0),(0,1),(0,-1)):
            for j in range(-6,6):
                put(part+'/Pillar',cx+dx,y+j,cz+dz,P,axis='y')
            put(part+'/Base lamps',cx+dx,y+1,cz+dz,L)
        for j,r in ((4,2),(5,3),(6,4),(7,5)):
            tier(part+'/Flared bowl',cx,cz,y+j,r)
        disc(part+'/Bowl ice',cx,cz,y+8,3,I)
        for dx,dz in ((1,0),(-1,0),(0,1),(0,-1)):
            for k in range(2,7):
                slab(part+'/Petal tips',cx+dx*k,y+8+(k==6),cz+dz*k)
            for yy in range(y+3,y+7):
                put(part+'/Hanging mint',cx+dx*4,yy,cz+dz*4,G)
            rod(part+'/Hanging light',cx+dx*4,y+2,cz+dz*4)
        put(part+'/Beacon',cx,y+9,cz,G)
        put(part+'/Beacon',cx,y+10,cz,L)
        tier(part+'/Beacon',cx,cz,y+11,1)
        slab(part+'/Beacon',cx,y+12,cz)

    # Lower double orbit and four diagonal spokes, as visible in the plan frames.
    orbit(46,45,'01 Lower orbit/Outer ring')
    orbit(32,45,'01 Lower orbit/Inner ring')
    for sx,sz in ((1,1),(1,-1),(-1,1),(-1,-1)):
        for k in range(5,33):
            for w in (-1,0,1):
                put('02 Lower bridges/Diagonal spokes',sx*k+w,45,sz*k,Q)
            if k%8==0:
                put('02 Lower bridges/Lights',sx*k,45,sz*k,L)
        # Satellites sit on the outer orbit, midway between the narrow bridges.
    for cx,cz in ((46,0),(-46,0),(0,46),(0,-46)):
        buttress(cx,cz,45,'03 Satellite pavilions/'+str((cx,cz)))
    disc('04 Lower core/Floor',0,0,45,7)
    for yy,r in ((27,3),(28,4),(29,5),(30,5),(31,4),(44,6),(46,6)):
        tier('04 Lower core/Mouldings',0,0,yy,r)
    for yy in range(30,45):
        for x in range(-3,4):
            for z in range(-3,4):
                if max(abs(x),abs(z))==3:
                    put('04 Lower core/Carved shaft',x,yy,z,G if (x+z+yy)%5==0 else Q)
        for x,z in ((4,0),(-4,0),(0,4),(0,-4)):
            put('04 Lower core/Flutes',x,yy,z,P,axis='y')
    # Long central needle and the four open, bow-shaped ribs below the shaft.
    for yy in range(0,28):
        put('05 Lower finial/Central needle',0,yy,0,L if yy%9==0 else P,**({} if yy%9==0 else {'axis':'y'}))
    for dx,dz in ((1,0),(-1,0),(0,1),(0,-1)):
        for yy in range(2,29):
            r=round(2+9*max(0,sin(pi*(yy-2)/26))**1.3)
            put('05 Lower finial/White bow ribs',dx*r,yy,dz*r,Q)
            put('05 Lower finial/Mint lining',dx*(r-1),yy,dz*(r-1),G)
            if yy in (9,18,26):
                put('05 Lower finial/Bow lights',dx*r,yy,dz*r,L)
        for yy in range(6,27):
            rod('05 Lower finial/Light strings',dx*2,yy,dz*2)
    # Open central suspension above the lower promenade: four sculpted legs.
    for dx,dz in ((1,0),(-1,0),(0,1),(0,-1)):
        for yy in range(46,67):
            r=round(3+7*(yy-46)/20)
            put('06 Upper suspension/Outward legs',dx*r,yy,dz*r,L if yy in (49,58,64) else Q)
            if yy>56:
                put('06 Upper suspension/Flutes',dx*r+dz,yy,dz*r+dx,P,axis='y')
        for yy in range(48,66):
            put('06 Upper suspension/Thin inner spine',dx*2,yy,dz*2,D if yy%4 else L)
        for yy in range(58,66):
            r=10+(yy-58)//2
            slab('06 Upper suspension/Hanging fins',dx*r,yy,dz*r)
    orbit(18,66,'07 Upper orbit')
    disc('08 Tower terrace/Floor',0,0,69,11)
    for yy,r in ((65,6),(66,8),(67,10),(68,12)):
        tier('08 Tower terrace/Blue base',0,0,yy,r)
        if yy==68:
            disc('08 Tower terrace/Ice rim',0,0,yy,11,I,10)
    # Four short bridges lie on the ring datum; shallow stairs reach the terrace.
    for dx,dz in ((1,0),(-1,0),(0,1),(0,-1)):
        for k in range(11,18):
            for w in (-1,0,1):
                put('08 Tower terrace/Radial bridges',dx*k+dz*w,66,dz*k+dx*w)
        for k,y in ((10,67),(9,68),(8,69)):
            for w in (-1,0,1):
                put('08 Tower terrace/Steps',dx*k+dz*w,y,dz*k+dx*w,T,
                    facing={ (1,0):'west',(-1,0):'east',(0,1):'north',(0,-1):'south'}[(dx,dz)],
                    half='bottom',shape='straight',waterlogged='false')
        # Tiny lotus turrets at the terrace corners of the composition.
        for yy,r in ((70,1),(71,2),(72,3),(73,2)):
            tier('08 Tower terrace/Lotus turrets',dx*10,dz*10,yy,r)
        put('08 Tower terrace/Lotus lamps',dx*10,74,dz*10,L)
        slab('08 Tower terrace/Lotus lamps',dx*10,75,dz*10)
    # Main white lantern tower: a wider lower drum, balcony and slimmer belfry.
    for yy in range(70,89):
        rr=6 if yy<80 else 5
        for x in range(-rr,rr+1):
            for z in range(-rr,rr+1):
                h=hypot(x,z)
                if rr-.9<h<rr+.4:
                    # Clear tall openings between the eight narrow buttresses.
                    a=atan2(z,x)
                    pier=abs(sin(4*a))<.45
                    band=yy in (70,71,78,79,80,87,88)
                    if pier or band:
                        put('09 Main tower/Lantern walls',x,yy,z,Q if band else P,**({} if band else {'axis':'y'}))
                    elif yy in (73,82):
                        put('09 Main tower/Mint sill',x,yy,z,G)
                    elif yy in (76,85):
                        rod('09 Main tower/Window lights',x,yy,z)
    for yy in (70,80):
        disc('09 Main tower/Interior floors',0,0,yy,5)
        put('09 Main tower/Interior glow',0,yy,0,L)
    for yy,r in ((78,7),(79,8),(80,7),(87,6),(88,7)):
        tier('09 Main tower/Balconies',0,0,yy,r)
        if yy in (79,88):
            for x,z in ((r,0),(-r,0),(0,r),(0,-r)):
                put('09 Main tower/Balcony lamps',x,yy+1,z,L)
    # Scalloped ice onion crown, not a cone: bulges below, pinches to a needle.
    roof_radii=[7,8,8,7,6,5,4,3,2]
    for j,rr in enumerate(roof_radii):
        yy=89+j
        for x in range(-rr-1,rr+2):
            for z in range(-rr-1,rr+2):
                h=hypot(x,z)
                if rr-1.25<h<=rr+.3:
                    a=atan2(z,x)
                    rib=abs(sin(4*a))<.25
                    put('10 Crown/White ribs' if rib else '10 Crown/Ice shell',x,yy,z,Q if rib else I)
        if j in (0,1):
            for dx,dz in ((1,0),(-1,0),(0,1),(0,-1)):
                slab('10 Crown/Scalloped eaves',dx*(rr+1),yy,dz*(rr+1))
    for yy in range(98,106):
        put('10 Crown/Pinnacle',0,yy,0,L if yy==101 else (G if yy in (100,102) else Q))
    rod('10 Crown/Tip',0,106,0)
    rod('10 Crown/Tip',0,107,0)
    return b,groups
