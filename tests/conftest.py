"""pytest 配置: 无头环境运行 Qt."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
