# 鸢坡站 · Irisbank Station

一座原创 BMC4 新艺术运动山城车站。南侧街道经露台和扇形雨棚进入两跑楼梯大厅。到达站棚后，可向东进入带转角窗的候车室，向西跨轨到远侧站台。低票房、主大厅的单主峰和东侧候车亭构成三个不同高度。施工坐标为 X 向东、Y 向上、Z 向南。路线中的 Y 指脚下的方块。街道、入口、中间平台和站台分别位于 Y=-3、0、3、6，跨轨桥面位于 Y=9。

[定稿图片](../../docs/previews/art_nouveau_hillside_station/hero.png) · [纵剖](../../docs/previews/art_nouveau_hillside_station/section.png) · [冻结的 V1 基线](versions/v1-baseline) · [蓝图](building.json) · [设计源码](design.py) · [材料清单](materials.csv) · [BMC4 贴图拼图](../../docs/previews/art_nouveau_hillside_station/palette.png) · [相机和剖切](views.json)

## 设计和基线

在 `fd38c34` 的 Blender Workflow V2 和 Architecture Intelligence V1 下完成两份中性体量草案。固定视角下，单主峰能从街面直接指向大厅，两个等高塔把焦点移到侧翼。选择单主峰，保留低西翼和高东角。首轮实渲染后，调整了雨棚柱头、将站棚柱位与拱肋对齐，并把高反差的原版透明玻璃换成浅灰玻璃。冻结时已有 8 张审阅图、真实用料与 4 条通过的路线。 [体量对照](../../docs/previews/art_nouveau_hillside_station/massing.png) · [基线渲染记录](versions/v1-baseline/render.json)

