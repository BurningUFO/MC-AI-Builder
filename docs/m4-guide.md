# M4 仓库供料与生存施工

## 打开隔离生存世界

在仓库根目录使用已有的 Java 17、Python 虚拟环境和 PCL 资源。已下载构建依赖时可离线构建。`$javaHome` 是本机 Java 17 JDK 目录，`$instance` 是 PCL 的 Forge 1.20.1 版本目录。测试启动器从该实例读取资源，游戏数据写入项目的 `mc-test`。

~~~powershell
& .\tools\m1.ps1 Build -Offline
$py = '.\.venv\Scripts\python.exe'
$game = '.\mc-test\survival-client'
& $py -B -m tools.m1_client --java-home $javaHome --source-instance $instance --game $game --port 18775 --survival
~~~

要在 BMC4 中体验，将 `$game` 指向另一个新的隔离目录，再追加 `--bmc4-pack $isolatedPack`。`$isolatedPack` 指向以前创建的带测试标记的 BMC4 隔离实例。启动器复用该实例的模组和默认配置，独立创建游戏与存档。

在另一个 PowerShell 中启动 Architect Runtime。可在同一会话重用 `$py`、`$game` 和 `$instance` 的值。

~~~powershell
& $py -m pip install -r .\requirements-codex.txt
$config = Join-Path $game 'config\mcaibuilder-architect.json'
$budget = '.\.cache\m4\architect-turns.sqlite3'
& $py -B -m src.architect --config $config --instance $instance --provider codex --timeout 50 `
  --codex-model gpt-6.2-sol --codex-effort medium --codex-max-turns 10 `
  --codex-max-tokens 150000 --codex-budget-log $budget
~~~

Codex 用本机已有 ChatGPT 登录。同一路径的预算账本跨 Runtime 重启累加。模型只接收建筑需求及受限场地、设计和供料模式摘要。标准 HTTP 提供方及确定性后端也可用于生存施工。 [M3 模型配置](m3-guide.md)

## 批准箱子与施工

1. 在批准范围内放下一个原版单箱，放入你自己的方块和工具。为箱盖和 NPC 的通行路线留出空间。全新的平坦隔离世界从空背包开始，可用下方的验收入口为测试玩家准备首批物资。
2. 在同一维度站到箱旁，在游戏中输入 `/architect supply x y z`，用箱子的实际坐标替换 `x y z`。Forge 校验玩家身份、批准区域、视线和触及距离，随后记录这份供料授权。
3. 输入建筑需求。例如 `/architect 请在 6 -60 6 朝北建一面 8 格宽 3 格高的中世纪窗墙，用云杉、石砖和透明玻璃。只用获准仓库的材料。`
4. 核对聊天中的材料及缺口，打开 Blender 预览。点击该次审阅的 **批准** 后，任务才会取料施工。审批引用候选版本、箱子授权和当时的库存摘要。
5. 缺料、工具耗尽、箱内库存变化或路径不可用时，从聊天查看原因。核对并向同一箱子补货，再逐次批准继续。`/architect materials` 可以直接查询，`/architect 还缺多少材料？` 则由模型识别查询意图，当前数字来自游戏回读。
6. 施工中可说 `/architect 先暂停施工，把窗户换成蓝色染色玻璃。材料不够告诉我。`。在装有对应模组的隔离实例中，可以说 `/architect 先暂停施工，把屋顶改用 Quark 的红色瓦片，其他部分保持不变。` 每次改图都再次审阅差量与物资需求。
7. 用游戏菜单正常保存退出。重开同一实例后，确认箱子、施工方块与背包的检查点，再用聊天的继续按钮执行。需要 Runtime 的自然语言指令可在重连后继续使用。

Forge 取料先走 `ServerPlayerGameMode.useItemOn`，核对真实箱子交互和 `PlayerContainerEvent.Open`。实际搬运在游戏主线程通过 Forge 的 `InvWrapper.extractItem` 和 Fake Players 的 `SimpleContainer.addItem` 完成。回执逐笔记录箱子扣除与背包收到的数量，并限制每趟用量和空间。游戏没有为 NPC 打开客户端 GUI。放置和拆除仍使用原生游戏物品与工具交互。

旧材料保留在 NPC 背包。拆除的实际掉落会留在世界中，玩家可捡起并补入供料箱。审阅按注册表和掉落表估算潜在回收，材料缺口只依赖已核对的现有库存。工具耐久以当前铁镐或铁斧的剩余使用次数计算。BMC4 当前限于白名单里的单格方块，每份建筑计划最多 512 格。双箱、陷阱箱、模组仓库、带数据的建筑物品、附魔工具及多格家具明确受限。

正常重开时会核对两个库存和未完成步骤。遇到突然退出造成的箱子、背包或落地方块检查点冲突，任务停在检查点，等待管理员检查现场。进行中的任务限定为已批准的同一箱子。玩家在任务结束后可批准另一个箱子供下次使用。

## 兼容范围

当前支持清单和完整状态见 [M4 验收](m4-validation.md)。后续可逐项增加 Decorative Blocks 梁柱、Supplementaries 特殊状态和 Handcrafted 家具。每类都需要核对注册物品、放置姿态、邻接变化、原生拆除与重开。多格方块和动态方块实体还需要确认它们的占位、数据和掉落规则。

## 游戏验收入口

~~~powershell
& $py -B -m tests.m4_supply_smoke --java-home $javaHome --source-instance $instance
& $py -B -m tests.m4_bmc4_smoke --java-home $javaHome --source-instance $instance --bmc4-pack $isolatedPack --ledger .\.cache\m4\bmc4-test-turns.sqlite3
~~~

第一项通过隔离夹具给测试玩家提供起始测试物资，再经玩家原生箱子菜单放入供料箱。NPC 的全部物资由仓库转移取得。第二项先用确定性后端验证白名单，再用真实 Codex 请求建模和中途改图。两张真实模型审阅需要玩家逐份批准。测试的运行日志、回执、预览与存档保存在各自的 `mc-test` 隔离目录。 [验收记录](m4-validation.md)
