# M5-A Windows 启动与私聊验收

验收环境为 Windows、Minecraft Java 1.20.1、Forge 47.4.20 和 Fake Players 3.0.0。使用项目的 D 盘隔离练习目录。 [三步体验指南](m5-a-guide.md)

## Windows 启动与故障处理

| 实测场景 | 结果 |
| --- | --- |
| Windows 双击入口 | Windows ShellExecute 打开项目的 CMD。启动器自动找到 Java 17、现有 `.venv`、Blender、Forge 运行资源和通过 M4 验收的 BMC4 副本。缺少 Bridge JAR 时进行了离线构建。产物齐全后重开复用同一个 JAR。 |
| 重复启动与端口冲突 | 重复启动保留原 Minecraft 和 Runtime 进程。端口占用时先提示，游戏和 Runtime 均未启动。端口释放后可在原窗口启动。 |
| 私聊与断线 | 通过 Minecraft 窗口的 Alt+B 原生按键路径切换。停止 Runtime 后 Minecraft 保持运行，离线 `@Builder` 收到私有状态提醒，模型账本与世界均无新增动作。 |
| 重连与保存 | Agent 重连复用运行中的 Minecraft。模拟该隔离游戏的异常退出后，启动器释放 Runtime，重新打开同一个世界。正常退出时 Minecraft 日志确认所有维度保存，两个子进程退出码为 0。 |

此前发现 Bridge 在客户端接到世界之前就已启动。启动器现在等授权玩家进入及新 Agent 心跳。接入期的退出请求会等客户端接到世界后再走 Minecraft 保存流程。启动器只读取专用健康接口，施工回读仍由原有接口负责。 [最小 Forge 验收证据](m5-a-evidence/lifecycle.json)

## BMC4 真实游戏闭环

在 387 个启用模组的隔离世界中，`@Builder` 问候进入官方 Codex SDK。Alt+B 开启模式后，聊天栏的无前缀需求实际生成 8×3 的窗墙和台阶，共 30 块。普通聊天在开启前及退出后均进入游戏频道。定向文字没有出现在广播中，模型账本仅有问候、设计和改图三轮。

设计和改图都经过人工审阅。测试用授权客户端分别提交与玩家决定一致的审阅 `87a692ac26982969` 和 `dcecd0f378d75452`。首份审阅等待期间没有施工。施工到 17/30 块时，玩家在对话模式提出换蓝色玻璃，NPC 暂停。真实模型只修改玻璃材质。V2 保留 15 块，替换 2 块，补建 13 块。第二次批准前，暂停中的动作计数保持不变。

同一 `players:fake_player` 使用 `ServerPlayerGameMode.destroyBlock` 拆除 2 次，用 `useItemOn` 放置 15 次。实测逐次记录 Forge 事件、交互距离、物品消耗及工具耐久。最后 30/30 个坐标、方块 ID 和完整状态吻合，未涉及的目标和区域外均为零差异。重复审批未增加动作或消耗。退出模式后再次发送普通聊天，随后由启动器按 Minecraft 正常流程保存，并确认两进程退出码为 0。 [游戏记录](m5-a-evidence/bmc4.json)

重开两个 Blender 工程，核对了现场和拟建场景、审阅哈希及两个拟建相机。V1 对应空现场与 30 块拟建，V2 对应 17 块现场与 30 块拟建。 [V2 正面预览](m5-a-evidence/proposed.png) · [V2 后方预览](m5-a-evidence/proposed-rear.png)

首次后台按键未切换 BMC4 模式。调整为使用 Forge 按键绑定，并检查配置组合键的按下边沿后，最小 Forge 和 BMC4 均完成原生按键复测。重开沿用已验证的问候和同一个账本。BCLib 提示出现时保留备份，后续重开进入同一个隔离世界。

## Codex 调用

官方 Codex SDK `0.171.0` 复用 ChatGPT 登录。三轮均实测为 `gpt-6.2-sol`、`medium`，沙箱为只读，工具审批策略为 `never`。三轮累计 34,970 Tokens。 [逐轮记录](m5-a-evidence/codex-turns.json)

| 请求 | 实测耗时 | Tokens |
| --- | ---: | ---: |
| `@Builder` 问候 | 12.276 秒 | 11,493 |
| 无前缀窗墙设计 | 12.585 秒 | 11,685 |
| 施工中改蓝色玻璃 | 8.152 秒 | 11,792 |

## 回归

25 项 Python 单测及 Forge 离线构建通过。独立最小 Forge 游戏中的既有 `/architect` 流程以确定性 HTTP 后端重跑，涵盖审批、重复与无权限确认、版本改图、冲突及保存重开。M4 生存烟测重新验证了原版箱取料、补给、材料守恒和运输恢复。
