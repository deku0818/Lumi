---
name: workflow
description: |
  用一段确定性的 Python 脚本编排一群子代理（多 agent 编排）。

  适合三类**结构性困难**：要全面（分解问题并行覆盖）、要有把握（多视角独立验证后再下结论）、规模超出一个上下文（大范围审计 / 迁移 / 扫描）。单点查询、琐碎改动、纯对话**不要**用它。

  ## 何时允许使用（重要）
  本工具会扇出大量子代理、开销很大，**只在用户明确选择编排时才调用**，满足以下任一：
  - **Ultra 档位已开启**（你会在对话里收到「Ultra 编排模式已开启」的 system-reminder）；
  - **用户用自己的话明确要求**用 workflow / 多 agent 编排（如「用 workflow 审一遍」「并行扇出子代理」）。

  否则，即使任务看起来很适合编排，也**不要主动调用**——正常处理任务即可；若任务确实庞大，可一句话建议用户「开启 Ultra 档位后我能并行拆解处理」，由用户决定。

  ## 执行模型
  后台执行：本工具立即返回 task_id，脚本在后台跑完后你会自动收到 task-notification。脚本里的「干活」单元是 `agent()`——派一个独立上下文的 LLM 子代理（语义推理），子代理可用 bash / filesystem 等工具自主完成确定性的活。并发上限约 min(16, CPU-2)，传多少都收，只是排队。

  ## 脚本怎么写
  脚本是一段 Python 代码片段（**不要**再包 `def` / `async def`，引擎会自动包进 async 函数），可直接用顶层 `await` 和 `return`。`return` 的值就是最终产物，形状由你定。两种给法：内联 `script`，或把脚本写进文件、用 `path` 引用（版本化、可复核，**优先于** `script`）。
  注入的钩子（只在脚本内可用）：

  - `agent(prompt, *, schema=None, label=None, phase=None, agent_name=None)` —— async，派一个 LLM 子代理。给了 `schema`（JSON Schema dict）就强制结构化输出、返回校验过的 dict；否则返回子代理最终文本。`agent_name` 指定 .lumi/agents 里的具名子代理，缺省用通用子代理。**注意**：schema 模式下若子代理多次填不对结构会被中止，此时返回 `None`——拿来索引前先判空（`r or {}`、`[x for x in r if x]`）。
  - `parallel(thunks)` —— async，**屏障**：并发跑一组无参 thunk（`lambda: agent(...)`），等全部完成才返回列表；失败项落 `None`，用前 `[x for x in r if x]` 过滤。
  - `pipeline(items, stage1, stage2, ...)` —— async，**无屏障**：每个 item 独立穿过所有 stage，谁先走完谁先往下。stage 收 `(prev, item, idx)`（按形参个数截取，`lambda d: ...` 也行），第一个 stage 的 prev 就是 item。**默认优先用 pipeline**，只有 stage N 真需要 N-1 的全部结果时才用 parallel。
  - `phase(title)` / `log(msg)` —— 标记阶段 / 发进度。`args` —— 你传入的输入值。

  ## 关键规则
  - thunk 必须是**无参函数**：`lambda: agent(...)`，不是 `agent(...)`（后者会立即执行，parallel 失去调度权）。
  - 脚本本身不能 `import` / 读写文件（它只是编排骨架）；干活靠 `agent()`——确定性的重活让子代理用 bash / filesystem 等工具去做。
  - 让结果可信：每条发现派独立 skeptic 用 `schema` 对抗式验证（prompt 里要求"默认证伪，须独立核对源码"）。

  ## 规范范式（Review：维度→找问题→对抗验证→汇总）
  ```python
  DIMENSIONS = [
      {"key": "security", "prompt": "审查 X 的安全问题，读 a.py b.py，只报有证据的问题"},
      {"key": "correctness", "prompt": "审查 X 的正确性 bug ..."},
  ]
  FINDINGS = {"type": "object", "properties": {"findings": {"type": "array",
      "items": {"type": "object", "properties": {
          "title": {"type": "string"}, "file": {"type": "string"},
          "severity": {"type": "string"}, "detail": {"type": "string"}},
          "required": ["title", "file", "severity", "detail"]}}}, "required": ["findings"]}
  VERDICT = {"type": "object", "properties": {
      "is_real": {"type": "boolean"}, "reason": {"type": "string"}},
      "required": ["is_real", "reason"]}

  phase("Review")
  reviewed = await pipeline(
      DIMENSIONS,
      lambda d: agent(d["prompt"], schema=FINDINGS, label=d["key"], phase="Review"),
      lambda r: parallel([
          (lambda f=f: agent(
              f"对抗式验证这条发现，默认证伪除非能独立核对源码确认：{f}",
              schema=VERDICT, phase="Verify"))
          for f in (r or {}).get("findings", [])
      ]),
  )
  confirmed = [f for stage in reviewed if stage
               for f in stage if f and f.get("is_real")]
  return {"confirmed": confirmed, "count": len(confirmed)}
  ```
  （注意 `lambda f=f:` 的默认参数绑定，避免闭包都引用最后一个 f。）
---
