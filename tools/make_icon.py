"""一次性生成应用图标: 离屏渲染 bench_icon 输出 assets/shuimu.ico 与 .png.

用法 (仓库根; Pillow 仅此处需要, 不入 requirements.txt):
    pip install pillow
    python tools/make_icon.py

说明: Qt 的 qico 插件只实现读取, 跨平台写 .ico 不可靠, 故经 QBuffer
转 PNG 字节后由 Pillow 组多帧 ICO; 逐尺寸独立调 bench_icon(size) 渲染
(Qt smooth 绘制质量优于事后缩放).
"""

import io
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image                              # noqa: E402
from PySide6.QtCore import QBuffer, QIODevice      # noqa: E402
from PySide6.QtWidgets import QApplication         # noqa: E402

from shuimu.gui.artwork import bench_icon          # noqa: E402

SIZES = (16, 24, 32, 48, 64, 128, 256)


def render_png(size: int) -> bytes:
    """渲染单尺寸 logo 为 PNG 字节."""
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    bench_icon(size).save(buf, "PNG")
    return buf.data().data()


def main() -> int:
    """生成 assets/shuimu.ico (多帧) 与 assets/shuimu.png (256px)."""
    _app = QApplication([])            # 离屏渲染需要实例存活
    os.makedirs("assets", exist_ok=True)
    frames = [Image.open(io.BytesIO(render_png(s))) for s in SIZES]
    frames[-1].save("assets/shuimu.ico", format="ICO",
                    append_images=frames[:-1],
                    sizes=[(s, s) for s in SIZES])
    with open("assets/shuimu.png", "wb") as fh:    # 256px, 文档/Linux 桌面用
        fh.write(render_png(256))
    print("assets/shuimu.ico 与 assets/shuimu.png 已生成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
