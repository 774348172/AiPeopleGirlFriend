# 家具 v2 — 2026-09-15

以用户提供的沙发2.zip 和凳子2.zip 的外形比例为准。原 FBX 保持不变，2048 BaseColor 和 Normal 为发布候选贴图。

已生成 `../Prefabs-v2/SofaV2.prefab`、`StoolV2.prefab` 及独立 URP Lit 材质。Normal 使用 NormalMap 类型和线性采样；Prefab 的 UniformScale 层仅进行整体等比缩放，轴心位于底部中心，BoxCollider 按完整层级变换计算。

| 资产 | 三角形 | Unity 原始 XYZ 尺寸（米） | 整体缩放倍数 | Prefab XYZ 尺寸（米） |
|---|---:|---|---:|---|
| 沙发 | 12,453 | 0.412474 × 0.377942 × 0.980346 | 2.550120 | 1.051859 × 0.963797 × 2.500000 |
| 茶几（文件名凳子2） | 24,287 | 0.981384 × 0.422516 × 0.919953 | 1.018969 | 1.000000 × 0.430531 × 0.937403 |

此前直接用 mesh.bounds 判定沙发比例错误是不准确的：它忽略了 FBX 子节点的轴向转换。这里的 Y 才是 Unity 高度，沙发宽度沿 Z；禁止重新按旧宽高深逐轴拉伸。

家具已放入 `Assets/Scenes/ApartmentFurnitureV2Review.unity` 客厅验收场景，原未保存空白场景另存于 `Assets/Scenes/UserDrafts/`。验收场景不在发行场景清单中，程序运行时低模尚未替换。

菜单：Tools > Apartment > Open Furniture V2 Living Room Review。Validate Furniture V2 Review 输出比例、材质和碰撞检查以及 `output/furniture-v2-review/living-room.png` 实际渲染。
