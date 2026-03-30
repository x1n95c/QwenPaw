---
name: acp_delegation
description: Use this skill when a task is a good fit for one-shot delegation to an external runner such as opencode, qwen, or gemini via `spawn_agent`. Use it to decide whether delegation is appropriate, choose the right runner, verify tool and runner availability, write a complete self-contained delegation prompt, and handle common runner failures honestly. | 当任务适合通过 `spawn_agent` 一次性委派给 opencode、qwen、gemini 等外部 runner 时使用；用于判断是否该委派、选择 runner、确认工具和 runner 可用、编写完整自包含的 delegation prompt，并如实处理常见 runner 失败
metadata: { "builtin_skill_version": "1.0", "copaw": { "emoji": "🛰️" } }
---

# ACP Delegation

把 ACP delegation 当作“一次性外包执行”能力。
只有当任务边界清晰、输入能一次写全、结果能直接回收进当前会话时，才使用它。

## 什么时候用

- 需要外部 runner 做一次性代码分析、review、实现建议或定向调研
- 用户明确指定要用 `opencode`、`qwen`、`gemini` 之一
- 任务可以写成完整 prompt，不依赖多轮追问
- 你已经能确认 `spawn_agent` 和目标 runner 可用

## 什么时候不要用

- 任务很简单，你自己直接完成更快
- 任务依赖持续 session、多轮往返或长期状态
- 输出强依赖当前会话里的隐式上下文
- 你还没确认 `spawn_agent` 或 runner 可用
- 用户只是提到某个 runner 名字，但你还没核实能不能用

## 先选 runner

按这个顺序决策：

1. 优先选择用户明确指定的 runner
2. 否则选择当前环境里最匹配任务的 runner
3. 若没有明显匹配，就不要强行委派
4. 若用户指定的 runner 不可用，直接说明不可用，不要伪造调用

## 调用前检查

在调用 `spawn_agent` 前，只检查两类前提：

1. `spawn_agent` 工具已启用
2. 目标 runner 在当前环境可用

优先查看当前 agent 的工具配置和 `agent.json`。runner 可能来自：

- 代码内置 runner 预设
- 当前 agent 的 `agent.json` 覆盖配置

如果需要检查 runner 配置，关注：

```json
{
  "spawn_agent": {
    "runners": {
      "<agent_type>": {
        "enabled": true
      }
    }
  }
}
```

如果你无法确认可用性：

- 不要盲目调用 `spawn_agent`
- 先告诉用户当前 runner 尚未就绪，或暂时无法确认可用性

## 怎么写 delegation prompt

每次 delegation prompt 都要写成完整、自包含的一次性任务说明。至少写清楚：

- 目标：希望 runner 完成什么
- 范围：涉及哪些文件、目录、模块、接口或限制范围
- 约束：哪些不能改、不能假设、不能忽略
- 输出：希望它返回什么，格式是什么

推荐顺序：

1. 先说明任务目标
2. 再限定范围和约束
3. 最后说明输出格式

不要把当前会话里的隐式背景留给对方猜。

## 推荐输出要求

按任务类型要求外部 runner 返回明确结果：

- 代码分析：问题列表 + 风险判断 + 涉及文件
- review：按严重程度列 findings
- 实现建议：修改思路 + 受影响文件 + patch 建议
- 调研：结论 + 依据 + 未确认项

## 使用约束

这是一次性 delegation。

- 不要假设外部 runner 会记住之前的对话
- 如果后续还要再次委派，重新提供完整上下文
- 如果任务天然依赖多轮往返，就不要用这个 skill

## 最小工作流

1. 判断任务是否适合一次性委派
2. 选择合适 runner
3. 确认 `spawn_agent` 和 runner 可用
4. 写完整 delegation prompt
5. 调用 `spawn_agent`
6. 把结果回收进当前会话并继续推进

## 失败处理

如果 `spawn_agent` 返回失败、未认证、环境冲突或其他 runner 级错误：

- 先把失败原因原样解释给用户
- 明确指出是外部 runner 失败，不要伪装成委派已经成功
- 如果错误提示要求安装、登录、认证或修复环境配置，就直接告诉用户先处理这些前置条件
- 不要把失败说成“我来帮你继续完成”并偷偷切换成自己本地执行
- 不要在未经用户同意的情况下，自动改用另一个 runner
- 不要在未经用户同意的情况下，自动退化成本地工具来冒充 delegation 结果

只有在用户明确同意后，才能改成：

- 使用另一个已配置 runner
- 改为你自己直接完成任务
- 改为使用本地工具继续分析

常见可操作提示包括：

- runner 未安装或不在 PATH
- runner 未认证
- 环境变量冲突
- 执行超时
- 非零退出
