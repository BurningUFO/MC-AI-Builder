# Minecraft AI Builder

用 Minecraft 方块坐标、注册 ID 和状态描述建筑，支持在 Blender 里审阅模型，并通过 Forge Bridge 让 Fake Players NPC 在游戏里施工。

M1 在 Minecraft Java 1.20.1 和 Forge 47.4.20 中实测完成了 30 块窗墙。Python 提交施工计划，NPC 寻路并使用库存中的物品逐块放置。Bridge 提供进度查询、暂停、恢复、取消和完工回读。

![M1 游戏内完工截图](docs/m1-evidence/m1-wall-built.png)

## 快速验证

在 Windows 的仓库根目录运行。准备 Python 3.13、Java 17 JDK 和已经安装的 PCL Forge 1.20.1 实例。将 `$instance` 设为该实例的版本目录。

~~~powershell
py -3.13 -m venv .venv
$instance = '<PCL 的 BMC4 版本目录>'
& .\tools\m1.ps1 Build
& .\tools\m1.ps1 Smoke -SourceInstance $instance
~~~

首次构建会下载 Gradle、Forge 开发依赖和 Fake Players 3.0.0。脚本用本机的 Minecraft 运行库在 `mc-test` 下创建独立测试世界，保存截图、逐块结果和游戏日志。

- [详细运行说明、手动演示和 Blender 流程](docs/README.md)
- [执行机制、游戏内验收和 BMC4 隔离复测](docs/m1-validation.md)
- [样例建筑数据](projects/m1_wall/building.json) · [施工计划](projects/m1_wall/construction-plan.json)
- [M2 动态修改与施工协调计划](docs/m2-plan.md)
