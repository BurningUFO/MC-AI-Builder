# 天空即为极限 · 东花亭

[打开全亭效果](review/overview.png) · [成品封面对照](review/cover.png) · [施工檐面对照](review/canopy.png) · [白托架对照](review/bracket.png)

从 `3c713d3` 的整塔蓝图提取东花亭，以原坐标 `(40,45,0)` 为局部原点。基线 224 格，新版 198 格，尺寸为 **13 × 30 × 13**（X × Y × Z）。本项目可独立生成和在 Blender 中绕看，原整塔仍可照常打开。三栏图依次为原作、基线和新版。[设计源码](design.py) · [蓝图](building.json) · [基线快照](versions/east-3c713d3) · [原整塔](../sky_limit_replica/README.md)

## 看到了哪些变化

| 部位 | 原作和两版的实际差异 |
| --- | --- |
| 蓝白盖面与白檐 | 施工图集二 R8C4 露出连续的锯齿状冰色边，R8C7 已搭上外伸白片。基线是四颗相互分离的冰块。新版铺成一层菱形台面，连同上层四角共 24 个冰块，通过共享方块面连成一体。四个白色檐头从侧面挡住部分冰边。封面裁图第 78 行的两段暗面约在 x=208–235、262–292。最终投影第 73 行两段蓝面约在 x=210–242、265–306。右段偏宽，标高还差约 5 个原图像素。[檐面](review/canopy.png) · [成品](review/cover.png) |
| 倒阶托架 | R8C2 中白色横轨之间露出方孔，到 R8C4 上方才被盖住。基线 Y=7 的四个对角格均实心，新版该层保留四个 1×1 格孔，成对的短臂向中心柱收分。切换到 Blender 的 `Before the blue deck` 场景可揭开盖面。远侧孔数及被盖住的孔深采用对称推断。[施工顺序](references/courses.png) · [施工态托架](review/bracket.png) |
| 柱颈 | 在 528×440 封面裁图的 y=143 行计入两侧细亮边，原作柱颈宽约 31 px（x=247–277）。基线和第一稿约 50–51 px，最终约 30 px（x≈247–276）。放宽的柱套从下一层开始。720×600 Blender 图按 11/15 换回原图尺度。[同机位封面](review/cover.png) |
| 檐下悬饰 | 成品封面可分辨靠外的绿挂件和细白杆，以及更靠柱身、终点更低的绿色短坠。基线只有外圈。新版在半径 5 与 2 格分别悬挂，内侧绿坠接在较低灯座上，中央长尾另行收细。四向重复和精确材质是补全，后侧接缝在原图中被遮挡。[封面局部](references/cover-east.png) · [材料表](materials.csv) |

独立 Critic 先从原作与旧渲染指出断开的冰面、单层悬饰和上翘檐头。第一稿在两个方向都改善了这些部位，复验时又发现柱颈偏宽，成品露出贯通的蓝带。最终稿收窄柱颈，并在冰层前补白色檐头。三栏图使用相同相机、布景和 CPU Cycles 档位。蓝图差异为新增 66 格、移除 92 格、替换 63 格，均在单亭范围内。

## 参考与相机

