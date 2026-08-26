from importlib.metadata import version

# 发布名 ≠ 包名：PyPI 上叫 lumi-harness（lumi 已被占），import 名与命令名仍是 lumi
__version__ = version("lumi-harness")
