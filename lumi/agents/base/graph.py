from abc import ABC, abstractmethod

from langgraph.graph import StateGraph


class BaseGraph(ABC):
    state_cls = None  # 子类应覆盖此类属性

    def __init__(self):
        if self.state_cls is None:
            raise NotImplementedError("子类必须定义state_cls类属性")
        self.builder = StateGraph(self.state_cls)
        self._draw_nodes()
        self._draw_edges()

    @abstractmethod
    def _draw_nodes(self):
        """添加节点"""
        raise NotImplementedError("必须实现_draw_nodes方法")

    @abstractmethod
    def _draw_edges(self):
        """添加边"""
        raise NotImplementedError("必须实现_edges方法")
