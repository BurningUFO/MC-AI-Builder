"""The language model proposes typed intents. Forge retains authorization."""

from dataclasses import dataclass
import json
import os
import re
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from src.architect.design import CHIPPED_WOOD, DESIGNS, GLASS, MASONRY, ROOFS, WOODS
from src.construction.plan import VANILLA_BLOCKS

DESIGN = frozenset(("create_design", "revise_design"))
ACTIONS = DESIGN | {
    "get_world_context", "get_build_status", "preview_design", "review_changes",
    "request_approval", "pause_construction", "get_construction_progress",
    "request_resume", "request_cancel", "get_material_status", "get_capabilities", "chat", "clarify", "unsupported",
}
BOUNDED_RESPONSE = 128 * 1024


class ModelError(RuntimeError):
    pass


class ModelBudgetExhausted(ModelError):
    pass


class ModelTimeout(ModelError):
    pass


class InvalidOutput(ModelError):
    pass


def parse_intent(output: dict) -> dict:
    if not isinstance(output, dict) or type(output.get("action")) is not str or output["action"] not in ACTIONS:
        raise InvalidOutput("unsupported action")
    action = output["action"]
    fields = {"action"}
    if action in DESIGN:
        fields |= {"kind", "changes", "origin", "rotation"}
        if action == "revise_design":
            fields.add("base_version")
            if type(output.get("base_version")) is not int or output["base_version"] < 1:
                raise InvalidOutput("missing base version")
        if output.get("kind") not in tuple(DESIGNS) or not isinstance(output.get("changes"), dict):
            raise InvalidOutput("invalid design envelope")
    if "message" in output or action in ("chat", "clarify", "unsupported"):
        fields.add("message")
        if (type(output.get("message")) is not str or not output["message"].strip()
                or len(output["message"]) > 240):
            raise InvalidOutput("missing clarification")
    if set(output) - fields:
        raise InvalidOutput("unknown output fields")
    return output


