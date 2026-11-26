"""Visual reconstruction of 夜言-碎雨's BV11U4y127EJ, from cover and tutorial frames.

Minecraft coordinates; Y=45 is the lower orbital promenade. Original vanilla
palette intentionally retained in BMC4. No terrain or unrelated additions.
"""
from math import atan2, hypot, sin, sqrt
from src.core.building import Building

Q = 'minecraft:smooth_quartz'
P = 'minecraft:quartz_pillar'
S = 'minecraft:quartz_slab'
T = 'minecraft:quartz_stairs'
I = 'minecraft:packed_ice'
G = 'minecraft:prismarine'
D = 'minecraft:dark_prismarine'
L = 'minecraft:sea_lantern'
R = 'minecraft:end_rod'
CARDINAL = ((1, 0), (-1, 0), (0, 1), (0, -1))
DIAGONAL = ((1, 1), (1, -1), (-1, 1), (-1, -1))


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
    def tier(part,cx,cz,y,r):
        disc(part,cx,cz,y,r)
        for x in range(-r-1,r+2):
            for z in range(-r-1,r+2):
                if r-.7 < hypot(x,z) <= r+.65:
                    slab(part,cx+x,y,cz+z)
    def ring_cells(radius):
        octant = [(round(sqrt(radius**2 - step**2)), step) for step in range(radius + 1)]
        return {(sx * x, sz * z) for a, b in octant if a >= b
                for x, z in ((a, b), (b, a)) for sx, sz in DIAGONAL}

    def orbit(radius, y, part, *, framed):
        center = ring_cells(radius)
        for x, z in center:
            step = min(abs(x), abs(z))
            if framed:
                block = 'minecraft:glowstone' if step % 4 == 1 else I
                put(part+'/Recessed inlay', x, y, z, block)
                put(part+'/Green soffit', x, y-1, z, 'minecraft:prismarine_slab', type='bottom', waterlogged='false')
            else:
                put(part+'/White ring', x, y, z, L if step % 4 == 1 else Q)
        if framed:
            for x, z in (ring_cells(radius-1) | ring_cells(radius+1)) - center:
                slab(part+'/Upper white lip', x, y, z, top=True)
                slab(part+'/Lower white lip', x, y-1, z, top=True)

    def pavilion(cx,cz,y,part):
        for j in range(-14, 4):
            put(part+'/Pendant',cx,y+j,cz,L if j in (-11,-4) else P,**({} if j in (-11,-4) else {'axis':'y'}))
        rod(part+'/Pendant',cx,y-16,cz)
        put(part+'/Pendant',cx,y-15,cz,G)
        for j, r in ((3, 1), (4, 2), (5, 2), (6, 3), (7, 2)):
            disc(part+'/Corbel heart', cx, cz, y+j, r)
        for dx, dz in CARDINAL:
            for j in range(-5, 4):
                block = L if j in (-4, 1) else P
                put(part+'/Faceted shaft', cx+dx, y+j, cz+dz, block, **({'axis': 'y'} if block == P else {}))
            for reach in range(2, 7):
                x, z = cx+dx*reach, cz+dz*reach
                j = 5 + reach // 2
                put(part+'/Stepped petal', x, y+j-1, z, Q)
                slab(part+'/Stepped petal', x, y+j, z)
                if reach in (3, 4):
                    for w in (-1, 1):
                        slab(part+'/Petal shoulders', x+dz*w, y+j, z+dx*w, top=True)
            x, z = cx+dx*5, cz+dz*5
            put(part+'/Hanging mint', x, y+6, z, G)
            put(part+'/Hanging mint', x, y+5, z, 'minecraft:prismarine_wall',
                north='none', east='none', south='none', west='none', up='true', waterlogged='false')
            rod(part+'/Hanging light', x, y+4, z)
        for dx, dz in DIAGONAL:
            slab(part+'/Petal shoulders', cx+dx*3, y+7, cz+dz*3)
        for dx, dz in CARDINAL:
            put(part+'/Ice cap', cx+dx*2, y+8, cz+dz*2, I)
            put(part+'/Cap inlay', cx+dx, y+8, cz+dz, G)
            slab(part+'/Beacon foot', cx+dx, y+9, cz+dz)
        put(part+'/Beacon foot', cx, y+9, cz, Q)
        put(part+'/Beacon',cx,y+10,cz,L)
        for dx, dz in ((0, 0), *CARDINAL):
            slab(part+'/Beacon cap', cx+dx, y+11, cz+dz)
        slab(part+'/Beacon cap',cx,y+12,cz)

    # The lower rings leave open air both between the rings and around the core.
    orbit(46,45,'01 Lower orbit/Outer ring',framed=True)
    orbit(35,45,'01 Lower orbit/Inner ring',framed=False)
    for dx, dz in CARDINAL:
        cx, cz = dx*40, dz*40
        pavilion(cx,cz,45,'03 Satellite pavilions/'+str((cx,cz)))
    disc('04 Lower core/Floor',0,0,45,7)
    for yy,r in ((17,3),(18,4),(19,5),(20,5),(21,4),(43,5),(44,6)):
        tier('04 Lower core/Mouldings',0,0,yy,r)
    disc('04 Lower core/Ice collar',0,0,19,5,I,4)
    for dx, dz in DIAGONAL:
        for yy in range(21, 43):
            put('04 Lower core/Corner piers', dx*3, yy, dz*3, P, axis='y')
        for yy in (22, 28, 33, 40):
            slab('04 Lower core/Pier mouldings', dx*4, yy, dz*3, top=True)
            slab('04 Lower core/Pier mouldings', dx*3, yy, dz*4, top=True)
    for dx, dz in CARDINAL:
        put('04 Lower core/Collar lamps', dx*5, 19, dz*5, L)
        for yy in range(21, 43):
            for w in (-2, 2):
                put('04 Lower core/Bay jambs', dx*3+dz*w, yy, dz*3+dx*w, Q)
            for w in (-1, 0, 1):
                depth = 3 if yy in (23, 29, 35, 41) else 2
                block = L if w == 0 and yy in (22, 32, 39) else Q
                if w and yy in (24, 31, 38):
                    block = D
                put('04 Lower core/Recessed bays', dx*depth+dz*w, yy, dz*depth+dx*w, block)
    # Four open ribs meet at a short lit corner and taper down to the needle.
    bottom = -6
    for height in range(24):
        block = L if height % 9 == 0 else P
        put('05 Lower finial/Central needle',0,bottom+height,0,block,**({'axis':'y'} if block == P else {}))
    finial = (1, 1, 1, 1, 2, 2, 2, 3, 3, 4, 5, 6, 7, 8, 9, 10, 9, 8, 7, 6, 5, 5, 4, 3, 3)
    for dx, dz in DIAGONAL:
        for height, reach in enumerate(finial, start=2):
            yy = bottom + height
            x, z = dx*reach, dz*reach
            put('05 Lower finial/White diamond ribs',x,yy,z,L if height == 17 else Q)
            if reach > 1:
                for sx, sz in ((x-dx, z), (x, z-dz)):
                    put('05 Lower finial/Mint lining',sx,yy,sz,'minecraft:prismarine_slab',type='top',waterlogged='false')
        for height in range(6,24):
            if finial[height-2] > 1:
                rod('05 Lower finial/Light strings',dx,bottom+height,dz)
    # The X in the tutorial plan is four rising supports, ending at the upper rim.
    for dx, dz in DIAGONAL:
        previous = 45
        for reach, y in enumerate((45, 47, 49, 51, 53, 54, 55, 56, 57, 58, 59, 59, 60), start=3):
            x, z = dx*reach, dz*reach
            for yy in range(previous, y+1):
                put('06 Upper suspension/Diagonal supports', x, yy, z, L if yy == 53 else Q)
            if reach > 3:
                slab('06 Upper suspension/Diagonal supports', x-dx, previous, z)
                slab('06 Upper suspension/Diagonal supports', x, previous, z-dz)
            previous = y
        for yy in range(53, 62):
            reach = 8 if yy >= 58 else 9
            block = L if yy in (56, 60) else Q
            put('06 Upper suspension/Short hanging piers', dx*reach, yy, dz*reach, block)
        put('06 Upper suspension/Pier tips',dx*9,52,dz*9,G)
        rod('06 Upper suspension/Pier tips',dx*9,51,dz*9)
    spine_inlays = {47: D, 48: D, 49: D, 50: I, 51: I, 52: D, 53: D, 54: L, 58: L}
    for yy in range(46, 62):
        put('06 Upper suspension/Central spine',0,yy,0,spine_inlays.get(yy, Q))
    for dx, dz in CARDINAL:
        for yy in (48, 49, 52, 53):
            rod('06 Upper suspension/Spine lights',dx,yy,dz)
    orbit(21,60,'07 Upper orbit',framed=True)
    disc('08 Tower terrace/Floor',0,0,65,11)
    for yy,r in ((61,5),(62,7),(63,9),(64,11)):
        tier('08 Tower terrace/Blue base',0,0,yy,r)
    disc('08 Tower terrace/Ice rim',0,0,64,11,I,10)
    for dx, dz in DIAGONAL:
        cx, cz = dx*8, dz*8
        for yy,r in ((65,1),(66,2),(67,3),(68,2)):
            tier('08 Tower terrace/Lotus turrets',cx,cz,yy,r)
        put('08 Tower terrace/Lotus lamps',cx,69,cz,L)
        slab('08 Tower terrace/Lotus lamps',cx,70,cz)
    # Main white lantern tower: a wider lower drum, balcony and slimmer belfry.
    for yy in range(65,80):
        rr=6 if yy<74 else 5
        for x in range(-rr,rr+1):
            for z in range(-rr,rr+1):
                h=hypot(x,z)
                if rr-.9<h<rr+.4:
                    a=atan2(z,x)
                    pier=abs(sin(4*a))<.7
                    band=yy in (65, 66, 72, 73, 74, 78, 79)
                    if pier or band:
                        put('09 Main tower/Lantern walls',x,yy,z,Q if band else P,**({} if band else {'axis':'y'}))
                    elif (abs(x)+abs(z)) % 2 == 0:
                        put('09 Main tower/Window inlays',x,yy,z,G if yy in (69, 76) else L if yy in (70, 77) else Q)
    for yy in (65,74):
        disc('09 Main tower/Interior floors',0,0,yy,5)
        put('09 Main tower/Interior glow',0,yy,0,L)
    for yy,r in ((72,7),(73,8),(74,7),(78,6),(79,7)):
        tier('09 Main tower/Balconies',0,0,yy,r)
        if yy in (73,79):
            for dx, dz in CARDINAL:
                x, z = dx*r, dz*r
                put('09 Main tower/Balcony lamps',x,yy+1,z,L)
    # Individual downward balcony brackets, separated by open window bays.
    for dx,dz in CARDINAL:
        for yy,rr in ((70,5),(71,6),(72,7)):
            put('09 Main tower/Balcony corbels',dx*rr,yy,dz*rr,T,
                facing={(1,0):'east',(-1,0):'west',(0,1):'south',(0,-1):'north'}[(dx,dz)],
                half='top',shape='straight',waterlogged='false')
        rod('09 Main tower/Balcony pendants',dx*7,70,dz*7)
    # Four blue facets between raised cardinal ribs and stepped diagonal seams.
    for yy, rr in enumerate((5, 6, 7, 8, 8, 8, 7, 6, 5, 4, 3, 2), start=79):
        for x in range(-rr-1,rr+2):
            for z in range(-rr-1,rr+2):
                h=hypot(x,z)
                if rr-1.5<h<=rr+.3:
                    put('10 Crown/Ice facets',x,yy,z,I)
        diagonal = round(rr / sqrt(2))
        for dx, dz in DIAGONAL:
            put('10 Crown/Diagonal seams', dx*diagonal, yy, dz*diagonal, Q)
        for dx, dz in CARDINAL:
            put('10 Crown/Raised ribs',dx*rr,yy,dz*rr,Q)
            if 81 <= yy <= 86:
                slab('10 Crown/Rib caps',dx*(rr+1),yy,dz*(rr+1),top=True)
    disc('10 Crown/Finial foot',0,0,91,1,Q)
    for yy, block in ((92,Q),(93,D),(94,L),(95,D)):
        put('10 Crown/Pinnacle',0,yy,0,block)
    rod('10 Crown/Tip',0,96,0)
    return b,groups
