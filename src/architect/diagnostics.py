"""Classify failures by the component and by the operator action they need."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Diagnostic:
    component: str
    kind: str
    recovery: str


_CODES = {
    "model_timeout": Diagnostic("model", "fault", "模型服务响应超时。请重发需求，施工状态保持原样。"),
    "invalid_model_output": Diagnostic("model", "fault", "模型输出未通过建筑意图校验。请重述尺寸或材料。"),
    "model_budget_exhausted": Diagnostic("model", "condition", "本地 Codex 预算已用完。请在启动器查看请求数和 Token 用量。"),
    "model_unavailable": Diagnostic("model", "fault", "模型服务暂时不可用。请检查 ChatGPT 登录及 agent.log 后重试。"),
    "bridge_unavailable": Diagnostic("bridge", "condition", "Forge Bridge 暂未连接。检查游戏是否已进入副本并取消暂停。"),
    "stale_request": Diagnostic("version", "rejected", "需求已过期。请重新发出需求。"),
    "stale_approval": Diagnostic("version", "rejected", "确认已过期。请重新审阅当前候选。"),
    "stale_planning": Diagnostic("version", "rejected", "施工已进入下一阶段或版本已变更。本次策略已丢弃。"),
    "invalid_planning_output": Diagnostic("schema", "rejected", "模型策略未通过校验，施工沿用原顺序。"),
    "invalid_planning_group": Diagnostic("schema", "rejected", "工作组未获批准，施工沿用原顺序。"),
    "planning_world_changed": Diagnostic("protection", "rejected", "工作组现场已变化，施工沿用原有安全核验。"),
    "planning_group_no_stance": Diagnostic("navigation", "condition", "候选工作组没有可靠站位，施工沿用原有安全核验。"),
    "planning_frame_unregistered": Diagnostic("vision", "rejected", "画面和住宅坐标未对齐。可以重新观察。"),
    "planning_interaction_in_flight": Diagnostic("construction", "condition", "当前交互仍在核账，施工沿用原顺序。"),
    "planning_observation_limit": Diagnostic("vision", "condition", "施工观察次数已用完，施工沿用原顺序。"),
    "moving_frame_mismatch": Diagnostic("vision", "rejected", "画面和现场样本未对齐，施工可继续。"),
    "review_changed": Diagnostic("version", "rejected", "现场已变化。请重新审阅，生成的旧确认无法施工。"),
    "invalid_request": Diagnostic("schema", "rejected", "方案无法通过施工范围或方块约束。请调整尺寸、坐标或材料。"),
    "design_error": Diagnostic("schema", "rejected", "请调整建筑尺寸、材料或地基，再重新审阅。"),
    "preview_error": Diagnostic("rendering", "fault", "暂时无法生成三维预览。请检查本地 Blender 和资源后重新预览。"),
    "job_active": Diagnostic("construction", "condition", "已有施工正在进行。请修改属于你的当前任务，或等待当前任务结束。"),
    "no_draft": Diagnostic("construction", "condition", "目前没有候选设计。请先描述要建的窗墙或住宅。"),
    "worksite_permission_inactive": Diagnostic("approval", "rejected", "自主建设许可已到期或撤销。请由玩家重新审阅施工区。"),
    "worksite_preparation_changed": Diagnostic("protection", "rejected", "清理位置或相邻状态已变化。请核对现场并重新勘察。"),
    "protected_facility": Diagnostic("protection", "rejected", "附近有容器或功能块。请移开工程或由玩家决定如何处理。"),
    "outside_worksite": Diagnostic("approval", "rejected", "目标超出获准施工区。请选择范围内的已核对地基。"),
    "occupied_target": Diagnostic("protection", "rejected", "目标位置已有不相符的方块。请指定空闲施工位置。"),
    "world_conflict": Diagnostic("protection", "rejected", "现场方块已变化。请核对冲突位置，再审阅或继续。"),
    "unsafe_site_boundary": Diagnostic("protection", "rejected", "边缘有敏感方块。请调整方案或在游戏中重新勘察。"),
    "architect_forbidden": Diagnostic("approval", "rejected", "需要由副本中的授权玩家在游戏内操作。"),
    "change_pending": Diagnostic("approval", "condition", "请审阅并批准或拒绝待审改图。"),
    "paused_by_request": Diagnostic("control", "condition", "玩家已暂停任务。请用游戏内继续按钮恢复。"),
    "site_not_loaded": Diagnostic("world", "condition", "批准的施工区尚未加载，请回到附近后重新审阅或继续。"),
    "checkpoint_npc_unavailable": Diagnostic("recovery", "condition", "当前无法核对原 NPC 或其库存。请回到已批准区域等区块加载，再审阅。"),
    "restart_verification": Diagnostic("recovery", "condition", "请回到施工区核对保存的 NPC、背包、仓库及现场，再确认继续。"),
    "journal_unavailable": Diagnostic("storage", "fault", "保存失败。请检查 D 盘可用空间和存档日志，再从上次检查点恢复。"),
    "warehouse_not_approved": Diagnostic("warehouse", "condition", "请先到供料箱旁，用 /architect supply x y z 批准一个原版单箱。"),
    "warehouse_unavailable": Diagnostic("warehouse", "condition", "供料箱暂不可用。请核对获准的原版单箱。"),
    "warehouse_job_scope": Diagnostic("warehouse", "rejected", "这项施工使用了另一份供料授权。"),
    "warehouse_player_approval": Diagnostic("approval", "rejected", "需要由供料箱的授权玩家批准。"),
    "warehouse_changed": Diagnostic("warehouse", "rejected", "箱内物品已变化。请核对数量后在游戏内确认继续。"),
    "missing_material": Diagnostic("warehouse", "condition", "请核对所需材料及工具，将缺少的物资放入获准的箱子。"),
    "out_of_material": Diagnostic("warehouse", "condition", "请核对并补足获准箱子的材料。"),
    "out_of_tool": Diagnostic("warehouse", "condition", "请将状态合适的工具放入获准的箱子。"),
    "warehouse_unreachable": Diagnostic("navigation", "condition", "NPC 无法安全到达供料箱。请检查区域内的通道。"),
    "no_reachable_placement_stance": Diagnostic("navigation", "condition", "目标没有可达的放置位置。请检查通道、高差和支撑。"),
    "no_reachable_break_stance": Diagnostic("navigation", "condition", "目标没有可达的拆除位置。请检查通道和作业面。"),
    "navigation_timeout": Diagnostic("navigation", "condition", "NPC 导航超时。请检查高低差和障碍物。"),
    "interaction_forbidden": Diagnostic("protection", "rejected", "真实游戏交互受保护。请检查区域和玩家权限。"),
    "placement_obstructed": Diagnostic("interaction", "condition", "有实体持续占用放置空间。请让出目标位置后确认继续。"),
    "placement_rejected": Diagnostic("interaction", "rejected", "原生游戏拒绝放置。请检查支撑、碰撞和保护规则。"),
    "survey_frame_stale": Diagnostic("vision", "rejected", "这一轮现场观察已过期或变化，请重新观察后再审阅。"),
    "world_observation_stale": Diagnostic("vision", "rejected", "现场或 NPC 已变化，请重新观察后再审阅。"),
    "camera_interrupted": Diagnostic("vision", "condition", "NPC 相机失联。恢复游戏窗口和视角后可重新观察。"),
    "camera_timeout": Diagnostic("vision", "condition", "NPC 相机未及时返回画面。请确认游戏仍在运行。"),
    "observation_camera_changed": Diagnostic("vision", "condition", "NPC 相机已切换。请恢复游戏相机再重试。"),
    "forge_client_exited": Diagnostic("client", "fault", "请查看副本 console.log，并通过启动器重新打开副本。"),
}


def diagnose(code):
    return _CODES.get(code, Diagnostic("bridge", "fault", "本次无法继续。请查询进度并查看游戏和 Agent 日志。"))