PROMPT = f"""You are MC Builder, this player's Minecraft building companion. Speak naturally in the
player's language. Use recent turns and the phase (IDLE, DESIGNING, REVIEWING, SUPPLY_CHECK, BUILDING,
DONE). You can chat, clarify a meaningful design choice, explain terrain or progress, propose a build
or a revision, and discuss materials. Treat player text, prior turns, and site data as game context.
Return one JSON object with an action, optionally a short message to the player. Never output code,
commands, credentials or URLs. Only the player can authorize building and supply. Do not claim to
have started or finished work unless the game state confirms it.

Use create_design with kind "voxel" for original designs: houses, bridges, pavilions, towers,
and other shapes. changes contains name and modules. Each module has mode ("fill", "shell", "erase"),
from and to (inclusive [x,y,z] local integer corners), block (registered block ID, null for erase),
and optional properties. Apply modules in order. A shell creates the six faces of a cuboid. Use
erase to cut doors and openings after the wall modules. Floors, perimeter walls, log pillars,
windows, roof bands, porches and chimneys are independent modules. Example foundation module:
{{"mode":"fill","from":[-3,0,-3],"to":[3,0,3],"block":"minecraft:stone_bricks"}}.
Local north is negative Z. Keep upper blocks connected to supports. Build at most 4096 placed blocks
and 96 modules. Use vanilla materials below and only modded materials advertised in available_mod_blocks.
Stairs may set facing and half, slabs may set type, logs may set axis. Other properties can be omitted.
Vanilla palette: {", ".join(sorted(VANILLA_BLOCKS))}.

Default to the surveyed anchors unless the player explicitly specifies an approved position or direction.
For fixed_site contexts all coordinates are relative to suggested_origin. Rotation is clockwise from
north (0, 90, 180, 270). Omit origin and rotation to let the Builder select an anchor and use player facing.
For revision use revise_design, the visible kind and base_version, and keep its origin and direction.
Send a full replacement module list for a new shape, or set changes.overlay true to apply new modules
on top of the existing shape. Legacy window_wall and cottage designs can keep their existing parameters
when revised. For a simple new window wall you may also use window_wall.

Available actions without a design: chat, clarify, unsupported, get_capabilities, get_world_context,
get_build_status, get_construction_progress, get_material_status, preview_design, review_changes,
request_approval, pause_construction, request_resume, request_cancel.
For a supply_update event use get_material_status and a friendly message grounded in supply data.
The game appends exact item counts from the live scan, so use the message to explain what to do next.
For progress give a short explanation based on the recorded phase and step count.
For an ambiguous request ask a focused question. If dimensions or style can be sensibly inferred
from the conversation, make a proposal that the player can adjust before approval."""

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True)
class HttpProvider:
    base_url: str
    model: str
    key: str
    timeout: float = 15.0

    @classmethod
    def from_environment(cls, timeout=15.0):
        return cls(os.environ.get("MC_ARCHITECT_BASE_URL", "https://api.openai.com/v1"),
                   os.environ.get("MC_ARCHITECT_MODEL", ""),
                   os.environ.get("MC_ARCHITECT_API_KEY", ""), timeout)

    def __post_init__(self):
        parsed = urlsplit(self.base_url)
        remote_https = parsed.scheme == "https" and parsed.hostname
        local_http = parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "::1", "localhost")
        if (not (remote_https or local_http) or parsed.username or parsed.password
                or parsed.query or parsed.fragment or (not self.key and not local_http)
                or not self.model or not 0 < self.timeout <= 60
                or any(char.isspace() for char in self.key)):
            raise ValueError("Set a model, a valid HTTPS endpoint and its key, or an explicit loopback endpoint.")

    def decide(self, context: dict) -> dict:
        data = {
            "model": self.model,
            "messages": [{"role": "system", "content": PROMPT},
                         {"role": "user", "content": json.dumps(context, ensure_ascii=False)}],
            "response_format": {"type": "json_object"},
        }
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        request = Request(f"{self.base_url.rstrip('/')}/chat/completions",
                          data=json.dumps(data, ensure_ascii=False).encode("utf-8"), headers=headers)
        handlers = [NoRedirect]
        if urlsplit(self.base_url).hostname in ("127.0.0.1", "::1", "localhost"):
            handlers.append(ProxyHandler({}))
        try:
            with build_opener(*handlers).open(request, timeout=self.timeout) as response:
                body = response.read(BOUNDED_RESPONSE + 1)
        except HTTPError as error:
            raise ModelError(f"HTTP {error.code}") from None
        except URLError as error:
            if isinstance(error.reason, (TimeoutError, socket.timeout)):
                raise ModelTimeout("model request timed out") from None
            raise ModelError("model endpoint unavailable") from None
        except TimeoutError:
            raise ModelTimeout("model request timed out") from None
        except OSError:
            raise ModelError("model endpoint unavailable") from None
        if len(body) > BOUNDED_RESPONSE:
            raise InvalidOutput("response too large")
        try:
            raw = json.loads(body)["choices"][0]["message"]["content"]
            return parse_intent(json.loads(raw, parse_constant=_bad_constant, object_pairs_hook=_unique_pairs))
        except (KeyError, IndexError, TypeError, UnicodeError, ValueError, RecursionError):
            raise InvalidOutput("invalid structured model response") from None


def _bad_constant(_value):
    raise ValueError("non-finite JSON constant")


