# M1 施工执行验证

## Fake Players 3.0.0

检查对象为 Modrinth 发布的 `players-forge-1.20.1-3.0.0.jar`，版本 ID `IoNTsmcR`，SHA-256 为 `5ae6b53d57d648fadc6851e5bba4d729fc64c36040727ff5dcc0632a66482104`。下载后核对了官方 API 的 SHA-512。使用 Vineflower 1.11.2 反编译该 JAR，并用 Forge 的 SRG 映射核对 Minecraft 方法。证据文件在 `.cache/m1/fakeplayers-decompiled`。[Modrinth 发布元数据](https://api.modrinth.com/v2/version/IoNTsmcR) · [Forge 1.20.1 发布页](https://files.minecraftforge.net/net/minecraftforge/forge/index_1.20.1.html)

| 问题 | 结论和证据 |
|---|---|
| Forge 1.20.1 加载 | **JAR 确认** `META-INF/mods.toml` 的模组 ID 为 `players`，版本为 3.0.0，Forge 要求 `[47.2.30,)`，Minecraft 要求 `[1.20.1,1.20.2)`。**运行验证** 在 Forge 47.4.20 客户端及其单机服务端成功加载并生成 `players:fake_player`。 |
| Builder 格式 | **JAR 确认** `SchematicStore.load/read` 读取 gzip 压缩的 Minecraft 结构 NBT。可从 `schematics/`、上传目录、`config/players/schematics/` 或数据包结构 ID 找到它。**官方文档**提到 Create 蓝图。所查版本的读取入口没有 Sponge `.schem` 或 Litematica `.litematic` 解析器。[项目文档](https://github.com/duzos/fakeplayer) |
| Builder 实际写入 | **JAR 确认** `BuilderJobExecutor.place` 调用 `Level.m_7731_(pos, state, 3)`，SRG 对应 `Level.setBlock(pos, state, flags)`。摆臂、显示手持物、放置音效和游戏事件是额外步骤。 |
| 库存和工具 | **JAR 确认** `BuilderJobExecutor` 使用 `FakePlayerInventory`，检查材料并在写入成功后扣除。库存支持补货与材料请求。`BlockBreaker` 使用工具相关的破坏速度和耐久计算。 |
| 寻路和任务接口 | **JAR 确认** `FakePlayerEntity` 是带地面导航的 `PathfinderMob`，提供 `getInventory`，`JobHelpers` 包含候选站位与导航帮助方法。`FPJobs.register` 可注册 `JobType` 及其 `JobExecutor`。 |
| 外部调用 | **JAR 确认** 有游戏内命令、客户端到服务端的配置包及 Java 扩展入口。此次检查未发现可供 Python 调用的 HTTP 或 IPC 接口。 |
| 暂停和更新 | **JAR 确认** `StartStopJobPacketC2S` 可更新运行标志，`BuilderJobExecutor.onPause` 清理导航和工作占用。拥有者可通过 `SetBuildPacketC2S` 修改蓝图参数。**尚未验证** 原生 Builder 中途改变目标的实际效果及状态迁移。 |
| 楼梯、台阶、门 | **JAR 确认** `BuildPlan` 从结构调色板读取状态并旋转，`MaterialRules` 和伙伴块逻辑处理双台阶成本及门、床的对应结构块。直接写入路径也会调整水含量等状态，实际邻接结果取决于环境。**尚未验证** 原生 Builder 的状态还原准确度。 |
| 模组自定义方块 | **JAR 确认** 解析使用游戏注册表，材料规则取方块的 `asItem`，并筛查液体、无物品、不可破坏等块。原生放置函数直接使用方块状态。**尚未验证** BMC4 各模组的具体方块与方块实体行为。 |
| BMC4 兼容性 | **JAR 确认** 内置 Common Networking 1.0.5，元数据列出 Aquaculture、Tide 和 TACZ 的可选关系。**运行验证** 本机 387 个启用 JAR 的隔离副本加载了 Fake Players 与 Bridge，完成 30 块原版墙体。**尚未验证** 模组方块、联机保护和正式存档迁移。 |

## M1 实现

施工样例和常规项目共用 `Building`。Python 按层导出目标坐标、方块 ID、状态和物品 ID，并记录建筑版本、原点、局部边界、朝向及可选方块实体字段。服务端根据原点和朝向变换坐标，用 Minecraft 注册表补齐默认状态、旋转状态并计算世界边界。M1 使用原版单块放置，适用范围见 `BuildPlan.palette()`。[样例模型](../projects/m1_wall/building.json) · [施工计划](../projects/m1_wall/construction-plan.json)

Bridge 保留真实的 `players:fake_player` 实体、库存和地面导航。NPC 用 Fake Players 的 `PossessionReconciler` 初始化库存状态，并在站位搜索中使用 `JobHelpers.canStandAt`。每次放置从该实体的位置检查实际视线、点击面、距离和目标状态。Forge 的 `FakePlayer` 以同一 UUID 和选定物品栈提供生存模式的 `ServerPlayer` 上下文，调用 `ServerPlayerGameMode.useItemOn`。真实物品交互的结果返回给可见 NPC 的库存。事件监听检查 `RightClickBlock` 和 `EntityPlaceEvent`，同时取消超出单个目标或状态不符的放置。

Bridge 按服务端 tick 取任务，只允许配置批准的 Overworld 测试区域。一项任务包含至多 512 个方块。目标必须为空或已匹配，已有其他状态时任务被拒绝。作业结束时重新读取目标并对比区域内未计划坐标以及区域外一格邻域。读回是终态快照，覆盖日志列出的坐标范围。预置材料来自施工所需的精确清单。

| HTTP | 操作 |
|---|---|
| `GET /status` | NPC 类型、位置、库存、活动计划和简要进度 |
| `POST /build` | 提交施工计划 JSON，计划 ID 用于同任务重试 |
| `POST /pause` | 通过 `job_id` 暂停当前工作并停止导航 |
| `POST /resume` | 通过 `job_id` 恢复已暂停的工作 |
| `POST /cancel` | 通过 `job_id` 结束未完成任务并回读世界 |
| `GET /progress` | 逐块动作、失败原因、事件和材料证据、终态验收结果 |

请求发送 `Authorization: Bearer <token>`。令牌、批准区域和世界名位于各独立实例的 `config/mcaibuilder.json`，HTTP 仅监听 `127.0.0.1`。操作在 Minecraft 服务端线程执行，任务和控制 ID 在会话内有效。原版门、方块实体、水含量及其他多块放置留待后续里程碑。

## 游戏内验收

2026 年 11 月 21 日在 Windows 的独立超平坦世界 **M1 Construction Lab** 运行生产映射后的 Bridge JAR。客户端使用 Minecraft 1.20.1、Forge 47.4.20、Fake Players 3.0.0 和 Java 17.0.8。测试创建时关闭随机刻和刷怪，使非目标位置的比较可复现。`Smoke` 运行后会在本地 `mc-test/runs` 保存世界和完整游戏日志。[验收摘要](m1-evidence/acceptance.json)

| 检查 | 实际结果 |
|---|---|
| 生成与移动 | 生成了 `players:fake_player`。同一 NPC 完成窗口墙，在记录期间行走约 19.56 格，并利用已建的一格高站位完成台阶屋檐。 |
| 独立交互 | 30 个动作均在不同游戏 tick 执行。每块触发一次 Forge 右键和一次放置事件，并消耗对应物品 1 个。初始库存 30 个，完工库存为空。 |
| 方块验收 | 30/30 块的 ID、坐标和完整状态吻合。样例含朝北底部楼梯和底部石砖台阶。旋转 90° 的第二个任务实测楼梯朝东。 |
| 施工边界 | 比较 12,255 个非目标位置，其中 3,210 个位于批准区域外的一格邻域。两组均为零变化。越界计划和占用目标被拒绝。 |
| 暂停和恢复 | 3 块放置后暂停，经过 33 个服务端 tick 数量保持 3 块。恢复后完成全部 30 块。 |
| 取消和重放 | 第二个任务在 4 块后取消。再经过 30 个 tick 仍为 4 块，已取消的计划 ID 无法通过恢复或重放继续施工。 |
| 不可达 | 塔柱前 3 块放置成功，第四块 `(22, -57, 9)` 返回 `no_reachable_placement_stance`，该任务结束。 |
| 构建和单测 | Gradle 8.8 和 ForgeGradle 6.0.54 构建通过。Python 单元测试 12 个通过。 |

[逐块结果](m1-evidence/completed.json) · [暂停](m1-evidence/pause.json) · [旋转和取消](m1-evidence/cancelled.json) · [不可达](m1-evidence/unreachable.json) · [游戏截图](m1-evidence/m1-wall-built.png)

本次验收使用的 Bridge JAR 的 SHA-256 为 `f287b3a9be7a38259dfb99770f426e6c7d4ff5375905e69cb772f704c9f01447`。用 `tools/m1.ps1 Build` 可生成 `forge/build/libs/mcaibuilder-bridge-0.1.0.jar`。

## BMC4 隔离复测

通过 PCL2 最近一次启动命令的 `--gameDir` 确认 BMC4 的实际版本目录。兼容性测试只读复制了该目录下启用的 387 个模组 JAR 和 `defaultconfigs`，在 `mc-test/bmc4-compat` 中加入 Fake Players 和 Bridge，并生成新的超平坦存档。模组客户端使用隔离目录内生成的配置。

首轮在 210 秒内未进入内置服务端。复测把等待上限提高到 360 秒，并在副本中关闭 BCLib 更新提示、实验性世界提示和 Forge 版本检查。服务端进入游戏后，NPC 用同一份计划完成 30 次物品交互，30 块的 ID、坐标和状态全部匹配。NPC 行走约 19.71 格，30 次各消耗 1 个物品。12,255 个非目标位置及其中批准区域外的 3,210 个位置均无变化。[复测摘要](m1-evidence/bmc4-compatibility.json) · [逐块结果](m1-evidence/bmc4-wall-result.json)

复测在游戏内回读通过后触发客户端退出。退出超过 25 秒等待上限，测试脚本结束了进程。这次 BMC4 的结果以退出前取得的服务端回读为准，存档退出和重开仍待验证。原版最小测试世界正常保存和退出。

M2 在 BMC4 独立副本重新建造同一面 M1 窗墙，并验证正常保存与退出。重开后的 30 个坐标、方块 ID 和状态一致，全部跳过施工。 [M2 的 BMC4 保存与重开证据](m2-validation.md#bmc4-隔离复测)
