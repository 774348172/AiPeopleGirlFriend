# 运行时处理候选 v1

这里是第一阶段处理结果：保留原始 FBX 几何，仅将 BaseColor 降为 2048×2048 JPEG，作为运行时材质候选。

在 Unity 中先执行菜单 `Tools > Apartment > Audit Generated Furniture`，读取 `Temp/generated-furniture-audit.txt` 中的顶点、三角形、材质和包围尺寸。确认统计后，再决定减面比例和是否替换当前场景家具。

当前尚未替换运行时对象，也没有把这一目录标记为最终资产。
