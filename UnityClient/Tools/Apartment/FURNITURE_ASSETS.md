# 客厅独立家具设计图 v1 — 2026-09-14

用户批准开始制作沙发和茶几。本轮交付两件家具各自的单体三分之四视角设计图，透明版和白底版均已存入 `AiGirlFriendUnity/Assets/Art/Apartment/Furniture/References/`。状态为设计图已生成；尚未产出这两件正式 3D 模型或替换当前运行时低模，也未将新图记为用户已确认。

## 设计与建模目标

| 资产 | 风格与结构 | 目标尺寸（宽 × 深 × 高） | 游戏接入 |
|---|---|---|---|
| 灰绿布艺三人沙发 | 三坐垫、三靠垫、圆润包边、暖木底框、短脚、两只米白抱枕；细漫画线稿、克制手绘材质 | 2.50 × 0.94 × 0.92m，坐面约 0.46m | 保留 `LivingSofa` 的世界 X/Z=(1.45,-2.55)，世界碰撞包围尺寸=(0.94,0.92,2.50) |
| 暖木色茶几 | 单层空桌面、微圆角、窄裙边、四条略收分木腿；木纹和少量边缘磨损 | 1.00 × 1.18 × 0.47m | 保留 `LivingCoffeeTable` 的世界 X/Z=(0.05,-2.55)，世界碰撞包围尺寸=(1.00,0.47,1.18) |

沙发资产建议以局部 X 为宽、Y 为高、Z 为深，正面朝 -Z，落地底面中心为轴心；导入现有场景时绕 Y 旋转 +90°，使沙发朝世界 -X。茶几保持零旋转，轴心同样在落地底面中心。最终 Y 由现有房屋的地面采样确定。

以上数字来自已验证的游戏布局，是后续网格缩放和校准标准；生成图片的透视不能当作 CAD 测量。模型返回后核查背面、底面、四脚和坐垫连接，处理单图不可见面的重建结果。不要因为设计图只显示三只脚就把模型做成三脚结构。

## 交付文件

目录：`AiGirlFriendUnity/Assets/Art/Apartment/Furniture/References/`

- `living-sofa-manga-v1-transparent.png`：真实 alpha 通道，1672×941。
- `living-sofa-manga-v1-white.png`：相同图像合成白底，适合不支持 alpha 的图生模型工具。
- `living-coffee-table-manga-v1-transparent.png`：真实 alpha 通道，1514×1039。
- `living-coffee-table-manga-v1-white.png`：相同图像合成白底。

每次给图生模型工具上传对应家具的**一张单体图**；透明版和白底版是同一视角的两种底色，不作为两张不同角度同时上传。茶几桌面保持空置，沙发仅包含配套抱枕，避免把书、花盆或地毯合进家具网格。两图均无地面、室内背景、标签或尺寸文字；没有强方向光和地面投影，保留了必要的材质笔触与轻微体块明暗，不是严格测量的纯 albedo 贴图。

## 生成方式与来源

沿用用户此前明确选择的 CLI 和 `gpt-image-2.5-flare`。使用 imagegen 技能自带的 `image_gen.py edit`，通过已配置的 API 传入两张支持参考：

1. `Assets/Art/SceneReferences/apartment-layout-approved-20260914.png`：家具轮廓、灰绿/暖木配色和出租屋气质。
2. `Assets/Art/StyleReferences/apartment-anime-background-style-v1.png`：已确认的漫画线稿和手绘材质。

此次是使用参考生成新单体设计图，CLI 以 images/edits 接口携带输入图；不是从布局截图中裁剪家具。沙发请求尺寸为 1536×1024，茶几请求为 1024×1024；服务实际返回上述非标准尺寸，保留原生像素而未强行拉伸。

精确提示词：

- [沙发提示词](../../output/imagegen/living-sofa-manga-v1.prompt.txt)
- [茶几提示词](../../output/imagegen/living-coffee-table-manga-v1.prompt.txt)

