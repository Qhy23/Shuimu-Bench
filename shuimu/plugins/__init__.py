"""内置插件包: 每个子目录一个插件(manifest.yaml + 入口 + 资源).

内置插件与外部插件经完全相同的路径加载(core.pluginman 按文件位置
importlib 加载), 无特权代码路径; 仅依赖 shuimu.core 的插件契约层.
"""
