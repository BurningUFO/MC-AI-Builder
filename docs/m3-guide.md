# M3 游戏内建筑师

在 `/architect` 后输入建筑需求。独立 Python 服务把当前施工上下文和玩家的建筑需求交给模型，生成 `Building`、材料摘要和 Blender 预览。游戏侧确认后，原来的 Fake Player 执行施工或增量拆换。

## 启动独立测试世界

从仓库根目录运行。沿用 Java 17、项目 `.venv`、Blender 和 PCL 的 Forge 1.20.1 版本目录。

~~~powershell
$instance = '<PCL 的 BMC4 版本目录>'
& .\tools\m1.ps1 Build
& .\tools\m1.ps1 ArchitectClient -SourceInstance $instance
~~~

保持世界开启。启动脚本在 `mc-test/architect-client` 下生成两份本地配置。`mcaibuilder.json` 的 `architect.players` 是明确获准的玩家 UUID 列表，`architect.token` 是单独的本机令牌。独立 Runtime 读取 `mcaibuilder-architect.json`，其权限限于拉取需求、暂存设计、请求暂停和回复。

## 启动模型 Runtime

另开 PowerShell。HTTP 提供方追加 `/chat/completions`，使用 JSON 对象输出。从提供方取得 API 基础 URL、模型名及密钥。外部端点使用 HTTPS。 [Chat Completions API 结构](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)

~~~powershell
$instance = '<PCL 的 BMC4 版本目录>'
$env:MC_ARCHITECT_BASE_URL = 'https://api.openai.com/v1'
$env:MC_ARCHITECT_MODEL = '<有权限调用的模型名>'
$secure = Read-Host '模型 API 密钥' -AsSecureString
$env:MC_ARCHITECT_API_KEY = [System.Net.NetworkCredential]::new('', $secure).Password
$py = '.\.venv\Scripts\python.exe'
$config = '.\mc-test\architect-client\config\mcaibuilder-architect.json'
& $py -B -m src.architect --config $config --instance $instance
~~~

要使用确定性演示后端，在同样的启动命令末尾加 `--provider fixture`。Runtime 可用 Ctrl+C 停止，再用同一配置重启。它会重新连接 Bridge，并从本地 SQLite 读取设计版本和近期对话。模型请求超时或输出非法参数时，聊天会提示重试或澄清。

仅 `/architect` 后的需求和完成它所需的范围、位置、近期设计对话进入模型请求。Runtime 会过滤常见密钥、本机绝对路径和当前已知的令牌。Blender 使用本机资源生成文件。因权限和世界状态的变化，Forge 可能要求再审阅一次。

### 登录 Builder 的 Codex 账号

