"""技能加载器模块 - 扫描并解析 `app/skills/*/SKILL.md`

职责：把 Markdown 格式的 SKILL.md 解析成结构化的 Skill 对象，供匹配器和执行链路使用。

SKILL.md 规范（参考 Claude Code 的 skill 格式）：
    - 文件头 `---` 包裹的 frontmatter，提供三个字段：
      - `name`        技能唯一标识（缺失则该 skill 被忽略）
      - `description` 技能一句话说明（用于日志/兜底）
      - `when_to_use` 逗号分隔的触发关键词（用于 SkillMatcher 规则匹配）
    - 正文 `## 执行步骤` 章节下的有序列表（`1. xxx`）= 固化 plan 的步骤来源
    - 全文（frontmatter + 所有章节）= skill_context，注入 executor 每一步执行

frontmatter 用轻量手写解析（`key: value` 行），不引入 pyyaml 依赖——
字段只有三个固定 key，手写解析足够且少一个运行时依赖。
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger

# app/skills 目录（本文件所在目录），每个技能一个子目录，内含 SKILL.md
SKILLS_ROOT = Path(__file__).resolve().parent

# 有序列表项：`1. xxx` / `1、xxx`（开头是数字 + 点/顿号 + 空格）
_STEP_RE = re.compile(r"^\d+[\.、]\s*(.*)$")


@dataclass
class Skill:
    """一个已加载的业务技能"""

    name: str              # 技能唯一标识（来自 frontmatter）
    description: str       # 一句话说明
    when_to_use: List[str]  # 触发关键词列表（匹配器按子串匹配）
    steps: List[str]       # 固定执行步骤（来自 `## 执行步骤` 章节）
    full_text: str         # SKILL.md 全文（注入 executor 作为执行上下文）
    path: Path             # SKILL.md 文件路径


def _parse_frontmatter(text: str) -> tuple[Dict[str, str], str]:
    """解析 frontmatter。

    返回 (meta, body)。文件头不是 `---` 或解析异常时返回 ({}, text)，
    即视为没有 frontmatter，正文就是全文。
    """
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    meta_text, body = parts[1], parts[2]
    meta: Dict[str, str] = {}
    for line in meta_text.strip().splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    return meta, body.lstrip("\n")


def _extract_steps(body: str) -> List[str]:
    """提取 `## 执行步骤` 章节下的有序列表项。

    只收集标题为「执行步骤」的章节内的 `1. xxx` 行；
    `## 验证步骤` 等其它章节的有序列表不会被误收集。
    章节内的说明性文字（非编号行）跳过。
    """
    steps: List[str] = []
    in_section = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("##"):
            # 进入/离开「执行步骤」章节（后续三级标题 `###` 不影响）
            in_section = stripped == "## 执行步骤"
            continue
        if in_section:
            m = _STEP_RE.match(stripped)
            if m:
                content = m.group(1).strip()
                if content:
                    steps.append(content)
    return steps


class SkillLoader:
    """技能加载器：扫描 skills 根目录下的所有 SKILL.md 并缓存。"""

    def __init__(self, root: Optional[Path] = None):
        self.root = root or SKILLS_ROOT
        self._cache: Optional[List[Skill]] = None

    def load_all(self) -> List[Skill]:
        """返回所有已加载技能（懒加载 + 缓存，首次调用时扫描）。"""
        if self._cache is None:
            self._cache = self._scan()
        return self._cache

    def get(self, name: str) -> Optional[Skill]:
        """按技能名查找（未命中返回 None）。"""
        for skill in self.load_all():
            if skill.name == name:
                return skill
        return None

    def refresh(self) -> None:
        """清空缓存，下次 load_all 重新扫描（新增/修改 SKILL.md 后调用）。"""
        self._cache = None

    def _scan(self) -> List[Skill]:
        """扫描 `app/skills/*/SKILL.md`，逐个解析；解析失败的文件跳过并告警。"""
        skills: List[Skill] = []
        for skill_dir in sorted(self.root.glob("*/")):
            if not skill_dir.is_dir():
                continue
            skill_file = skill_dir / "SKILL.md"
            if not skill_file.is_file():
                continue
            skill = self._parse(skill_file)
            if skill:
                skills.append(skill)
                logger.info(
                    f"加载技能: {skill.name} ({skill.path.name}), "
                    f"步骤 {len(skill.steps)} 个, 关键词 {len(skill.when_to_use)} 个"
                )
        return skills

    def _parse(self, path: Path) -> Optional[Skill]:
        """解析单个 SKILL.md；缺 name 或读取失败时返回 None。"""
        try:
            text = path.read_text(encoding="utf-8")
        except Exception as e:
            logger.error(f"读取 SKILL.md 失败: {path}, 错误: {e}")
            return None

        meta, body = _parse_frontmatter(text)

        name = meta.get("name", "").strip()
        if not name:
            logger.warning(f"SKILL.md 缺少 frontmatter name 字段, 已跳过: {path}")
            return None

        return Skill(
            name=name,
            description=meta.get("description", "").strip(),
            when_to_use=[k.strip() for k in meta.get("when_to_use", "").split(",") if k.strip()],
            steps=_extract_steps(body),
            full_text=text,
            path=path,
        )


# 全局单例（与项目其他 service 保持一致）
skill_loader = SkillLoader()
