"""技能匹配器模块 - 根据用户输入匹配最相关的业务技能

采用**规则匹配**（关键词子串）而非 embedding 语义匹配：
    - 技能数量少（十几个以内），告警名 + 中文触发短语用子串匹配足够可靠；
    - 不引入对 embedding 服务的运行时依赖，匹配即时、无外部调用。

匹配策略：对每个技能 `when_to_use` 里的关键词，在输入文本中做大小写不敏感的
子串匹配并计数（得分）；取得分最高的技能返回，得分必须 > 0 才视为命中。
"""

from typing import Optional

from loguru import logger

from app.skills.loader import Skill, skill_loader


class SkillMatcher:
    """技能匹配器 - 输入一段任务描述，返回最相关的技能（未命中返回 None）"""

    def match(self, text: str) -> Optional[Skill]:
        """根据任务描述匹配业务技能。

        Args:
            text: 用户任务描述 / 诊断任务文本

        Returns:
            Optional[Skill]: 得分最高且 > 0 的技能；否则 None（走 RAG 兜底）
        """
        if not text or not text.strip():
            return None

        lowered = text.lower()
        best: Optional[Skill] = None
        best_score = 0

        for skill in skill_loader.load_all():
            # 统计该技能有多少个关键词命中了输入文本（大小写不敏感）
            score = sum(1 for kw in skill.when_to_use if kw and kw.lower() in lowered)
            if score > best_score:
                best, best_score = skill, score

        if best is not None:
            logger.info(f"技能匹配: 命中 '{best.name}'（{best_score} 个关键词命中）")
        return best if best is not None else None


# 全局单例
skill_matcher = SkillMatcher()
