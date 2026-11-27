# 细部构件复验

## 折岸植物标本馆门廊

铜质半砖和楼梯让檐面逐级起伏并向前伸出。方解石砖墙的独立细柱立在方解石柱脚上，倒置的方解石砖楼梯与云杉托臂承接上檐。檐心采用闭合的上半部桦木窗格活板门，厚 3/16 格。固定相机复验时，窗格比全块玻璃试稿更容易从整体图读出，眼高图中檐口、柱身和托臂仍是视觉重点。较远的一盏入口灯在斜视图里部分被遮住。 [入口眼高](portico-path.webp) · [檐下](portico-soffit.webp) · [整体](portico-approach.webp) · [设计源码](../../../projects/oxbow_herbarium/design.py)

前后图共用机位、光照和 CPU Cycles review 档位。新增 36、移除 14、替换 13 格，集中在 `(-9,1,9)` 至 `(-1,5,12)`。`walk` 从台阶 `(-5,0,12)` 经 `(-5,0,8)` 到展厅 `(-5,0,4)`，全程 8 步。 [改造前快照](../../../projects/oxbow_herbarium/versions/before-portico)

## 东花亭承台

同相机比较露出蓝底时、上座就位后和完成时的外观。暖白内胆外贴独立的绿侧饰，开孔和边缘从施工斜视可辨。成品水平机位仍能看到窄蓝边和两处侧饰，渲染中孔内亮度低于原图。灯数、内胆的确切材质、冰种和背侧重复取自有依据的推断。 [蓝底](pavilion-deck.webp) · [上座](pavilion-pad.webp) · [成品机位](pavilion-cover.webp) · [原作证据与方块状态](../../../projects/sky_limit_pavilion/README.md#承台构件复验)

## 八格样本盲读

将完工俯视、反面低视角和较早施工态分别交给独立读图的 Critic，给出的输入只有三张画面。它从较早阶段读出 5 个方块位置，完成态增加两块下半砖和一扇开启的橡木活板门。把自选坐标系旋转 90° 后，8 个位置与源蓝图一致。不同视角确认了倒置直楼梯、木梁沿长向横放和侧片的贴合方向。打开状态下两种 `half` 共用模型，双层平滑砂岩半砖与同材质整块外形一致，因此读图保留了这些不确定性。 [三张观察图](control.webp) · [源蓝图脚本](control/design.py) · [机位和早期剖面](control/views.json)

用样本的同一模型、灯光与机位比较 `--look game` 普通材质自发光 0.11、0.05 和 0。0.05 增加楼梯凹槽的明暗层次，门孔后仍能看见木梁。0 的凹槽更深，门孔内的木纹较难辨认。现有游戏观感采用 0.05，海晶灯等光源材质继续使用原强度。 [着色对照](game-shading.png)

## 复现

在仓库根目录运行，先按工作流设置 `MCBUILDER_INSTANCE`，并在有 Pillow 的 Python 中运行。 [本机环境与重开命令](../../blender-workflow.md)

~~~powershell
python -B -m src restore --sample oxbow_herbarium --version before-portico --as oxbow_before_portico
python -B -m src generate --sample oxbow_before_portico
python -B -m src generate --sample oxbow_herbarium
python -B -m src compare-views projects/oxbow_before_portico projects/oxbow_herbarium --views-file projects/oxbow_herbarium/views.json --preset review --look showcase --cpu --view portico-path --view portico-soffit --view approach --out previews/oxbow_herbarium/portico-repeat
python -B -m src walk projects/oxbow_herbarium --from -5 0 12 --via -5 0 8 --to -5 0 4
~~~

使用 `build --sample oxbow_herbarium --preset final --cpu --view approach --view portico-path` 可生成含所有机位和剖面的标本馆工程。东花亭的施工取景及三栏对照命令随项目提供。 [东花亭复现](../../../projects/sky_limit_pavilion/README.md#复现)

用 BMC4 资源分别生成两次，两个项目各自的蓝图 SHA-256 一致。改前快照从公开文件恢复并重建材料索引后，也得到同一份改前蓝图。Blender 5.2.4 LTS 重开两份最终工程，检查了下表所列内容。

| 工程 | 方块 | 场景 | 机位 | 嵌入贴图 | 复开后解码的 1440×1200 图 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 折岸植物标本馆 | 3,568 | 5 | 15 | 38 | 2 |
| 天空即为极限 · 东花亭 | 202 | 4 | 7 | 12 | 3 |
