"""技能注册表 - 业务技能（Skill）的加载与匹配

Skill 是结构化的标准业务流程定义（SKILL.md），区别于知识库里的 SOP 参考文本：
- 知识库 RAG：把 SOP 当"参考文本"检索出来，LLM 自由决定怎么用；
- Skill：主动匹配，命中后把技能步骤固化为执行计划，严格执行。

对外导出：
    Skill         技能数据结构
    skill_loader  加载器（扫描/解析/缓存）
    skill_matcher 匹配器（按任务描述选技能）
"""

from app.skills.loader import Skill, skill_loader
from app.skills.matcher import skill_matcher

__all__ = ["Skill", "skill_loader", "skill_matcher"]