夜言-碎雨的原作元数据标注 1920×1080。公开播放器本次给出最高 852×480 的视频轨，主节点和播放器提供的备用节点都返回 HTTP 403。本次可用画面为已有的 1528×955 封面与 480×270 施工缩略帧。参考用 Pillow 在原像素上裁取，三栏仅为阅读显示放大。近景呈现两个不同方向，两个施工阶段共用同一个斜俯机位。[原作视频](https://www.bilibili.com/video/BV11U4y127EJ/) · [原封面](../sky_limit_replica/references/cover.jpg) · [原施工图集二](../sky_limit_replica/references/sheet2.jpg)

- **成品低视角**取封面 `(916,367,528,440)`，坐标为左、上、宽、高。用四根柱轴的屏幕横坐标和外环前后缘定取景。半径和轴距沿用整塔的估计。四轴拟合横残差为 2–4 px。留出的内环前缘约在第 565 行，预测为 565.6。裁图用镜头平移保留原透视。[封面裁图](references/cover-east.png)
- **施工斜俯视角**取图集二第 8 行第 7 列内的 `(96,20,288,240)`。第 7 行第 10 列露出的四个灯座和目标帧共用环廊背景，两处背景纹理配准位移为零。假定灯座距轴心两格、全帧焦距 240 px、相机无滚转，用四个灯心估计姿态，再用两环定垂直位置。拟合灯心高度为 6.87 格，蓝图取最近的方块中心 6.5 格。重开 Blender 后，四个拟合点投影与原像素相距 0.40–0.76 px。另选四个环上点回投到水平面，径向误差绝对值为 0.03–0.28 格。这些数值依赖格距、焦距与环半径假设，只用于检查机位。第 8 行第 2 列重复同一裁框，验证较早的托架阶段。[施工裁图](references/canopy.png) · [托架裁图](references/open-bracket.png) · [配准计算](references/align.py)

两方向足以核对这几处可见结构。原视频近景、无遮挡的成品背面和仰视尚缺，无法确定所有方块状态、背侧接法、灯座绝对高度和 BSL 下的真实颜色。该成绩只适用于这座隔离花亭。

## 复现

在仓库根目录运行，`MCBUILDER_INSTANCE` 指向本机 BMC4 版本目录。普通建模和渲染沿用仓库的 Python、Pillow 与 Blender。只有重算上述相机才用 NumPy/SciPy，运行 `python projects/sky_limit_pavilion/references/align.py` 会输出机位和留出点误差。

```powershell
python -B -m src build --sample sky_limit_pavilion --preset final --look game --cpu --view overview
python -B -m src open --sample sky_limit_pavilion --preset final
```

工程在 `previews/sky_limit_pavilion/final/sky_limit_pavilion.blend`。时间轴可选封面或檐下机位，Scenes 可选两个施工阶段。蓝图、分组和材料表从独立目录重生后逐字节一致。Blender 5.2.4 LTS 重开复核了 198 格、3 个场景、5 个相机、11 张嵌入贴图及最终代表图。[复开记录](review/verification.json) · [渲染记录](review/render.json)

从公共归档重新比较时，先将基线恢复到新工程并生成本机材料索引。`--out` 取一个新目录。

```powershell
python -B -m src restore --sample sky_limit_pavilion --version east-3c713d3 --as sky_limit_pavilion_before
python -B -m src generate --sample sky_limit_pavilion_before
python -B -m src compare-views projects/sky_limit_pavilion_before projects/sky_limit_pavilion --views-file projects/sky_limit_pavilion/views.json --view cover --reference cover projects/sky_limit_pavilion/references/cover-east.png --view canopy --reference canopy projects/sky_limit_pavilion/references/canopy.png --view bracket --reference bracket projects/sky_limit_pavilion/references/open-bracket.png --look game --cpu --out previews/sky_limit_pavilion/repeat
python -B -m src diff projects/sky_limit_pavilion_before projects/sky_limit_pavilion --bounds -6 -17 -6 6 12 6
```

## 技术取舍

- 视频获取到可解码流时，FFmpeg 可直接按时码寻址。本次节点拒绝视频数据，已有施工图集能直接定位搭建顺序，故未引入 PySceneDetect 的画面差分切镜。FFmpeg 许可视编译选项而定，PySceneDetect 采用 BSD-3-Clause。[FFmpeg](https://ffmpeg.org/ffmpeg.html) · [FFmpeg 许可](https://ffmpeg.org/legal.html) · [ContentDetector](https://github.com/Breakthrough/PySceneDetect/blob/main/scenedetect/detectors/content_detector.py)
- Minecraft Voxelizer 按相机射线与背景掩膜删体素，Minecraft Reconstruction 在合法离散状态上检查每条射线的首个可见表面。这里的环廊、白杆和亭身相互遮挡，也缺少准确全掩膜。采用轮廓和留白约束、按施工进度揭开遮挡的办法。所查版本的仓库根目录均未见整体代码许可证。[Voxelizer 源码](https://github.com/justiny7/minecraft-voxelizer/blob/47f25662b320cecdb80f39efcc839827c6aa4ccb/src/voxelizer.cpp) · [首个可见表面约束](https://github.com/dmitrilvovich/Minecraft-Reconstruction/blob/2e39c04daa367e6dbe6729477e5db78f65a52acd/src/inference/a1_constraint.cpp)
- fSpy 用消失点定相机，PinSolver 用带权定位点和重投影误差。两者均为 GPL 系列。施工帧中的灯心共面，可见的直线段短且受遮挡，单凭通用 PnP 难以确定原始内参。这里用已有 SciPy 最小二乘拟合灯座与环的位置，固定施工焦距，在修改花亭前确定两个机位。多视角取景复用 Blender 的原生镜头平移。[fSpy](https://github.com/stuffmatic/fSpy/blob/702189ec5acbbd2c8ba492db0e52ecb5fc908f5c/src/gui/solver/solver.ts) · [PinSolver](https://github.com/kurono73/PinSolver/blob/e27c50e844c2b8ae63b245848b815ae4db3d0782/reprojection.py) · [OpenCV PnP](https://github.com/opencv/opencv/blob/4.x/modules/calib3d/src/solvepnp.cpp) · [SciPy least_squares](https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.least_squares.html)
- BlendHarness 的复验器逐条对照问题、改前改后图和场景差异，mcbuild 可主动查看不同方向。沿用已有独立 Critic，再用截面、同一原图行和绝对格坐标验证。img2blockbench 的轮廓重合需要可投影的源网格，两张不同进度的施工图无法提供这个输入。mcbuild 和 img2blockbench 为 MIT。所查 BlendHarness 版本根目录未见整体代码许可证。上述实现均只用于研究。[BlendHarness](https://github.com/cccky0813/BlendHarness/blob/53d6b5dcb125f52cea4430197a3399cad679ad0f/blendharness/agents/verifier.py) · [mcbuild 相机](https://github.com/harrynull/mcbuild/blob/34ce86372d4c23f4d83d4e24fc256cb399d6c3e3/src/mcbuild/render/camera.py) · [img2blockbench 轮廓审计](https://github.com/orca-gamedev/img2blockbench/blob/315fff912f83a9923e954ff6c596c00dc9832fa3/skill/img2blockbench/scripts/audit_source_overlap.py)
