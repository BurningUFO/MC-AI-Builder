"""Optional static water preview: keep source blocks, tint first water texture frame.
Run after `generate --approximate` and before `render`; this does not change blueprint.
"""
import hashlib
import io
import json
import os
from pathlib import Path
from zipfile import ZipFile
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
PROJECT = Path(__file__).resolve().parent
INSTANCE = Path(os.environ['MCBUILDER_INSTANCE'])
JAR = INSTANCE / 'BMC4.jar'
index_path = PROJECT / 'material-index.json'
index = json.loads(index_path.read_text(encoding='utf-8'))
with ZipFile(JAR) as archive:
    data = archive.read('assets/minecraft/textures/block/water_still.png')
im = Image.open(io.BytesIO(data)).convert('RGBA')
im = im.crop((0, 0, im.width, im.width))
# Minecraft tints the greyscale animated texture at runtime; use a fixed estuary blue.
pixels=[]
for r,g,b,a in im.getdata():
    value=(r+g+b)/765
    pixels.append((int(26+24*value),int(90+55*value),int(114+70*value),235))
im.putdata(pixels)
out=io.BytesIO(); im.save(out,format='PNG'); png=out.getvalue()
hashval=hashlib.sha256(png).hexdigest()
rel=f'.cache/assets/textures/minecraft/block/tidemark-water-{hashval[:12]}.png'
path=ROOT/rel; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(png)
ref='minecraft:block/tidemark_water_preview'
index['textures'][ref]={'file':rel,'sha256':hashval,'source_jar':'BMC4.jar',
  'source_texture':'assets/minecraft/textures/block/water_still.png',
  'note':'Tinted first animation frame; cube preview only, not fluid simulation.'}
water=index['states']['minecraft:water']
for face in water['faces']: face['texture']=ref
index['render_fallbacks']['minecraft:water']['rendered_as']='tinted first water texture on a static cube'
index['render_fallbacks']['minecraft:water']['detail']+='; source water blocks retained; no fluid simulation'
index_path.write_text(json.dumps(index,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
