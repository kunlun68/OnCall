# 更新日志：SOP 从知识库升级为 Skill 业务处理

> 功能：把 AIOps 的"处理文档 / SOP"从知识库里的**被动检索文本**，升级为**主动匹配、严格执行的业务技能（Skill）**。系统按告警类型自动匹配技能，命中后按技能固定步骤执行；未命中才回退到知识库 RAG 兜底。
> 日期：2026-08-09

---

## 一、背景：SOP 之前是怎么用的，问题在哪

系统里处理告警的 SOP（CPU 高、服务不可用等）一直存在**知识库（Milvus）**里。诊断时的流程是这样的：

```
用户触发诊断
  → Planner 用 retrieve_knowledge 工具（RAG + 重排）从知识库检索出相关 SOP 片段
  → 把片段当"参考文本"塞进 Planner 的 prompt
  → LLM 看着这些文本，自己决定怎么制定计划、执行
```

**问题在于**：RAG 检索出来的是**一段参考文字**，对 LLM 只是"建议"。它不强制：

- SOP 里明明写了"步骤2 用 query_logs，地域 ap-guangzhou，主题 system-metrics，查 `level:ERROR OR cpu_usage:>80`"，但 LLM 可能不按这个来——记错参数、漏掉步骤、自创流程。
- 处理结果**不可控、不可复制**。同一类告警，今天和明天的处理方式可能不一样。

这就是"SOP 存在知识库里"的局限：**知识库只负责"给你看"，不负责"逼你做"**。

## 二、Skill 是什么：从"参考"到"执行"

Skill（业务技能）把 SOP 变成**结构化的流程定义**，系统主动做三件事：

| | 知识库 RAG（旧） | Skill 业务（新） |
|---|---|---|
| 获取方式 | 被动检索，向量相似度召回 | 主动匹配，按告警名/关键词精确命中 |
| 对 LLM 的约束 | 参考文本，可听可不听 | 固定步骤，严格执行 |
| 步骤来源 | LLM 自由规划 | 技能定义固化 |
| 失败兜底 | 无 | 匹配不到 → 回退 RAG |

一句话：**RAG 把 SOP 给你看，Skill 让系统照着 SOP 做。**

---

## 三、SKILL.md 长什么样

每个技能一个目录，放在 `app/skills/` 下，内含一个 `SKILL.md`：

```
app/skills/
  cpu_high_usage/SKILL.md         # CPU 高告警技能
  service_unavailable/SKILL.md    # 服务不可用技能
```

文件分两部分：

### 1. frontmatter（文件头 `---` 包裹的元信息）

```markdown
---
name: handle_cpu_high_usage
description: 处理 CPU 使用率过高告警（HighCPUUsage）的标准诊断与处理流程
when_to_use: HighCPUUsage, CPU使用率, CPU 使用率, cpu 高, CPU 告警, 性能排查
---
```

三个字段，各有用途：

| 字段 | 用途 |
|------|------|
| `name` | 技能唯一标识。**缺失则整个 skill 被忽略**（安全校验） |
| `description` | 一句话说明，用于日志和调试 |
| `when_to_use` | 逗号分隔的**触发关键词**，匹配器用它判断"这个任务归不归我管" |

### 2. 正文：`## 执行步骤` 是灵魂

```markdown
## 执行步骤

1. 使用 `get_current_time` 获取当前时间，作为后续日志查询的时间范围基准
2. 使用 `query_logs` 查询系统日志：地域 `ap-guangzhou`，日志主题 `system-metrics`，最近 30 分钟，条件 `level:ERROR OR cpu_usage:>80`
3. ...

## 常见原因分析
- **死循环**：单进程 CPU≈100%，日志有重复错误堆栈 → 重启+回滚+通知开发
- **流量突增**：多进程均匀升高，请求量增加 → 扩容/限流
```

**`## 执行步骤` 下的有序列表 = 系统执行时的固定计划**。它是这个机制的核心——loader 只认这个标题下的 `1. xxx` 行，`## 验证步骤` 等其它章节的列表不会被误当成执行步骤。

**注意**：执行步骤里要写清"用哪个工具、什么参数、什么目的"，因为严格模式下 Executor 就是照它执行的。