原始 API 输出为 `output/imagegen/*-v1-keyed.png`，背景为洋红色。使用技能自带 `remove_chroma_key.py`，参数 `--auto-key corners --soft-matte --transparent-threshold 55 --opaque-threshold 150 --despill` 转为 alpha；这是本地抠图，不是服务原生透明输出。随后 `prepare_furniture_references.py` 合成白底并复制到项目目录；该 v1 打包脚本保护已有文件，重新制作应使用新版本文件名。

## 校验与后续

已目视检查两张白底成图：主体完整、轮廓与木腿清楚、没有房间/地面背景和明显洋红描边；已检查 alpha 四角为零、主体不贴边，透明/白底交付文件与源图哈希对应。制作清单、实际像素尺寸、alpha 统计、提示词及文件 SHA-256 保存在 `output/imagegen/living-furniture-concepts-v1.manifest.json`。

下一步将两张单体图分别用于图生模型，再检查拓扑、UV、实际面数和背面结构，按表中米制尺寸校准后替换 Unity 中对应家具。沿用现有碰撞边界和站位；正式模型接入后复验门口净空、人物通行与壁纸镜头。地毯以及其他家具的正式美术后续继续。

## 上下左右独立四视图 — 2026-09-15

用户随后要求两件家具的“上下左右”四视图。本次按字面制作俯视、仰视、左侧、右侧，每件四张独立图，保留漫画画风和原灰绿/暖木配色。不是前后左右，也没有将四个角度拼成一张模型输入图。

交付：`output/imagegen/furniture-four-views-v1/sofa-four-views-v1.zip` 和 `coffee-table-four-views-v1.zip`。各包的 `transparent/` 与 `white/` 各有同一套四角度 PNG，选择一种底色使用。项目副本在 `Assets/Art/Apartment/Furniture/References/FourViews-v1/{sofa,coffee-table}/{transparent,white}/`。

方向定义：top=正上方俯视、bottom=正下方仰视；上下图后方都在图片上侧。left=从局部 -X 看向 +X，前方在画面左；right=从 +X 看向 -X，前方在画面右。若模型工具只提供前/后/左/右槽位，不要把上下图误填为前后图。

八张图分别通过此前已授权的 `gpt-image-2.5-flare` CLI edit 生成。先生成俯视/左侧，再将对应已校准视图与原单体图一起作为底部/右侧的支持输入。茶几第一版底部透视和木纹方向不一致，已单独修正为 v2。底部构造来自保守设计补全：沙发为木框/防尘底布/四脚，茶几为原桌板背面/窄裙边/四脚，没有已存在模型可用于测量底部。

生成图去除洋红底后，按目标外轮廓宽高分别缩放，再居中放入 2048×2048 画布：沙发每米 640 像素，茶几每米 1350 像素，同一家具四张统一比例标尺。该过程仅校准外轮廓比例，不保证针脚、木纹和内部结构达到 CAD 级跨视图一致性；原始 API 图和抠图原图均保留。

预览拼图 `sofa-four-views-preview.png` / `coffee-table-four-views-preview.png` 仅供浏览，格内分别放大，不供模型输入。已检查八个角度、16 张交付 PNG 的透明边缘、完整轮廓、2048 画布、项目副本哈希和两个 ZIP 完整性；尚未在第三方模型生成工具中验收。

精确提示词、输出比例校准记录和 SHA-256：`output/imagegen/furniture-four-views-v1/*.prompt.txt` 与 `manifest.json`。整理工具：`Tools/Apartment/prepare_furniture_four_views.py --final`。本轮只添加参考资产，不修改已验证的家具运行时代码或 Windows 包。

## 当前推荐：前后左右独立四视图 — 2026-09-15

用户确认模型工具只有前、后、左、右四个输入槽位，后续使用本节的新套图。沙发与茶几各四张独立 PNG，依次为 `01_front.png`、`02_back.png`、`03_left.png`、`04_right.png`。之前的上下图保留作补充参考，不填入前后槽位。

