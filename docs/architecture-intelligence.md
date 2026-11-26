# Architecture Intelligence

复杂建筑先用参考图确定比例、轮廓或空间关系，再比较低成本体量。把选定的几何、材质和细节写入 Python，最后用固定机位、关键路线和蓝图差异决定保留哪一版。[操作命令](blender-workflow.md#设计比较和动线)

## 从需求确定设计目标

| 需求 | 设计与审阅重点 |
| --- | --- |
| 设计一座蓝白色的幻想空中宫殿 | 自由创作。审阅体量主次、配色、空间和用途，参考帮助解决构造问题。 |
| 复刻这个视频里的建筑 | 忠实复刻。先核对原作可见的构件数量、相对尺寸、连接与留白，再核对材质和细节。 |
| 保留整体外观，改造成生存基地 | 参考改编。用原作检查约定保留的轮廓与外观，用空间走查验证新布局和动线。 |

设计者和独立 Critic 共用一套工程与命令。复刻时，在项目笔记列出真正打开过的画面及来源、可见关系、可计数尺寸或估计范围，以及看不到的部分。用体量图先验证比例与留白，针对重要偏差选一个可比视角手动对齐。修改后同时查原作和同机位前版，再用蓝图 `diff` 核对真实构件变化。复验从原作重新查起，记录仍有依据可修的偏差和证据不足的细节。[参考对照命令](blender-workflow.md#设计比较和动线)

## 研究与采用

| 项目 | 实际核查与工程决定 |
| --- | --- |
| LL3M | 论文 §3–4 与附录 A 给出了规划、按部件检索 Blender API 示例、共享 Python 源码、视觉 Critic 和逐条复验。作者的 3D 资产实验显示自动迭代能修正部分几何问题，复杂位置仍有漏判。公开的 `main.py` 轮询服务端指令，在本地执行脚本并回传图像，托管服务已停，仓库许可限学术与评估。采用共享源码和逐条视觉复验的办法。 [论文](https://arxiv.org/html/2508.08228v1#S3.SS2) · [客户端](https://github.com/threedle/ll3m/blob/main/main.py#L481) · [项目状态](https://github.com/threedle/ll3m) · [许可](https://github.com/threedle/ll3m/blob/main/LICENSE) |
| mcbuild | `prompts.py`、`tools.py` 和 `loop.py` 实现了主动选角、剖切、ASCII 分层查询、参考图重现与分块差异。完成前会检查是否观察过当前版本的内部。仓库有可运行实现和示例，外观收益缺少对照。MIT。采用问题驱动的机位与空间查询，参考图用于提炼设计语言，继续用现有 Python 和 Blender。 [提示词](https://github.com/harrynull/mcbuild/blob/main/src/mcbuild/agent/prompts.py) · [循环](https://github.com/harrynull/mcbuild/blob/main/src/mcbuild/agent/loop.py#L538) · [许可](https://github.com/harrynull/mcbuild/blob/main/LICENSE) |
| MineAnyBuild | 正文 §2–3 和 `prompter.py`、`evaluator.py` 把子结构与连接、室内常识、可执行规划和画面创造性分别检查。多选题有明确答案，开放任务中的部分评分来自模型 Critic。它是可检查实现的基准，开放任务分数不能直接估计本地作品的审美。数据为 CC BY-NC-SA 4.0，代码仓库根目录未查到许可。采用从需求导出可检查的连接关系，用本地蓝图验证。 [论文](https://proceedings.neurips.cc/paper_files/paper/2025/hash/8048e2b9b631b15e1158f36275d8fc11-Abstract-Datasets_and_Benchmarks_Track.html) · [评估源码](https://github.com/MineAnyBuild/MineAnyBuild/blob/main/mineanybuild/evaluator.py) · [数据许可](https://huggingface.co/datasets/SaDil/MineAnyBuild) |
| SceneCraft | 正文 §2–3、附录 C/E 给出了子场景、关系约束、求解、渲染反馈及从多次成功修订汇集 Python 关系函数的做法。表 1 在合成关系任务中报告约束分 88.9 对 BlenderGPT 的 5.6，表 2 的审美比较仅抽了 10 对场景。采用先解决体量和连接、再写详细构件，以及沉淀小型几何函数。论文为可读依据，未找到对应完整实现与可移植代码许可。 [会议论文](https://proceedings.mlr.press/v235/hu24g.html) · [函数与提示词](https://arxiv.org/html/2403.01248v1#A3) |
| BlendHarness | `critic.py` 接收图像和场景摘要，`verifier.py` 联合前后渲染与几何差异，技能入库会试运行并审阅效果。项目自己的小样本实验中，整件技能很少被采用，失败经验的成本收益未在确认实验中复现。公开源码根目录未查到许可。采用具体问题、同条件图像和蓝图变化联合复验，经验优先沉淀到可组合的构件方法。 [复验实现](https://github.com/cccky0813/BlendHarness/blob/main/blendharness/agents/verifier.py) · [技能筛选](https://github.com/cccky0813/BlendHarness/blob/main/blendharness/workflow/learning/admission.py) · [实验与限制](https://github.com/cccky0813/BlendHarness#results) |

工程代码根据本地 `Building`、相机、材料、快照接口实现。以上项目供设计机制参考。

## 建筑参考与可复用做法

- **中式檐口、开间、柱梁。** 文昌阁照片的长檐中央平缓，端部曲起。青绿在檐下形成窄线，窗格退在朱柱后，明暗层次比整片高饱和屋瓦更显眼。澄漪阁据此收窄上檐，低端渐抬，以实际暗海晶石贴图在角部和脊线上点色。照片作者 Xiquinho Silva，CC BY 2.0。 [文昌阁照片](https://commons.wikimedia.org/wiki/File:Summer_Palace_-_Wenchang_Pavilion.jpg)
- **观测空间、径向刻度、器物与建筑。** Jai Prakash Yantra 的照片可见浅色刻度、下沉曲面和外侧游览环线。月隙观星台把环形主仪器提到塔顶，侧台保留低矮观测器和有两端入口的下沉盘。照片作者 Hiroki Ogawa，CC BY 3.0。浑仪的子午圈与斜环布局亦参考实物馆藏说明。 [Jai Prakash Yantra 照片](https://commons.wikimedia.org/wiki/File:Jai_Prakash_Yantra_Jantar_Mantar_Jaipur_India_-_panoramio.jpg) · [测量结构](https://whc.unesco.org/uploads/nominations/1338.pdf#page=17) · [Whipple 浑仪](https://collections.whipplemuseum.cam.ac.uk/objects/9238/)
- **半格曲面和接缝。** [`roof_courses`](../src/core/roof.py) 接收任意 Python 高度图 `{(x, z): 离 Y=0 的半格数}`，给出整块、台阶、直楼梯的朝向及需要补实的接缝格。适合连续屋坡、有意起翘的檐口或薄斜面，材质要有配套整块、台阶和楼梯。先令邻列高差足够平缓，再选择脊、檐、屋面三种着色范围。大落差应明确归入山花或背衬，檩木写入空位以免覆盖屋面。澄漪阁屋面、月隙观星台档案厅和浑仪斜环复用了它。屋脊和斜环近景能看出半格和接缝的作用。 [楼阁檐口](previews/architecture_v1/waterside_pavilion/eaves.png) · [浑仪近景](previews/astral_observatory_architecture_v1/meridian.png)

两栋项目的 [澄漪阁](../projects/waterside_pavilion_architecture_v1/README.md) 和 [月隙观星台](../projects/astral_observatory_architecture_v1/README.md) 记录了体量草案、实拍对比和走查结果。


## 按设计问题找案例

| 设计问题 | 实图观察与方块做法 |
| --- | --- |
| 新艺术运动的入口、檐下和花窗 | Guimard 的 Porte Dauphine 近照中，深绿肋条穿过弧梁再向扇缘分开，玻璃之间有大段留白。先画柱、分叉和每根肋的末端，再填玻璃。鸢坡站把深色限在主枝与托臂，铜用于薄肋，白玻璃和少量琥珀玻璃留在扇窗。先从檐下确认肋条、柱头与玻璃的交接，再看远景主次。 [RATP](https://www.ratp.fr/en/discover/heritage/hector-guimard) · [实拍](https://commons.wikimedia.org/wiki/File:Metro_station_entrance_(%C3%A9dicule_Guimard)_Porte_Dauphine_Paris_16e_001.jpg) · [鸢坡站檐下](previews/art_nouveau_hillside_station/comparison/canopy-under.png) |
| 不对称街角与局部装饰 | Ålesund 的 Apotekergata 10 把尖冠和高窗集中到转角凸面，侧墙靠重复开间维持连贯。鸢坡站用低票房衬托东侧高候车亭，保留主大厅的视觉重心。暖浅色壁面配深色檐顶，先以中性体量确认轮廓再试真实贴图。 [街角实拍](https://commons.wikimedia.org/wiki/File:%C3%85lesund_hus.jpg) · [鸢坡站体量比较](previews/art_nouveau_hillside_station/massing.png) |
| 眼平高度的上行动线 | Horta 故居照片中，栏杆、平台和屋顶光带把几个高差读成一条路线。鸢坡站用窄化上跑、区分平台和踏步的材质、打开站棚过渡拱口来重现这层关系。用眼高图查出口与光线，再用剖面及 `walk` 查楼梯和各层标高。 [Horta Museum](https://hortamuseum.be/maison-musee) · [楼梯实拍](https://www.admirable-artnouveau.be/nl/facades/horta-huis-en-atelier-horta-museum/) · [入口眼高对照](previews/art_nouveau_hillside_station/comparison/entrance-eye.png) |
| 宽跨玻璃屋顶的主次 | 安特卫普中央站棚内，重复的承重拱横跨轨道，细分格跟随大面。先以一种低反差的玻璃连接各跨，将对比留给起脚至脊顶的肋。鸢坡站把两条高反差白玻璃带改为与瓦间相同的浅灰玻璃，再从站台验证拱肋序列、透光和端部纵深。 [Visit Antwerpen](https://visit.antwerpen.be/en/info/centraal-station-en) · [站棚实拍](https://www.locationscout.net/belgium/52417-antwerp-central-station) · [站台对照](previews/art_nouveau_hillside_station/comparison/platform.png) |
| 体素曲线接缝 | 用 [`cubic_cells`](../src/core/curves.py) 将任意二维三次 Bézier 路径转成逐格共享边的线，映射到墙面、平面或屋顶高度图。用于接合分叉、栏带、地面镶线。几根线在短跨内并行时会合成暗块，先保留主枝和分叉间的空白，再定装饰层。斜面厚度与半格构件仍由 `roof_courses` 和具体材质决定。 [鸢坡站花窗源码](../projects/art_nouveau_hillside_station/design.py) · [入口对照](previews/art_nouveau_hillside_station/comparison/canopy.png) |

需要选择陌生风格的做法时，先写下参考图中一条要保留的关系，例如“从立柱能看见肋条伸向扇缘”，再决定方块状态和同机位的检验方式。遇到空间疑点先补相机或查询坐标，再修改几何。需要独立判断时，沿用 [建筑工作流的审阅步骤](blender-workflow.md#设计比较和动线)。主动请求机位和切片参考 mcbuild，按原问题结合图像与几何复验参考 BlendHarness。 [mcbuild 提示词](https://github.com/harrynull/mcbuild/blob/main/src/mcbuild/agent/prompts.py#L524-L551) · [BlendHarness 复验源码](https://raw.githubusercontent.com/cccky0813/BlendHarness/main/blendharness/agents/verifier.py)
