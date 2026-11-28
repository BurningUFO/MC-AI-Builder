# 潮痕工坊 / Tidemark Workshop

一座开放给公众参观的小型造船工坊。三跨氧化铜锯齿屋面为通高工作厅引入高侧光，低矮的人行入口和展廊引导访客进入；另一侧的高船门和木质作业带与室内船台对位。灰色石灰石砌体、深色基座、云杉梁和铜屋面形成材料层次。场地仅为示意性铺装，作业带止于边界，未设计真实水岸或水面。

## 设计与审阅

初稿的屋檐出现突出的木块，入口后隔墙阻断视线，先去掉突块、打开隔墙及采光屋面。独立 Critic 随后认为建筑像通用锯齿屋厂房，建议优先建立面向前庭的大船门、与船台对位的作业带，并改善暗入口空间。修改后石柱与木梁框出大门，拓宽展廊与大厅的通口，增加顶窗与实际灯笼；独立复审确认主次和空间关系改善，但认为船体仍像箱体，三跨屋面仍显常规。之后加了尖端、桅杆和部分帆，船体仍偏方块化，不能仅凭这些渲染称作高品质杰作。展示翼前景偏暗；外墙窗跨重复；未设计可见水面和室外场景，都是明确局限。

[整体](images/approach.png) · [船门](images/boat-door.png) · [大厅](images/hall.png) · [展廊](images/gallery.png) · [船体](images/boat.png) · [屋面](images/roof.png) · [整体对比](images/approach-comparison.png) · [室内对比](images/hall-comparison.png)

## 交付与验证

- `design.py` 为独立原创源码，`building.json` 为 1.20.1 蓝图，`materials.csv` 为材料清单；`views.json` 和 `design-groups.json` 保留机位及部件。`versions/` 保存体量初稿和独立审阅基线。
- `materials --state` 查询了深色橡木活板门、Quark 磨制石灰石楼梯和玻璃板的真实模型状态；材料索引标明灯笼预览为静态近似，其余本设计材料为静态资源核验。方块状态没有在实际游戏中逐一实测。
- `walk-public.json`：前庭 → 入口 → 展廊 → 主厅路线通过；`walk-slipway.json`：作业带 → 船门 → 船台旁路线通过。步行分析不等于完整游戏碰撞实测。
- 最终 Blender 工程在本机 `previews/tidal_workshop/final/tidal_workshop.blend`（仓库依约忽略 `.blend`）。final 输出含八张 1440×1200 图；`verify` 重开确认 4568 方块、2 场景、10 机位、16 张打包贴图及八张可解码 PNG。
- `critic-diff.json` 和两张同机位对比图记录审阅前后的变化。最终仅做 Blender 和蓝图检查，未进入 Minecraft 游戏，未验证实机放置、照明、连接与碰撞。
