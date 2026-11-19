"""LaTeX 数学公式 -> HTML 的轻量转换(Qt 富文本可显示).

只覆盖常见数学记号的近似渲染(希腊字母/运算符/上下标/分数/根号/
绝对值等), 不追求排版级还原; 复杂结构(矩阵/多行对齐)退化为可读
文本. 纯标准库, 零重依赖 — 完整 LaTeX 排版需要 QtWebEngine 级方案,
本模块是轻量近似(2026-11-19 与用户确认的取舍).

渲染管线配合: extract_formulas 把正文里的公式替换为占位符, 富文本
渲染后再逐个替换回公式 HTML.
"""

import re

# 占位符形态: 不可见分隔符包裹序号, markdown 渲染不会破坏它
PLACEHOLDER_FMT = "⁣F{}⁣"
_PLACEHOLDER_RE = re.compile("⁣F(\\d+)⁣")

_SYMBOLS = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ",
    "epsilon": "ε", "varepsilon": "ε", "zeta": "ζ", "eta": "η",
    "theta": "θ", "iota": "ι", "kappa": "κ", "lambda": "λ",
    "mu": "μ", "nu": "ν", "xi": "ξ", "pi": "π", "rho": "ρ",
    "sigma": "σ", "tau": "τ", "upsilon": "υ", "phi": "φ",
    "varphi": "φ", "chi": "χ", "psi": "ψ", "omega": "ω",
    "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ",
    "Xi": "Ξ", "Pi": "Π", "Sigma": "Σ", "Phi": "Φ", "Psi": "Ψ",
    "Omega": "Ω", "infty": "∞", "partial": "∂", "nabla": "∇",
    "times": "×", "div": "÷", "pm": "±", "mp": "∓", "cdot": "·",
    "leq": "≤", "geq": "≥", "neq": "≠", "approx": "≈",
    "equiv": "≡", "sim": "∼", "propto": "∝",
    "leftarrow": "←", "rightarrow": "→", "to": "→",
    "Rightarrow": "⇒", "Leftrightarrow": "⇔", "mapsto": "↦",
    "in": "∈", "notin": "∉", "subset": "⊂", "supset": "⊃",
    "subseteq": "⊆", "cup": "∪", "cap": "∩", "emptyset": "∅",
    "forall": "∀", "exists": "∃", "neg": "¬", "land": "∧",
    "lor": "∨", "oplus": "⊕", "otimes": "⊗",
    "sum": "∑", "prod": "∏", "int": "∫", "iint": "∬",
    "ldots": "…", "cdots": "⋯", "vdots": "⋮", "dots": "…",
    "perp": "⊥", "parallel": "∥", "angle": "∠", "degree": "°",
    "prime": "′", "hbar": "ℏ", "ell": "ℓ",
    "mathbb{R}": "ℝ", "mathbb{Z}": "ℤ", "mathbb{N}": "ℕ",
    "mathbb{Q}": "ℚ", "mathbb{C}": "ℂ",
    "mathcal{L}": "ℒ", "mathrm{d}": "d",
}

_FUNCTION_NAMES = (
    "sin", "cos", "tan", "cot", "sec", "csc", "arcsin", "arccos",
    "arctan", "sinh", "cosh", "tanh", "log", "ln", "lg", "exp",
    "min", "max", "det", "dim", "ker", "deg", "gcd", "sup", "inf",
    "lim", "arg", "Re", "Im",
)

_ARG_RE = re.compile(r"\{([^{}]*)\}")
_SCRIPT_RE = re.compile(
    r"([_^])(\{[^{}]*\}|\\[A-Za-z]+|[A-Za-z0-9])")
_CMD_RE = re.compile(r"\\([A-Za-z]+)")


def latex_to_html(latex: str) -> str:
    """单条 LaTeX 公式 -> HTML 片段(近似渲染).

    上下标用 sup/sub, \\frac 用斜杠分数, \\sqrt 用根号字符,
    符号命令查表; 未识别的命令去掉反斜杠保留原词.
    """
    text = latex.strip().strip("$")
    text = _convert_commands(_convert_scripts(text))
    return ('<span style="font-family:\'Noto Serif\';'
            f'font-style:italic;">{text}</span>')


def extract_formulas(text: str) -> tuple:
    """把正文里的公式替换为占位符.

    返回 (新文本, [公式原文, ...]); 行间 $$..$$ 与行内 $..$ 都处理,
    连续 $$ 视为行间定界. 占位符在 markdown 渲染后仍保持原样, 供
    富文本层替换回公式 HTML.
    """
    formulas = []
    pattern = re.compile(r"\$\$(.+?)\$\$|(?<!\$)\$([^$\n]+?)\$(?!\$)",
                         re.DOTALL)
    result = pattern.sub(lambda m: _stash(m, formulas), text)
    return result, formulas


