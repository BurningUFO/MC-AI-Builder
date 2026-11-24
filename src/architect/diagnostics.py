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
    "model_budget_exhausted": Diagnostic("model", "condition", "当前任务的本地 Codex 预算已用完。请在启动器查看回合和 Token 用量。"),
    "model_rate_limited": Diagnostic("model", "condition", "ChatGPT 服务端当前限制了 Codex 使用。请等额度恢复后再试。"),
    "model_unavailable": Diagnostic("model", "fault", "模型服务暂时不可用。请检查 ChatGPT 登录及 agent.log 后重试。"),
    "bridge_unavailable": Diagnostic("bridge", "condition", "Forge Bridge 暂未连接。检查游戏是否已进入副本并取消暂停。"),
    "stale_request": Diagnostic("version", "rejected", "需求已过期。请重新发出需求。"),
    "stale_approval": Diagnostic("version", "rejected", "确认已过期。请重新审阅当前候选。"),
    "stale_planning": Diagnostic("version", "rejected", "施工已进入下一阶段或版本已变更。本次策略已丢弃。"),
    "planning_blocked": Diagnostic("construction", "condition", "Builder 停在检查点。可提供施工顺序建议，或在游戏内手动继续。"),
    "invalid_planning_output": Diagnostic("schema", "rejected", "模型策略未通过校验，施工沿用原顺序。"),
    "invalid_planning_group": Diagnostic("schema", "rejected", "工作组未获批准，施工沿用原顺序。"),
    "planning_world_changed": Diagnostic("protection", "rejected", "工作组现场已变化，施工沿用原有安全核验。"),
    "planning_group_no_stance": Diagnostic("navigation", "condition", "候选工作组没有可靠站位，施工沿用原有安全核验。"),
    "planning_frame_unregistered": Diagnostic("vision", "rejected", "画面和住宅坐标未对齐。可以重新观察。"),
    "planning_observation_limit": Diagnostic("vision", "condition", "施工观察次数已用完，施工沿用原顺序。"),
    "moving_frame_mismatch": Diagnostic("vision", "rejected", "画面和现场样本未对齐，施工可继续。"),
    "invalid_request": Diagnostic("schema", "rejected", "方案无法通过施工范围或方块约束。请调整尺寸、坐标或材料。"),
    "design_error": Diagnostic("schema", "rejected", "请调整建筑尺寸、材料或地基，再重新审阅。"),
    "job_active": Diagnostic("construction", "condition", "已有施工正在进行。请修改属于你的当前任务，或等待当前任务结束。"),
    "no_draft": Diagnostic("construction", "condition", "目前没有候选设计。告诉我你想建什么。"),
    "worksite_permission_inactive": Diagnostic("approval", "rejected", "自主建设许可已到期或撤销。请由玩家重新审阅施工区。"),
    "protected_facility": Diagnostic("protection", "rejected", "附近有容器或功能块。请移开工程或由玩家决定如何处理。"),
    "outside_worksite": Diagnostic("approval", "rejected", "目标超出获准施工区。请选择范围内的已核对地基。"),
    "architect_forbidden": Diagnostic("approval", "rejected", "需要由副本中的授权玩家在游戏内操作。"),
    "change_pending": Diagnostic("approval", "condition", "请审阅并批准或拒绝待审改图。"),
    "paused_by_request": Diagnostic("control", "condition", "玩家已暂停任务。请用游戏内继续按钮恢复。"),
    "site_not_loaded": Diagnostic("world", "condition", "批准的施工区尚未加载，请回到附近后重新审阅或继续。"),
    "checkpoint_npc_unavailable": Diagnostic("recovery", "condition", "原施工 NPC 尚未加载。请回到工地。"),
    "restart_verification": Diagnostic("recovery", "condition", "回到施工区后，可以叫我继续上次的工程。"),
    "journal_unavailable": Diagnostic("storage", "fault", "保存失败。请检查 D 盘可用空间和存档日志，再从上次检查点恢复。"),
    "warehouse_job_scope": Diagnostic("warehouse", "rejected", "这项施工使用了另一份供料授权。"),
    "warehouse_player_approval": Diagnostic("approval", "rejected", "需要由授权玩家在游戏里批准施工。"),
    "awaiting_materials": Diagnostic("warehouse", "condition", "请把缺的材料补到附近箱子里，再告诉我一声。"),
    "scaffold_material": Diagnostic("warehouse", "condition", "高处需要临时垫脚。可以在附近箱子里放一些泥土。"),
    "protected_area": Diagnostic("protection", "rejected", "设计碰到了你标记的保护区域。请调整位置或布局。"),
    "change_conflicts": Diagnostic("protection", "rejected", "方案碰到了保护资产。请改图或换个位置。"),
    "out_of_material": Diagnostic("warehouse", "condition", "请核对并补足获准箱子的材料。"),
    "out_of_tool": Diagnostic("warehouse", "condition", "请将状态合适的工具放入获准的箱子。"),
    "warehouse_unreachable": Diagnostic("navigation", "condition", "我走不到下一批材料所在的容器。请把材料挪到容易走到的箱子。"),
    "no_reachable_placement_stance": Diagnostic("navigation", "condition", "目标没有可达的放置位置。请检查通道、高差和支撑。"),
    "no_reachable_break_stance": Diagnostic("navigation", "condition", "目标没有可达的拆除位置。请检查通道和作业面。"),
    "navigation_timeout": Diagnostic("navigation", "condition", "NPC 导航超时。请检查高低差和障碍物。"),
    "placement_stalled": Diagnostic("navigation", "condition", "当前方块长期没有施工进展，Builder 已停在检查点。"),
    "interaction_forbidden": Diagnostic("protection", "rejected", "真实游戏交互受保护。请检查区域和玩家权限。"),
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
