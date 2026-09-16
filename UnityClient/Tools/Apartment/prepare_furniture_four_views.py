"""Calibrate orthographic concept outlines to specified dimensions and package separate views.

These are generated concept views, not projections from a measured 3D mesh.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import uuid
from PIL import Image, ImageDraw, ImageFont
import zipfile

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'output/imagegen/furniture-four-views-v1'
VIEWS={'top':'上 · 俯视','bottom':'下 · 仰视','left':'左侧','right':'右侧'}
ASSETS={'sofa':{'name':'沙发','width':2.5,'depth':.94,'height':.92,'ppm':640},
        'coffee-table':{'name':'茶几','width':1.,'depth':1.18,'height':.47,'ppm':1350}}
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--final',action='store_true')
args=parser.parse_args()
def project_copy(source,asset,variant):
    base=ROOT/'AiGirlFriendUnity/Assets/Art/Apartment/Furniture/References'
    target=base/'FourViews-v1'/asset/variant/source.name
    target.parent.mkdir(parents=True,exist_ok=True)
    for folder in (base/'FourViews-v1',base/'FourViews-v1'/asset,target.parent):
        meta=Path(str(folder)+'.meta')
        if not meta.exists():
            meta.write_text(f'fileFormatVersion: 2\nguid: {uuid.uuid4().hex}\nfolderAsset: yes\nDefaultImporter:\n  externalObjects: {{}}\n  userData: \n  assetBundleName: \n  assetBundleVariant: \n')
    if target.exists() and target.read_bytes()!=source.read_bytes():raise FileExistsError(target)
    shutil.copy2(source,target)
    meta=Path(str(target)+'.meta')
    if not meta.exists():
        template=(base/f'living-{asset}-manga-v1-{variant}.png.meta').read_text()
        meta.write_text(re.sub(r'^guid: .*$',f'guid: {uuid.uuid4().hex}',template,flags=re.M))
    return str(target.relative_to(ROOT)).replace('\\','/')
manifest={'requested_model':'gpt-image-2.5-flare','mode':'authorized imagegen CLI edit',
          'views':'literal top, underside, left and right; not front/back elevations',
          'calibration':'RGBA silhouette cropped, then width and height calibrated independently to target orthographic dimensions; not CAD or measured mesh projections.',
          'underside':'Conservative inferred construction from a single original three-quarter image.',
          'canvas':[2048,2048],'images':[]}
for asset,spec in ASSETS.items():
    processed=[]
    for view,label in VIEWS.items():
        src=OUT/f'{asset}-{view}-alpha-raw.png'
        if not src.exists():
            if args.final: raise FileNotFoundError(src)
            continue
        im=Image.open(src).convert('RGBA');a=im.getchannel('A');bbox=a.getbbox()
        assert bbox and bbox[0]>0 and bbox[1]>0 and bbox[2]<im.width and bbox[3]<im.height,src
        raw_width,raw_height=bbox[2]-bbox[0],bbox[3]-bbox[1]
        dims=(spec['width'],spec['depth']) if view in ('top','bottom') else (spec['depth'],spec['height'])
        size=tuple(round(x*spec['ppm']) for x in dims)
        crop=im.crop(bbox).resize(size,Image.Resampling.LANCZOS)
        canvas=Image.new('RGBA',(2048,2048),(0,0,0,0))
        canvas.alpha_composite(crop,((2048-size[0])//2,(2048-size[1])//2))
        white=Image.new('RGBA',canvas.size,'white');white.alpha_composite(canvas)
        delivered=[]
        for variant,result in [('transparent',canvas),('white',white.convert('RGB'))]:
            path=OUT/asset/variant/f'{asset}-{view}.png';path.parent.mkdir(parents=True,exist_ok=True)
            result.save(path)
            delivered.append({'path':str(path.relative_to(ROOT)).replace('\\','/'),
                              'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
            if args.final:delivered[-1]['project_path']=project_copy(path,asset,variant)
        manifest['images'].append(dict(asset=asset,view=view,projection_dimensions_m=dims,
            source_pixel_size=im.size,source_bbox=bbox,output_object_size=size,
            axis_scale_ratio=(size[0]/raw_width)/(size[1]/raw_height),files=delivered,
            prompt=f'output/imagegen/furniture-four-views-v1/{asset}-{view}'+('-v2' if asset=='coffee-table' and view=='bottom' else '')+'.prompt.txt'))
        processed.append((view,label,canvas))
    if args.final:
        board=Image.new('RGB',(1600,1160),'#f3f0e9');draw=ImageDraw.Draw(board)
        font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',28)
        title=ImageFont.truetype('C:/Windows/Fonts/msyhbd.ttc',40)
        small=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',22)
        draw.text((40,25),spec['name']+' · 上下左右四视图',font=title,fill='#303d40')
        draw.text((40,86),'预览各格单独放大；生成模型请使用文件夹内四张独立图片。',font=small,fill='#59655e')
        for i,(view,label,im) in enumerate(processed):
            x=40+(i%2)*780;y=145+(i//2)*485
            draw.rounded_rectangle((x,y,x+740,y+455),radius=14,fill='white')
            draw.text((x+20,y+15),label,font=font,fill='#303d40')
            crop=im.crop(im.getchannel('A').getbbox());crop.thumbnail((690,345),Image.Resampling.LANCZOS)
            board.paste(crop,(x+(740-crop.width)//2,y+82+(345-crop.height)//2),crop)
        draw.text((40,1120),'底部结构为设计补全；目标尺寸以交付说明为准。',font=small,fill='#59655e')
        board.save(OUT/f'{asset}-four-views-preview.png')
        note=(f"{spec['name']}：上下左右四视图\n"
              "top=俯视；bottom=仰视；left=左侧；right=右侧。\n"
              "white 和 transparent 是同一套视角的两种底色，各选择一套使用，不要重复上传。\n"
              "上下图后方在图片上侧；左视图前方在左，右视图前方在右。\n"
              "若工具只提供前/后/左/右槽位，不要把俯视/仰视当作前/后视图。\n"
              f"目标尺寸：宽 {spec['width']}m × 深 {spec['depth']}m × 高 {spec['height']}m。\n"
              "全部独立图为2048×2048，同一家具使用统一像素/米标尺；预览拼图不供模型输入。\n"
              "图片由原单视角设计图推演；底部为保守补全，未见部分并非实际3D模型的测量投影。\n")
        (OUT/asset/'使用说明.txt').write_text(note,encoding='utf-8')
        with zipfile.ZipFile(OUT/f'{asset}-four-views-v1.zip','w',zipfile.ZIP_DEFLATED) as z:
            for f in sorted((OUT/asset).rglob('*')):
                if f.is_file():z.write(f,f.relative_to(OUT/asset))
manifest['complete']=len(manifest['images'])==8
(OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(f"Prepared {len(manifest['images'])} independent views; final={args.final}")
