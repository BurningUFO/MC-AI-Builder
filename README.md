# MC-AI-Builder

Minecraft 社区项目。此项目非 Minecraft 官方产品，与 Mojang 或 Microsoft 无关联，亦未获其批准。

MC Builder 是 Minecraft Java 1.20.1 Forge 的建筑伙伴。它能根据附近地形提出体素方案，跟玩家商量改图和材料，并由 Fake Players NPC 在游戏里施工。游戏内可以直接查看全息虚影。Blender 也可在后台生成工程和多角度预览。

## 独立 Blender 建筑设计

在工程目录编写自由 Python 设计，然后用同一套命令检索 BMC4 材料、生成蓝图、选择机位、审阅剖面和交付 Blender 工程。 [操作指南](docs/blender-workflow.md) · [建筑与版本归档](docs/previews/README.md) · [Architecture Intelligence](docs/architecture-intelligence.md)

## 在游戏里使用

1. 在真实世界副本里用 `/architect site area 30x30` 或 `@Builder 以我为中心 30x30` 标记工作区，再点聊天里的「批准施工区」。`/architect site look 30x30` 以视线为中心。两个命令都支持在尺寸后指定高度。
2. 说 `@Builder 帮我在眼前这块地盖一个带烟囱的小木屋，用深色橡木风格`。Builder 会给出方案和耗材。可以继续讨论改图，也可以回复「建吧」「可以」「批准」或点击批准。
3. 把材料放进附近的箱子、大箱子或木桶，再说「材料齐了」，也可使用 `/architect supply`。Builder 会盘点施工区及共享位置周围 16 格，按需搬运。缺料时会列出缺口，补齐后告诉它即可继续。
4. Builder 可以在批准区域内清理普通地形、回收掉落物和拆除自己的临时垫脚块。容器、红石、床、传送门和用 `/architect site protect` 标记的位置受保护。施工中可用 `/architect pause`、`/architect resume` 或 `/architect cancel`。

## 工作区建筑项目

用 `@Builder 你有哪些现成的建筑？` 查看项目库。可以直接说 `@Builder 帮我建一下 medieval_house`、`@Builder 复现附魔图书馆` 或 `@Builder 把仓库拱桥朝东盖在这`。Builder 会读取工程原有的方块和状态，在批准施工区内投影并列出材料。可以穿过虚影检查布局，再回复「建吧」。已安装的模组材料按 Forge 注册表核对，供料支持 Quark 箱子。

新设计会归档到 `projects/<工程名>/building.json`，以后可直接报工程名复现。启动器配置了 Blender 时，后台会写入 `previews/<工程名>/`，并把打开图片和 .blend 的链接发给审阅者。缺少 Blender 时仍可在游戏内审阅和施工。动态模组模型缺少可用贴图时，材质索引会记录预览使用的原版替代素材，工程蓝图保留原方块。

## Windows 双击启动

双击 **Start MC AI Builder.cmd**，等隔离 Minecraft 练习世界与 Agent 显示已连接。用 `@Builder` 发送需求，或按 **Alt+B** 开启对话模式。建筑预览和审批会在游戏聊天中显示。 [三步体验指南](docs/m5-a-guide.md) · [验收记录](docs/m5-a-validation.md)

在启动器点 **选择世界** 可复制现有 BMC4 单人存档。进入 D 盘副本后，可以在游戏里用区域命令同时选出施工范围、出发点和建筑原点。 [真实世界副本指南](docs/m5-b-guide.md) · [真实地形验收记录](docs/m5-b-validation.md)

M6 支持 NPC 第一人称主动观察及施工复查。使用 `/architect survey` 根据地形设计，或用 `/architect supervise` 在半程、异常和完工时复查。模型发出的修改候选仍需在游戏中批准。 [M6 游戏内体验指南](docs/m6-guide.md) · [施工闭环验收](docs/m6-d-validation.md)