def placeholder_indices(text: str):
    """按出现顺序产出 (占位符起始位置, 公式序号); 无则空."""
    return [(match.start(), int(match.group(1)))
            for match in _PLACEHOLDER_RE.finditer(text)]


def _stash(match, formulas: list) -> str:
    """记录公式原文并返回占位符(保留 $ 定界便于识别)."""
    formulas.append(match.group(0))
    return PLACEHOLDER_FMT.format(len(formulas) - 1)


def _take_arg(text: str, pos: int) -> tuple:
    """从 pos 起取一个 {...} 参数; 返回 (内容, 结束位置), 无参数取单字符."""
    match = _ARG_RE.match(text, pos)
    if match:
        return match.group(1), match.end()
    if pos < len(text):
        return text[pos], pos + 1
    return "", pos


_THIN_SPACES = ("\\,", "\\;", "\\!", "\\ ")


def _convert_commands(text: str) -> str:
    """符号命令查表; \\frac/\\sqrt 递归; 函数名直立; 其余去反斜杠."""
    output = []
    pos = 0
    while pos < len(text):
        if text[pos] == "\\":
            match = _CMD_RE.match(text, pos)
            if match is None:
                if text[pos:pos + 2] in _THIN_SPACES:
                    output.append("\u2009")
                    pos += 2
                else:
                    pos += 1       # 转义字符(如 \$)本身丢弃
                continue
            converted = _convert_command(match.group(1), text, match)
            replacement, end = converted if converted \
                else (match.group(1), match.end())
            output.append(replacement)
            pos = end
            continue
        if text[pos:pos + 2] in _THIN_SPACES:
            output.append("\u2009")
            pos += 2
            continue
        if text[pos] in "{}":
            pos += 1               # 裸花括号分组: 去括号保内容
            continue
        output.append(text[pos])
        pos += 1
    return "".join(output)


def _convert_command(name: str, text: str, match) -> tuple:
    """单个命令 -> (替换文本, 结束位置); 不认识的命令返回 None."""
    if name in ("frac", "dfrac", "tfrac"):
        numerator, end = _take_arg(text, match.end())
        denominator, end2 = _take_arg(text, end)
        return (f"({_convert_commands(numerator)})"
                f"/({_convert_commands(denominator)})", end2)
    if name == "sqrt":
        # \sqrt{x} 根号体; \sqrt[n]{x} 方括号先取次数
        end = match.end()
        index = ""
        if text[end:end + 1] == "[":
            close = text.find("]", end)
            if close >= 0:
                index = text[end + 1:close]
                end = close + 1
        radicand, end2 = _take_arg(text, end)
        prefix = (_convert_scripts(index) + "\u221a") if index else "\u221a"
        return f"{prefix}({_convert_commands(radicand)})", end2
    if name in ("mathbb", "mathcal"):
        inner, end = _take_arg(text, match.end())
        symbol = _SYMBOLS.get(f"{name}{{{inner}}}")
        return (symbol if symbol else _convert_commands(inner)), end
    if name in ("text", "mathrm", "mathbf", "operatorname"):
        inner, end = _take_arg(text, match.end())
        upright = _convert_commands(inner)
        if name == "mathbf":
            upright = f"<b>{upright}</b>"
        return upright, end
    if name in _FUNCTION_NAMES:
        return name, match.end()
    if name in ("left", "right"):
        pos = match.end()
        if pos < len(text) and text[pos] != ".":
            return text[pos], pos + 1
        return "", pos
    if name in ("quad", "qquad"):
        return "  ", match.end()
    if name in _SYMBOLS:
        return _SYMBOLS[name], match.end()
    if match.group(0) == "\\":
        return "  ", match.end()
    return None


def _convert_scripts(text: str) -> str:
    """^ 与 _ -> sup/sub(先下标后上标的组合顺序无碍)."""
    def repl(match):
        mark, body = match.group(1), match.group(2)
        inner = body[1:-1] if body.startswith("{") else body
        inner = _convert_commands(inner)
        tag = "sup" if mark == "^" else "sub"
        return f"<{tag}>{inner}</{tag}>"
    previous = None
    while previous != text:
        previous = text
        text = _SCRIPT_RE.sub(repl, text)
    return text
