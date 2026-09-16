# Generated furniture v1

本目录保存从 `GirlModel/凳子.zip` 和 `GirlModel/沙发.zip` 导入 Unity 前的原始模型副本。

- `sofa/sofa.fbx`：沙发模型
- `stool/stool.fbx`：凳子模型
- 各目录的 `basecolor.jpg`：对应 BaseColor 贴图

当前阶段只完成资源导入准备，尚未替换运行时家具。Unity 首次导入后需要记录模型的顶点数、三角形数、材质槽、包围盒和单位比例，再进行减面、贴图压缩、材质整理、碰撞体和 LOD 设置。

原始贴图约为 8192×8192，暂不直接用于最终运行时；处理阶段目标为 2048×2048 或更低，并保留本目录原始副本。
