"""Render an annotated furnishing brief from the exact user-selected image; no image generation."""
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

root = Path(__file__).resolve().parents[2]
data = json.loads((root/'Tools/Apartment/reference-layout.json').read_text(encoding='utf-8'))
ref = Image.open(root/data['reference']).convert('RGB')
out = root/'output/apartment-furnishing-layout'
out.mkdir(parents=True, exist_ok=True)
board = Image.new('RGB', (1600, 1190), '#f4f0e8')
d = ImageDraw.Draw(board)
font_path = 'C:/Windows/Fonts/msyh.ttc'
bold_path = 'C:/Windows/Fonts/msyhbd.ttc'
def font(size, bold=False): return ImageFont.truetype(bold_path if bold else font_path, size)
ink, muted, accent = '#303d40', '#65716c', '#55766a'
def text(x, y, value, size=21, color=ink, bold=False):
    d.text((x, y), value, font=font(size,bold), fill=color)
def wrap(x, y, value, width, size=20, line=31, color=ink):
    row=''
    for char in value:
        if font(size).getlength(row+char)>width:
            text(x,y,row,size,color); y+=line; row=''
        row+=char
    if row: text(x,y,row,size,color); y+=line
    return y

text(36,28,'出租屋 · 家具布局基准',38,bold=True)
text(38,88,'按你选定的图片布置  /  先落实位置与朝向，再替换正式模型',22,muted)
d.line((36,132,1564,132),fill='#d5d5c8',width=2)
plan=ref.crop((3,1,425,575))
scale=565/plan.width
plan=plan.resize((565,round(plan.height*scale)),Image.Resampling.LANCZOS)
board.paste(plan,(36,162))
for zone in data['zones']:
    px,py=zone['marker'];x=36+(px-3)*scale;y=162+(py-1)*scale
    d.ellipse((x-21,y-21,x+21,y+21),fill=accent,outline='#f9f7ee',width=3)
    d.text((x,y),zone['id'],font=font(20,True),fill='white',anchor='mm')
text(36,955,'左侧俯视图确定布局',25,bold=True)
wrap(36,1001,'电视在左、沙发在右；餐桌在左下，中央留给茶几。房间位置和家具朝向沿用原图。',565,22,34)

front=ref.crop((433,2,1032,220))
front=front.resize((910,round(front.height*910/front.width)),Image.Resampling.LANCZOS)
board.paste(front,(650,162))
text(650,507,'右侧视图补充陈设与朝向',23,bold=True)
summaries=[
 ('01  厨房','沿左墙和窗下布置台面；补齐灶台、烟机、冰箱及小家电。'),
 ('02  玄关','保留狭长入口；鞋柜和挂放区沿侧墙布置。'),
 ('03  主卧','床头朝上方窗墙，床尾朝客厅；床边放柜与灯。'),
 ('04  次卧 / 杂物间','保留储物架、桌面和杂物收纳，中央留通道。'),
 ('05  客厅','左墙电视与低柜；右侧沙发朝左，中间茶几与地毯。'),
 ('06  餐桌区','独立小餐桌与餐椅放左下方，靠阳台一侧。'),
 ('07  卫生间','洗手台、洗衣机等沿侧墙布置；保留洗浴通道。'),
 ('08  阳台与纸箱','阳台两侧植物、中间通行；纸箱实测后放门内左侧窗边。')]
for i,(title,body) in enumerate(summaries):
    x=650+(i%2)*465;y=554+(i//2)*122
    d.rounded_rectangle((x,y,x+445,y+108),radius=12,fill='#e8e9df')
    text(x+16,y+10,title,23,accent,True)
    wrap(x+16,y+47,body,410,19,28)
d.rounded_rectangle((650,1058,1560,1153),radius=12,fill='#e5d9c7')
text(668,1068,'落地检查：通道 · 门扇开合 · 纸箱到入户门的视线',22,bold=True)
text(668,1106,'图示右侧纸箱遮挡入口视线；落地改为门内左侧窗边。',19)
text(36,1147,'布局标注预览  ·  2026-09-14',19,muted)
board.save(out/'reference-layout-board.png')
print(out/'reference-layout-board.png')
