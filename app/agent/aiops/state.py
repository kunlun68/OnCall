"""
通用 Plan-Execute-Replan 状态定义
"""

from typing import List, TypedDict, Annotated
import operator


class PlanExecuteState(TypedDict):
    """Plan-Execute-Replan 状态"""

    # 用户输入（任务描述）
    input: str

    # 执行计划（步骤列表）
    plan: List[str]

    # 已执行的步骤历史
    # 使用 operator.add 实现追加式更新（而非覆盖）
    past_steps: Annotated[List[tuple], operator.add]

    # 最终响应/报告
    response: str

    # 命中的业务技能（Skill）：名称 + 全文上下文。
    # 由 Planner 在 skill 匹配命中时写入，Executor/Replanner 据此严格执行。
    # 为空表示未命中 skill，走知识库 RAG 兜底流程。
    skill_name: str
    skill_context: str
