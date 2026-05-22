#!/usr/bin/env python3
"""
Bad Apple!! — 控制台 ASCII 动画播放器

Usage:
    python bad_apple.py                  # 自动尝试下载视频
    python bad_apple.py --video a.mp4    # 使用本地视频文件
    python bad_apple.py -w 120 --fps 15  # 自定义参数
    python bad_apple.py --invert         # 反转颜色

首次运行会从视频生成缓存，之后直接从缓存播放。
"""

import os
import sys
import time
import zlib
import pickle
import shutil
import subprocess
import argparse
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.resolve()
CACHE_FILE = PROJECT_DIR / ".bad_apple_cache"
DEFAULT_VIDEO_NAME = "bad_apple_video.mp4"

# 多源 URL：Bilibili 优先（国内可访问），YouTube 备用
VIDEO_URLS = [
    "https://www.bilibili.com/video/BV1x5411o7Kn",   # Bilibili Bad Apple
    "https://www.youtube.com/watch?v=FtutLA63Cp8",    # YouTube
]

DEFAULT_CHARS = "@%#*+=-:. "   # 暗→亮 灰度字符集


def setup_console():
    """配置控制台：启用 ANSI 转义 + UTF-8 编码。"""
    if os.name == "nt":
        # 启用虚拟终端（ANSI 转义）
        import ctypes
        try:
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-11)
            mode = ctypes.c_uint32()
            kernel32.GetConsoleMode(handle, ctypes.byref(mode))
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
        except Exception:
            pass
        # 设置控制台代码页为 UTF-8
        try:
            subprocess.run(["chcp", "65001"], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, shell=True)
        except Exception:
            pass
    # 确保 Python stdout 使用 UTF-8
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def install_pip(package, import_name=None):
    """安装 pip 包（如未安装）并验证导入。"""
    if import_name is None:
        import_name = package.replace("-", "_").replace("opencv-python-headless", "cv2")
    try:
        __import__(import_name)
    except ImportError:
        print(f"正在安装 {package}...")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "-q", package],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def find_local_video():
    """在项目目录下查找本地视频文件。"""
    for ext in (".mp4", ".mkv", ".webm", ".avi", ".flv"):
        for f in PROJECT_DIR.glob(f"*{ext}"):
            if f.stat().st_size > 100 * 1024:  # 大于 100KB，排除小文件
                return f
    return None


def download_video(video_path):
    """下载 Bad Apple!! 视频到指定路径。成功返回实际文件路径。"""
    if video_path.exists():
        size_mb = video_path.stat().st_size / (1024 * 1024)
        print(f"使用已缓存视频: {video_path.name} ({size_mb:.1f} MB)")
        return video_path

    install_pip("yt-dlp", "yt_dlp")
    import yt_dlp

    for url in VIDEO_URLS:
        try:
            print(f"尝试下载: {url}")
            ydl_opts = {
                "format": "bestvideo[height<=480]/best[height<=480]/best",
                "outtmpl": str(video_path),
                "quiet": True,
                "no_warnings": True,
                "ignoreerrors": True,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])

            # yt-dlp 可能会给文件追加格式后缀，如 .f100110.mp4
            if video_path.exists():
                print("下载完成！")
                return video_path

            # 尝试查找带后缀的视频文件并重命名
            parent = video_path.parent
            stem = video_path.stem
            for f in parent.glob(f"{stem}.*"):
                if f.suffix in (".mp4", ".mkv", ".webm", ".flv"):
                    f.rename(video_path)
                    print("下载完成！")
                    return video_path

        except Exception as e:
            print(f"  失败: {e}")
            continue

    return None


def generate_cache(video_path, width, height):
    """从视频提取所有帧的原始灰度数据，压缩保存到缓存文件。"""
    install_pip("opencv-python-headless", "cv2")
    install_pip("numpy")
    import cv2
    import numpy as np

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        print(f"错误: 无法打开视频 {video_path}")
        return False

    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if total <= 0:
        print("错误: 视频没有帧")
        cap.release()
        return False

    # 存储原始 uint8 灰度数据（每帧 width*height 字节）
    raw_frames = []
    print(f"正在处理 {total} 帧 ({width}x{height})...")

    for i in range(total):
        ret, frame = cap.read()
        if not ret:
            break
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        resized = cv2.resize(gray, (width, height))
        raw_frames.append(resized.tobytes())  # 原始 uint8 字节

        if (i + 1) % 500 == 0 or i == total - 1:
            pct = (i + 1) / total * 100
            print(f"  {i + 1}/{total} ({pct:.0f}%)")

    cap.release()

    if not raw_frames:
        print("错误: 未能提取任何帧")
        return False

    data = pickle.dumps({"width": width, "height": height, "raw_frames": raw_frames})
    compressed = zlib.compress(data, 9)
    CACHE_FILE.write_bytes(compressed)
    print(f"缓存已保存: {len(compressed) / 1024:.0f} KB ({len(raw_frames)} 帧)")
    return True


def load_cache():
    """加载并解压帧缓存。返回 (width, height, raw_frames)。"""
    compressed = CACHE_FILE.read_bytes()
    data = pickle.loads(zlib.decompress(compressed))
    return data["width"], data["height"], data["raw_frames"]


