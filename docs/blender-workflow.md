# 独立 Blender 建筑工作流

在仓库根目录运行。准备本机的 BMC4 实例目录及 Blender。默认使用 `tools/blender/blender.exe`，也可以传 `--blender` 指定安装位置。方块资源从 BMC4 安装目录读取。

~~~powershell
$py = '.\.venv\Scripts\python.exe'
$env:MCBUILDER_INSTANCE = '<PCL 的 BMC4 版本目录>'
& $py -B -m src new --sample courtyard --title '庭院'
~~~

在 `projects/courtyard/design.py` 编写 `build()`。返回 `Building`，或返回 `(Building, {(x, y, z): "02 一楼/门窗"})`。部件路径用斜线组织，未分组的方块归入 Structure。项目使用原有的 Minecraft X 东、Y 上、Z 南坐标。

~~~powershell
& $py -B -m src build --sample courtyard --preset quick --view overview
& $py -B -m src render --sample courtyard --preset review --view overview --view top
& $py -B -m src open --sample courtyard
& $py -B -m src render --sample courtyard --preset final
& $py -B -m src verify --sample courtyard
~~~

`build` 运行设计源码、校验真实安装资源并渲染。`generate` 单独生成蓝图和材料索引。只调相机、光照或表现方式时使用 `render`。每档保留最新一次输出，位置为 `previews/<工程>/<档位>/`。

| 档位 | Cycles 设置 | 默认输出 |
| --- | --- | --- |
| `quick` 快速设计 | 720×600，最多 4 采样 | 首个视角。可重复传 `--view`，用 `--save-scene` 同时保存工程 |
| `review` 建筑审阅 | 1080×900，最多 16 采样 | 前四个视角及含全部机位的工程 |
| `final` 最终交付 | 1440×1200，最多 64 采样 | 全部视角、工程及复验。传 `--view` 可选代表图，工程仍包含其他机位 |

Cycles 优先探测 OptiX，再探测 CUDA，必要时使用 CPU。`render.json` 记录输入哈希和每张图耗时。`--look game` 使用中性日光、Standard 色彩和 JAR 模型贴图。`--look showcase` 使用带色温的日光和 AgX，适合表现建筑体量与室内外明暗。静态预览使用第一张动画帧和近似的植被染色，相关说明见材料索引或交付复验。

## 机位和剖面

省略 `views.json` 时，自动生成 `overview, front, rear, west, east, top`。顶部为正俯视，图像上方朝北。按需在项目目录加入 `views.json`。

~~~json
{
  "views": {
    "street": {"position": [-18, 8, 24], "target": [0, 4, 0], "scale": 29},
    "inside": {"position": [2.5, 2.65, 5.5], "target": [2, 2.6, 0], "lens": 20},
    "floor": {"position": [0, 36, 0], "target": [0, 1, 0], "scale": 24, "section": "Ground plan"}
  },
  "sections": {
    "Ground plan": {"min": [null, 0, null], "max": [null, 3, null]}
  },
  "stage": {"ground_y": -0.04, "sun": [-4, 6, 5]}
}
~~~

相机用 `scale` 指定正交宽度，或用毫米 `lens` 指定透视镜头。近距离可设置 `clip_start`。剖面 `min/max` 包含边界方块，用 `null` 代表该轴不限，`include/exclude` 可用部件路径挑选或隐藏整组。剖面按保留的方块重新合并网格。建筑和布景分别列在 Outliner。可用鼠标中键和滚轮自由观察，顶部 Scenes 菜单切换剖面。时间轴上每个标记绑定一个机位，选对应的帧后按小键盘 0 查看相机。要看地下室实景，也可从指定的室内相机直接切入。

## 选材和版本

材料索引用 Pillow 读取像素色并生成拼图。使用已装 Pillow 的 Python，或在当前虚拟环境运行 `& $py -m pip install Pillow`。目录索引和已查询的外观缓存写入 `.cache/material-library/`。

~~~powershell
& $py -B -m src materials '方解石' --kind stone --style classical --color '#cbc1ab' --limit 8 --sheet calcite.png
& $py -B -m src materials --id twigs:spruce_table --id quark:cyan_framed_glass --json
~~~

搜索支持中英文名称、方块 ID、模组、用途、风格标签和代表贴图平均色。中文取自本机语言文件和启用的资源包。拼图展示真实的代表贴图，有些家具贴图会展开成多个面。JSON 提供静态模型匹配的状态示例。特殊模型会标记原因，确有需要时用 `--approximate` 为已安装方块显式启用视觉替代，并查看报告。`building.json` 记录原始方块状态。

~~~powershell
& $py -B -m src snapshot --sample courtyard --version before-window --preset review --view overview --view top
# 修改 design.py 后重跑 build，再检查定向修改的范围
& $py -B -m src diff projects/courtyard/versions/before-window projects/courtyard --bounds 3 2 6 7 4 6
& $py -B -m src restore --sample courtyard --version before-window --as courtyard-before-window
~~~

