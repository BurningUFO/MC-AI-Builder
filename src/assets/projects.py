"""Discover standard Builder projects and resolve their names from Minecraft chat."""

from dataclasses import dataclass
from pathlib import Path
import re
from threading import Lock
from unicodedata import normalize
from uuid import UUID

from src.core.building import Building

ROOT = Path(__file__).resolve().parents[2]
BUILT_INS = {
    "medieval_house": ("中世纪双层民居", "中世纪双层木屋", "中世纪房屋", "中世纪木屋", "双层中世纪木屋",
                       "双层别墅", "medieval house"),
    "enchanting_library": ("附魔图书馆", "附魔台", "图书室", "enchanting library"),
    "warehouse_bridge": ("仓库拱桥", "拱桥", "石桥", "warehouse bridge"),
    "warehouse_flat_bridge": ("木石平桥", "平桥", "flat_bridge", "warehouse flat bridge"),
}
NAME = re.compile(r"[\w][\w .-]{0,95}")
CATALOG_QUESTION = re.compile(
    r"(?:项目库|建筑库|现成|已有|内置|预置|可复现|有哪些建筑项目|项目清单|项目列表|项目目录|工程清单|"
    r"list\s+projects|existing\s+projects|available\s+(?:projects|buildings))", re.I)
BUILD_REQUEST = re.compile(r"建|盖|复现|重现|照着|加载|载入|摆|放|抄|build|recreat|reproduc|load|place|copy", re.I)
CUSTOM_REQUEST = re.compile(r"设计|自定义|全新|风格|design|custom|in the style of", re.I)


class ProjectError(ValueError):
    pass


def valid_name(name):
    return isinstance(name, str) and NAME.fullmatch(name) is not None and not name.endswith((".", " "))


def key(name):
    return re.sub(r"[\s_.-]+", " ", normalize("NFKC", name).casefold()).strip()


@dataclass(frozen=True)
class Project:
    name: str
    title: str
    structure: Path
    preview: Path
    aliases: tuple[str, ...]

    def context(self):
        return {"name": self.name, "title": self.title, "aliases": self.aliases}


class ProjectLibrary:
    def __init__(self, workspace=ROOT):
        self.workspace = Path(workspace).resolve()
        self.projects = self.workspace / "projects"
        self.previews = self.workspace / "previews"
        self._cache = {}
        self._lock = Lock()

    def catalog(self):
        with self._lock:
            root = self.projects.resolve()
            if root != self.projects or not root.is_dir():
                return ()
            cache = {}
            for path in sorted(root.iterdir(), key=lambda entry: (entry.name not in BUILT_INS, entry.name.casefold())):
                try:
                    name = path.name
                    structure = path / "building.json"
                    if not valid_name(name) or not structure.is_file() or path.resolve() != path or structure.resolve() != structure:
                        continue
                    stat = structure.stat()
                    stamp = (stat.st_mtime_ns, stat.st_size)
                    saved = self._cache.get(name)
                    if saved and saved[0] == stamp:
                        cache[name] = saved
                        continue
                    try:
                        building = Building.read(structure)
                        title = building.name
                        if not isinstance(title, str) or not title.strip() or any(ord(c) < 32 for c in title):
                            raise ValueError("Invalid project title")
                    except (OSError, ValueError, TypeError, KeyError, AttributeError):
                        cache[name] = (stamp, None)
                        continue
                    names = BUILT_INS.get(name, ())
                    project = Project(name, (names[0] if names else title.strip())[:100], structure,
                                      self.previews / name, tuple(dict.fromkeys((*names, title.strip()[:100]))))
                    cache[name] = (stamp, project)
                except (OSError, RuntimeError):
                    continue
            self._cache = cache
            return tuple(project for _, project in cache.values() if project is not None)

    def resolve(self, name_or_alias, *, sentence=False):
        query = key(name_or_alias)
        projects = self.catalog()
        if not query:
            return None
        by_name = next((project for project in projects if project.name == name_or_alias), None)
        if by_name is not None:
            return by_name
        ranked = []
        for project in projects:
            for alias in (project.name, *project.aliases):
                word = key(alias)
                if query == word:
                    ranked.append((2, len(word), project))
                elif sentence and word and re.search(
                        (r"(?<![a-z0-9])" if word[0].isascii() else "") + re.escape(word)
                        + (r"(?![a-z0-9])" if word[-1].isascii() else ""), query):
                    ranked.append((1, len(word), project))
        if not ranked:
            return None
        score = max((exact, length) for exact, length, _ in ranked)
        choices = {project.name: project for exact, length, project in ranked if (exact, length) == score}
        if len(choices) > 1:
            raise ProjectError("我找到了几个同名项目。请告诉我完整工程名。")
        return next(iter(choices.values()))

    def load_building_project(self, name_or_alias):
        project = self.resolve(name_or_alias, sentence=True)
        if project is None:
            raise ProjectError("项目库里找不到这份建筑。可以问我有哪些现成项目，或直接告诉我工程名。")
        try:
            building = Building.read(project.structure)
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
            raise ProjectError("这份工程的 building.json 无法读取，请检查项目文件。") from error
        return project.name, building, project.structure, project.preview

    def archive(self, building, plan_id, kind):
        title = key(building.name)
        slug = next((word for term, word in (("凉亭", "pavilion"), ("小屋", "cabin"), ("木屋", "cabin"),
                                              ("桥", "bridge"), ("塔", "tower")) if term in title),
                    re.sub(r"[^a-z0-9]+", "_", title).strip("_")[:24] or kind)
        name = f"custom_{slug}_{UUID(plan_id).hex}"
        project = self.projects / name
        structure = project / "building.json"
        with self._lock:
            if self.projects.resolve() != self.projects:
                raise ProjectError("项目库不是工作区内的实际目录。")
            project.mkdir(parents=True, exist_ok=True)
            if project.resolve() != project or structure.is_symlink():
                raise ProjectError("项目目录指向了工作区之外，请检查 projects 文件夹。")
            if structure.is_file():
                if Building.read(structure) != building:
                    raise ProjectError("这次设计的工程文件已由玩家修改。请重新发起设计。")
            else:
                building.write(structure)
        return name

    def conversation_intent(self, message):
        if CATALOG_QUESTION.search(message) and re.search(r"[?？]|哪些|什么|有啥|列|看看|介绍|list|what|available", message, re.I):
            return {"action": "list_projects"}
        if CUSTOM_REQUEST.search(message):
            return None
        project = self.resolve(message, sentence=BUILD_REQUEST.search(message) is not None)
        if project is not None:
            intent = {"action": "load_project", "project_name": project.name}
            point = re.search(r"(?<!\d)(-?\d+)[ ,，]+(-?\d+)[ ,，]+(-?\d+)(?!\d)", message)
            if point:
                intent["origin"] = [int(value) for value in point.groups()]
            for word, rotation in (("朝北", 0), ("朝东", 90), ("朝南", 180), ("朝西", 270)):
                if word in message:
                    intent["rotation"] = rotation
            return intent
        return None


def load_building_project(name_or_alias: str) -> tuple[str, Building, Path, Path]:
    return ProjectLibrary().load_building_project(name_or_alias)