---

## 四、两个新组件：loader 和 matcher

### 1. `app/skills/loader.py` — 把 SKILL.md 变成 Python 对象

`SkillLoader` 做的事：

```
扫描 app/skills/*/SKILL.md
  → 解析 frontmatter（name/description/when_to_use）
  → 提取 `## 执行步骤` 下的编号列表 → steps
  → 保存全文 → full_text（注入 executor 用）
  → 缓存（懒加载，首次调用才扫描）
```

解析出的是 `Skill` 数据类，五个字段：

```python
@dataclass
class Skill:
    name: str
    description: str
    when_to_use: List[str]   # 关键词列表
    steps: List[str]         # 固定执行步骤
    full_text: str           # SKILL.md 全文
    path: Path
```

两个实现细节：

- **frontmatter 用轻量手写解析，不引入 pyyaml**：`---` 分隔 + 每行 `key: value`。因为字段就三个固定的，手写十几行足够，少一个运行时依赖。
- **`_extract_steps` 只认 `## 执行步骤` 章节**：遍历行，遇到 `##` 标题判断是不是"执行步骤"；章节内的 `1. xxx` 行才收集。`## 验证步骤` 的有序列表不会被误收（验证过了）。

### 2. `app/skills/matcher.py` — 判断任务归哪个技能

`SkillMatcher.match(text)` 的逻辑：

```
对每个技能：
  统计 when_to_use 里有多少个关键词出现在 text 里（大小写不敏感的子串匹配）→ 得分
取得分最高的技能；得分必须 > 0 才算命中
```

**为什么用规则匹配，不用 embedding 语义匹配？**

- 技能数量少（十几个以内），触发场景是**告警名 + 中文短语**（`HighCPUUsage`、`服务不可用`），子串匹配足够精确可靠。
- 语义匹配需要额外调 embedding 服务，引入运行时依赖和失败点。
- 规则匹配是纯本地字符串运算，即时返回、零成本。

代价是：技能数量涨到几十上百、触发词覆盖不了所有说法时，规则匹配会力不从心——那时再升级语义匹配不迟。

---

## 五、接入链路：四处改动怎么串起来

改动核心是 Plan-Execute-Replan 工作流（`app/agent/aiops/`）。四个节点各改一处：

```
① aiops_service.diagnose   注入活跃告警名 → 让 planner 有"告警名"可匹配
② planner                  Skill 优先：命中 → 固化步骤，跳过 LLM 规划
③ executor                 Skill 模式下注入流程全文 → 严格执行
④ replanner                Skill 模式下禁止 replan → 机械执行完所有步骤
```

### ① `aiops_service.py` — 为什么先注入活跃告警？

诊断入口 `/api/aiops` 的任务描述是**固定的**："诊断当前系统是否存在告警……"。它不含具体告警名，Planner 拿到它做关键词匹配什么都匹配不到。

所以 `diagnose()` 先查一次 Prometheus 的活跃告警，把告警名拼进任务描述：

```
（原任务描述）…
当前活跃告警: HighCPUUsage
```

这样 Planner 的 matcher 就能按 `HighCPUUsage` 精确命中 cpu 技能。

**优雅降级**：Prometheus 挂了或没有告警时，注入为空字符串，走原 RAG 兜底——行为与改动前完全一致。

### ② `planner.py` — Skill 优先，命中就固化

Planner 原本做两件事：RAG 检索经验 → LLM 生成计划。现在在**最前面**插入技能匹配：

```python
skill = skill_matcher.match(input_text)
if skill is not None:
    return {
        "plan": skill.steps,          # 技能步骤直接成为执行计划
        "skill_name": skill.name,
        "skill_context": skill.full_text,
    }
# ↓ 未命中才走原来的 RAG + LLM 规划
```

**关键点**：命中技能时**不调 LLM 规划**，直接把 `skill.steps` 当计划。步骤怎么走、顺序怎么排，由技能定义说了算，不再让 LLM 自由发挥。这正是"严格模式"的第一处落地。

### ③ `executor.py` — 每步执行都带着流程全文

Executor 原本每步只告诉 LLM"请执行任务: {task}"。现在如果状态里有 `skill_context`，就把技能全文注入 SystemMessage：

