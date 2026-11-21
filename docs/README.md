# Minecraft AI Builder

这个项目为本机 Better MC 4（Minecraft 1.20.1、Forge 47.4.20）生成 Minecraft 方块建筑，并用 Blender 制作可旋转审阅的场景。随项目提供的样例是一座占地 16×12 方块的双层半木结构住宅，含带阁窗的山墙、完整屋顶、烟囱、双门、带护栏的室内楼梯、家具和地毯。计入台阶、出檐和烟囱，结构的整数坐标外包尺寸为 18×18×15（X×Y×Z）。

## M1 游戏内施工

施工计划直接读取项目的 `Building` 方块模型。样例是 30 块的玻璃窗墙，包含朝北的橡木楼梯和底部石砖台阶。独立测试实例在 `mc-test` 下，用 Minecraft 1.20.1、Forge 47.4.20 和 Fake Players 3.0.0 运行。启动脚本从现有 PCL 实例只读复制对应版本的运行库与原版资源。以下命令在仓库根目录执行。

~~~powershell
$instance = '<PCL 的 BMC4 版本目录>'
& .\tools\m1.ps1 Build
& .\tools\m1.ps1 Smoke -SourceInstance $instance
~~~

首次构建需要联网。前端脚本自动查找 Java 17 JDK，可用 `-JavaHome` 指定 JDK 目录。构建缓存就绪后可以用 `Build -Offline`。产物是 `forge/build/libs/mcaibuilder-bridge-0.1.0.jar`。如 PCL 实例在其他位置，给 `Smoke` 或 `Client` 传入 `-SourceInstance`，值为包含对应版本 JSON 和客户端 JAR 的 PCL 版本文件夹。

`Smoke` 启动一个新的超平坦世界，外部 Python 提交预生成计划，验证施工、暂停恢复、取消、材料扣减和不可达错误。客户端关闭后，截图、逐块结果和游戏日志位于脚本打印的 `mc-test/runs` 子目录。

要自己观看施工，在第一个 PowerShell 窗口运行以下命令。客户端会打开独立的 **M1 Construction Lab**，玩家出生时面向样例区域。保持世界运行。

~~~powershell
$instance = '<PCL 的 BMC4 版本目录>'
& .\tools\m1.ps1 Client -SourceInstance $instance
~~~

在第二个 PowerShell 窗口提交样例。返回的任务 ID 可用来控制施工。逐行执行下面的命令，暂停后可以先查看游戏，再恢复。暂停测试宜在提交后数秒内调用。

~~~powershell
$py = '.\.venv\Scripts\python.exe'
& $py -B -m src.construction status
$submitted = & $py -B -m src.construction submit .\projects\m1_wall\construction-plan.json | ConvertFrom-Json
$job = $submitted.job.job_id
& $py -B -m src.construction pause $job
& $py -B -m src.construction resume $job
& $py -B -m src.construction progress --output .\mc-test\forge-client\wall-result.json
~~~

若要取消运行中的任务，执行 `& $py -B -m src.construction cancel $job`。再次体验时可把同一模型导出到空闲位置，例如 `(18, -60, 6)`。每次导出生成新的计划 ID。

~~~powershell
& $py -B -m src.construction export .\projects\m1_wall\building.json `
  --building-id m1_wall --version 1 --origin 18 -60 6 --rotation 90 `
  --output .\mc-test\forge-client\next-wall.json
& $py -B -m src.construction submit .\mc-test\forge-client\next-wall.json
~~~

Bridge 在配置指定的世界启用，使用本机 `127.0.0.1` 和独立实例生成的访问令牌。配置同时限定 Overworld 施工范围及 NPC 出生点。用 `--config` 连接自定义测试实例时，把参数放在子命令前。详见[执行验证报告](m1-validation.md)与 [M2 任务规划](m2-plan.md)。

## 在 PowerShell 中运行

项目和虚拟环境位于 D 盘。样例使用 Python 3.13.2 的虚拟环境，源码使用 Python 标准库。渲染由 Blender 5.2.4 LTS 自带的 Python、`bpy`、`mathutils` 和 Cycles 完成。生成命令依次检测 OptiX 和 CUDA 设备，并以 CPU 作为后备。

~~~powershell
$instance = '<PCL 的 BMC4 版本目录>'
$python = '.\.venv\Scripts\python.exe'

