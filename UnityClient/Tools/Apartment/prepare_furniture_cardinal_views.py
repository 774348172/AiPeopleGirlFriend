"""Package front/back/left/right PNGs for four-slot reconstruction tools."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import uuid
import zipfile
from PIL import Image, ImageDraw, ImageFont

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'output/imagegen/furniture-front-back-left-right-v1'
OLD=ROOT/'output/imagegen/furniture-four-views-v1'
ART=ROOT/'AiGirlFriendUnity/Assets/Art/Apartment/Furniture/References'
PROJECT=ART/'FrontBackLeftRight-v1'
VIEWS={'front':'前','back':'后','left':'左','right':'右'}
ASSETS={'sofa':('沙发',2.50,.94,.92,640),'coffee-table':('茶几',1.00,1.18,.47,1350)}
manifest={'date':'2026-09-15','model':'gpt-image-2.5-flare','mode':'authorized imagegen CLI edit',
          'views':list(VIEWS),'canvas':[2048,2048],
          'calibration':'Front/back silhouettes resized independently in X/Y to target width and height; left/right reused unchanged from previous calibrated set.',
          'limitations':'Generated design views, not exact measured mesh projections. Unseen sofa rear is conservatively inferred.',
          'images':[]}

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def folder(path):
    path.mkdir(parents=True,exist_ok=True)
    meta=Path(str(path)+'.meta')
    if not meta.exists():meta.write_text(f'fileFormatVersion: 2\nguid: {uuid.uuid4().hex}\nfolderAsset: yes\nDefaultImporter:\n  externalObjects: {{}}\n  userData: \n  assetBundleName: \n  assetBundleVariant: \n')

folder(PROJECT)
for asset,(label,width,depth,height,ppm) in ASSETS.items():
    folder(PROJECT/asset)
    preview=Image.new('RGB',(1600,1120),'#f3f0e9');d=ImageDraw.Draw(preview)
    font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',28)
    title=ImageFont.truetype('C:/Windows/Fonts/msyhbd.ttc',38)
    d.text((36,25),label+' · 前 / 后 / 左 / 右',font=title,fill='#303d40')
    d.text((36,85),'上传各方向的独立 PNG；此拼图仅供预览。',font=font,fill='#59655e')
    for i,(view,chinese) in enumerate(VIEWS.items()):
        record={'asset':asset,'view':view,'files':[]}
        if view in ('front','back'):
            source=OUT/f'{asset}-{view}-alpha-raw.png'
            im=Image.open(source).convert('RGBA');bbox=im.getchannel('A').getbbox()
            assert bbox and bbox[0]>0 and bbox[1]>0 and bbox[2]<im.width and bbox[3]<im.height
            target=(round(width*ppm),round(height*ppm))
            crop=im.crop(bbox).resize(target,Image.Resampling.LANCZOS)
            canvas=Image.new('RGBA',(2048,2048),(0,0,0,0))
            canvas.alpha_composite(crop,((2048-target[0])//2,(2048-target[1])//2))
            white=Image.new('RGBA',canvas.size,'white');white.alpha_composite(canvas)
            record.update(source_bbox=bbox,object_pixels=target,raw_sha256=sha(OUT/f'{asset}-{view}-keyed.png'),
                          prompt=str((OUT/f'{asset}-{view}.prompt.txt').relative_to(ROOT)).replace('\\','/'))
        else:
            source=OLD/asset/'transparent'/f'{asset}-{view}.png'
            canvas=Image.open(source).convert('RGBA')
            white=Image.open(OLD/asset/'white'/f'{asset}-{view}.png').convert('RGBA')
            record['reused_source']=str(source.relative_to(ROOT)).replace('\\','/')
        for variant,result in [('transparent',canvas),('white',white.convert('RGB'))]:
            path=OUT/asset/variant/f'{i+1:02d}_{view}.png';path.parent.mkdir(parents=True,exist_ok=True)
            assert not path.exists(),path
            result.save(path)
            folder(PROJECT/asset/variant)
            project=PROJECT/asset/variant/path.name;assert not project.exists(),project
            shutil.copy2(path,project)
            template=(ART/f'living-{asset}-manga-v1-{variant}.png.meta').read_text()
            Path(str(project)+'.meta').write_text(re.sub(r'^guid: .*$',f'guid: {uuid.uuid4().hex}',template,flags=re.M))
            record['files'].append(dict(path=str(path.relative_to(ROOT)).replace('\\','/'),project_path=str(project.relative_to(ROOT)).replace('\\','/'),sha256=sha(path)))
        manifest['images'].append(record)
        x=36+(i%2)*782;y=152+(i//2)*476
        d.rounded_rectangle((x,y,x+744,y+445),radius=14,fill='white')
        d.text((x+20,y+14),f'{i+1:02d}  {chinese}视图 / {view}',font=font,fill='#303d40')
        crop=canvas.crop(canvas.getchannel('A').getbbox());crop.thumbnail((700,340),Image.Resampling.LANCZOS)
        preview.paste(crop,(x+(744-crop.width)//2,y+80+(340-crop.height)//2),crop)
    preview.save(OUT/f'{asset}-front-back-left-right-preview.png')
    text=(f'{label} 前后左右四视图\n01_front.png -> 前；02_back.png -> 后；03_left.png -> 左；04_right.png -> 右。\n'
          'white/ 和 transparent/ 是相同四个角度的两种底色，选择一套上传；不要把两件家具混在一起生成。\n'
          f'目标尺寸：宽 {width} × 深 {depth} × 高 {height} 米。\n'
          '按物体局部坐标：正面朝 -Z，前视相机在 -Z；后视在 +Z；左视在 -X；右视在 +X。\n'
          '全部为 2048×2048；同一物体四图使用相同像素/米标尺。侧图显得较窄是实际深度所致。\n'
          '原图未露出的沙发背部是同款布艺面板的保守设计补全；图像不是实际3D网格的精确投影。\n')
    (OUT/asset/'使用说明.txt').write_text(text,encoding='utf-8')
    with zipfile.ZipFile(OUT/f'{asset}-front-back-left-right-v1.zip','w',zipfile.ZIP_DEFLATED) as archive:
        for p in sorted((OUT/asset).rglob('*')):
            if p.is_file():archive.write(p,p.relative_to(OUT/asset))
(OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print('Packaged 8 cardinal views, 16 PNGs and two archives.')
