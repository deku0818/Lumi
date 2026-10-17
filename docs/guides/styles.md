# 内置资源与用户覆盖

Lumi 的通用提示词、子 Agent 和技能直接放在 `lumi/styles/` 下，所有会话共用，不再区分风格。

## 内置资源

- `prompts/COMPACT.md`：通用上下文压缩提示词。
- `agents/explore.md`、`agents/general-purpose.md`：探索与通用子 Agent。
- `skills/lumi-config/`：Lumi 配置技能。

系统提示词 `SOUL.md`、`AGENTS.md` 由用户在全局或项目 `.lumi/prompts/` 中提供；两文件按 `SOUL → AGENTS` 顺序直接拼接，缺失的部分跳过。框架随后追加项目说明按需读取规则，项目根及子目录的 `AGENTS.md` 正文由模型通过工具读取，与这里的提示词配置分开。

## 加载优先级

三类资源均按同名覆盖：

```
内置 lumi/styles/ < 全局层 ~/.lumi/ < 项目层 <项目>/.lumi/
```

桌面端 / `lumi serve` 的全局层固定在用户数据目录；`lumi -p` 的进程配置目录按当前目录向上发现。显式设置 `LUMI_CONFIG_DIR` 时使用指定目录。

压缩调用当前未传入项目目录，COMPACT 只读取进程配置层和内置版本；项目单独配置的 COMPACT 暂不参与该调用。

## 用户覆盖

用户无需修改内置文件，在全局或项目 `.lumi/` 下放置同名文件即可覆盖：

```
.lumi/
├── prompts/
│   ├── SOUL.md
│   ├── AGENTS.md
│   └── COMPACT.md
├── agents/
│   └── explore.md
└── skills/
    └── my-skill/SKILL.md
```

提示词为空或只有 frontmatter 时视同未配置，继续查找下一层。技能与子 Agent 定义需包含有效的 `name`、`description` 等 frontmatter，加载规则见 [子 Agent 指南](agents.md)。

项目主页提供提示词、技能和子 Agent 的查看与编辑。编辑和删除只作用于项目层；内置与全局资源可先复制到项目再修改。删除项目覆盖后，同名的低层资源重新生效。
