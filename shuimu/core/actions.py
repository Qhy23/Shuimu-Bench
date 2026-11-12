"""动作结果数据: ActionResult 与动作层共享常量.

独立成模块避免 controller 与动作混入模块(learn_actions)互相导入.
"""

from dataclasses import dataclass, field


@dataclass
class ActionResult:
    """动作结果: ok 是否成功, message 给用户看的摘要, detail 留痕详情."""

    ok: bool
    message: str
    detail: dict = field(default_factory=dict)


KIND_LABEL = {"file": "文件", "dir": "目录", "link": "链接"}
