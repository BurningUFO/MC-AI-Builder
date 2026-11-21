# 教堂仓库的单层平桥

一层石质桥板底面贴水，水下由直柱承托。桥上用方解石砖墙做低栏，石英柱承托青蓝灵魂灯笼。磨制闪长岩铺主道，灰蓝磨制页岩压边，石英砖中线对准教堂大门。

## 尺寸和落位

| 项目 | 尺寸 |
|---|---|
| 桥长 | 25 格样例 |
| 桥宽 | 中央 7 格主道，两侧各 1 格栏杆位置，共 9 格 |
| 桥面标高 | 桥板一层整方块，踏面高于水面 1 格 |
| 水下支撑 | 五组直柱，每组横向 3 格、沿桥长 1 格，样例深度 4 格 |

把石英砖中线对准教堂现有七格台阶的第 4 格。现场先找与桥面同高的台阶踏面，再从该踏面沿中线量到岸边。25 格是样例长度。若需接长，可以沿着相同的断面铺桥面，再接续水下支柱。

## 搭建顺序

以水面为 Y=0。桥面方块铺在 Y=0，踏面顶是 Y=1。宽度 X 为 0–8，长度 Z 为 0–24。

1. 从水下河床开始砌支柱。样例的五组支柱在 Z=2、7、12、17、22，每组占 X=3、4、5，示例从 Y=-4 一直砌到 Y=-1。河床更深时从各柱底部向下补砌。
2. 在 Y=0 铺一层 9 格宽的桥面。X=4 整列铺石英砖，门侧和岸侧各一横排也用石英砖接头。Z=7、12、17 在中线两侧各用一块石英砖做横纹。X=1、7 用磨制页岩压边，主道其余位置用磨制闪长岩。
3. X=0、8 铺平滑石英饰边。Z=8、16 各嵌一对海晶灯，共 4 盏。灯的顶面直接参加桥面铺装。
4. 在 X=0、8 的 Y=1 砌方解石砖墙。Z=0、7、12、17、24 改用石英柱。Z=0、7、17、24 的柱顶各放一盏灵魂灯笼，共 8 盏。Z=12 的两个柱顶用平滑石英下半砖收顶。

侧视图把水面暂时隐藏，白色桥身的下缘就是水位。打开 Blender 后也可以在大纲的 Studio 集合里切换 River surface 的可见性，查看柱子。

## 文件

- 本地生成的 Blender 场景位于 `previews/warehouse_flat_bridge/warehouse-flat-bridge.blend`。
- [水面总览](previews/warehouse_flat_bridge/01-overview.png) · [桥头视角](previews/warehouse_flat_bridge/02-approach.png) · [支柱侧视](previews/warehouse_flat_bridge/03-supports.png) · [桥面平面](previews/warehouse_flat_bridge/04-deck-plan.png) · [带方块图例的平面放样图](previews/warehouse_flat_bridge/bridge-plan-with-legend.png)
- [材料和数量](../projects/warehouse_flat_bridge/materials.csv) · [逐块坐标和状态](../projects/warehouse_flat_bridge/building.json)

在仓库根目录用 PowerShell 重建样例。

~~~powershell
$instance = '<PCL 的 BMC4 版本目录>'
& .\.venv\Scripts\python.exe -B -m src build --instance $instance --sample warehouse_flat_bridge
~~~

