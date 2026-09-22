"""内置风格包

每个子目录代表一种风格，可含 prompts/、agents/、skills/ 三类子目录（均可选）。
"""

from pathlib import Path

# 各层序（loader.config_layers / manager.prompt_layers）拼风格路径的基准；
# 缺某个子目录属正常，调用方直接拼路径、由读取侧跳过不存在的层
STYLES_ROOT = Path(__file__).parent