def render_frame(raw, width, height, chars, invert):
    """将原始灰度字节渲染为 ASCII 帧字符串（纯 Python，无依赖）。"""
    n = len(chars)
    lines = []
    for y in range(height):
        offset = y * width
        if invert:
            line = "".join(chars[(n - 1) - (raw[offset + x] * (n - 1) // 255)]
                           for x in range(width))
        else:
            line = "".join(chars[raw[offset + x] * (n - 1) // 255]
                           for x in range(width))
        lines.append(line)
    return "\n".join(lines)


def get_console_size():
    """获取控制台大小（列, 行）。"""
    try:
        size = shutil.get_terminal_size()
        return size.columns, max(size.lines - 1, 10)
    except Exception:
        return 80, 30


def find_video_file():
    """查找视频文件：命令行指定 > 项目目录本地文件 > 下载。"""
    # 先检查项目目录是否有视频文件
    local = find_local_video()
    if local:
        print(f"发现本地视频: {local.name}")
        return local, False  # (path, need_download)

    # 没有本地文件，需要下载
    return PROJECT_DIR / DEFAULT_VIDEO_NAME, True


def play(args):
    """播放动画。"""
    # --- 确定视频文件 ---
    if args.video:
        video_path = Path(args.video)
        if not video_path.exists():
            print(f"错误: 视频文件不存在: {args.video}")
            sys.exit(1)
        need_download = False
    else:
        video_path, need_download = find_video_file()

    # --- 加载或生成缓存 ---
    if CACHE_FILE.exists():
        width, height, raw_frames = load_cache()
        print(f"从缓存加载: {len(raw_frames)} 帧 ({width}x{height})")
    else:
        if need_download:
            print("未找到本地视频，尝试在线下载...")
            downloaded = download_video(video_path)
            if not downloaded:
                print("\n下载失败。请手动下载 Bad Apple 视频，然后：")
                print(f"  方法1: 将视频文件放到项目目录下 (如 {PROJECT_DIR / DEFAULT_VIDEO_NAME})")
                print(f"  方法2: python bad_apple.py --video <视频路径>")
                print(f"\nBilibili: {VIDEO_URLS[0]}")
                sys.exit(1)
            video_path = downloaded  # 使用实际下载的文件路径

        if args.width and args.height:
            width, height = args.width, args.height
        else:
            width, height = get_console_size()
            width = width // 2 * 2  # 考虑字符长宽比，保持偶数宽度
            height = max(10, height - 1)

        print(f"画面尺寸: {width}x{height}")
        if not generate_cache(video_path, width, height):
            sys.exit(1)
        width, height, raw_frames = load_cache()

    # --- 播放设置 ---
    chars = args.chars or DEFAULT_CHARS
    invert = args.invert
    fps = args.fps or 30
    frame_duration = 1.0 / fps
    total = len(raw_frames)

    print(f"播放: {total} 帧 @ {fps} FPS | 字符: '{chars}'"
          f"{' | 反转' if invert else ''}")
    print("按 Ctrl+C 停止播放")
    time.sleep(1.5)

    # 隐藏光标，清屏
    sys.stdout.write("\033[?25l\033[2J\033[H")
    sys.stdout.flush()

    start = time.time()
    played = 0

    try:
        for i in range(total):
            frame_str = render_frame(raw_frames[i], width, height, chars, invert)
            sys.stdout.write("\033[H" + frame_str)
            sys.stdout.flush()
            played = i + 1

            target = start + (i + 1) * frame_duration
            delay = target - time.time()
            if delay > 0.001:
                time.sleep(delay)
            elif delay < -0.5:
                start = time.time() - (i + 1) * frame_duration
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\033[?25h\033[2J\033[H")  # 恢复光标，清屏
        sys.stdout.flush()
        elapsed = time.time() - start
        if played > 0:
            actual_fps = played / elapsed if elapsed > 0 else 0
            print(f"播放结束: {played}/{total} 帧 | "
                  f"用时 {elapsed:.1f}s | 实际 {actual_fps:.1f} FPS")


def main():
    parser = argparse.ArgumentParser(
        description="Bad Apple!! — 控制台 ASCII 动画播放器"
    )
    parser.add_argument("--video", type=str, help="本地视频文件路径")
    parser.add_argument("-w", "--width", type=int, help="画面宽度（字符数），默认自适应")
    parser.add_argument("-H", "--height", type=int, help="画面高度（行数），默认自适应")
    parser.add_argument("-f", "--fps", type=int, default=30, help="播放帧率 (默认: 30)")
    parser.add_argument("-i", "--invert", action="store_true", help="反转颜色")
    parser.add_argument("-c", "--chars", type=str, default=DEFAULT_CHARS,
                        help="自定义字符集，从暗到亮 (默认: '@%%#*+=-:. ')")
    args = parser.parse_args()

    setup_console()

    print("Bad Apple!! 控制台播放器")
    print("=" * 40 + "\n")

    play(args)


if __name__ == "__main__":
    main()
