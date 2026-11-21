"""界面主题: 设计 token 体系, 内置亮/暗双主题, 用户主题扩展与 QSS 生成.

VS Code 视觉语言: 平直分层 (base/panel/elevated/hover), 细边框, 实色主按钮,
主色为清华紫. 用户可在用户数据目录 themes/<id>.yaml 写主题
(name/extends/colors), 只覆盖想改的 token, 见 load_user_themes().

字号以 QSS 为单一来源(build_qss(font_px)); QSS 的字号不回写
QWidget.font(), 自绘部件须显式 setPixelSize 并经 set_font_px 通道联动.
缩放度量分两类: 文字邻近的随 k=font_px/DEFAULT_FONT_PX 缩放; 结构性
chrome(圆角/滚动条宽/边框)保持固定, 放大后显笨重.
"""

import dataclasses
import hashlib
import os

import yaml

from shuimu.core.config import DEFAULT_FONT_PX, clamp_font_px
from shuimu.core.paths import user_themes_dir

# ---- 字体与结构常量(跨主题不变) ----
RADIUS = "4px"
FONT_FAMILY = '"Noto Sans CJK SC", "Microsoft YaHei", sans-serif'
MONO_FAMILY = '"JetBrainsMono NFM", "DejaVu Sans Mono", monospace'
ACCENT = "#3fc1a9"        # 手动日程强调色(青绿, 主题无关)


# ---- 主题 token ----

@dataclasses.dataclass(frozen=True)
class ThemeTokens:
    """一套主题的全部颜色 token.

    base/panel/elevated/hover/border 为 VS Code 式平直分层; accent 系为
    主色(选中/焦点/主按钮); selection 为列表树选中底; danger/warn/success
    为语义色. 全部值形如 #rrggbb.
    """

    name: str
    label: str
    base: str
    panel: str
    elevated: str
    hover: str
    border: str
    fg: str
    fg_dim: str
    accent: str
    accent_hover: str
    accent_press: str
    accent_fg: str
    selection: str
    focus: str
    danger: str
    danger_press: str
    warn: str
    success: str


# 颜色 token 字段(用户主题 colors: 段只允许这些键)
COLOR_FIELDS = tuple(f.name for f in dataclasses.fields(ThemeTokens)
                     if f.name not in ("name", "label"))

DARK_TOKENS = ThemeTokens(
    name="dark",
    label="深色",
    base="#1e1e1e",        # 窗口底 (VS Code editor.bg)
    panel="#252526",       # 面板/输入框/侧栏 (sideBar.bg)
    elevated="#2d2d30",    # 浮层 (菜单/工具提示 dropdown.bg)
    hover="#2f2f31",       # 条目悬停
    border="#3c3c3c",
    fg="#d4d4d4",
    fg_dim="#9d9d9d",
    accent="#660874",      # 清华紫
    accent_hover="#8c2f9e",
    accent_press="#4c0556",
    accent_fg="#ffffff",
    selection="#3d1a4a",   # 列表树选中底 (紫色 tint)
    focus="#8c2f9e",
    danger="#e5484d",
    danger_press="#c5393d",
    warn="#f0a020",
    success="#3fb950",
)

LIGHT_TOKENS = ThemeTokens(
    name="light",
    label="浅色",
    base="#ffffff",
    panel="#f3f3f3",
    elevated="#ffffff",
    hover="#e4e4e4",
    border="#d0d0d0",
    fg="#1f1f1f",
    fg_dim="#6e6e6e",
    accent="#660874",
    accent_hover="#8c2f9e",
    accent_press="#4c0556",
    accent_fg="#ffffff",
    selection="#e5d5ea",
    focus="#660874",
    danger="#d64545",
    danger_press="#b93a3e",
    warn="#9a6700",
    success="#1a7f37",
)

BUILTIN_THEMES = {t.name: t for t in (DARK_TOKENS, LIGHT_TOKENS)}


# ---- 运行时状态 ----

_state = {"font_px": DEFAULT_FONT_PX, "theme": DARK_TOKENS.name,
          "tokens": DARK_TOKENS}
_last_user_themes = {}  # theme_choices 调用时刷新的用户主题快照


def tokens() -> ThemeTokens:
    """当前生效的主题 token."""
    return _state["tokens"]


def current_name() -> str:
    """当前主题 id."""
    return _state["theme"]


def current_font_px() -> int:
    """当前生效字号."""
    return _state["font_px"]


# ---- 用户主题 ----

