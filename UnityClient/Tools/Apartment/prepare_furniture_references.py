"""Package generated furniture concepts, preserving source pixels and alpha for image-to-3D tools."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import uuid
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT/'output/imagegen'
DEST = ROOT/'AiGirlFriendUnity/Assets/Art/Apartment/Furniture/References'
ASSETS = {
    'living-sofa-manga-v1': dict(name='灰绿布艺三人沙发', local_dimensions_m=[2.50,.92,.94],
        local_axes='X=width, Y=height, Z=depth; seating front=-Z',
        unity_position_xz=[1.45,-2.55], unity_yaw_degrees=90,
        unity_collision_xyz=[.94,.92,2.50], seat_height_m=.46),
    'living-coffee-table-manga-v1': dict(name='暖木色茶几', local_dimensions_m=[1.00,.47,1.18],
        local_axes='X=width, Y=height, Z=depth',
        unity_position_xz=[.05,-2.55], unity_yaw_degrees=0,
        unity_collision_xyz=[1.00,.47,1.18], leg_count=4),
}

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

DEST.mkdir(parents=True, exist_ok=True)
for folder in (DEST.parent, DEST):
    meta = Path(str(folder)+'.meta')
    if not meta.exists():
        meta.write_text(f'fileFormatVersion: 2\nguid: {uuid.uuid4().hex}\nfolderAsset: yes\nDefaultImporter:\n  externalObjects: {{}}\n  userData: \n  assetBundleName: \n  assetBundleVariant: \n')
template = (ROOT/'AiGirlFriendUnity/Assets/Art/StyleReferences/apartment-anime-background-style-v1.png.meta').read_text()
manifest = dict(status='concept_images_generated; models_not_yet_generated_or_integrated',
    mode='previously authorized fallback CLI; reference-conditioned images/edits endpoint',
    requested_model='gpt-image-2.5-flare',
    transparency='Local chroma-key removal; original API images preserved, not native generated alpha.',
    references=[str(p.relative_to(ROOT)).replace('\\','/') for p in [
        ROOT/'AiGirlFriendUnity/Assets/Art/SceneReferences/apartment-layout-approved-20260914.png',
        ROOT/'AiGirlFriendUnity/Assets/Art/StyleReferences/apartment-anime-background-style-v1.png']],
    assets=[])
for stem, specification in ASSETS.items():
    src = SOURCE/f'{stem}-transparent.png'
    image = Image.open(src).convert('RGBA')
    alpha = image.getchannel('A')
    bbox = alpha.getbbox()
    assert bbox and all(alpha.getpixel(p)==0 for p in [(0,0),(image.width-1,0),(0,image.height-1),(image.width-1,image.height-1)])
    assert bbox[0]>0 and bbox[1]>0 and bbox[2]<image.width and bbox[3]<image.height, 'Object touches frame'
    white = Image.new('RGBA',image.size,'white')
    white.alpha_composite(image)
    white_path = SOURCE/f'{stem}-white.png'
    if white_path.exists():
        raise FileExistsError(white_path)
    white.convert('RGB').save(white_path)
    delivered = []
    for source in (src,white_path):
        target=DEST/source.name
        if target.exists():
            raise FileExistsError(target)
        shutil.copy2(source,target)
        meta=re.sub(r'^guid: .*$',f'guid: {uuid.uuid4().hex}',template,flags=re.M)
        meta=meta.replace('enableMipMap: 1','enableMipMap: 0').replace('nPOTScale: 1','nPOTScale: 0')
        meta=meta.replace('alphaIsTransparency: 0',f'alphaIsTransparency: {1 if source==src else 0}')
        meta=meta.replace('textureCompression: 1','textureCompression: 0')
        Path(str(target)+'.meta').write_text(meta)
        delivered.append(dict(path=str(target.relative_to(ROOT)).replace('\\','/'),sha256=sha(target)))
    hist=alpha.histogram()
    manifest['assets'].append(dict(id=stem,**specification,pixel_size=list(image.size),alpha_bbox=list(bbox),
        alpha_zero_pixels=hist[0],alpha_partial_pixels=sum(hist[1:255]),alpha_opaque_pixels=hist[255],
        prompt=str((SOURCE/f'{stem}.prompt.txt').relative_to(ROOT)).replace('\\','/'),
        prompt_sha256=sha(SOURCE/f'{stem}.prompt.txt'),raw_sha256=sha(SOURCE/f'{stem}-keyed.png'),delivered=delivered))
(SOURCE/'living-furniture-concepts-v1.manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps(manifest,ensure_ascii=True,indent=2))
