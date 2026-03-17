"""配置模型类

定义所有 Pydantic 配置模型，用于解析和验证 config.yaml 配置文件。
"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

type CheckpointMode = Literal["memory", "sqlite", "postgres"]


class AgentsConfig(BaseModel):
    """Agents配置类"""

    tools: list[str] = Field(
        default=[],
        description="启用的工具列表（白名单），空列表表示启用所有工具",
    )
    disabled_tools: list[str] = Field(
        default=[],
        description="禁用的工具列表（黑名单），优先级高于 tools",
    )
    max_tokens: int = Field(
        default=8192,
        description="模型输出最大token数",
    )
    recursion_limit: int = Field(
        default=5000,
        description="Agent 递归执行限制，控制 agent 最大执行轮次",
    )
    vision_mode: Literal["model", "tool"] = Field(
        default="model",
        description="图片识别模式：'model' - 使用模型多模态能力（默认）；'tool' - 将图片 URL 转为文本，通过工具识别",
    )
    checkpoint: CheckpointMode = Field(
        default="memory",
        description="检查点存储模式：'memory' - 内存存储（默认）；'sqlite' - SQLite 持久化存储；'postgres' - PostgreSQL 持久化存储",
    )
    postgres_uri: str = Field(
        default="",
        description="PostgreSQL 连接 URI，仅在 checkpoint 为 'postgres' 时使用",
    )

    @model_validator(mode="after")
    def validate_postgres_uri(self) -> "AgentsConfig":
        if self.checkpoint == "postgres" and not self.postgres_uri:
            raise ValueError("checkpoint 为 'postgres' 时必须配置 agents.postgres_uri")
        return self


class TokenConfig(BaseModel):
    """Token处理配置类"""

    once_tool_max_tokens: int = Field(
        default=10000, description="单次工具调用返回结果最大token数"
    )
    trim_messages_max_tokens: int = Field(
        default=192000, description="消息修剪器最大token数"
    )
    context_length: int = Field(default=200000, description="模型上下文窗口最大token数")
    summary_threshold: float = Field(
        default=0.7,
        description="触发总结的阈值比例，当消息token数 >= context_length * summary_threshold 时触发",
    )


class ToolArgsConfig(BaseModel):
    """工具参数配置类

    支持动态配置工具参数映射关系。配置格式：
    tool_args:
      参数名1:
        - 工具名1
        - 工具名2
      参数名2:
        - 工具名3

    例如：
    tool_args:
      extra_match:
        - "knowledge_retrieval"
        - "qs_retrieval"
      another_param:
        - "tool1"
    """

    model_config = {"extra": "allow"}

    # 保留原始数据用于获取所有映射
    def __init__(self, **data):
        super().__init__(**data)
        # 将所有额外字段存储下来
        self._param_mappings = {k: v for k, v in data.items() if isinstance(v, list)}

    def get_allowed_tools_for_param(self, param_name: str) -> list:
        """获取可以接收指定参数的工具列表

        Args:
            param_name: 参数名称

        Returns:
            工具名称列表，如果参数不存在则返回空列表
        """
        return getattr(self, param_name, [])

    def get_all_param_mappings(self) -> dict:
        """获取所有参数到工具的映射关系

        Returns:
            {参数名: [工具名列表]} 的字典
        """
        # 返回存储的参数映射
        return getattr(self, "_param_mappings", {})


class ToolOffloadConfig(BaseModel):
    """工具结果卸载配置类

    用于配置将特定工具的大量返回结果卸载到文件系统，避免占用过多上下文窗口。
    """

    enabled: bool = Field(default=False, description="是否启用工具结果卸载")
    token_threshold: int = Field(default=2000, description="触发卸载的token阈值")
    tools: list = Field(default=[], description="需要卸载结果的工具列表")


class ModelTypeParamsConfig(BaseModel):
    """单个模型类型的参数配置"""

    model_config = {"extra": "allow"}  # 允许任意额外参数

    def to_dict(self) -> dict:
        """转换为字典，用于参数合并"""
        return self.model_dump()


class LlmParamsConfig(BaseModel):
    """LLM 参数配置类 - 按模型类型分别配置

    支持为不同模型类型配置不同参数：
    - openai: OpenAI 系列模型参数
    - anthropic: Claude 系列模型参数
    """

    openai: ModelTypeParamsConfig = Field(default_factory=ModelTypeParamsConfig)
    anthropic: ModelTypeParamsConfig = Field(default_factory=ModelTypeParamsConfig)

    def get_params_for_model_type(self, model_type: str) -> dict:
        """根据模型类型获取对应的参数配置"""
        match model_type:
            case "anthropic" | "bedrock":
                return self.anthropic.to_dict()
            case _:
                return self.openai.to_dict()


class SkillExecutionConfig(BaseModel):
    """技能命令执行配置类

    用于配置技能中嵌入式命令的执行行为。
    技能可以使用 !`command` 语法执行命令并将输出渲染到提示词中。
    """

    enabled: bool = Field(default=True, description="是否启用技能命令执行")
    command_timeout: float = Field(default=10.0, description="命令执行超时时间(秒)")
    max_output_bytes: int = Field(default=10_000, description="命令输出最大字节数")


class PTCConfig(BaseModel):
    """PTC (Programmatic Tool Calling) 配置

    将 MCP 工具转换为可直接调用的 Python 函数，
    使模型可以通过生成代码调用工具，而非每次生成 JSON。
    """

    enabled: bool = Field(default=True, description="是否启用 PTC")
    tools: list[str] = Field(
        default=[], description="启用 PTC 的 MCP 工具列表，空表示所有 MCP 工具"
    )
    disabled_tools: list[str] = Field(
        default=[], description="排除的工具列表，优先级高于 tools"
    )


class FilesystemConfig(BaseModel):
    """文件系统工具配置"""

    grep_max_file_size_mb: int = Field(
        default=10, description="grep 搜索时跳过的最大文件大小(MB)"
    )


class Config(BaseModel):
    """主配置类"""

    token: TokenConfig = Field(default_factory=TokenConfig, description="Token处理配置")
    agents: AgentsConfig = Field(default_factory=AgentsConfig, description="Agents配置")
    tool_args: ToolArgsConfig = Field(
        default_factory=ToolArgsConfig, description="工具参数配置"
    )
    tool_offload: ToolOffloadConfig = Field(
        default_factory=ToolOffloadConfig, description="工具结果卸载配置"
    )
    llm_params: LlmParamsConfig = Field(
        default_factory=LlmParamsConfig, description="LLM参数配置"
    )
    skill_execution: SkillExecutionConfig = Field(
        default_factory=SkillExecutionConfig, description="技能命令执行配置"
    )
    ptc: PTCConfig = Field(
        default_factory=PTCConfig, description="PTC (Programmatic Tool Calling) 配置"
    )
    filesystem: FilesystemConfig = Field(
        default_factory=FilesystemConfig, description="文件系统工具配置"
    )
    env: dict[str, str] = Field(
        default_factory=dict,
        description="自定义环境变量，启动时注入到 os.environ",
    )