def _norm_color(value) -> str:
    """颜色归一化为小写 #rrggbb; 非法抛 ValueError."""
    if not isinstance(value, str):
        raise ValueError(f"颜色须为字符串: {value!r}")
    text = value.strip().lower()
    if len(text) == 4 and text.startswith("#"):
        text = "#" + "".join(ch * 2 for ch in text[1:])
    if not (len(text) == 7 and text.startswith("#")):
        raise ValueError(f"颜色须为 #rgb/#rrggbb: {value!r}")
    int(text[1:], 16)  # 十六进制合法性
    return text


def load_user_themes(themes_dir: str = None):
    """加载用户主题目录下的 YAML 主题.

    返回 (themes, warnings): themes 为 {id: ThemeTokens}(可覆盖内置
    同名), warnings 为可读警告列表(损坏/非法文件不阻断). 目录缺失
    返回空. 格式:

        name: 显示名        # 可选, 缺省用文件名
        extends: dark      # 可选, 缺省 dark; 只许继承内置主题
        colors:            # 只覆盖列出的 token, 未知键忽略并告警
          accent: "#0b5cad"
    """
    directory = themes_dir if themes_dir is not None else user_themes_dir()
    themes, warnings = {}, []
    if not os.path.isdir(directory):
        return themes, warnings
    for filename in sorted(os.listdir(directory)):
        if not filename.endswith((".yaml", ".yml")):
            continue
        path = os.path.join(directory, filename)
        tid = os.path.splitext(filename)[0]
        try:
            with open(path, encoding="utf-8") as fh:
                raw = yaml.safe_load(fh)
        except (OSError, yaml.YAMLError) as exc:
            warnings.append(f"主题 {tid}: 文件损坏已跳过 ({exc})")
            continue
        if not isinstance(raw, dict):
            warnings.append(f"主题 {tid}: 内容须为映射, 已跳过")
            continue
        base_name = raw.get("extends", "dark")
        if base_name not in BUILTIN_THEMES:
            warnings.append(f"主题 {tid}: extends={base_name!r} 非内置主题,"
                            f" 已跳过")
            continue
        overrides = {}
        colors = raw.get("colors", {})
        if not isinstance(colors, dict):
            warnings.append(f"主题 {tid}: colors 须为映射, 已跳过")
            continue
        for key, value in colors.items():
            if key not in COLOR_FIELDS:
                warnings.append(f"主题 {tid}: 未知 token {key!r} 已忽略")
                continue
            try:
                overrides[key] = _norm_color(value)
            except ValueError as exc:
                warnings.append(f"主题 {tid}: token {key!r} 颜色非法 ({exc}),"
                                f" 已忽略")
        label = raw.get("name")
        if not isinstance(label, str) or not label.strip():
            label = tid
        themes[tid] = dataclasses.replace(BUILTIN_THEMES[base_name],
                                          name=tid, label=label.strip(),
                                          **overrides)
    return themes, warnings


def theme_choices(themes_dir: str = None):
    """可用主题清单 [(id, 显示名)], 内置在前, 用户主题按文件名序.

    顺带刷新用户主题快照, apply_theme(theme=...) 用快照切换, 避免
    清单与切换两次磁盘扫描不一致.
    """
    global _last_user_themes  # pylint: disable=global-statement
    choices = [(t.name, t.label) for t in BUILTIN_THEMES.values()]
    user_themes, _warnings = load_user_themes(themes_dir)
    if themes_dir is None:
        _last_user_themes = user_themes
    choices.extend((t.name, t.label) for t in user_themes.values())
    return choices


# ---- 课程稳定配色(借鉴 thu-info-app: md5(课程名)前 6 位 % 11 色板,
# 同名课全学期同色; 主题无关) ----

COURSE_PALETTE = ["#00a6f2", "#3cb3c8", "#fc6b50", "#f067bb", "#ef5b75",
                  "#7d7aea", "#bf73e5", "#ff9900", "#eebe00", "#5b8eff",
                  "#139161"]


def course_color(title: str) -> str:
    """课程色: md5(标题)前 6 位十六进制对色板长度取模."""
    digest = hashlib.md5(title.encode("utf-8")).hexdigest()
    return COURSE_PALETTE[int(digest[:6], 16) % len(COURSE_PALETTE)]


# ---- QSS 生成 ----