交付目录：`output/imagegen/furniture-front-back-left-right-v1/`，内含 `sofa-front-back-left-right-v1.zip` 与 `coffee-table-front-back-left-right-v1.zip`。每包包含 `white/` 和 `transparent/` 两种底色的四张图及使用说明，选一套分别上传对应方向。项目副本位于 `Assets/Art/Apartment/Furniture/References/FrontBackLeftRight-v1/{sofa,coffee-table}/{white,transparent}/`。

沿用已授权的 `gpt-image-2.5-flare` 和 imagegen CLI edit，以对应的 `living-*-manga-v1-white.png` 单体图及上一套已校准左视图作为支持参考，新生成两件家具的正面和背面；左右图复用上一套成图。四份精确提示词为交付目录内 `sofa-front.prompt.txt`、`sofa-back.prompt.txt`、`coffee-table-front.prompt.txt`、`coffee-table-back.prompt.txt`。洋红底使用技能脚本去除，再由 `Tools/Apartment/prepare_furniture_cardinal_views.py` 整理透明和白底版本。

全部图片为 2048×2048，同一家具统一像素/米标尺；新正背面按目标宽高分别校准外轮廓。该校准不保证内部细节的精确跨视图一致性，沙发未露出的背部为同款布艺面板的设计补全。预览拼图仅用于浏览，不上传为模型输入。

已目视检查两件家具全部方向，并验证 16 张 PNG 尺寸、透明四角、项目副本哈希、左右图像素复用和两个 ZIP 完整性。清单与来源记录在 `manifest.json`。尚未生成或接入正式 3D 网格，也未在第三方模型工具中验收。


## v2 模型运行时接入 — 2026-09-15

用户提供的 `GirlModel/沙发2.zip` 与 `GirlModel/凳子2.zip` 已接入运行时资源。发布副本位于 `AiGirlFriendUnity/Assets/Resources/ApartmentFurniture/{SofaV2,StoolV2}.prefab`，由 `ApartmentFurnishings.Build` 优先加载；资源缺失时保留原程序化家具回退。对象 ID 仍为 `LivingSofa` 与 `LivingCoffeeTable`，现有交互和测试引用不变。Prefab 使用用户模型自身比例，仅整体等比缩放，并关闭子 Prefab 碰撞体以使用运行时外层 BoxCollider。

Prefab 和材质验收通过：`output/furniture-v2-review/living-room.png`、`output/furniture-v2-review/validation.txt`。正式运行时 Windows 包尚未重新构建；接下来应运行现有 PlayMode 回归测试和一次启动场景检查，再重新打包。

## 运行时家具合批修复 — 2026-09-16

旧装饰合批会递归收集导入 FBX 网格；构建版网格不可读时 `CombineMeshes` 报警告，但随后仍删除源对象，导致沙发/凳子消失。`ApartmentFurnishings.Merge` 现仅合并本类生成的可读 Cube 网格，保留导入 Prefab 的完整网格、子材质和变换层级；可读导入网格也不参与这条装饰合批路径。未开启 FBX Read/Write，未改变模型比例、碰撞或房屋尺寸。

新增回归验证导入模型几何、材质、激活状态和相对变换，以及可读/不可读的多子网格保留。程序化家具仍执行原 5,000 三角面预算；导入沙发 12,453、凳子 24,287 三角面单独与源 Prefab 核对，避免把模型被删除误判成预算达标。

Apartment PlayMode **21/21 通过**。新 Windows 包 `Builds/CatGirlfriend-FurnitureBatchFix-20260916/CatGirlfriend.exe` 构建成功；两次壁纸启动和 12 次门窗操作通过，运行日志无不可读网格合批警告。第三次启动临时切到普通窗口，用 Computer Use 检查真实游戏画面：沙发、抱枕、木腿和前方木质凳子/茶几可见，之后恢复壁纸并正常退出。完整证据与重现方法见 `output/furniture-batching-20260916/RESULTS.md`。
