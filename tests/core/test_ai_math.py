"""ai_math.py 单元测试: LaTeX 子集转 HTML 与公式提取."""

from shuimu.core.ai_math import (
    extract_formulas,
    latex_to_html,
    placeholder_indices,
)


def _body(html: str) -> str:
    """去掉外层 span 取正文."""
    return html[html.index(">") + 1:html.rindex("<")]


class TestLatexToHtml:
    """公式转换."""

    def test_symbols(self):
        """希腊字母与运算符查表."""
        assert _body(latex_to_html(r"\alpha + \beta \times \pi")) \
            == "α + β × π"
        assert _body(latex_to_html(r"x \leq y \neq z")) == "x ≤ y ≠ z"

    def test_scripts(self):
        """上下标与嵌套."""
        assert _body(latex_to_html("x^2")) == "x<sup>2</sup>"
        assert _body(latex_to_html("y_{n}")) == "y<sub>n</sub>"
        assert _body(latex_to_html("x^{a_i}")) == "x<sup>a<sub>i</sub></sup>"

    def test_frac_and_sqrt(self):
        """分数与根号(含次数)."""
        assert _body(latex_to_html(r"\frac{a}{b}")) == "(a)/(b)"
        assert _body(latex_to_html(r"\sqrt{x+1}")) == "√(x+1)"
        assert _body(latex_to_html(r"\sqrt[3]{8}")) == "3√(8)"

    def test_functions_upright(self):
        """函数名与 text 直立体."""
        assert _body(latex_to_html(r"\sin x + \log y")) == "sin x + log y"
        assert _body(latex_to_html(r"\text{（元）}")) == "（元）"

    def test_delimiters(self):
        """\\left( \\right) 括号保留, \\left. 丢弃."""
        assert _body(latex_to_html(r"\left(x\right)^2")) == "(x)<sup>2</sup>"

    def test_blackboard(self):
        """mathbb 字母映射."""
        assert _body(latex_to_html(r"A \in \mathbb{R}")) == "A ∈ ℝ"

    def test_unknown_command_kept(self):
        """未知命令去反斜杠保留词."""
        assert "foo" in _body(latex_to_html(r"\foo{1}"))

    def test_thin_space(self):
        """薄空格族转窄空格."""
        assert " " in _body(latex_to_html(r"f(x)\,dx"))


class TestExtractFormulas:
    """正文公式提取与占位符."""

    def test_inline_and_display(self):
        """行内 $..$ 与行间 $$..$$ 都提取."""
        text = "面积 $S=\\pi r^2$ 满足 $$V = \\frac{4}{3}\\pi r^3$$ 成立"
        plain, formulas = extract_formulas(text)
        assert "$" not in plain
        assert len(formulas) == 2
        assert formulas[0] == "$S=\\pi r^2$"
        assert "frac" in formulas[1]

    def test_placeholder_roundtrip(self):
        """占位符序号可定位, 不与正文混淆."""
        plain, formulas = extract_formulas("先 $a$ 后 $b$")
        positions = placeholder_indices(plain)
        assert [index for _pos, index in positions] == [0, 1]
        del formulas

    def test_no_formulas(self):
        """无公式的原文不动."""
        plain, formulas = extract_formulas("普通文本 100% 无公式")
        assert plain == "普通文本 100% 无公式"
        assert not formulas