```
当前任务属于标准处理流程（handle_cpu_high_usage），以下是该流程的完整定义。
请严格按流程中规定的方法和参数执行当前步骤：
【skill 全文】
```

每一步的 LLM 都能看到：这一步该用什么工具、什么参数、下一步该干什么、常见原因怎么判断。**参数以流程规定为准**，这就是第二处落地。

### ④ `replanner.py` — 规范流程不可改序

Replanner 原本有 `continue / replan / respond` 三个决策，`replan` 允许它调整计划。但在 Skill 模式下**禁止 replan**：

```python
if state.get("skill_name"):
    if plan:
        return {}              # continue：继续执行下一个步骤
    return await _generate_response(state, llm)  # 步骤执行完 → 生成最终响应
```

理由：技能步骤是经过验证的标准流程，Replanner 随意改序会破坏流程规范。**技能内部的分支决策**（比如"是流量问题还是代码 bug"）由 Executor 每步结合技能的分支章节自己判断，不需要 Replanner 在计划层面动手。

---

## 六、状态扩展：`state.py`

LangGraph 的 `TypedDict` 是**严格状态**——节点返回未声明的 key 会报错。所以 `PlanExecuteState` 加了两个字段：

```python
skill_name: str     # 命中的技能名（空 = 未命中）
skill_context: str  # 技能全文（注入 executor）
```

Planner 命中时写入，Executor 读它注入上下文，Replanner 靠它判断是否进入 Skill 模式。

---

## 七、如何新增一个技能（三步）

不需要改任何 Python 代码，在 `app/skills/` 下建目录即可：

1. **建目录**：`app/skills/磁盘空间不足/SKILL.md`（目录名随意，SKILL.md 固定）
2. **写 frontmatter**：`name` 唯一、`description` 说明、`when_to_use` 放告警名 + 中文触发短语
3. **写 `## 执行步骤`**：编号列表，每步写清工具、参数、目的

Skill 加载器启动时自动扫描新目录。改完已加载的 SKILL.md 需要调 `skill_loader.refresh()` 清缓存（默认懒加载 + 缓存，避免每次请求都读盘）。

**关键词选取注意**：`when_to_use` 用**告警名 + 精确中文短语**（`HighDiskUsage`、`磁盘空间不足`），别用泛词（如"性能""故障"），否则会误命中别的场景。匹配规则是"命中数最多者胜"，但精准的词永远好过泛泛的词。

---

## 八、验证结果

23 项低成本验证全部通过（不跑 LLM、不依赖 Milvus 写入）：

- **loader**：扫描到 2 个技能；frontmatter 三字段解析正确；`## 执行步骤` 提取 6 步；`## 验证步骤` 的有序列表**未**被误收集；按名查找/未命中返回 None
- **matcher**：`HighCPUUsage` → cpu 技能；`服务不可用，健康检查失败` → service 技能；`今天天气怎么样` → 未命中（None）
- **planner 严格模式**：真实调用命中分支（命中时不碰 RAG/MCP，无外部依赖），返回 `plan=skill.steps`（6 步）、`skill_name`、`skill_context` 全部正确
- **aiops_service**：Prometheus 不可用时 `_fetch_active_alert_hint` 返回空串，优雅降级为 RAG

---

## 九、设计取舍与后续

1. **知识库 SOP 为什么不删？** 用户决策"Skill 优先 + RAG 兜底"。知识库保留参考资料（非流程类文档），技能匹配不到时 RAG 仍能找到；两个示范 skill 与知识库 SOP 内容重复是预期行为。
2. **匹配从规则升级为语义**：技能超过几十个、关键词覆盖不了自然语言表达时，再引入 embedding 匹配。
3. **chat 链路暂不接 Skill**：本次只接 AIOps 诊断链路（`/api/aiops`）。chat 问答（`/api/chat`）仍是 RAG 回答，符合"处理文档/SOP"所在的场景定位。
4. **技能步骤里的工具依赖**：SKILL.md 写的 `query_logs` 等工具依赖 MCP 服务。工具不可用时 Executor 会如实记录该步失败，不静默编造——这与系统"失败要看得见"的原则一致。
