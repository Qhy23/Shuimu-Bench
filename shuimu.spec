# -*- mode: python ; coding: utf-8 -*-
"""ShuimuBench 打包 spec: onedir 单 COLLECT 双可执行.

用法: pyinstaller --noconfirm shuimu.spec
产物: dist/ShuimuBench/{ShuimuBench, shuimu-cli}[.exe] + _internal/
(GUI 无控制台, CLI 保留控制台; 两可执行共享 _internal 依赖).

datas 落点对齐 shuimu/core/paths.py 的 frozen 分支:
  config.yaml -> _MEIPASS 根 (frozen 下 project_root() 上溯三级 == _MEIPASS)
  data/*.example.yaml -> _MEIPASS/data/ (首启播种模板)
  shuimu/plugins -> _MEIPASS/shuimu_plugins/ (pluginman 按文件路径加载)
  assets/shuimu.png -> _MEIPASS/assets/ (Linux .desktop 备用)

全部 shuimu 模块被 main.py/cli.py 静态可达 (插件 GUI 页签由主窗口
import, 插件 cli 由主 CLI import, 插件入口只 import shuimu.core.*),
故无需 hiddenimports; 构建后核对 warn-*.txt 无 "missing module named
shuimu" (CI 内置此步骤).
"""

import sys

from PyInstaller.building.datastruct import Tree

WIN = sys.platform == "win32"
ICON = "assets/shuimu.ico" if WIN else None   # icon 仅 Windows 生效

# 公共 datas: 两可执行运行时都经 paths.py 读到它们
DATAS = [
    ("config.yaml", "."),                        # _MEIPASS/config.yaml
    ("data/schedule.example.yaml", "data"),      # 首启日程模板
    ("data/links.example.yaml", "data"),         # 首启链接模板
    ("assets/shuimu.png", "assets"),             # Linux 桌面集成备用
]
# 内置插件整目录: 排除字节码缓存, 前缀对齐 builtin_plugins_dir()
EXCLUDES = ["pytest", "pytestqt", "_pytest", "pylint"]   # dev 依赖保险排除

a_gui = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    hiddenimports=[],
    datas=DATAS,
    excludes=EXCLUDES,
    noarchive=False,
)
a_gui.datas += Tree("shuimu/plugins", prefix="shuimu_plugins",
                    excludes=["__pycache__", "*.pyc"])
pyz_gui = PYZ(a_gui.pure)

a_cli = Analysis(
    ["cli.py"],
    pathex=[],
    binaries=[],
    hiddenimports=[],
    datas=DATAS,
    excludes=EXCLUDES,
    noarchive=False,
)
a_cli.datas += Tree("shuimu/plugins", prefix="shuimu_plugins",
                    excludes=["__pycache__", "*.pyc"])
pyz_cli = PYZ(a_cli.pure)

exe_gui = EXE(
    pyz_gui,
    a_gui.scripts,
    [],
    exclude_binaries=True,       # onedir 必需
    name="ShuimuBench",          # Windows 下自动 .exe
    console=False,               # GUI 无控制台
    icon=ICON,
    debug=False,
    strip=False,
    upx=False,
)

exe_cli = EXE(
    pyz_cli,
    a_cli.scripts,
    [],
    exclude_binaries=True,
    name="shuimu-cli",
    console=True,                # CLI 保留控制台 (REPL/getpass 需要)
    icon=ICON,
    debug=False,
    strip=False,
    upx=False,
)

# TOC 为 set 语义, 两 Analysis 的同名 binaries/datas 自动去重
coll = COLLECT(
    exe_gui,
    a_gui.binaries,
    a_gui.datas,
    exe_cli,
    a_cli.binaries,
    a_cli.datas,
    name="ShuimuBench",          # dist/ShuimuBench/
    strip=False,
    upx=False,
)
