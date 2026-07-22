# 风格系统架构

风格系统的内部加载机制。用户使用指南见 [`docs/guides/styles.md`](../guides/styles.md)。

---

## 目录结构

每种风格可含 `prompts/`、`agents/`、`skills/` 三类子目录，三者均为可选：

```
lumi/styles/
├── default/              # 默认风格：不带 prompts/，提示词全部来自 .lumi/prompts/
│   ├── agents/           # （当前为空）
│   └── skills/           # （当前为空）
└── code/
    ├── prompts/
    │   ├── SOUL.md
    │   └── AGENTS.md
    └── agents/
        ├── explore.md
        └── plan.md
```

---

## 加载机制

### 配置三层（skills / agents 共用）

层序单一事实源是 `loader.config_layers(subdir, project_dir)`，优先级从低到高、逐层同名覆盖：

1. **style 内置**：`lumi/styles/{style}/{subdir}/`（只读，随发布）
2. **全局层**：进程配置目录 `{config_dir}/{subdir}/`——`lumi serve` 恒钉在 `~/.lumi`（serve 是多项目网关，全局层不随启动目录漂移）；`lumi -p` 单项目 CLI 仍走 cwd 发现链
3. **项目层**：`<项目>/.lumi/{subdir}/`——随会话绑定的项目传入（`load_skills/load_agents` 的 `project_dir` 参数），只对该项目的会话生效

消费方全部走这一份层序：`load_skills`/`load_agents`、变更检测器（`FileSetChangeDetector` 按 子类×项目 一实例，digest 只扫可变的全局+项目两层）、桌面项目主页的 UI 聚合（`gateway/project_config.py`，来源标签 `builtin/global/project` 与本层序一一对应——UI 展示的「生效」即会话实际加载的）。

### 提示词解析链（load_prompt / resolve_prompt）

层序在 `LumiConfig.prompt_layers`，命中层判定在 `resolve_prompt`（`load_prompt` 与项目主页共用）：

1. `<项目>/.lumi/prompts/{name}.md` —— 项目层，优先级最高
2. `{config_dir}/prompts/{name}.md` —— 全局层
3. `lumi/styles/{style}/prompts/{name}.md` —— 风格内置（风格无 `prompts/` 目录即跳过）
4. `lumi/prompts/{name}.md` —— 框架内置兜底

**空文件（或只剩 frontmatter）视同不存在**，继续往下找——否则一个被误清空的提示词会静默生效。各层都没有有效内容才返回 `None`。

**系统提示词（SOUL.md / AGENTS.md）**：两文件各走一次上述解析链，按 `SOUL → AGENTS` 顺序以 `\n\n` **直接拼接**（不做 XML 包裹），任一缺失则跳过该段；都没有时 `load_system_prompt` 返回空串，agent 以无系统提示词运行（不 fail-loud）。

**SUMMARY.md（压缩用）**：框架内置了兜底（`lumi/prompts/SUMMARY.md`），故未配置也能正常压缩，各调用点不再有「未配置 SUMMARY」的错误分支。第四层目前只放这一份——它是运行时基础设施而非风格表达，放进某个 style 会让其它 style 拿不到。

### 工具描述

内置工具的 description 直接写在各工具函数的 docstring 里，由 `registry._collect_tools_from_module` 在加载时统一 `inspect.cleandoc` 抹掉缩进。工具描述不再经 style / `.lumi/` 配置覆盖。

### 定义文件校验（validate_definition）

技能 `SKILL.md` 与 agent `*.md` 在**写入时**（桌面项目主页的编辑/新建）校验 frontmatter：须含 `name`/`description` 且 `name` 与目录名/文件名一致——加载侧对坏文件静默跳过，不在写入侧拦住会产生「写成功却列不出、也删不掉」的幽灵文件；name 一致性则保证 UI 按文件身份的 CRUD 与运行时按 frontmatter name 的归并覆盖不背离。

---

## 关键实现

- **`active_style_for(project_dir)`**（`LumiConfig`）：某项目会话生效的风格，CLI override > 项目 `.lumi/config.json` 的 `style` > 进程配置 > "default"；无项目参数的 `active_style` 保持进程级语义
- **`list_styles()`**（`lumi/styles/__init__.py`）：扫描 `lumi/styles/` 子目录，列出所有可用风格
- **缓存友好**：工具定义在启动时一次性加载，运行时不变，保持 Prompt Caching 前缀稳定