Codex Provider 通过官方 Python SDK 使用 Builder 在 `%LOCALAPPDATA%\MC-AI-Builder\codex-home` 中的 ChatGPT 登录。它接受同一套建筑意图并复用 M3 的校验和审批。对话状态来自 Runtime 保存的近期对话和最新设计，最多四轮历史。每次提交意图使用隔离的只读会话，默认提供方仍是 `http`。 [官方 Python SDK](https://github.com/openai/codex/tree/main/sdk/python)

~~~powershell
& $py -m pip install -r .\requirements-codex.txt
& $py -B -m tools.codex_sdk_probe status
$budget = '.\.cache\m3-codex-sdk\architect-turns.sqlite3'
& $py -B -m src.architect --config $config --instance $instance --provider codex --timeout 50 `
    --codex-model gpt-6.2-sol --codex-effort medium --codex-budget-log $budget
~~~

需要重新登录时，可运行 `& $py -B -m tools.codex_sdk_probe login`，在打开的官方网页亲自完成登录。Codex 模式使用 ChatGPT 登录，模型和思考深度默认分别为 `gpt-6.2-sol` 和 `medium`。Runtime 在请求前核对 SDK 回报的模型、深度、只读沙箱和审批策略。SDK 关闭命令工具、MCP 和网页搜索。转给 SDK 的建筑上下文限制为 4 KiB。

本地预算默认每任务 100 回合和 3,000,000 Token。同一账本路径在 Runtime 重启后继续计数。使用命令行开启新任务时，可以为 `--codex-budget-log` 指定新路径。每次请求预留 24,000 Token，收到 SDK 用量后按实际值记账，缺少用量的回合继续计入预留值。游戏分别报告本地预算耗尽和账户服务端限制，已生成的审批仍由 Forge 校验。

用 `tools.codex_sdk_probe sdk` 和 `tools.codex_sdk_probe provider` 分别重现独立调用与多轮意图到 `Building` 的测试。各命令发起一个回合，报告保存在被 Git 忽略的 `.cache/m3-codex-sdk`，使用合成数据。 [Codex SDK 实测结果](m3-codex-sdk-validation.md)

## 游戏内体验

在 `M1 Construction Lab` 出生后，依次尝试以下指令。在看到第一条预览后，再提出第二条。

~~~text
/architect 请在 6 -60 6 朝北建一面 7 格宽的中世纪窗墙，用云杉和石砖。
/architect 把这面窗墙改成 8 格宽，把木材换成深色橡木。
/architect 这个版本可以，开始建造。
~~~

聊天显示材料、候选版本和相对游戏实例的预览路径。用 Blender 打开 `revision-review.blend`，同目录提供正侧、后侧和现场图片。确认自己看过这版后，点击聊天中的 `批准` 或输入其完整 `/architect approve <确认码>`。确认码由 Forge 发出，限获准玩家在指定维度和施工区域内使用，有效期 10 分钟。新的自然语言请求结束上一张确认卡，需要审批时会发出新卡。

等 Fake Player 放下若干块，在同一游戏里输入 `/architect 先暂停施工，把窗户换成蓝色染色玻璃，其他已经建好的部分保持不变。`。审阅显示保留、补建、拆除、替换和冲突。选择批准后，M2 按世界状态执行增量施工。现场变化导致旧确认失效时，用 `/architect 重新审阅现场变化` 取得新的回读和预览。

`/architect 施工进度如何？` 查询进度，`/architect 帮我暂停施工` 暂停。要求继续或取消时，Forge 会给出与当前任务绑定的操作，由玩家亲自确认。重开时待审阅候选及已发行的确认记录保留，玩家可以重新审阅，也可在有效期内使用原确认码。

中世纪样式的入口采用橡木楼梯。当前生成器支持宽 5–12 格、高 3–5 格的中世纪或简洁木石窗墙。存在关键歧义时，聊天请求补充尺寸、材料或位置。支持原版橡木、云杉、深色橡木、石砖、圆石、石头及三种玻璃。

## 自动验收

~~~powershell
& .\tools\m1.ps1 M3Smoke -SourceInstance $instance
& .\tools\m1.ps1 M3Bmc4 -SourceInstance $instance
~~~

两项测试都连接已登录的 Minecraft 测试客户端并运行独立 Python 进程。HTTP 模型夹具给出可复现的意图，也模拟超时和非法输出。结果、原始聊天跟踪、测试世界和 Blender 工程均保存在 `mc-test`，正式 BMC4 版本目录只提供只读资源。 [M3 验收和证据](m3-validation.md)

Codex 实测可以用同一个本地账本跨两个测试客户端累计回合与 Token。运行期间，在对应测试客户端聊天中亲自确认各次审阅。最小世界通过后，可追加 `--bmc4-pack <已有隔离客户端目录>`，并换用空闲端口复测。`$javaHome` 取本机 Java 17 JDK。

~~~powershell
& $py -B -m tests.m3_codex_live --java-home $javaHome --source-instance $instance --ledger $budget
~~~

真实提供方配置好后，可运行 `& .\tools\m1.ps1 M3Live -SourceInstance $instance` 自动重放游戏内核心流程。该入口向已配置的模型端点发送测试 `/architect` 需求、经过过滤的近期设计对话和受限 Minecraft 场地上下文。确认有权使用该端点及发送这些数据。真正跑通后会写出带 `real_llm` 验收标记的结果，存于 `mc-test/m3-live-runs`。
