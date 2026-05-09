#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成 Unified Control Panel 高清桌面图标 (.ico)
每个尺寸独立绘制，确保小尺寸下清晰锐利
"""

from PIL import Image, ImageDraw, ImageFilter
import math

# 主题色
BG = (13, 17, 23)          # #0d1117
PANEL = (22, 27, 34)       # #161b22
BLUE = (88, 166, 255)      # #58a6ff
GREEN = (63, 185, 80)      # #3fb950
ORANGE = (240, 136, 62)    # #f0883e
PURPLE = (163, 113, 247)   # #a371f7
RED_DOT = (248, 81, 73)    # #f85149
YELLOW_DOT = (210, 153, 34) # #d99a22
BORDER = (48, 54, 61)      # #30363d
GRAY = (139, 148, 158)     # #8b949e

def draw_rounded_rect(draw, xy, radius, fill, outline=None, width=1):
    """绘制圆角矩形"""
    x1, y1, x2, y2 = xy
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)

def draw_circle(draw, center, radius, fill, outline=None, width=1):
    """绘制圆"""
    cx, cy = center
    draw.ellipse([cx-radius, cy-radius, cx+radius, cy+radius], fill=fill, outline=outline, width=width)

def hexagon_points(cx, cy, r):
    """正六边形顶点"""
    return [(cx + r * math.cos(math.radians(60*i - 30)),
             cy + r * math.sin(math.radians(60*i - 30))) for i in range(6)]

def draw_icon(size):
    """独立绘制指定尺寸的图标"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    s = size

    # 背景（圆角方形）
    corner = max(3, s // 10)
    draw_rounded_rect(draw, [1, 1, s-2, s-2], corner, BG, BORDER, max(1, s//128))

    # 标题栏高度
    title_h = max(6, s // 7)
    draw_rounded_rect(draw, [1, 1, s-2, title_h], corner, PANEL)
    # 标题栏底部分隔线
    draw.line([(1, title_h), (s-2, title_h)], fill=BORDER, width=max(1, s//128))

    # 三个窗口控制圆点
    dot_r = max(2, s // 32)
    dot_y = title_h // 2
    dot_x_start = max(4, s // 12)
    dot_spacing = max(6, s // 10)
    for i, color in enumerate([RED_DOT, ORANGE, GREEN]):
        draw_circle(draw, (dot_x_start + i * dot_spacing, dot_y), dot_r, color)

    # 标题文字（简化为一行横线）
    bar_w = s // 3
    bar_h = max(2, s // 60)
    bar_x = (s - bar_w) // 2
    bar_y = title_h // 2 - bar_h // 2
    draw_rounded_rect(draw, [bar_x, bar_y, bar_x + bar_w, bar_y + bar_h], bar_h//2, GRAY)

    # 内容区起始
    content_top = title_h + max(2, s // 25)

    # 四色标签指示条（简化版）
    if s >= 32:
        tab_h = max(2, s // 25)
        tab_w = (s * 3 // 4) // 4
        tab_gap = max(1, s // 40)
        tab_x_start = s // 8
        tab_colors = [BLUE, GREEN, ORANGE, PURPLE]
        for i in range(4):
            tx = tab_x_start + i * (tab_w + tab_gap)
            color = tab_colors[i] if i == 0 else (100, 110, 120)
            draw_rounded_rect(draw, [tx, content_top, tx + tab_w, content_top + tab_h], tab_h//2, color)

    # 中央核心图形区域
    center_y = (content_top + s) // 2 - max(2, s // 30)

    if s >= 48:
        # 六边形（大图标才有）
        hex_r = s // 6
        hex_pts = hexagon_points(s//2, center_y, hex_r)
        # 填充半透明
        draw.polygon(hex_pts, fill=(*BLUE, 40))
        draw.polygon(hex_pts, outline=BLUE, width=max(1, s//60))

        # 六边形中心绿点
        inner_r = max(3, s // 18)
        draw_circle(draw, (s//2, center_y), inner_r, GREEN)

        # 脉冲光环
        ring_r = max(4, s // 10)
        draw_circle(draw, (s//2, center_y), ring_r, None, (*GREEN, 120), max(1, s//80))

        # 连接线
        if s >= 64:
            for i in range(0, 6, 2):
                p1 = hex_pts[i]
                p2 = hex_pts[(i + 3) % 6]
                draw.line([p1, p2], fill=(*BLUE, 100), width=max(1, s//80))

    elif s >= 24:
        # 中等图标：简化为菱形+圆点
        r = s // 5
        pts = [(s//2, center_y-r), (s//2+r, center_y), (s//2, center_y+r), (s//2-r, center_y)]
        draw.polygon(pts, fill=(*BLUE, 60), outline=BLUE, width=max(1, s//50))
        draw_circle(draw, (s//2, center_y), max(2, s//15), GREEN)
    else:
        # 小图标：简化为一个圆点
        draw_circle(draw, (s//2, center_y), max(2, s//8), GREEN, BLUE, max(1, s//40))

    # 底部波形（仅大图标）
    if s >= 64:
        wave_y = s - s // 6
        wave_h = s // 10
        wave_w = s * 3 // 5
        wave_x = (s - wave_w) // 2
        points = []
        steps = min(30, s // 4)
        for i in range(steps + 1):
            x = wave_x + wave_w * i // steps
            phase = i / steps * math.pi * 3
            y = wave_y + int(wave_h * 0.4 * math.sin(phase))
            points.append((x, y))
        for i in range(len(points) - 1):
            draw.line([points[i], points[i+1]], fill=BLUE, width=max(1, s//50))

    # 底部四色方块（仅大图标）
    if s >= 48:
        sq_size = max(3, s // 20)
        sq_y = s - s // 12
        sq_x_start = s // 8
        sq_gap = max(3, s // 20)
        for i, color in enumerate([BLUE, GREEN, ORANGE, PURPLE]):
            sx = sq_x_start + i * (sq_size + sq_gap)
            draw_rounded_rect(draw, [sx, sq_y, sx + sq_size, sq_y + sq_size], sq_size//3, color)

    # 外圈发光（仅大图标）
    if s >= 64:
        glow = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        glow_draw = ImageDraw.Draw(glow)
        glow_draw.rounded_rectangle([0, 0, s-1, s-1], radius=corner, outline=(*BLUE, 30), width=max(2, s//80))
        img = Image.alpha_composite(img, glow)

    return img


def main():
    # 生成所有标准 ICO 尺寸
    sizes = [16, 24, 32, 48, 64, 96, 128, 256, 512]
    icons = []
    for sz in sizes:
        icon = draw_icon(sz)
        icons.append(icon)
        print(f"Generated {sz}x{sz}")

    # 保存 ICO（包含所有尺寸）
    ico_path = r"E:\项目\变电站图像监控运维平台\unified-panel.ico"
    # Pillow 的 ICO 保存：第一个图像作为基础，append_images 添加其余
    icons[0].save(
        ico_path,
        format="ICO",
        sizes=[(sz, sz) for sz in sizes],
        append_images=icons[1:],
    )
    print(f"ICO saved: {ico_path}")

    # 保存高分辨率 PNG 预览
    png_512 = draw_icon(512)
    png_path = r"E:\项目\变电站图像监控运维平台\unified-panel-512.png"
    png_512.save(png_path, format="PNG")
    print(f"PNG preview: {png_path}")

    # 保存 256 预览
    png_256 = draw_icon(256)
    png_path2 = r"E:\项目\变电站图像监控运维平台\unified-panel-256.png"
    png_256.save(png_path2, format="PNG")
    print(f"PNG preview: {png_path2}")

    # 保存小尺寸预览对比
    for sz in [16, 32, 48]:
        small = draw_icon(sz)
        # 放大到 256 以便看清细节
        preview = small.resize((256, 256), Image.Resampling.NEAREST)
        preview.save(rf"E:\项目\变电站图像监控运维平台\unified-panel-{sz}preview.png", format="PNG")
        print(f"Preview {sz}x{sz} -> 256x256 (nearest)")


if __name__ == "__main__":
    main()