- **Guimard 的 Porte Dauphine 入口。** 实拍中，肋条从弧梁放射到玻璃端部，立柱和扇缘之间有大片可见的玻璃。鸢坡站以这层承托关系组织主入口，保留暖色灯与绿铜之间的差异。 [RATP](https://www.ratp.fr/en/discover/heritage/hector-guimard) · [实拍](https://commons.wikimedia.org/wiki/File:Metro_station_entrance_(%C3%A9dicule_Guimard)_Porte_Dauphine_Paris_16e_001.jpg)
- **Ålesund 的 Apotekergata 10。** 临街转角的凸面和高冠集中装饰，相邻立面较平缓。鸢坡站据此把上层候车集中在东角，主拱与低票房留出各自的轮廓。 [实拍](https://commons.wikimedia.org/wiki/File:%C3%85lesund_hus.jpg) · [Viti 的地区建筑资料](https://www.vitimusea.no/kunnskap/freda-bygg-i-aalesund)
- **安特卫普中央站站棚。** 站台照片里，横跨轨道的重复拱肋比细密玻璃分格更醒目。鸢坡站沿用这种跨度与节奏。后续补看 Horta 故居的楼梯，发现栏带、灯具和顶光共同标出转折与平台，于是用侧灯和不同铺面提示两跑，抬高去站棚的开口。 [站棚实拍](https://www.locationscout.net/belgium/52417-antwerp-central-station) · [安特卫普城市资料](https://visit.antwerpen.be/en/info/centraal-station-en) · [Horta 楼梯实拍](https://www.admirable-artnouveau.be/nl/facades/horta-huis-en-atelier-horta-museum/)

材料通过本机 BMC4 的贴图拼图选定。大面用平滑砂岩与 Quark 石灰石，阴影处用 Twigs 凝灰岩砖。青色陶瓦和氧化切制铜强调屋面轮廓。花窗用白、浅灰、少量蓝和琥珀玻璃，候车用云杉木和 Handcrafted 长椅。蓝图记录完整方块 ID 与用到的状态。

## 独立审阅与改动

未参与设计的 Codex 子 Agent 先看需求、三张实景参考与基线渲染，再查看路线和方块。它要求补入口眼高与西端机位。换版后沿用这四项意见逐条核对。对照图左为 V1，右为定稿。

| 问题 | 证据与处理 | 复验 |
| --- | --- | --- |
| 高 · 远侧站台缺连接 | 基线远侧与近侧均有站台面，中间是低一格的轨床。两端都没有通路。初次走查只覆盖近侧。西端加三格宽天桥及两段梯口。 | **已解决。** 同一对端点在 V1 无路径，定稿经桥面走通 12 步。跨轨桥下保有原线的 69 格轨道。 [站台端部](../../docs/previews/art_nouveau_hillside_station/comparison/crossing.png) · [剖面对照](../../docs/previews/art_nouveau_hillside_station/comparison/cross-section.png) |
| 高 · 雨棚外檐压过托臂 | 基线整条绿铜边和白玻璃看起来像平托盘，支点只读成两根柱。定稿把冠面拆为浅灰玻璃与放射肋，柱头向两边出叉。 | **部分改善。** 檐下能辨分叉，正面遮挡减少。近看托臂仍粗，且全块玻璃前缘有厚度。 [檐下](../../docs/previews/art_nouveau_hillside_station/comparison/canopy-under.png) · [正面细节](../../docs/previews/art_nouveau_hillside_station/comparison/canopy.png) |
| 中高 · 大厅上行方向不清楚 | 基线两跑同材，画面中叠成亮面，顶部横框挡住站棚。定稿缩窄上跑、用浅深差显示平台、抬高拱口，在两侧放灯标示转折。 | **已解决。** 撤去候选的中轴构件后，入口眼平可辨踏步、侧灯、两跑分界与去站棚的开口。纵剖显示完整的层级关系。 [大厅](../../docs/previews/art_nouveau_hillside_station/comparison/hall.png) · [入口眼平](../../docs/previews/art_nouveau_hillside_station/comparison/entrance-eye.png) |
| 中 · 站棚两条白线争夺焦点 | 白玻璃带在 Z=-22、-11 处重复贯通。统一改用相邻瓦间的浅灰玻璃。 | **已解决。** 站台内先看到横向铜肋，棚面连续透光。 [站台长视](../../docs/previews/art_nouveau_hillside_station/comparison/platform.png) |

首个候选把五条相近的枝纹栅格化后，主枝挤成了暗块。保留三条间隔更大的线，才让玻璃留白和接缝连续同时成立。另一候选的中轴柱灯与孤立栏杆遮住了入口眼高视线，主路线增加了 4 步绕行。复验后撤去这组构件，恢复 49 步主路线，并恢复大厅顶窗的透明玻璃。定稿沿用了原入口、坡地层级、主山墙与不对称的侧翼。 [整体对照](../../docs/previews/art_nouveau_hillside_station/comparison/hero.png)

## 归因与沉淀

- 远侧站台是建筑连通缺陷。初次设计把近侧路线当作站厅通行的代表，原指导也只要求检查关键落脚点。既有 `walk` 加测公共目的地就能复现，配合 `--via` 可验证天桥。现在的指导明确逐个检查公共目的地及相应剖面。
- 楼梯视觉上的亮墙、过密枝纹和固定白玻璃带来自构图、线条尺度及材质选择。短跨花窗对方块宽度尤其敏感。真实 BMC4 全块玻璃仍会显出厚度，近景也会看到半格与整格的接缝。
- V1 能用高度场生成半格屋面。此次新增的 [`cubic_cells`](../../src/core/curves.py) 用于面上连续的分叉和长线，测试覆盖曲线端点与逐格连通。花窗、雨棚平面肋和柱头托臂已用同一函数。花窗指定切面上的深色线由 13 个分离组连成 1 组。主枝数量与铺面效果仍需实图作判断。
- 单主峰与两塔属构图取舍。依据入口焦点和层级选择单主峰，并保留了低票房与较高的候车角亭。

| 此前三项不足 | 本轮处理和证据边界 |
| --- | --- |
| P0 建筑知识积累 | [知识索引](../../docs/architecture-intelligence.md#按设计问题找案例) 按构件和空间问题收录已观察案例、BMC4 材料做法、可复用曲线及其失败条件。实图直接指导了扇肋、楼梯和站棚。其他材料和构件尺度的效果有待继续积累。 |
| P1 独立视觉 Critic | 发现了漏查的对侧通路，补两个判定疑点的相机，还指出中轴柱灯引入的退化。审阅与复验步骤写回 [AGENTS.md](../../AGENTS.md) 和[工作流](../../docs/blender-workflow.md#设计比较和动线)。 |
| P1 陌生主题泛化 | 全新车站完成参考、体量、施工几何和视觉修订，从空白工程和体量比较出发。用通用曲线、半格屋面和连通验证解决新问题。更广的风格与复杂空间仍需独立案例验证。 |

## 复验

| 路线 | V1 | 定稿 |
| --- | --- | --- |
| 下街 → 主入口 → 大厅中间平台 → 近侧站台 | 49 步 | 49 步 |
| 近侧站台 → 上层候车室 | 29 步 | 29 步 |
| 东侧室外梯 → 站台 | 18 步 | 18 步 |
| 大厅 → 票房 | 22 步 | 22 步 |
| 西端近侧站台 → 远侧站台 | 无通路 | 12 步 |
| 主入口 → 远侧站台 |  | 54 步 |

[路线与脚下坐标](evidence/routes.json) · [方块差异](evidence/blueprint-diff.json) · [同机位设置及图像哈希](evidence/comparison.json) · [定稿 Blender 复开记录](evidence/verification.json) · [基线 Blender 复开记录](evidence/verification-v1.json)

定稿有 36,796 格和 44 种方块 ID，含局部坡地。相比 V1 新增 171 格、移除 207 格、替换 480 格，改动范围为 `(-29,1,-23)..(29,18,14)`。两图共用相机、剖切和日光，在 Cycles CPU quick 下生成。自动灯笼点光在两边均关闭。步行模型检验标高和两格净空。灯笼使用首帧，草地和树叶使用近似着色。实际游戏邻接和复杂碰撞仍需实机验证。

定稿的 Blender 5.2.4 工程复开覆盖 4 个场景、16 个机位、42 张内嵌贴图和 5 张可解码的 1440×1200 图。CPU 生成这批定稿图约用 4 分 55 秒。迭代用 quick，八个机位每侧约 16 秒。材料拼图和并排图使用本机已有 Pillow 的 Python。

本地成品工程在 `previews/art_nouveau_hillside_station/final/art_nouveau_hillside_station.blend`，冻结基线工程在同级 `review/`。仓库根目录设置 `MCBUILDER_INSTANCE` 后可重生成。参照 [工作流](../../docs/blender-workflow.md#选材和版本) 中的 `restore` 还原 V1，再用本机生成其材料索引。

~~~powershell
$py = '.\.venv\Scripts\python.exe'
$sample = 'art_nouveau_hillside_station'
& $py -B -m src build --sample $sample --preset final --cpu --view hero --view hall --view platform --view waiting --view section
& $py -B -m src verify --sample $sample --preset final
~~~