& $python -B -m src build --instance $instance
~~~

已有结构数据时，可以单独渲染或复验。默认输出四张 1440×1200 PNG。用 `render --quality draft` 可以快速更新同一组预览，分辨率为 720×600。再跑一次默认渲染即可恢复交付分辨率。

新克隆先用 `generate --instance $instance` 生成本地材料索引和贴图。Blender 可安装到 `tools/blender`，也可用 `--blender` 指定已有的 `blender.exe`。

~~~powershell
& $python -B -m src generate --instance $instance
& $python -B -m src render
& $python -B -m src verify
& $python -B -m unittest discover -s tests -v
~~~

生成阶段从所给 BMC4 版本目录只读打开本地归档。缺少某种模组资源时，对应材料会换成原版材料，并在终端和材料索引中说明原因。显式运行 `build --instance $instance --vanilla` 可生成一套独立的原版样例，输出目录名为 `medieval_house_vanilla`，仍使用本机 BMC4 客户端 JAR 提供原版资源。

## 打开样例

用 Blender 打开本地生成的 `previews/medieval_house/medieval-house.blend`。场景默认以材质预览打开，直接显示嵌入的贴图。用鼠标中键拖动旋转，滚轮缩放。在顶部场景菜单切换整栋建筑、首层剖视和二层剖视。切换到「渲染」模式可查看场景灯光和阴影，工程已打开 Cycles 视口降噪，并在整栋建筑的两层室内加入柔和的审阅灯光。本机住宅的三个场景都设置为 GPU 计算，便携版的 Cycles 设备偏好已选择 RTX 5070 Ti（OptiX）。

- `projects/medieval_house/building.json` 保存每个块的坐标、注册 ID、状态和可选方块实体数据，采用 Minecraft 的 X 向东、Y 向上、Z 向南。
- `projects/medieval_house/materials.csv` 按注册 ID 列出数量、来源 JAR 和样例使用的状态数量。
- `projects/medieval_house/material-index.json` 保存源 JAR、方块状态文件、父模型链、贴图文件及对应该结构的 SHA256。新增生成器可通过 `src/assets/` 中的解析器补充材料。
- `previews/medieval_house/` 包含完整场景、正面和背面外观、首层和二层剖视以及 `verification.json`。复验会重新打开场景、检查建筑网格与数据哈希、确认贴图已嵌入，并实际解码四张 PNG。

屋顶、楼层、地基按部位合并为网格。楼梯、台阶、玻璃板、栅栏以及已选桌椅使用 JAR 模型的实际几何。剖视图用同一份方块数据按楼层及可见墙面取子集。Blender 坐标转换为 `(X, Y, Z) = (x, -z, y)`。

## 目录

~~~text
仓库根目录
├─ src\
│  ├─ core\        方块模型与结构 JSON
│  ├─ construction\  施工计划导出和本地 Bridge 客户端
│  ├─ generators\  住宅生成器
│  ├─ renderers\   Blender 场景、渲染和复验
│  ├─ assets\      JAR 检查、方块状态、模型和纹理解析
│  └─ exporters\   后续蓝图导出接口
├─ projects\       建筑数据、材料清单及索引
├─ previews\       Blender 场景和 PNG
├─ assets\         从本地 JAR 提取的贴图
├─ forge\          Forge Bridge 源码和构建产物
├─ mc-test\        独立游戏实例、运行依赖与验收日志
├─ tests\          结构与资源解析测试
├─ tools\          Blender Portable 及安装记录
├─ .venv\          项目 Python 虚拟环境
├─ .cache\         下载清单、pip 缓存位置和临时工作目录
└─ docs\           使用说明
~~~