M7-A 支持带入口、窗组、屋顶和简单家具的三维单层住宅。使用 `/architect survey` 把现场地形和 NPC 视野纳入布局选择，并在 Blender 审阅外观与室内剖视。 [住宅体验指南](docs/m7-a-guide.md) · [验收记录](docs/m7-a-validation.md)

M7-P 改进住宅的连续施工和分批供料。启动器可选开启并行规划，让 Codex 在 NPC 施工期间观察现场并调整下一个已批准的屋顶工作组。 [性能测量](docs/m7-p-profiling.md) · [对照验收](docs/m7-p-validation.md)

M7-Q 可在已签署自主许可的 BMC4 副本工作区中批量清除普通植被、整理表层土石，并按许可处理确认的天然小树。玩家可预先标记保护区，Builder 将现场出现的重要资产汇总后询问。 [工作区使用与验证](docs/m7-q-validation.md)

M10 在获准的 BMC4 副本中使用 `/architect design <需求>`。Codex 根据 Forge 标记周围的真实高度、支撑和障碍设计建筑、永久基础与开挖范围，并审阅 Blender 正面、背面和适用的室内剖面。收到最终预览后可用 `@Builder` 指定局部修改，再用当前版本的按钮批准。Task Brain 管理批准计划中的场地处理、供料和施工。清障完成后也可用 `/architect design` 提议更换原目标格的材料或状态，经重新审阅后从已有施工进度继续。 [M10 实机验收记录](docs/m10-evidence.json)

施工 NPC 可由授权玩家用 `/architect observe` 观察第一人称视角。图像与姿态元数据保存到本地缓存，并与同一观测 ID 的局部 Forge 样本配对。 [NPC 观测说明](docs/m6-a-validation.md) · [视觉与世界状态](docs/m6-b-validation.md)

![M2 游戏内完工截图](docs/m2-evidence/wall-v2.png)

## 快速验证

在 Windows 的仓库根目录运行。准备 Python 3.13、Java 17 JDK 和已经安装的 PCL Forge 1.20.1 实例。将 `$instance` 设为该实例的版本目录。

~~~powershell
py -3.13 -m venv .venv
$instance = '<PCL 的 BMC4 版本目录>'
$java = '<Java 17 JDK 目录>'
& .\tools\m1.ps1 Build -JavaHome $java
& .\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py'
& .\.venv\Scripts\python.exe -m tests.companion_smoke --java-home $java --source-instance $instance
& .\.venv\Scripts\python.exe -m tests.m4_bmc4_smoke --java-home $java --source-instance $instance
~~~

Forge 实测会在 mc-test 创建隔离世界。BMC4 项目库烟测从 PCL 只读提取建筑模组及其依赖，验证两份经典工程的虚影、真实 Blender 归档和 Quark 箱子取料施工。用 `--bmc4-pack` 可以改用已有的隔离整合包目录。首次构建需要 Gradle、Forge 开发依赖和 Fake Players 3.0.0。

早期里程碑的 [运行说明和历史验证记录](docs/README.md) 保留了各版本当时使用的命令和验收方式。

## 游戏内建筑师的早期版本

M3 增加 `/architect`、独立 Python Runtime、参数化窗墙与游戏内审批。确定性后端可复现从设计到增量施工的流程。真实模型可用标准 HTTP 接口，或由官方 Codex SDK 复用本机 ChatGPT 登录。 [启动与体验](docs/m3-guide.md) · [验收与证据](docs/m3-validation.md) · [M4 规划](docs/m4-plan.md)

## 生存施工的早期版本

M4 允许玩家批准实际供料箱，NPC 分批搬运并使用箱内物品施工。缺料、工具损坏和检查点冲突会暂停任务。BMC4 支持经过实测的 Chipped 与 Quark 建筑方块。 [启动与演示](docs/m4-guide.md) · [验收记录](docs/m4-validation.md)