def build_qss(font_px: int = None, tk: ThemeTokens = None) -> str:
    """按字号与主题生成全局 QSS; 文字邻近 padding 随 k 缩放, chrome 固定."""
    font_px = clamp_font_px(font_px if font_px is not None
                            else _state["font_px"])
    tk = tk or tokens()
    k = font_px / DEFAULT_FONT_PX

    def pad(base: int) -> str:
        """文字邻近内边距按 k 缩放, 下限 2px."""
        return f"{max(2, round(base * k))}px"

    # 文字三级(对齐 thu-info-app 的 title/body/caption 阶梯)
    title_px = round(font_px * 1.35)
    caption_px = max(9, round(font_px * 0.85))

    return f"""
* {{
    font-family: {FONT_FAMILY};
    font-size: {font_px}px;
    color: {tk.fg};
}}
QMainWindow, QWidget {{
    background-color: {tk.base};
}}
QTreeWidget, QTreeView, QTableWidget, QTableView, QListWidget, QListView,
QPlainTextEdit, QTextEdit, QTextBrowser, QLineEdit, QSpinBox {{
    background-color: {tk.panel};
    border: 1px solid {tk.border};
    border-radius: {RADIUS};
    padding: {pad(4)};
    selection-background-color: {tk.selection};
    selection-color: {tk.fg};
}}
QTreeWidget::item, QTreeView::item {{
    padding: {pad(4)} {pad(2)};
    border-radius: 4px;
}}
/* 嵌入式部件行(如插件面板的启用/禁用按钮): 垂直 padding 归零,
   否则 setItemWidget 的部件被压到行内容区, 低于按钮自身最小高度,
   文字底部被裁(2026-11-16 实测: 行 31px, 按钮仅得 23px). */
QTreeWidget#pluginTree::item {{
    padding: 0 {pad(2)};
}}
QLineEdit:focus, QTreeWidget:focus, QTreeView:focus, QTableWidget:focus,
QPlainTextEdit:focus, QTextEdit:focus, QTextBrowser:focus,
QListWidget:focus, QListView:focus, QComboBox:focus, QDateEdit:focus,
QTimeEdit:focus, QSpinBox:focus {{
    border: 1px solid {tk.focus};
}}
QComboBox, QDateEdit, QTimeEdit {{
    background-color: {tk.panel};
    border: 1px solid {tk.border};
    border-radius: {RADIUS};
    padding: {pad(3)} {pad(6)};
}}
QComboBox QAbstractItemView {{
    background-color: {tk.elevated};
    border: 1px solid {tk.border};
    selection-background-color: {tk.accent};
    selection-color: {tk.accent_fg};
}}
QTreeWidget::item:hover, QTreeView::item:hover {{
    background-color: {tk.hover};
}}
QTreeWidget::item:selected, QTreeView::item:selected {{
    background-color: {tk.selection};
}}
QHeaderView::section {{
    background-color: {tk.base};
    color: {tk.fg_dim};
    border: none;
    border-bottom: 1px solid {tk.border};
    padding: {pad(4)} {pad(6)};
    font-size: {caption_px}px;
}}
QPushButton {{
    background-color: {tk.panel};
    border: 1px solid {tk.border};
    border-radius: {RADIUS};
    padding: {pad(5)} {pad(14)};
}}
QPushButton:hover {{
    background-color: {tk.hover};
    border-color: {tk.focus};
}}
QPushButton:pressed {{
    background-color: {tk.base};
}}
QPushButton:disabled {{
    color: {tk.fg_dim};
    border-color: {tk.border};
}}
QPushButton[tool="true"] {{
    padding: {pad(2)} {pad(8)};
}}
QPushButton[primary="true"] {{
    background-color: {tk.accent};
    border: none;
    color: {tk.accent_fg};
    font-weight: 600;
}}
QPushButton[primary="true"]:hover {{
    background-color: {tk.accent_hover};
}}
QPushButton[primary="true"]:pressed {{
    background-color: {tk.accent_press};
}}
QPushButton[danger="true"] {{
    background: transparent;
    border: 1px solid {tk.danger};
    color: {tk.danger};
}}
QPushButton[danger="true"]:hover {{
    background-color: {tk.danger};
    color: white;
}}
QPushButton[danger="true"]:pressed {{
    background-color: {tk.danger_press};
    border-color: {tk.danger_press};
    color: white;
}}
QTabWidget::pane {{
    background-color: {tk.base};
    border: 1px solid {tk.border};
    border-radius: {RADIUS};
    top: -1px;
}}
QTabBar::tab {{
    background-color: {tk.panel};
    color: {tk.fg_dim};
    padding: {pad(6)} {pad(16)};
    border-top-left-radius: {RADIUS};
    border-top-right-radius: {RADIUS};
    margin-right: 2px;
}}
QTabBar::tab:selected {{
    background-color: {tk.base};
    color: {tk.fg};
    border-top: 2px solid {tk.accent};
}}
QTabBar::tab:hover:!selected {{
    background-color: {tk.hover};
    color: {tk.fg};
}}
QSplitter::handle {{
    background-color: transparent;
    width: 4px;
}}
QSplitter::handle:hover {{
    background-color: {tk.accent};
}}
QLabel {{
    color: {tk.fg_dim};
    background: transparent;
    border: none;
}}
QStatusBar {{
    background-color: {tk.accent};
    color: {tk.accent_fg};
    border-top: none;
}}
QStatusBar QLabel {{
    color: {tk.accent_fg};
    background: transparent;
}}
QStatusBar::item {{
    border: none;
}}
QMenu {{
    background-color: {tk.elevated};
    border: 1px solid {tk.border};
    border-radius: {RADIUS};
    padding: 4px 0;
}}
QMenu::item {{
    padding: {pad(5)} {pad(24)} {pad(5)} {pad(12)};
    background: transparent;
}}
QMenu::item:selected {{
    background-color: {tk.accent};
    color: {tk.accent_fg};
}}
QMenu::item:disabled {{
    color: {tk.fg_dim};
}}
QMenu::separator {{
    height: 1px;
    background: {tk.border};
    margin: 4px 10px;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {tk.border};
    border-radius: 5px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{
    background: {tk.accent_hover};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {tk.border};
    border-radius: 5px;
    min-width: 24px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {tk.accent_hover};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}
QToolTip {{
    background-color: {tk.elevated};
    color: {tk.fg};
    border: 1px solid {tk.border};
    padding: {pad(4)} {pad(8)};
}}
QFrame#daycol, QFrame#daycol-today {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
}}
QFrame#daycol-today {{
    background-color: {tk.hover};
    border: 1px solid {tk.accent};
}}
QFrame#slotcard, QFrame#slotcard-manual {{
    background-color: {tk.panel};
    border: 1px solid {tk.border};
    border-radius: 6px;
}}
QFrame#cardbar {{
    border: none;
    border-radius: 1px;
}}
QPushButton#weektitle {{
    background: transparent;
    border: none;
    color: {tk.fg};
    font-weight: 600;
    font-size: {title_px}px;
    padding: {pad(2)} {pad(4)};
}}
QPushButton#weektitle:hover {{
    color: {tk.accent_hover};
}}
QWidget#weekpicker {{
    background-color: {tk.elevated};
    border: 1px solid {tk.border};
    border-radius: {RADIUS};
}}
QWidget#weekpicker QPushButton {{
    padding: {pad(2)} {pad(6)};
}}
QPushButton[today="true"] {{
    color: {tk.accent_hover};
    font-weight: 600;
    border-color: {tk.accent_press};
}}
QLabel#apptitle {{
    color: {tk.fg};
    background: transparent;
    font-weight: 600;
    font-size: {title_px}px;
}}
QLabel#dayhead {{
    color: {tk.fg_dim};
    background: transparent;
    font-weight: 600;
    font-size: {title_px}px;
}}
QLabel#cardtitle {{
    color: {tk.fg};
    background: transparent;
}}
QLabel#cardtime {{
    color: {tk.fg_dim};
    background: transparent;
    font-family: {MONO_FAMILY};
    font-size: {caption_px}px;
}}
QLabel#cardloc {{
    color: {tk.fg_dim};
    background: transparent;
    font-size: {caption_px}px;
}}
QLabel#warnbar {{
    color: {tk.danger};
    background: transparent;
}}
"""


QSS = build_qss()


def on_theme_changed(listener) -> None:
    """注册主题切换监听器(无参回调).

    artwork 等按色缓存的模块在 import 时注册清缓存回调, 避免 theme
    反向依赖 artwork 造成环.
    """
    _listeners.append(listener)


def apply_theme(app, font_px: int = None, theme: str = None) -> None:
    """把主题(含字号)应用到 QApplication.

    font_px/theme 为 None 时保持当前值; theme 为可用主题 id(内置或用户
    主题), 非法抛 ValueError. 切换主题时通知全部监听器并重生成 QSS.
    """
    if font_px is not None:
        _state["font_px"] = clamp_font_px(font_px)
    if theme is not None:
        all_themes = dict(BUILTIN_THEMES)
        all_themes.update(_last_user_themes)
        if theme not in all_themes:
            raise ValueError(f"未知主题: {theme}")
        if all_themes[theme] is not _state["tokens"]:
            _state["theme"] = theme
            _state["tokens"] = all_themes[theme]
            for listener in list(_listeners):
                listener()
    app.setStyleSheet(build_qss())


_listeners = []  # 主题切换监听器(artwork 清缓存等)