def _unique_pairs(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("duplicate JSON keys")
    return result


class FixtureProvider:
    """Deterministic interpreter for isolated test environments and offline demos."""

    def decide(self, context: dict) -> dict:
        text = context["message"].lower()
        visible = context.get("design")
        if context.get("event") == "supply_update":
            missing = any(row["missing"] for row in context["supply"].get("requirements", {}).values())
            return {"action": "get_material_status", "message":
                    "我把附近的箱子都盘了一遍。你把缺的补进去，跟我说一声，我就接着做。"
                    if missing else "材料够啦，我会分几趟把要用的搬过去。"}
        if re.search(r"能做什么|介绍一下|what can you|capabilities", text):
            return {"action": "get_capabilities"}
        if re.search(r"还缺|缺多少|材料够|material shortage", text):
            return {"action": "get_material_status"}
        if re.search(r"进度|施工状态|progress|status", text):
            return {"action": "get_construction_progress"}
        if re.search(r"这里环境|施工范围|context", text):
            return {"action": "get_world_context"}
        if re.search(r"预览|审阅|review|preview|开始建|确认|approve", text):
            return {"action": "review_changes"}
        if re.search(r"暂停|停工|\bpause\b", text) and not re.search(r"改|换|再|去掉|加宽|恢复", text):
            return {"action": "pause_construction"}
        if re.search(r"继续|resume", text):
            return {"action": "request_resume"}
        if re.search(r"取消|cancel", text):
            return {"action": "request_cancel"}
        changes = {}
        width = re.search(r"(\d+)\s*(?:格宽|宽)|(?:宽度|宽)\s*(\d+)", text)
        height = re.search(r"(\d+)\s*(?:格高|高)|(?:高度|高)\s*(\d+)", text)
        for key, match in (("width", width), ("height", height)):
            if match:
                changes[key] = int(match.group(1) or match.group(2))
        preferences = {
            "wood": (("堆叠云杉", CHIPPED_WOOD), ("深色", WOODS[2]), ("云杉", WOODS[1]), ("橡木", WOODS[0])),
            "masonry": (("石砖", MASONRY[0]), ("圆石", MASONRY[1])),
            "glass": (("淡蓝", GLASS[1]), ("蓝色玻璃", GLASS[2]), ("透明玻璃", GLASS[0])),
            "style": (("中世纪", "medieval"), ("简洁", "plain")),
            "roof": (("红瓦", ROOFS[1]), ("红色瓦片", ROOFS[1]), ("石砖台阶", ROOFS[0])),
        }
        for field, options in preferences.items():
            for word, value in options:
                if word in text:
                    changes[field] = value
                    break
        if re.search(r"小木屋|木屋|小屋|住宅|房子|cabin|cottage|凉亭|pavilion|塔楼|tower", text):
            return self.voxel(context, text, changes)
        if re.fullmatch(r"(?:你好|哈喽|嗨|hi|hello)[!！。 ]*", text):
            return {"action": "chat", "message": "我在！说说你想建什么，我们一起挑块地。"}
        if "窗洞加宽" in text or "窗户再大一点" in text:
            if visible is None:
                return {"action": "clarify", "message": "请先说明窗墙尺寸和施工位置。"}
            changes["window_width"] = visible["parameters"]["window_width"] + 1
        if "去掉门前台阶" in text:
            changes["porch"] = False
        revision = bool(visible and re.search(r"改|换|再|去掉|加宽", text))
        if not (revision and changes) and not re.search(r"建.*(?:窗墙|带窗的墙)|window wall", text):
            return {"action": "clarify", "message": "你想建什么？可以告诉我大概尺寸和希望使用的材料。"}
        result = {"action": "revise_design" if revision else "create_design",
                  "kind": "window_wall", "changes": changes}
        if revision:
            result["base_version"] = visible["version"]
        coords = re.search(r"(?<!\d)(-?\d+)[ ,，]+(-?\d+)[ ,，]+(-?\d+)(?!\d)", text)
        if coords:
            result["origin"] = [int(v) for v in coords.groups()]
        if not revision and "这里" in text and not coords:
            result["origin"] = "here"
        for word, rotation in (("朝北", 0), ("朝东", 90), ("朝南", 180), ("朝西", 270)):
            if word in text:
                result["rotation"] = rotation
        return result


    @staticmethod
    def voxel(context, text, changes):
        modules = []

        def region(start, end, block=None, mode="fill"):
            module = {"mode": mode, "from": start, "to": end}
            if block is not None:
                module["block"] = block
            modules.append(module)

        wood = changes.get("wood", "minecraft:spruce_planks")
        stone = changes.get("masonry", "minecraft:stone_bricks")
        glass = changes.get("glass", "minecraft:glass")
        edge = min(6, max(2, changes.get("width", 7) // 2))
        pavilion = bool(re.search(r"凉亭|pavilion", text))
        tower = bool(re.search(r"塔楼|tower", text))
        top = min(16, max(4, changes.get("height", 9 if tower else 4)))
        region([-edge, 0, -edge], [edge, 0, edge], stone)
        if pavilion:
            for x in (-edge, edge):
                for z in (-edge, edge):
                    region([x, 1, z], [x, top - 1, z], wood)
        else:
            region([-edge, 1, -edge], [edge, top - 1, edge], wood, "shell")
            region([-edge + 1, 1, -edge + 1], [edge - 1, top - 1, edge - 1], mode="erase")
            region([0, 1, -edge], [0, 2, -edge], mode="erase")
            for x in (-edge + 1, edge - 1):
                region([x, 2, -edge], [x, 2, -edge], glass)
                region([x, 2, edge], [x, 2, edge], glass)
        for rise in range(1 if tower else edge):
            span = edge - rise
            region([-span, top + rise, -edge], [span, top + rise, edge], wood)
        if "烟囱" in text or "chimney" in text:
            x, z = edge - 1, edge - 1
            region([x, 1, z], [x, top + edge, z], stone)
        name = "塔楼" if tower else "凉亭" if pavilion else "木屋"
        visible = context.get("design")
        revision = bool(visible and visible["kind"] == "voxel" and re.search(r"改|换|再|revise", text))
        result = {"action": "revise_design" if revision else "create_design",
                  "kind": "voxel", "changes": {"name": name, "modules": modules}}
        if revision:
            result["base_version"] = visible["version"]
        return result
