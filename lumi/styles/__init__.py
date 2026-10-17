"""内置资源包

prompts/、agents/、skills/ 分别提供提示词、子 Agent 和技能。
"""

from pathlib import Path

# 各层序（loader.config_layers / manager.prompt_layers）拼内置资源路径的基准；
# 缺某个子目录属正常，调用方直接拼路径、由读取侧跳过不存在的层
STYLES_ROOT = Path(__file__).parent
