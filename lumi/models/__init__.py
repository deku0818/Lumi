"""模型层 — LLM 实例创建、模型目录、供应商配置与思考档位解析。

外部使用统一走全路径（如 ``from lumi.models.manager import create_llm``），
此 ``__init__`` 不做 re-export，避免双入口歧义。
"""