便携安装的 `portable` 目录位于 `blender.exe` 同级。后台生成时临时文件放入 `.cache/tmp`。如需重建虚拟环境，在项目根目录设置 `$env:PIP_CACHE_DIR = "$PWD\.cache\pip"`、`$env:TEMP = "$PWD\.cache\tmp"`、`$env:TMP = $env:TEMP`，再运行本机 Python 的 `-m venv .venv`。安装路径、构建号、下载地址和实际校验值记录在 `tools/blender-install.json`。官方 ZIP 已校验并解压，安装压缩包已清理。[官方发布](https://www.blender.org/releases/5-2/) · [官方 SHA256 清单](https://download.blender.org/release/Blender5.2/blender-5.2.4.sha256)

## BMC4 资源审阅

通过 PCL2 最近一次启动命令中的 `--gameDir`、BMC4 启动清单、客户端 JAR 内的 `version.json` 和本机 Forge 库文件确认实例。模组在 `BMC4\mods`，实例资源包在 `BMC4\resourcepacks`，共享资源位于 `.minecraft\assets`（索引 `5`）。下表的数量来自实际 JAR 中的 blockstate 文件。样例的每个模组方块还核对了同名方块翻译、物品模型或掉落表中的证据，并解析了实际使用的状态、父模型及贴图。

| 模组 | 本机 JAR 内的 blockstate 数量 | 样例用料 |
|---|---:|---|
| Chipped | 6,981 | `stacked_spruce_planks` |
| Handcrafted | 267 | `spruce_chair` |
| Quark | 809 | `red_shingles`、`red_shingles_stairs`、`red_shingles_slab`、`dark_oak_bookshelf` |
| Supplementaries | 231 | `daub`、`daub_cross_brace`、`daub_frame` |
| Twigs | 256 | `polished_tuff_bricks`、`spruce_table` |
| Decorative Blocks | 56 | `dark_oak_beam` |

审阅范围为安装归档中的静态资源。运行时 Forge 注册表、资源包覆盖、动态连纹、方块实体渲染、动画、游戏着色和邻接更新仍待验证。加权模型采用第一个变体。Chipped 的 `brick_bordered_cobblestone` 使用 Athena CTM，Supplementaries 的 `timber_frame[has_block=true]` 使用专用加载器，原版床依赖运行时方块实体渲染。这些实例已记录在索引的 `excluded_examples` 中。

## 下一阶段的蓝图文件

让导出器读取 `Building`，先做 Java 1.20.1 结构 `.nbt` 的离线往返测试，再按预定导入工具支持的版本实现 Sponge `.schem`。从 `DataVersion=3465` 和方块 ID 加完整状态构建调色板，统一处理负局部坐标到蓝图原点的偏移、空气策略以及方块实体的 NBT 类型。用离线读回结果核对每个位置、状态和方块实体，在目标实例验证注册 ID 后再进行实际导入。[Sponge Schematic 规格](https://github.com/SpongePowered/Schematic-Specification)


## 附魔书馆

这座建筑参考了视频中的石边地台、木柱入口和中央附魔区。施工画面在 0:15 展示方形地台，1:25 展示木构框架，4:25 展示屋顶书页，6:20 展示室内。开卷屋顶采用蓝紫林地配色。白石英作书页，蓝花楹木作页纹，雾蓝瓦作书边，紫珀作书脊。[视频《建筑教程：附魔台》](https://www.bilibili.com/video/BV1LKdGBAEpd)

主体占地 16×16，含石边地台占地 22×22。白桦柱配去皮蓝花楹木墙面，蓝灰魔法原木作横梁。四面各有双门、淡蓝与紫色玻璃窗，门楣嵌紫水晶。浅蓝灰檐口与靛蓝屋面衔接，樱木窗台和垂下的书签点出樱色。地台草边用紫、白薰衣草和落樱衔接周围的林地，花带沿四条入口石路分布。中央附魔台周围有 15 块原版书架及过道，两侧设有阅读台、座椅和蓝花楹盆栽。预览用本机 BMC4 的静态模型和纹理绘制。附魔台使用 JAR 中的台座几何，悬浮书属于游戏内的动态方块实体渲染。

~~~powershell
$instance = '<PCL 的 BMC4 版本目录>'
& .\.venv\Scripts\python.exe -B -m src build --instance $instance --sample enchanting_library
~~~

`projects/enchanting_library/` 保存建筑数据和材料清单。`previews/enchanting_library/` 保存 `enchanting-library.blend`、四张 1440×1200 PNG 和复验记录。打开场景后可以步行进入馆内。在右侧「场景」属性的相机字段选择 `Inside the library`，再按数字小键盘 0，可直接查看室内相机。


## 教堂仓库引桥

[三孔石拱桥的尺寸、搭建说明和模型](warehouse-bridge.md)。

[单层平桥的尺寸、支柱和搭建顺序](warehouse-flat-bridge.md)。
