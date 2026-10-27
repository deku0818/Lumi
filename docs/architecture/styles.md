# 内置资源加载架构

用户使用指南见 [内置资源与用户覆盖](../guides/styles.md)。

## 目录结构

```
lumi/styles/
├── prompts/
│   └── COMPACT.md
├── agents/
│   ├── general-purpose.md
│   └── explore.md
└── skills/
    └── lumi-config/
        ├── SKILL.md
        └── references/
```

`styles/` 只作为内置资源目录，不再包含风格子目录或风格选择逻辑。

## 加载机制

### 技能与子 Agent

层序单一事实源是 `loader.config_layers(subdir, project_dir)`，优先级从低到高：

1. **内置层**：`lumi/styles/{subdir}/`，来源标签 `builtin`，只读、随发布。
2. **全局层**：进程配置目录 `{config_dir}/{subdir}/`，来源标签 `global`。
3. **项目层**：`<项目>/.lumi/{subdir}/`，来源标签 `project`，仅在传入项目目录时存在。

`load_skills` / `load_agents` 逐层同名覆盖；同名资源以解析后的 `name` 为身份。`FileSetChangeDetector` 按子类和项目缓存加载结果，依据定义文件的路径、mtime 和大小感知变更。桌面项目主页 `gateway/project_config.py` 消费相同层序，编辑、删除和复制只作用于项目层。

技能正文只在调用时按当前上下文去重：`/技能名` 与 `skill` 工具共用 `render_skill`，只有当前消息里实际保留相同的完整正文（含资源目录）时才返回简短的「已加载」提示；正文变更、仅剩摘要或截断结果时，再次调用会返回全文。不维护额外的会话缓存，恢复会话和时间旅行直接沿用各自的消息历史。压缩不做任何技能专用处理，不自动补回正文或改写技能消息；需要时由模型读取文件，或在下一次调用时重新加载。技能命令通过 `<command-name>` 识别，正文放在独立的 `<skill-content>` 块中。并行调用在截断/卸载后只比较本批结果，不重复加载配置和扫描历史，保留每个 `tool_call_id` 的响应；只缩短新结果，不改历史消息。

`lumi serve` 的全局层固定在用户数据目录，不随启动目录漂移；`lumi -p` 通过 `ConfigDiscovery` 选择进程配置目录：显式目录 > `LUMI_CONFIG_DIR` > 当前目录的 `.lumi/` > 用户级 `.lumi/` > 当前目录的 `.lumi/`（尚不存在时）。不遍历父目录，判断依据是目录是否存在，而非 `config.json` 是否存在。

资源目录提示使用 `<skill-resources>技能资源位于 ... 目录下。</skill-resources>`，在技能命令中是与 `<skill-content>` 同级的独立内容块，首次全文和去重后的短提示都会附带该标签。

### 提示词

层序在 `LumiConfig.prompt_layers`，`resolve_prompt` 从高到低取第一个有效文件：

1. `<项目>/.lumi/prompts/{name}.md`，来源 `project`。
2. `{config_dir}/prompts/{name}.md`，来源 `global`。
3. `lumi/styles/prompts/{name}.md`，来源 `builtin`。

空文件或只有 frontmatter 时继续向下查找，各层都没有有效正文才返回 `None`。`load_prompt` 和项目主页的 SOUL 标签共用此判定。

**系统提示词**：仅加载 `SOUL.md`，剥掉 frontmatter 后直接使用正文；未配置时返回空串。框架仍追加项目说明按需读取规则。`prompts/AGENTS.md` 不再加载；项目主页的 AGENTS 标签直接读写根目录 `AGENTS.md`，不回退到全局或内置层，正文仍由模型按需读取。内置目录当前不提供 SOUL 文件。

**压缩提示词**：`COMPACT.md` 使用统一通用版，由内置层兜底。压缩调用当前未传入项目目录，只读取进程配置层和内置版本。

### 定义文件校验

项目主页写入技能 `SKILL.md` 与 agent `*.md` 时经 `validate_definition` 校验：frontmatter 须含非空 `name` / `description`，且 `name` 与目录名或文件名一致。

运行时加载侧的要求更宽：缺少有效 frontmatter 或 `name` 时跳过，`description` 缺失按空串处理，不检查名称与路径一致。因此手工编辑文件应遵守项目主页的完整要求；不能依赖加载侧拒绝所有无效覆盖。

### 工具描述

内置工具的 description 直接写在工具函数的 docstring 中，由 `registry._collect_tools_from_module` 加载时统一 `inspect.cleandoc` 抹掉缩进，不经提示词目录覆盖。
