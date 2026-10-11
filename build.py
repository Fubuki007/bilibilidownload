# -*- coding: utf-8 -*-
"""打包脚本：把 bili_downloader.py 打成单文件 exe。

用法：
    python build.py

要求：
    pip install pyinstaller
    pip install -U yt-dlp          （yt-dlp 会被打进包里）

产物：
    dist/B站视频下载器.exe

注意（踩过的坑）：
    若 Python 解释器与 Tcl/Tk 数据目录不在同一层级（例如解释器在 D 盘、
    而虚拟环境建在 E 盘），PyInstaller 的 Tcl/Tk 探测会失败，进而**静默剔除
    tkinter** —— 打包零报错，运行时却报 `No module named 'tkinter'`。
    本脚本会先把 TCL_LIBRARY / TK_LIBRARY 探测出来并写入环境变量，
    再调用 PyInstaller，从而绕开该问题。
"""
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
NAME = "B站视频下载器"
ENTRY = os.path.join(ROOT, "bili_downloader.py")


def detect_tcl_tk():
    """探测 Tcl/Tk 数据目录，返回 (tcl_dir, tk_dir) 或 (None, None)"""
    for base in {getattr(sys, "base_prefix", sys.prefix), sys.prefix}:
        for sub in ("tcl", "lib"):
            root = os.path.join(base, sub)
            if not os.path.isdir(root):
                continue
            try:
                entries = os.listdir(root)
            except OSError:
                continue
            for name in entries:
                if not name.lower().startswith("tcl"):
                    continue
                tcl_dir = os.path.join(root, name)
                if not os.path.isfile(os.path.join(tcl_dir, "init.tcl")):
                    continue
                tk_dir = os.path.join(root, "tk" + name[3:])
                return tcl_dir, (tk_dir if os.path.isdir(tk_dir) else None)
    return None, None


def main():
    # 先确保图标存在
    if not os.path.isfile(os.path.join(ROOT, "app.ico")):
        try:
            import PIL  # noqa: F401
        except ImportError:
            print("app.ico 不存在，且未安装 Pillow（生成图标需要）。")
            print("请先执行：  pip install pillow")
            print("或手动放置一个 app.ico 到项目根目录。")
            return 1
        print("[1/3] app.ico 不存在，调用 tools/make_icon.py 生成…")
        subprocess.check_call([sys.executable, os.path.join(ROOT, "tools", "make_icon.py")])
    else:
        print("[1/3] app.ico 已存在")

    # Tcl/Tk 环境变量（必须，否则 tkinter 会被剔除）
    tcl, tk = detect_tcl_tk()
    if tcl:
        os.environ.setdefault("TCL_LIBRARY", tcl)
        if tk:
            os.environ.setdefault("TK_LIBRARY", tk)
        print("[2/3] TCL_LIBRARY = %s" % tcl)
        if tk:
            print("      TK_LIBRARY  = %s" % tk)
    else:
        print("[2/3] 警告：未探测到 Tcl/Tk 数据目录，tkinter 可能无法打包")

    # 清理旧产物
    for d in ("build", "dist"):
        p = os.path.join(ROOT, d)
        if os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)

    print("[3/3] 调用 PyInstaller…")
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--onefile", "--noconsole",
        "--name", NAME,
        "--icon", os.path.join(ROOT, "app.ico"),
        "--add-data", "%s%s." % (os.path.join(ROOT, "app.ico"), os.pathsep),
        "--add-data", "%s%s." % (os.path.join(ROOT, "app_icon.png"), os.pathsep),
        "--add-data", "%s%s." % (os.path.join(ROOT, "app_icon_128.png"), os.pathsep),
        "--hidden-import", "yt_dlp",
        "--exclude-module", "imageio",
        "--exclude-module", "imageio_ffmpeg",
        ENTRY,
    ]
    rc = subprocess.call(cmd, cwd=ROOT)
    if rc != 0:
        print("打包失败，退出码 %d" % rc)
        return rc

    exe = os.path.join(ROOT, "dist", NAME + ".exe")
    if os.path.isfile(exe):
        print("\n完成：%s  (%.1f MB)" % (exe, os.path.getsize(exe) / 1024.0 / 1024.0))
        print("发布时请把 ffmpeg\\ 目录与 exe 放在同一层。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
