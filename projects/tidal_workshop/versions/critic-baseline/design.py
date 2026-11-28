"""Tidemark Workshop: a public boat-craft hall under three north-light roofs.
Coordinates: X east, Y up, Z south; public approach from south.
"""
from src.core.building import Building
from src.core.roof import roof_courses


def build():
    b = Building('潮痕工坊 / Tidemark Workshop')
    groups = {}
    def p(g, x, y, z, block, **s):
        b.place((x,y,z), block, **s); groups[x,y,z] = g
    def box(g, x0,x1,y0,y1,z0,z1, block, **s):
        for x in range(x0,x1+1):
            for y in range(y0,y1+1):
                for z in range(z0,z1+1): p(g,x,y,z,block,**s)
    def slab(g,x,y,z,base,kind='bottom'):
        p(g,x,y,z,base+'_slab',type=kind,waterlogged='false')
    def stair(g,x,y,z,base,facing,half='bottom'):
        p(g,x,y,z,base+'_stairs',facing=facing,half=half,shape='straight',waterlogged='false')
    def pane(g,x,y,z,axis='x'):
        p(g,x,y,z,'minecraft:glass_pane',east=str(axis=='x').lower(),west=str(axis=='x').lower(),north=str(axis=='z').lower(),south=str(axis=='z').lower(),waterlogged='false')
    def lamp(g,x,y,z,hang=False):
        p(g,x,y,z,'minecraft:lantern',hanging=str(hang).lower(),waterlogged='false')
    stone='quark:polished_limestone'; brick='quark:limestone_bricks'
    dark='minecraft:polished_deepslate'; wood='minecraft:stripped_spruce_log'
    copper='minecraft:waxed_weathered_cut_copper'; planks='minecraft:spruce_planks'
    paving='quark:polished_tuff'; glass='minecraft:light_gray_stained_glass'
    site='01 Forecourt'; hall='02 Workshop'; wing='03 Public gallery'; roof='04 Sawtooth roof'; fittings='05 Interior fittings'
    # Stone quay and a gently defined approach, kept at the workshop floor level.
    for x in range(-25,22):
        for z in range(-16,24):
            if (x < -19 and z < 6) or (x > 17 and z < -12): continue
            if z>18 and (x < -17 or x > 13): continue
            p(site,x,0,z,stone if -14 <= x <= -5 and z >= 9 else paving)
    # A recessed dry channel, a quiet reminder of the waterfront; crossing left clear.
    for x in range(0,17):
        for z in range(16,19): p(site,x,0,z,'minecraft:dark_prismarine')
    for x in range(0,17):
        slab(site,x,1,15,stone); slab(site,x,1,19,stone)
    # Main hall floor and enclosing brick walls.
    box(hall,-15,14,0,0,-12,9,planks)
    for x in range(-15,15):
        for z in (-12,9):
            for y in range(1,10):
                # Broad workshop windows, entrance at the south-west.
                opening=(z==9 and ((-12<=x<=-9 and y<=4) or (x in range(-5,12) and 2<=y<=7 and x%5!=0))) or (z==-12 and x in range(-12,13) and 2<=y<=7 and x%5!=0)
                if opening:
                    if not (z==9 and -12<=x<=-9 and y<=4): pane(hall,x,y,z)
                else: p(hall,x,y,z,brick if y>1 else dark)
    for z in range(-11,9):
        for x in (-15,14):
            for y in range(1,10):
                if z in range(-9,7) and z%5!=0 and 2<=y<=6: pane(hall,x,y,z,'z')
                else: p(hall,x,y,z,brick if y>1 else dark)
    # Structural piers with inset windows and a continuous projecting cornice.
    for x in (-15,-10,-5,0,5,10,14):
        for z in (-13,10):
            box(hall,x,x,1,9,z,z,stone)
            stair(hall,x,8,z,'quark:polished_limestone','north' if z==10 else 'south','top')
            slab(hall,x,10,z,stone)
    for x in range(-15,15):
        slab(hall,x,9,10,stone,'top'); slab(hall,x,9,-13,stone,'top')
    # Three independent rising roofs: long low slopes and glazed vertical drops.
    # Separate roofs permit genuinely vertical clerestories between the folds.
    for a in (-15,-5,5):
        heights={(x,z): 20+(x-a) for x in range(a,a+10) for z in range(-13,11)}
        for (x,y,z),form,state in roof_courses(heights,connect_steps=True):
            suffix={'backing':'','block':'','slab':'_slab','stairs':'_stairs'}[form]
            p(roof,x,y,z,copper+suffix,**state)
        # Exposed timber ribs reinforce the sectional idea inside the hall.
        for z in (-10,-3,4,8):
            for x in range(a,a+10):
                y=(20+x-a)//2-1
                if (x,y,z) not in b.blocks: p(roof,x,y,z,wood,axis='x')
        # The roof's tall vertical edge admits light into the next bay.
        for z in range(-11,9):
            for y in range(10,15):
                if (a+9,y,z) in b.blocks: continue
                if z in (-10,-3,4,8): p(roof,a+9,y,z,wood,axis='y')
                else: p(roof,a+9,y,z,glass)
    # Close sawtooth end profiles at front and rear with masonry and timber outline.
    for x in range(-15,15):
        a=-15+10*((x+15)//10); top=(20+x-a)//2
        for z in (-12,9):
            for y in range(10,top):
                if (x,y,z) not in b.blocks: p(hall,x,y,z,brick)
    # A low gallery projects toward the street and overlaps the hall entrance.
    box(wing,-22,-7,0,0,5,15,planks)
    for x in range(-22,-6):
        for z in (5,15):
            for y in range(1,6):
                if z==5 and -14<=x<=-8 and y<=4: continue
                if z==15 and -14<=x<=-11 and y<=3: continue
                if z==15 and (-20<=x<=-17 or -9<=x<=-8) and 2<=y<=4: pane(wing,x,y,z)
                else: p(wing,x,y,z,brick if y>1 else dark)
    for z in range(6,15):
        for x in (-22,-7):
            for y in range(1,6):
                if x==-7 and z<=9: continue
                if z in (8,9,11,12) and 2<=y<=4: pane(wing,x,y,z,'z')
                else: p(wing,x,y,z,brick if y>1 else dark)
    # Flat copper hood floats above a timber fascia, with a sheltered recessed entry.
    for x in range(-23,-6):
        for z in range(4,17): slab(wing,x,6,z,copper)
    for x in range(-23,-6):
        p(wing,x,5,16,wood,axis='x')
    for x in (-20,-8):
        box(wing,x,x,1,4,17,17,wood,axis='y')
        stair(wing,x,5,17,'minecraft:spruce','north','top')
    for x in range(-21,-7): slab(wing,x,6,17,'minecraft:spruce')
    # Sills and thin shutters articulate only the entrance and large public windows.
    for x in list(range(-20,-16))+list(range(-9,-7)):
        stair(wing,x,1,16,'quark:polished_limestone','south')
    for x in (-21,-16,-10):
        for y in (2,3,4):
            p(wing,x,y,16,'minecraft:dark_oak_trapdoor',facing='south',half='bottom',open='true',powered='false',waterlogged='false')
    # Work benches, a long display and suspended light, placed away from public route.
    for x in (-4,4,10):
        for z in (-9,-8,-7):
            p(fittings,x,1,z,'minecraft:spruce_fence',east='false',west='false',north='true',south='true',waterlogged='false')
            slab(fittings,x,2,z,'minecraft:spruce','top')
    # An unfinished boat is the object of the room, visible from the entrance.
    for x in range(-7,9):
        slab(fittings,x,1,-1,'minecraft:spruce')
        width = min(3, 1 + (x+7)//3, 1 + (8-x)//3)
        for side in (-1,1):
            z=-1+side*width
            stair(fittings,x,1,z,'minecraft:spruce','north' if side<0 else 'south')
            if x in (-5,-1,3,6):
                p(fittings,x,2,z,'minecraft:spruce_fence',east='false',west='false',north='false',south='false',waterlogged='false')
                slab(fittings,x,3,z,'minecraft:spruce')
    for x in range(-20,-16):
        p(fittings,x,1,9,'minecraft:bookshelf')
        slab(fittings,x,2,9,'minecraft:spruce')
    for x in (-10,0,10):
        for z in (-7,2): lamp(fittings,x,7,z,True)
    for x in (-19,-10): lamp(wing,x,4,16,True)
    return b,groups
