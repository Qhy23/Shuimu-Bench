# 打包与发布

本项目用 PyInstaller 打包, GitHub Actions 双平台构建. 打包配置在仓库根 `shuimu.spec`, CI 在 `.github/workflows/build.yml`.

## 产物形态

- onedir 目录包, 一个目录两个可执行文件: `ShuimuBench`(GUI, 无控制台) 与 `shuimu-cli`(命令行, 保留控制台), 共享 `_internal/` 依赖目录
- 命名: `ShuimuBench-<版本>-linux-x64.tar.gz` / `ShuimuBench-<版本>-win-x64.zip`; 版本来自 `shuimu/__init__.py` 的 `APP_VERSION`
- 解压即用, 无需 Python; 数据落用户数据目录(Windows `%APPDATA%\ShuimuBench`, Linux `~/.local/share/shuimu/`), 不在程序目录, 升级替换程序目录不丢数据

## datas 与 frozen 路径的对应

`shuimu/core/paths.py` 在 `sys.frozen` 时按 `_MEIPASS` 定位随包文件, spec 的 datas 必须对齐:

| spec 数据 | 落点 | 用途 |
|---|---|---|
| `config.yaml` | `_MEIPASS/` | frozen 下 `project_root()` == `_MEIPASS`, 作为配置合并的"项目根"层 |
| `data/schedule.example.yaml` | `_MEIPASS/data/` | 首启日程模板 |
| `data/links.example.yaml` | `_MEIPASS/data/` | 首启链接模板 |
| `shuimu/plugins/`(整目录 Tree) | `_MEIPASS/shuimu_plugins/` | 内置插件发现与按文件路径 importlib 加载 |
| `assets/shuimu.png` | `_MEIPASS/assets/` | Linux 桌面集成备用 |

全部 `shuimu` 模块被两个入口静态 import 到位(插件 GUI 页签由主窗口引, 插件 CLI 由主 CLI 引, 插件入口只 import `shuimu.core.*`), 因此无需 hiddenimports; 构建后核对 `build/**/warn-*.txt` 无 `missing module named shuimu`(CI 内置此步骤).

## 本地构建与冒烟

```bash
pip install "pyinstaller>=6,<7"
pyinstaller --noconfirm shuimu.spec          # 产物在 dist/ShuimuBench/

dist/ShuimuBench/shuimu-cli --help           # CLI 冒烟
dist/ShuimuBench/shuimu-cli plugins          # 插件发现(读 manifest)
QT_QPA_PLATFORM=offscreen timeout 15 dist/ShuimuBench/ShuimuBench
# 期待退出码 124(事件循环被 timeout 杀掉, 说明 GUI 正常运行)
```

Windows 侧构建在 Windows 机器或 CI 上进行, 本地 WSL 只能出 Linux 产物.

## 应用图标

`assets/shuimu.ico`(多尺寸) 与 `assets/shuimu.png`(256px) 由 `tools/make_icon.py` 离屏渲染 `artwork.bench_icon` 生成, 已入库; logo 改动后重跑再生成(Pillow 仅此处需要, 不入 requirements.txt):

```bash
pip install pillow && python tools/make_icon.py
```

## CI 触发与 Release

- 手动: 仓库 Actions 页 `build` -> Run workflow, 只构建与冒烟, 产物在 artifact(90 天)
- 发版: 推 `v*` 标签(如 `v0.1.0`), 两平台构建通过后自动创建 GitHub Release 并附产物; **标签版本必须与 `APP_VERSION` 一致**, 不一致构建直接红
- 重跑发布不冲突: 已存在 Release 时改为上载覆盖(`--clobber`)

## 已知限制

- Linux 产物在 ubuntu-latest(24.04, glibc 2.39) 构建, 只能在同等或更新的发行版上运行
- Windows 可执行未签名, 首次运行有 SmartScreen 提示, 选"仍要运行"即可
- 校园登录/学堂的网络路径依赖 Python 自带 SSL, 冒烟测试覆盖不到, 首次实机使用时验证
