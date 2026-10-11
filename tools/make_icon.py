# -*- coding: utf-8 -*-
"""图标生成脚本 —— 纯代码绘制，可复现，无需二进制素材。

按源图（68x66 的灰度小电视）实测几何比例重绘为 B站蓝 (#00A1D6) 版本，
以 2048px 超采样绘制后降采样，保证各尺寸边缘平滑。

源图实测几何：
    天线  y 0..10      两条圆头粗线，上宽下窄（V 形张开）
    机身  x 0.8..66.2, y 9.5..61.5    圆角矩形，边框厚约 8
    屏幕  x 9..58.5,   y 17.5..54     内部
    眼睛  左 中心(20.7,30.4) 右 中心(47.3,30.4)，约 15x6.5，内侧偏高
    嘴    x 24..43,    y 42..47       "w" 形（两个下弧）
    脚    左 x13..20.5 右 x47..54.5，y 61.5..66

用法：
    python tools/make_icon.py
产出（写到仓库根目录）：
    app.ico           多分辨率 ICO，用于 exe 图标与运行时窗口图标
    app_icon.png      256px PNG，用于 Tk 的 iconphoto
    app_icon_128.png  128px PNG
"""
import os
import sys

from PIL import Image, ImageDraw

SRC_W, SRC_H = 68.0, 66.0
SS = 2048                                   # 超采样画布边长
K = SS / SRC_W
YOFF = (SS - SRC_H * K) / 2.0

BLUE = (0, 161, 214, 255)                   # #00A1D6  B站蓝
WHITE = (255, 255, 255, 255)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def X(x):
    return x * K


def Y(y):
    return y * K + YOFF


def thick_line(d, pts, width_px, color):
    """带圆头的粗折线"""
    d.line(pts, fill=color, width=int(round(width_px)), joint="curve")
    r = width_px / 2.0
    for (px, py) in (pts[0], pts[-1]):
        d.ellipse([px - r, py - r, px + r, py + r], fill=color)


def make_master():
    img = Image.new("RGBA", (SS, SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 天线粗细：源图横向跨度约 5px，按倾角换算垂直笔宽约 4px。
    # 略加粗到 4.6 是为了 16x16 时天线不至于细到看不见。
    lw = int(round(4.6 * K))

    # 天线：两条圆头粗线，V 形（上端外张、下端收拢到机身顶）
    thick_line(d, [(X(19.5), Y(1.2)), (X(24.6), Y(11.0))], lw, BLUE)
    thick_line(d, [(X(48.2), Y(1.2)), (X(43.1), Y(11.0))], lw, BLUE)

    # 脚：两个小圆角块（先画，机身会盖住上缘）
    r = int(round(2.4 * K))
    d.rounded_rectangle([X(12.6), Y(60.0), X(20.4), Y(65.6)], radius=r, fill=BLUE)
    d.rounded_rectangle([X(47.2), Y(60.0), X(55.0), Y(65.6)], radius=r, fill=BLUE)

    # 机身：蓝色圆角矩形
    d.rounded_rectangle([X(0.9), Y(9.6), X(66.1), Y(61.4)],
                        radius=int(round(11.0 * K)), fill=BLUE)

    # 屏幕：白色圆角矩形（留出约 8px 蓝边）
    d.rounded_rectangle([X(8.9), Y(17.6), X(58.1), Y(53.4)],
                        radius=int(round(5.0 * K)), fill=WHITE)

    # 眼睛：倾斜圆角块。左眼右端偏高，右眼左端偏高（内侧偏高）
    eye_len, eye_th = 14.6, 6.4
    for (cx, cy, ang) in ((20.7, 30.4, -13.0), (47.3, 30.4, 13.0)):
        pad = int(eye_len * K) + 20
        layer = Image.new("RGBA", (pad, pad), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        hw, hh = eye_len * K / 2.0, eye_th * K / 2.0
        c = pad / 2.0
        ld.rounded_rectangle([c - hw, c - hh, c + hw, c + hh], radius=hh, fill=BLUE)
        layer = layer.rotate(-ang, resample=Image.BICUBIC, expand=False)
        img.alpha_composite(layer, (int(X(cx) - c), int(Y(cy) - c)))

    # 嘴：两个下弧组成 "w"
    mw = int(round(4.6 * K))
    a, b = 24.5, 42.6
    depth = 4.4
    pts = []
    for i in range(41):
        t = i / 40.0
        x = a + (b - a) * t
        u = (t * 2.0) % 1.0
        y = 42.7 + depth * (1 - (2 * u - 1) ** 2)
        pts.append((X(x), Y(y)))
    thick_line(d, pts[:21], mw, BLUE)
    thick_line(d, pts[20:], mw, BLUE)

    return img


def main():
    master = make_master()
    targets = [256, 128, 64, 48, 32, 24, 16]
    frames = [master.resize((s, s), Image.LANCZOS) for s in targets]

    ico = os.path.join(ROOT, "app.ico")
    frames[0].save(ico, format="ICO", sizes=[(s, s) for s in targets])
    frames[0].save(os.path.join(ROOT, "app_icon.png"))
    frames[1].save(os.path.join(ROOT, "app_icon_128.png"))
    print("生成完成：")
    for p in (ico, os.path.join(ROOT, "app_icon.png"),
              os.path.join(ROOT, "app_icon_128.png")):
        print("  %s  (%d bytes)" % (p, os.path.getsize(p)))


if __name__ == "__main__":
    main()