`snapshot` 保存设计源码、蓝图、分组、相机、材料和选中的关键图片。源码使用单个 `design.py` 并可复用仓库中的共享 Python 模块。`diff --bounds` 使用包含边界的最小和最大坐标，越界时返回非零。可用 `--out` 导出逐块差异。`restore` 把指定版本恢复为可继续生成和渲染的新工程。Git 中的快照保留原始文件哈希和图像。从公开快照恢复后，用本机 BMC4 运行 `generate` 补齐材料索引，再运行 `render`、`walk` 或 `compare-views`。经典建筑和仅存蓝图的历史稿可用原蓝图及 `render --instance <BMC4 目录>` 重建预览。 [公开效果与版本归档](previews/README.md)

## 设计比较和动线

先打开参考图，记下它解决的具体设计问题，例如哪一段屋脊应该压低、塔顶和侧台谁应成为焦点。体量有分歧时，用两份小型 Python 草案快速生成主要块面。几何确定后再查本机材质和构件状态。可从 [建筑参考与案例](architecture-intelligence.md) 按构件或空间问题查找线索。

~~~powershell
& $py -B -m src walk projects/courtyard --from 0 0 6 --via 2 3 1 --to 4 6 2
& $py -B -m src compare-views projects/courtyard/versions/before-window projects/courtyard --view overview --view top --cpu --out previews/courtyard/window-comparison
~~~

`walk` 的端点和途经点是**脚下方块**的 X、Y、Z。检查读取蓝图和同版材料索引，沿四邻格搜索全块、台阶与直楼梯的步行路径，要求支撑方块上面两格为空且相接边缘高差至多半格。可以重复传 `--via` 指定落脚平台。路线不通时返回非零，`--out` 可存路径坐标。门、转角楼梯、攀爬和模组特殊碰撞需结合剖面另验。桥宽、护栏和内部视线也要单独看图。

`compare-views` 接受已生成的工程或快照目录，需要 `materials` 同样使用的 Pillow。默认使用首个工程的机位和布景，`--views-file` 可指定给两边共用的新机位。默认以 quick 渲染，灯笼方块在画面中，随方块位置生成的补光在两边均关闭。命令把独立图、原尺寸并排图和蓝图增删替换统计写入新目录，工程和快照可直接继续使用。剖切视角用于对比时优先按坐标定义切面，组名过滤也适用于两边的部件。

复刻时先在 `views.json` 调整方位、俯仰、投影和取景。给每个有对应画面的选定机位传 `--reference VIEW IMAGE`。例如在上方命令中加入 `--reference overview references/oblique.jpg --reference top references/roof.png`，得到原作、前版、后版三栏，两个 Blender 画面仍共用相机和光照。原图按比例缩放并留边，`comparison.json` 记录原图路径、像素尺寸和 SHA-256。未指定参考的机位仍生成两栏。近似匹配的视角适合检查轮廓、结构数量和留白，尺寸估计另记拍摄角度和清晰度限制。

跨楼层、跨庭院或跨轨道的建筑，分别对每个公共目的地执行 `walk`。平面相邻的两块铺面也要查高差及连接。为判断路径是否经过预期设施，可用 `--via` 指定桥面、平台或门口，并查看输出路径。鸢坡站的远侧站台在基线外观和平面中均存在，独立审阅后加测该路线才证实断开。[站台剖面和路线记录](../projects/art_nouveau_hillside_station/README.md#复验)

复杂建筑需要独立判断时，用 `snapshot` 冻结作品，请未参与设计的 Codex 子 Agent 先打开原需求、参考图和真实渲染图。先请它记录少数问题的部位、现象、优先级、可能成因和复验时应看到的变化。观察角度不足时，请它提出一个能判定疑点的新机位或剖面，使用 `--views-file` 给两版共用相机。设计者的自评留到初判之后。

复验提供同机位的前后图、差异范围及对应的路线证据。沿用原问题逐项判定已解决、部分改善、未解决或引入退化。构图、空间可读性和公共连接各有相应的图像或蓝图证据。简单改色和小修可以直接定向检查。

现有 `--sample medieval_house` 等样例支持原命令和 `--quality draft`。也可加 `--preset quick --view 01-front` 使用新版单视角设计模式。

## 本机验收

[折岸植物标本馆](../projects/oxbow_herbarium/design.py)由椭圆展厅、斜向阅读翼、夹层露台和地下档案室组成。首稿和改窗前版本保存在项目的 `versions/`。南窗由透明玻璃换成 Quark 青色镶框玻璃，[方块差异](../projects/oxbow_herbarium/south-window-diff.json)记录了 12 格替换，窗外零改动。另将改窗前版本恢复到测试工程并重生成，蓝图 SHA-256 一致。

同一份中世纪住宅的八格窗户改色蓝图，在本机 Blender 5.2.4 中也测了两次命令。旧 draft 输出四张 720×600 图、保存并重开工程，用时 **9.54 秒**。新版 quick 输出一张同分辨率的正面图，用时 **3.52 秒**，包括这次修改的索引刷新。另用原有命令重新生成经典住宅并重开验证，四张图均可解码。此次会话的 GPU 探测未返回可用设备，测量使用 Cycles CPU。

标本馆的成品在 `previews/oxbow_herbarium/final/`。保存并重开的工程覆盖 3546 格、32 个合并部件、5 个场景、13 个机位和 35 张嵌入贴图。实际切换了相机标记和屋顶部件可见性。此次发布的 7 张 1440×1200 成图用时约 4 分 46 秒。
