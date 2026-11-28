# 潮痕工坊 / Tidemark Workshop

面向公众的临水造船工坊。较高、较宽的一跨覆盖船台并伸出船门，木结构支撑屋檐；双轨沿船台、船门与逐级下沉的下水作业带贯通至水线。低矮的公众展廊与栈台位于作业带一侧，新增的室内观察窗朝向工作厅。铜屋面、石灰石、深色岸基与云杉构件沿用原方案；未完工船体以龙骨、横向构件、舷侧和支座表达，不再安装帆与桅杆。

[临水整体](images/waterfront.png) · [反向整体](images/waterfront-opposite.png) · [水侧立面](images/quayside.png) · [下水作业带](images/launch.png) · [入口](images/entry.png) · [展廊观察窗](images/gallery-window.png) · [工作厅](images/hall.png) · [船体](images/boat-frame.png)

## 设计判断

基线 `77a48a4` 的船门与短作业带终止于场地边缘，三片屋面几乎等同；本轮重组为两片低工作跨和高船台跨，以外挑作业棚和真实水方块构成连续的下水方向。岸基、坡道、水深和观景栈台共同组织这一关系。船体改为无帆的未完成状态，其构件尺度和水线对齐关系服务于工作厅，而非仅添加船形道具。

独立 Critic 认为新版的船门、坡道和岸壁建立了更明确的临水船坞身份，但初版坡道仍像大楼梯、展廊看不清船体。随后增加贯穿室内至水线的双轨、观察窗和灯笼吊链。Critic 复审发现开门时删除了轨道两处各两格；清洞后补回，并由 Critic 独立逐格复核两列轨道、四处上坡与承托。参见 [基线与新版同机位](images/redesign-approach-comparison.png)、[大厅同机位](images/redesign-hall-comparison.png)、[审阅前后滑道](images/redesign-rails-comparison.png) 和 `redesign-diff.json`。基线对比共用新版较低的水岸地面，因此旧版在比较图中呈悬空；那不是原来展示用的地面。

仍有明显局限：外观保留较强的常规工业厂房特征；小尺度船体的首尾与构件仍偏方块化；展廊观察窗前景较暗且船体被窗格和柱梁部分遮挡。轨道只是建筑表达，没有设计可运船的车架或机械装置，不能据此宣称船舶能够下水。水域是有边界的展示场景，不是完整自然港湾。

## 验证与文件

- `design.py`、`building.json`、`materials.csv`、`design-groups.json` 与 `views.json` 为当前设计；`versions/before-waterfront-redesign/` 冻结了基线，`versions/waterfront-critic/` 保存独立审阅前稿。旧的 `critic-diff.json` 和 `images/approach-comparison.png` 是上一轮的历史记录。
- `walk-public.json`：入口至主厅；`walk-deck.json`：入口至公众栈台；`walk-slipway.json`：船台旁经轨道外侧至水边。另有 `walk-launch.json` 对轨道中间的坡道逐级核查。步行检查不模拟船只移动；分析器把铁轨视为障碍，跨轨碰撞以游戏为准。
- 用 `materials --state` 检查铁轨平直及北向上坡状态、活板门和链条，结果在 `state-study.json`。蓝图检查发现并修复了门口断轨；15 盏悬挂灯均有向上的链条或承托方块。未测试游戏中铁轨邻接更新、放置支持或灯光。
- 蓝图包含实际 `minecraft:water` 方块。现有静态模型解析器不能渲染水，`generate --approximate` 默认替换为橡木板；本项目 `water_preview.py` 使用已安装游戏的水纹理首帧和固定蓝色，为 Blender 制作静态方块近似，并在索引标记替代。这不是流体模拟，也不代表实际生物群系水色。场景边缘的水未封闭，放入实际世界前须根据地形处理流动；未做实机流体验证。
- 重新生成和渲染（Python 需 Pillow，设置 `MCBUILDER_INSTANCE` 为本机 BMC4 实例目录）：

```powershell
& $py -B -m src generate --sample tidal_workshop --approximate
& $py projects/tidal_workshop/water_preview.py
& $py -B -m src render --sample tidal_workshop --preset final --view waterfront --view quayside --view gallery-window --view boat-frame
& $py -B -m src verify --sample tidal_workshop --preset final
```

最终本机工程为 `previews/tidal_workshop/final/tidal_workshop.blend`（仓库依规则忽略 `.blend`）。重新打开验证了蓝图、2 个场景、21 个机位、19 张打包贴图及 9 张可解码成图。没有进入 Minecraft，不能宣称通过游戏实机验收。
