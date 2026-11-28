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
    # Solid quay substructure supports the site above the lower water datum.
    for x in range(-25,22):
        for z in range(-16,24):
            if (x < -19 and z < 6) or (x > 17 and z < -12): continue
            if z>18 and (x < -17 or x > 13): continue
            for y in range(-4,0): p('01 Waterfront/Quay foundation',x,y,z,brick)
    # A quay, water and a descending launch bed replace the former dead-end apron.
    # Water sources remain real blueprint blocks; the static renderer needs a stand-in.
    waterfront='01 Waterfront/Water and bed'
    for x in range(-26,23):
        for z in range(24,59):
            p(waterfront,x,-7,z,'minecraft:stone')
            for y in range(-6,-3): p(waterfront,x,y,z,'minecraft:water')
    # Masonry quay wall: the slip opening interrupts the face.
    for x in list(range(-25,3))+list(range(11,22)):
        box('01 Waterfront/Quay wall',x,x,-3,-1,23,23,brick)
        slab('01 Waterfront/Quay wall',x,0,23,stone,'top')
    # The launch bed falls one block in four toward the actual water surface.
    for z in range(10,48):
        level = 0 if z<=23 else -1-(z-24)//4
        level=max(level,-6)
        for x in range(3,11):
            if z in (23,27,31,35,39,43): stair('01 Waterfront/Launch bed',x,level,z,'minecraft:spruce','north')
            else: p('01 Waterfront/Launch bed',x,level,z,planks)
        for x in (2,11):
            p('01 Waterfront/Launch bed',x,level,z,dark)
    # Separate public viewing deck stands on piles above the harbor.
    deck='01 Waterfront/Public deck'
    for x in range(-20,-3):
        for z in range(23,33): p(deck,x,0,z,planks)
    for x in (-20,-15,-10,-4):
        for z in (24,31): box(deck,x,x,-6,-1,z,z,wood,axis='y')
    for x in range(-20,-3):
        if x in (-20,-4): continue
        p(deck,x,1,32,'minecraft:spruce_fence',east='true',west='true',north='false',south='false',waterlogged='false')
    for z in range(24,32):
        p(deck,-20,1,z,'minecraft:spruce_fence',east='false',west='false',north='true',south='true',waterlogged='false')
        p(deck,-4,1,z,'minecraft:spruce_fence',east='false',west='false',north='true',south='true',waterlogged='false')
    for x in (-20,-4):
        for z in (23,32):
            p(deck,x,1,z,wood,axis='y'); lamp(deck,x,2,z)
    # A small quay walk gives access around, but not across, the working slip.
    for x in range(-25,3):
        for z in range(19,24): p(site,x,0,z,stone if -14<=x<=-5 else paving)
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
    # Two lower craft bays and a taller, wider launch bay: roof section follows use.
    bays=[(-15,-8,20),(-7,1,20),(2,14,23)]
    for a,end,start_half in bays:
        heights={(x,z): start_half+(x-a) for x in range(a,end+1) for z in range(-13,16 if a==2 else 11)}
        for (x,y,z),form,state in roof_courses(heights,connect_steps=True):
            suffix={'backing':'','block':'','slab':'_slab','stairs':'_stairs'}[form]
            p(roof,x,y,z,copper+suffix,**state)
        for z in (-10,-3,4,8):
            for x in range(a,end+1):
                y=(start_half+x-a)//2-2
                if (x,y,z) not in b.blocks: p(roof,x,y,z,wood,axis='x')
        top=(start_half+end-a)//2
        for z in range(-11,9):
            for y in range(10,top+1):
                if (end,y,z) in b.blocks: continue
                if z in (-10,-3,4,8): p(roof,end,y,z,wood,axis='y')
                else: p(roof,end,y,z,glass)
        for x in range(a,end+1):
            top=(start_half+x-a)//2
            for z in (-12,9):
                for y in range(10,top):
                    if (x,y,z) not in b.blocks: p(hall,x,y,z,brick)
    # Project the high roof over a timber-framed working shelter.
    for x in (2,14):
        box(roof,x,x,1,10 if x==2 else 16,15,15,wood,axis='y')
        stair(roof,x,10,14,'minecraft:spruce','north','top')
    for x in range(2,15):
        p(roof,x,11,15,wood,axis='x')
        y=(23+x-2)//2-1
        if (x,y,15) not in b.blocks: p(roof,x,y,15,wood,axis='x')
    # Higher portal and a timber lintel express the boat-sized working bay.
    for x in range(2,12):
        for y in range(10,12): p(hall,x,y,9,brick)
    for x in (2,11): box(hall,x,x,9,11,10,10,stone)
    for x in range(3,11): p(hall,x,11,10,wood,axis='x')
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
        for z in range(4,17):
            if -20<=x<=-17 and 7<=z<=12: p(wing,x,6,z,glass)
            else: slab(wing,x,6,z,copper)
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
    # Hull under construction: a continuous, tapering open shell above a cradle.
    boat='05 Interior fittings/Boat and cradle'
    widths={-8:0,-7:1,-6:1,-5:2,-4:2,-3:3,-2:3,-1:3,0:3,1:3,2:3,3:2,4:2,5:1,6:1,7:0}
    for z,width in widths.items():
        p(boat,6,2,z,wood,axis='z')
        for x in range(6-width+1,6+width): slab(boat,x,2,z,'minecraft:spruce','top')
        for side in (-1,1):
            x=6+side*width
            if width:
                stair(boat,x,3,z,'minecraft:spruce','east' if side<0 else 'west')
                slab(boat,x,4,z,'minecraft:spruce')
                # Bridge diagonal transitions into a continuous gunwale.
                if z>-8 and widths[z-1] != width:
                    prev=6+side*widths[z-1]
                    for xx in range(min(x,prev),max(x,prev)+1):
                        slab(boat,xx,4,z,'minecraft:spruce')
    # Cross-timbers expose the building process without blocking the interior.
    for z in (-6,-3,0,3):
        for x in range(6-widths[z]+1,6+widths[z]):
            p(boat,x,3,z,wood,axis='x')
    for z in (-7,-3,1,5):
        p(boat,6,1,z,dark)
        for x in (5,7): stair(boat,x,1,z,'minecraft:spruce','east' if x==5 else 'west')
    for z in (-8,7):
        for y in (3,4): p(boat,6,y,z,wood,axis='y')
        slab(boat,6,5,z,'minecraft:spruce')
    # Fixed lifting beam belongs to the building, not to the unfinished boat.
    gantry='02 Workshop/Lifting frame'
    for x in (1,11): box(gantry,x,x,1,10,-5,-5,wood,axis='y')
    for x in range(1,12): p(gantry,x,10,-5,wood,axis='x')
    for y in range(6,10): p(gantry,6,y,-5,'minecraft:chain',axis='y',waterlogged='false')
    # Widen the passage between the low gallery and the workshop.
    for x in range(-14,-7):
        for y in range(1,5):
            for z in (9,10):
                b.blocks.pop((x,y,z),None); groups.pop((x,y,z),None)
    # Tall boat door: a deep frame replaces two repeated window bays.
    for x in range(3,11):
        for y in range(1,11):
            for z in (9,10):
                b.blocks.pop((x,y,z),None); groups.pop((x,y,z),None)
    for x in (2,11):
        for z in (9,10): box(hall,x,x,1,10,z,z,stone)
        stair(hall,x,10,11,'quark:polished_limestone','south','top')
    for x in range(3,11):
        p(hall,x,11,10,wood,axis='x')
        slab(hall,x,12,10,stone,'top')
    for x in (1,12):
        for y in range(2,7):
            p(hall,x,y,10,'minecraft:dark_oak_trapdoor',facing='south',half='bottom',open='true',powered='false',waterlogged='false')
    for x in (-19,-16,-12): lamp(wing,x,5,11,True)
    for x in range(-20,-16):
        p(fittings,x,1,9,'minecraft:bookshelf')
        slab(fittings,x,2,9,'minecraft:spruce')
    for x in (-10,0,10):
        for z in (-7,2): lamp(fittings,x,7,z,True)
    for z in (-6,3): lamp(fittings,4,7,z,True)
    for x in (-19,-10): lamp(wing,x,4,16,True)
    return b,groups

