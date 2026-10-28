# 临时旧数据迁移

`lumi migrate` 是过渡版本提供的一次性迁移命令。正常运行只使用当前格式，启动时不会调用迁移，也不会自动备份。

```bash
lumi migrate --check
lumi migrate --apply
```

不带参数等同 `--check`。先停止桌面后端或 `lumi serve`，再执行 `--apply`；完成后重新启动。命令读取当前机器的数据根（默认 `~/.lumi`，可通过 `LUMI_CONFIG_DIR` 指定）和设置中的 checkpoint 位置，不处理其他机器的数据。

迁移包括：供应商旧字段及未显式选择的协议、飞书单机器人配置和会话 ID、旧子代理 ID、旧审批节点、Todo 数据、旧工具名、无 ID 的消息、无历史索引的压缩记录、附件引用、子代理数据库旧表结构及记忆索引标签。不同协议或厂商方言混在同一旧 profile 中时，会按当前连接规则拆分并更新模型指针。SQLite 和 PostgreSQL 使用各自现有的 checkpoint 存储；加密数据沿用 `LUMI_CHECKPOINT_AES_KEY`，不会更改密钥。

`--check` 只报告待改数量，`--apply` 原地应用。重复执行不会再次迁移已经转换的数据。已被删除的 checkpoint 原文无法重建；迁移不会伪造丢失的历史。

迁移先读取并计算文件变更，再处理数据库，最后写入文件并保存 `lumi.json`。文件读取或解析失败时不会先修改数据库；写入中途失败可直接重跑，原配置中的映射依据会保留到最后。旧连接未明确指定协议时沿用 `llm_params.openai.use_responses_api` 的选择，已有显式协议保持不变；未知模型别名保留原来的 `local` 方言。拆分 profile 时同步迁移历史来源中的供应商 ID，避免思考字段因连接身份变化而丢失。

前端旧字体偏好直接回到默认值，不由后端读取浏览器存储。旧 CLI 隐式新建用法已淘汰，新建飞书机器人明确使用 `lumi feishu config --new ...`。

## 未来删除

保留几个版本后，可一起删除：

1. `lumi/cli.py` 中的 `migrate` 命令函数。
2. 整个 `lumi/_migrate/` 目录。
3. `tests/test_cli_migrate.py` 和 `tests/test_migrate_*.py`。
4. 本文及 CLI/文档导航中的入口链接。

正常运行模块不导入迁移模块；不需要保留旧数据类型或在正常启动中检测旧版本。
