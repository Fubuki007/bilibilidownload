# -*- coding: utf-8 -*-
"""
B站视频下载器 - 图形界面
粘贴链接 -> 解析 -> 选清晰度 -> 下载
依赖：yt-dlp（作为库调用，非子进程）

用法：
  双击运行            图形界面
  --url <链接> [...]   无界面下载（供脚本调用 / 自动化验证）
"""

import os
import sys
import re
import time
import threading
import queue
import shutil

APP_NAME = "B站视频下载器"
VERSION = "1.1"


# ---------------------------------------------------------------- Tcl/Tk 修复

def ensure_tcl_tk_env():
    """修正 Tcl/Tk 库路径，让 tkinter 在 venv / 打包环境下也能正常初始化。

    背景：当 Python 解释器与它的 Tcl/Tk 数据目录不在同一层级时（典型场景是
    基础解释器装在 D 盘、venv 建在 E 盘），Tcl 会按解释器所在盘符去猜
    ``<盘符>/lib/tcl8.6`` 之类的路径；猜错就抛
    ``Can't find a usable init.tcl in the following directories``，
    导致 tkinter 完全不可用（PyInstaller 也会因此判定"tkinter 损坏"并剔除它）。

    做法：显式提供 TCL_LIBRARY / TK_LIBRARY 指向真实数据目录。
    用 setdefault 写入 —— 只在变量缺失时生效，不覆盖已有的正确配置。
    """
    base = getattr(sys, "base_prefix", sys.prefix)
    roots = [
        os.path.join(base, "tcl"),
        os.path.join(sys.prefix, "tcl"),
        os.path.join(base, "lib"),
    ]
    for root in roots:
        if not os.path.isdir(root):
            continue
        try:
            entries = os.listdir(root)
        except OSError:
            continue
        for name in entries:
            low = name.lower()
            if not low.startswith("tcl"):
                continue
            tcl_dir = os.path.join(root, name)
            if not os.path.isfile(os.path.join(tcl_dir, "init.tcl")):
                continue
            tk_dir = os.path.join(root, "tk" + name[3:])
            os.environ.setdefault("TCL_LIBRARY", tcl_dir)
            if os.path.isdir(tk_dir):
                os.environ.setdefault("TK_LIBRARY", tk_dir)
            return True
    return False


# 必须早于 tkinter 导入
ensure_tcl_tk_env()

import tkinter as tk                       # noqa: E402
from tkinter import ttk, filedialog, messagebox   # noqa: E402

# ---------------------------------------------------------------- 路径/常量


def app_dir():
    """程序所在目录（兼容 PyInstaller onefile）"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def default_out_dir():
    for cand in (r"E:\B站下载", os.path.join(app_dir(), "downloads")):
        try:
            os.makedirs(cand, exist_ok=True)
            return cand
        except OSError:
            continue
    return os.path.join(os.path.expanduser("~"), "Downloads")


def resource_path(*parts):
    """打包后资源定位"""
    base = getattr(sys, "_MEIPASS", None) or app_dir()
    return os.path.join(base, *parts)


def find_ffmpeg_dir():
    """定位 ffmpeg 目录，返回所在目录或 None"""
    cands = [
        resource_path("ffmpeg"),
        resource_path("ffmpeg", "bin"),
        os.path.join(app_dir(), "ffmpeg"),
        os.path.join(app_dir(), "ffmpeg", "bin"),
        r"E:\bili-tool\ffmpeg",
        r"E:\bili-tool\ffmpeg\bin",
    ]
    for c in cands:
        if os.path.isfile(os.path.join(c, "ffmpeg.exe")):
            return c
    which = shutil.which("ffmpeg")
    if which:
        return os.path.dirname(which)
    return None


def find_asset(*names):
    """在打包资源目录和程序目录里找第一个存在的文件"""
    for name in names:
        for base in (resource_path(name), os.path.join(app_dir(), name)):
            if os.path.isfile(base):
                return base
    return None


# ---------------------------------------------------------------- 工具函数

URL_RE = re.compile(
    r"(?:https?://)?(?:www\.|m\.)?"
    r"(?:bilibili\.com|b23\.tv|bili2233\.cn)"
    r"[^\s，。！？、）)】]*",
    re.I,
)


# 裸编号：用户在 B 站复制时经常只拿到 BV 号（或老视频的 av 号），没有域名。
# BV 号为 "BV" + 10 位 Base58 字符；av 号为 "av" + 数字。
BV_RE = re.compile(r"(?<![0-9A-Za-z])(BV[0-9A-Za-z]{10})(?![0-9A-Za-z])")
AV_RE = re.compile(r"(?<![0-9A-Za-z])av(\d+)(?![0-9A-Za-z])", re.I)


def extract_url(text):
    """从任意文本里解析出 B 站视频链接。

    支持三种输入：
      1. 完整链接（bilibili.com / b23.tv 短链）
      2. 含链接的分享文案（自动提取第一条链接）
      3. 裸编号 BV1xxxxxxxxx / av123456（自动补全为完整链接）
    """
    if not text:
        return ""
    text = text.strip()

    m = URL_RE.search(text)
    if m:
        u = m.group(0)
        return u if u.startswith("http") else "https://" + u

    m = BV_RE.search(text)
    if m:
        return "https://www.bilibili.com/video/" + m.group(1)

    m = AV_RE.search(text)
    if m:
        return "https://www.bilibili.com/video/av" + m.group(1)

    return ""


def fmt_size(num):
    try:
        num = float(num)
    except (TypeError, ValueError):
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024 or unit == "GB":
            return "%.1f %s" % (num, unit)
        num /= 1024
    return "%.1f GB" % num


def fmt_speed(bps):
    if not bps or bps <= 0:
        return ""
    return "%s/s" % fmt_size(bps)


def fmt_eta(sec):
    try:
        sec = int(sec)
    except (TypeError, ValueError):
        return ""
    if sec < 0 or sec > 86400 * 30:
        return ""
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return "%d:%02d:%02d" % (h, m, s)
    return "%02d:%02d" % (m, s)


def human_duration(sec):
    try:
        sec = int(sec)
    except (TypeError, ValueError):
        return ""
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return ("%d:%02d:%02d" % (h, m, s)) if h else ("%d:%02d" % (m, s))


def sanitize(name, maxlen=80):
    bad = '<>:"/\\|?*\r\n\t'
    out = "".join("_" if c in bad else c for c in (name or "")).strip(" .")
    return (out or "video")[:maxlen]


# B 站同一清晰度会同时提供 avc1(H.264) / hvc1(H.265) / av01(AV1) 三种编码。
# yt-dlp 默认排序会选 AV1，但 AV1 在老播放器、VLC 及多数剪辑软件里兼容性很差，
# 因此统一锁死 H.264（必要时回退 H.265），保证下载产物「到处都能放」。
VCODEC_PREF = ("avc1", "hvc1")


def build_format_selector(height=None):
    """构造 format 表达式：优先 H.264，其次 H.265，最后任意编码兜底"""
    parts = []
    if height:
        for c in VCODEC_PREF:
            parts.append("bv[vcodec^=%s][height=%d]+ba" % (c, height))
        parts.append("bv[height=%d]+ba" % height)          # 编码不匹配时兜底
        parts.append("b[height=%d]" % height)
    else:
        for c in VCODEC_PREF:
            parts.append("bv[vcodec^=%s]+ba" % c)
        parts.append("bv+ba/b")
    return "/".join(parts)


def resolve_height(info, want):
    """把用户想要的分辨率（'best' / '720' / '720P'）解析为真实高度值。

    B 站返回的实际高度常是 718/478/358，与展示标签 720/480/360 不同，
    所以不能直接拿 720 去匹配，必须回到解析结果里找真实值。
    """
    if not want or str(want).strip().lower() in ("best", "max", "最高"):
        return None
    digits = re.sub(r"\D", "", str(want))
    if not digits:
        return None
    target = int(digits)
    opts = info.get("options") or []
    for o in opts:                                  # 标签精确匹配
        if re.sub(r"\D", "", o["label"]) == digits:
            return o["height"]
    cands = [o["height"] for o in opts if o["height"] <= target]
    if cands:                                       # 不超过目标的最高档
        return max(cands)
    return max((o["height"] for o in opts), default=None)   # 兜底：最高可用


# ---------------------------------------------------------------- yt-dlp 封装

def import_ytdlp():
    try:
        import yt_dlp  # noqa
        return yt_dlp
    except ImportError:
        return None


class ParseError(Exception):
    pass


def parse_video(url, cookies_from_browser=None, timeout=45):
    """解析视频元信息，返回 dict"""
    ydl = import_ytdlp()
    if ydl is None:
        raise ParseError(
            "未找到 yt-dlp 模块。\n\n"
            "若使用打包版 exe，请重新下载完整压缩包解压后运行；\n"
            "若使用源码版，请先执行：pip install -U yt-dlp"
        )

    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        "socket_timeout": timeout,
        "http_headers": {
            "Referer": "https://www.bilibili.com/",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
            ),
        },
    }
    if cookies_from_browser:
        opts["cookiesfrombrowser"] = (cookies_from_browser,)

    try:
        with ydl.YoutubeDL(opts) as y:
            info = y.extract_info(url, download=False)
    except Exception as exc:  # yt-dlp 异常类型很杂
        msg = str(exc)
        # 常见场景给更友好的提示
        lowered = msg.lower()
        if "private" in lowered or "大会员" in msg or "会员" in msg:
            msg = ("该视频是大会员/私有内容，需要登录 Cookie。\n"
                   "请在「设置」里启用「从浏览器读取 Cookie」后重试。\n\n" + msg)
        elif "404" in lowered or "not available" in lowered:
            msg = "视频不存在或已被删除。\n\n" + msg
        elif "login" in lowered or "登录" in msg:
            msg = ("需要登录才能访问，可启用「从浏览器读取 Cookie」。\n\n" + msg)
        elif "certificate" in lowered or "ssl" in lowered:
            msg = ("网络证书错误。请检查系统代理或尝试关闭代理后重试。\n\n" + msg)
        raise ParseError(msg)

    if not info:
        raise ParseError("解析失败：未获取到视频信息")

    # 收集可选清晰度（去重、按高度降序）
    # 注意：B 站 DASH 常返回 718/478/358 这类非偶数分辨率，
    # 展示时向上取整到标准档位（720P/480P/360P），但筛选仍用真实高度。
    # 体积预估只统计「最终会下载的编码」（VCODEC_PREF 优先），
    # 否则会显示 AV1 的小体积而实际下载更大的 H.264，对用户有误导。
    raw_heights = {}
    for f in (info.get("formats") or []):
        if not f:
            continue
        h = f.get("height")
        vcodec = (f.get("vcodec") or "").lower()
        if not h or vcodec in ("none", ""):
            continue
        size = f.get("filesize") or f.get("filesize_approx") or 0
        # 编码优先级：avc1=0 最好，hvc1=1 次之，其他=2
        if vcodec.startswith(VCODEC_PREF[0]):
            rank = 0
        elif vcodec.startswith(VCODEC_PREF[1]):
            rank = 1
        else:
            rank = 2
        cur = raw_heights.get(h)
        if cur is None or rank < cur["rank"]:
            raw_heights[h] = {"height": h, "ext": f.get("ext") or "?",
                             "size": size, "rank": rank}
        elif rank == cur["rank"] and size > cur["size"]:
            cur["size"] = size

    def ceil_to_ladder(h):
        for step in (240, 360, 480, 720, 1080, 1440, 2160, 4320):
            if h <= step:
                return step
        return h

    # 以「真实高度」为键，展示标签归一化后去重
    options = []
    used_labels = set()
    for h in sorted(raw_heights, reverse=True):
        info_h = raw_heights[h]
        label = "%dP" % ceil_to_ladder(h)
        if label in used_labels:
            continue          # 同档位只保留最高的那个真实高度
        used_labels.add(label)
        options.append({"height": h, "label": label,
                        "ext": info_h["ext"], "size": info_h["size"]})

    total = info.get("filesize") or info.get("filesize_approx") or 0

    return {
        "title": info.get("title") or "未知标题",
        "uploader": info.get("uploader") or info.get("channel") or "未知UP主",
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail"),
        "webpage_url": info.get("webpage_url") or url,
        "options": options,
        "filesize": total,
        "is_live": bool(info.get("is_live")),
        "extractor": info.get("extractor_key") or "",
    }


# ---------------------------------------------------------------- 主界面

class App(tk.Tk):
    BG = "#1e1f26"
    FG = "#e6e6e6"
    SUB = "#9aa0ad"
    ACCENT = "#fb7299"      # B站粉
    ACCENT_DARK = "#d75b80"
    CARD = "#262833"
    BORDER = "#343846"

    def __init__(self):
        super().__init__()
        self.title("%s v%s" % (APP_NAME, VERSION))
        self.configure(bg=self.BG)
        self.geometry("880x640")
        self.minsize(780, 560)
        self.resizable(True, True)

        self.msg_q = queue.Queue()
        self.cancel_flag = threading.Event()
        self.worker = None
        self.current_video = None

        # 设置项（内存态，启动时载入配置文件）
        self.opt_out_dir = tk.StringVar(value=default_out_dir())
        self.opt_cookies = tk.BooleanVar(value=False)
        self.opt_cookie_browser = tk.StringVar(value="chrome")
        self.opt_limit = tk.StringVar(value="0")   # 0 = 不限速，数字 = KB/s

        self._load_config()
        self._build_ui()
        self._set_window_icon()
        self._poll_messages()

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(300, self._auto_paste)

    # ---------------------------------------------------------- 配置持久化
    @property
    def config_path(self):
        return os.path.join(app_dir(), "bili_dl_config.json")

    def _load_config(self):
        import json
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            if cfg.get("out_dir") and os.path.isdir(cfg["out_dir"]):
                self.opt_out_dir.set(cfg["out_dir"])
            self.opt_cookies.set(bool(cfg.get("cookies", False)))
            self.opt_cookie_browser.set(cfg.get("browser", "chrome"))
            self.opt_limit.set(str(cfg.get("limit", 0)))
        except Exception:
            pass

    def _save_config(self):
        import json
        data = {
            "out_dir": self.opt_out_dir.get(),
            "cookies": bool(self.opt_cookies.get()),
            "browser": self.opt_cookie_browser.get(),
            "limit": self.opt_limit.get(),
        }
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    # ---------------------------------------------------------- UI 构建
    def _build_ui(self):
        self._style()
        self._build_header()
        self._build_input_card()
        self._build_video_card()
        self._build_log_card()
        self._build_footer()

    def _set_window_icon(self):
        """设置窗口与任务栏图标（小电视）。

        exe 自身的图标由打包时的 --icon 决定；这里管的是运行时窗口图标。
        图标文件缺失时静默跳过，不影响程序使用。
        """
        self._icon_ref = None       # 持有引用，防止 PhotoImage 被 GC
        ico = find_asset("app.ico")
        if ico:
            try:
                self.iconbitmap(default=ico)
                return
            except tk.TclError:
                pass
        png = find_asset("app_icon.png", "app_icon_128.png")
        if png:
            try:
                img = tk.PhotoImage(file=png)
                self.iconphoto(True, img)
                self._icon_ref = img
            except tk.TclError:
                pass

    def _style(self):
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure("TFrame", background=self.BG)
        st.configure("Card.TFrame", background=self.CARD)
        st.configure("TLabel", background=self.BG, foreground=self.FG,
                     font=("Microsoft YaHei UI", 10))
        st.configure("Card.TLabel", background=self.CARD, foreground=self.FG,
                     font=("Microsoft YaHei UI", 10))
        st.configure("Sub.TLabel", background=self.CARD, foreground=self.SUB,
                     font=("Microsoft YaHei UI", 9))
        st.configure("CardSub.TLabel", background=self.CARD, foreground=self.SUB,
                     font=("Microsoft YaHei UI", 9))
        st.configure("H1.TLabel", background=self.BG, foreground=self.FG,
                     font=("Microsoft YaHei UI", 15, "bold"))
        st.configure("Title.TLabel", background=self.CARD, foreground=self.FG,
                     font=("Microsoft YaHei UI", 12, "bold"))
        st.configure("TButton", font=("Microsoft YaHei UI", 10),
                     lightcolor=self.BORDER, darkcolor=self.BORDER,
                     bordercolor=self.BORDER)
        st.configure("Accent.TButton", background=self.ACCENT, foreground="#ffffff",
                     font=("Microsoft YaHei UI", 10, "bold"), padding=(14, 7),
                     lightcolor=self.ACCENT, darkcolor=self.ACCENT,
                     bordercolor=self.ACCENT)
        st.map("Accent.TButton",
               background=[("active", self.ACCENT_DARK),
                           ("disabled", "#5a4048")],
               lightcolor=[("active", self.ACCENT_DARK),
                           ("disabled", "#5a4048")],
               darkcolor=[("active", self.ACCENT_DARK),
                          ("disabled", "#5a4048")])
        st.configure("Ghost.TButton", background=self.CARD, foreground=self.FG,
                     font=("Microsoft YaHei UI", 9), padding=(10, 5),
                     lightcolor=self.BORDER, darkcolor=self.BORDER,
                     bordercolor=self.BORDER)
        st.map("Ghost.TButton",
               background=[("active", self.BORDER), ("disabled", self.CARD)],
               foreground=[("disabled", self.SUB)],
               lightcolor=[("active", self.BORDER), ("disabled", self.CARD)],
               darkcolor=[("active", self.BORDER), ("disabled", self.CARD)])

        # 滚动条：clam 默认是亮色，需显式改深
        st.configure("Vertical.TScrollbar", background=self.CARD,
                     troughcolor=self.BG, bordercolor=self.BG,
                     arrowcolor=self.SUB, lightcolor=self.CARD,
                     darkcolor=self.CARD, relief="flat")
        st.map("Vertical.TScrollbar",
               background=[("active", self.BORDER), ("!active", self.CARD)],
               arrowcolor=[("active", self.FG)])
        st.configure("TCheckbutton", background=self.CARD, foreground=self.FG,
                     font=("Microsoft YaHei UI", 9))
        st.map("TCheckbutton",
               background=[("active", self.CARD)],
               foreground=[("disabled", self.SUB)])
        st.configure("TRadiobutton", background=self.CARD, foreground=self.FG,
                     font=("Microsoft YaHei UI", 10))
        st.map("TRadiobutton", background=[("active", self.CARD)])

        st.configure("TEntry", fieldbackground="#1b1c23", foreground=self.FG,
                     insertcolor=self.FG, borderwidth=1, relief="flat",
                     lightcolor=self.BORDER, darkcolor=self.BORDER, padding=4)
        st.map("TEntry",
               fieldbackground=[("disabled", "#20222a"), ("!disabled", "#1b1c23")],
               foreground=[("disabled", self.SUB), ("!disabled", self.FG)],
               bordercolor=[("focus", self.ACCENT), ("!focus", self.BORDER)])

        st.configure("TSpinbox", fieldbackground="#1b1c23", foreground=self.FG,
                     insertcolor=self.FG, borderwidth=1, relief="flat",
                     arrowcolor=self.FG, arrowsize=12,
                     lightcolor=self.BORDER, darkcolor=self.BORDER, padding=3)

        # Combobox：fieldbackground 与 readonly 态都要显式指定，
        # 否则 clam 主题会回落到白底，在深色界面上非常突兀
        st.configure("TCombobox", fieldbackground="#1b1c23", background="#1b1c23",
                     foreground=self.FG, arrowcolor=self.FG, arrowsize=13,
                     borderwidth=1, relief="flat",
                     lightcolor=self.BORDER, darkcolor=self.BORDER, padding=3)
        st.map("TCombobox",
               fieldbackground=[("readonly", "#1b1c23"), ("disabled", "#20222a")],
               foreground=[("readonly", self.FG), ("disabled", self.SUB)],
               background=[("readonly", "#1b1c23"), ("active", self.BORDER)],
               bordercolor=[("focus", self.ACCENT), ("!focus", self.BORDER)])
        # 下拉列表（原生 Listbox）不在 ttk 体系内，需用 option 数据库配色
        self.option_add("*TCombobox*Listbox.background", "#1b1c23")
        self.option_add("*TCombobox*Listbox.foreground", self.FG)
        self.option_add("*TCombobox*Listbox.selectBackground", self.ACCENT)
        self.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
        self.option_add("*TCombobox*Listbox.borderWidth", "0")

        # 进度条
        st.configure("Bar.Horizontal.TProgressbar",
                     troughcolor="#1b1c23", background=self.ACCENT,
                     borderwidth=0, thickness=14)
        st.configure("BarIdle.Horizontal.TProgressbar",
                     troughcolor="#1b1c23", background="#3a3f4d",
                     borderwidth=0, thickness=14)

    def _build_header(self):
        hdr = ttk.Frame(self)
        hdr.pack(fill="x", padx=18, pady=(14, 6))
        ttk.Label(hdr, text="B站视频下载器", style="H1.TLabel").pack(side="left")
        ttk.Label(hdr, text="粘贴链接 → 选清晰度 → 下载到本地",
                  style="TLabel", foreground=self.SUB).pack(side="left", padx=(12, 0))
        ttk.Button(hdr, text="设置", style="Ghost.TButton",
                   command=self.toggle_settings).pack(side="right")

    def _build_input_card(self):
        card = ttk.Frame(self, style="Card.TFrame", padding=14)
        card.pack(fill="x", padx=18, pady=(4, 10))

        row = ttk.Frame(card, style="Card.TFrame")
        row.pack(fill="x")
        self.url_var = tk.StringVar()
        self.url_entry = ttk.Entry(row, textvariable=self.url_var,
                                   font=("Consolas", 10))
        self.url_entry.pack(side="left", fill="x", expand=True, ipady=6)
        self.url_entry.bind("<Return>", lambda e: self.on_parse())

        ttk.Button(row, text="粘贴", style="Ghost.TButton",
                   command=self.on_paste).pack(side="left", padx=(8, 0))
        self.parse_btn = ttk.Button(row, text="解析", style="Accent.TButton",
                                    command=self.on_parse)
        self.parse_btn.pack(side="left", padx=(6, 0))

        self.hint = ttk.Label(
            card,
            text="支持 bilibili.com / b23.tv 短链接；可整段粘贴含链接的分享文案",
            style="CardSub.TLabel")
        self.hint.pack(anchor="w", pady=(8, 0))

        # 设置面板（默认隐藏）
        self.settings = ttk.Frame(card, style="Card.TFrame")
        self._build_settings_panel(self.settings)
        self._settings_visible = False

    def _build_settings_panel(self, parent):
        line = ttk.Frame(parent, style="Card.TFrame")
        line.pack(fill="x", pady=(12, 0))

        ttk.Label(line, text="保存到", style="Card.TLabel").pack(side="left")
        self.out_entry = ttk.Entry(line, textvariable=self.opt_out_dir,
                                   width=42, font=("Consolas", 9))
        self.out_entry.pack(side="left", padx=(8, 0), ipady=4)
        ttk.Button(line, text="浏览", style="Ghost.TButton",
                   command=self.on_choose_dir).pack(side="left", padx=(6, 0))

        line2 = ttk.Frame(parent, style="Card.TFrame")
        line2.pack(fill="x", pady=(8, 0))
        ttk.Checkbutton(line2, text="从浏览器读取 Cookie（大会员 / 高码率 / 需登录视频）",
                        variable=self.opt_cookies).pack(side="left")
        self.browser_box = ttk.Combobox(line2, textvariable=self.opt_cookie_browser,
                                         values=["chrome", "edge", "firefox",
                                                 "brave", "opera"],
                                         state="readonly", width=9)
        self.browser_box.pack(side="left", padx=(8, 0))
        self.browser_box.state(["disabled"])

        line3 = ttk.Frame(parent, style="Card.TFrame")
        line3.pack(fill="x", pady=(8, 0))
        ttk.Label(line3, text="限速", style="Card.TLabel").pack(side="left")
        ttk.Spinbox(line3, from_=0, to=102400, increment=128,
                    textvariable=self.opt_limit, width=8).pack(side="left", padx=(8, 0))
        ttk.Label(line3, text="KB/s（0 = 不限速）", style="CardSub.TLabel").pack(side="left", padx=(6, 0))

        ttk.Label(parent,
                  text="Cookie 只在下载时由 yt-dlp 从本机浏览器读取，不落盘保存。",
                  style="CardSub.TLabel").pack(anchor="w", pady=(10, 0))

    def _build_video_card(self):
        self.video_card = ttk.Frame(self, style="Card.TFrame", padding=(16, 14))
        self.video_card.pack(fill="x", padx=18, pady=(0, 10))

        # --- 标题区
        self.video_title = ttk.Label(self.video_card, text="尚未解析视频",
                                     style="Title.TLabel",
                                     wraplength=790, justify="left")
        self.video_title.pack(anchor="w")

        self.video_meta = ttk.Label(self.video_card, text="粘贴链接后点击「解析」",
                                    style="Sub.TLabel", justify="left")
        self.video_meta.pack(anchor="w", pady=(6, 0))

        # --- 分隔线
        sep = tk.Frame(self.video_card, bg=self.BORDER, height=1)
        sep.pack(fill="x", pady=(12, 0))

        # --- 操作区：清晰度 + 预估 + 下载按钮
        row = ttk.Frame(self.video_card, style="Card.TFrame")
        row.pack(fill="x", pady=(12, 0))

        ttk.Label(row, text="清晰度", style="Card.TLabel").pack(side="left")
        self.quality_box = ttk.Combobox(row, state="disabled", width=16,
                                        font=("Microsoft YaHei UI", 10))
        self.quality_box.pack(side="left", padx=(8, 0), ipady=2)
        self.quality_box.bind("<<ComboboxSelected>>", lambda e: self._update_estimate())

        self.est_label = ttk.Label(row, text="", style="Sub.TLabel")
        self.est_label.pack(side="left", padx=(12, 0))

        self.dl_btn = ttk.Button(row, text="开始下载", style="Accent.TButton",
                                 command=self.on_download, state="disabled")
        self.dl_btn.pack(side="right")
        self.cancel_btn = ttk.Button(row, text="停止", style="Ghost.TButton",
                                     command=self.on_cancel, state="disabled")
        self.cancel_btn.pack(side="right", padx=(0, 8))

    def _build_log_card(self):
        card = ttk.Frame(self, style="Card.TFrame", padding=(12, 10))
        card.pack(fill="both", expand=True, padx=18, pady=(0, 10))

        top = ttk.Frame(card, style="Card.TFrame")
        top.pack(fill="x")
        ttk.Label(top, text="运行日志", style="Card.TLabel").pack(side="left")

        self.progress = ttk.Progressbar(
            top, style="Bar.Horizontal.TProgressbar", maximum=100, value=0,
            length=300)
        self.progress.pack(side="right")
        self.status_label = ttk.Label(top, text="就绪", style="CardSub.TLabel")
        self.status_label.pack(side="right", padx=(0, 12))

        txt = tk.Text(card, height=10, bg="#17181e", fg="#c9ccd4",
                      insertbackground=self.FG, relief="flat", wrap="word",
                      font=("Consolas", 9), padx=10, pady=8)
        self.log = txt
        sb = ttk.Scrollbar(card, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        txt.pack(fill="both", expand=True, pady=(8, 0))
        txt.tag_configure("info", foreground="#c9ccd4")
        txt.tag_configure("ok", foreground="#7bd88f")
        txt.tag_configure("warn", foreground="#e5c07b")
        txt.tag_configure("err", foreground="#f07178")
        txt.tag_configure("cmd", foreground="#7aa2f7")
        txt.configure(state="disabled")

        btns = ttk.Frame(card, style="Card.TFrame")
        btns.pack(fill="x", pady=(8, 0))
        ttk.Button(btns, text="打开文件夹", style="Ghost.TButton",
                   command=self.on_open_dir).pack(side="left")
        ttk.Button(btns, text="清空日志", style="Ghost.TButton",
                   command=self.clear_log).pack(side="left", padx=(8, 0))

    def _build_footer(self):
        f = ttk.Frame(self)
        f.pack(fill="x", padx=18, pady=(0, 12))
        self.status_bar = ttk.Label(
            f,
            text="就绪 · 仅供个人学习备份使用，请遵守版权规定",
            style="TLabel", foreground=self.SUB, font=("Microsoft YaHei UI", 8))
        self.status_bar.pack(side="left")

    # ---------------------------------------------------------- 界面状态
    def _set_video_visible(self, visible):
        """只切换**可交互控件**的可用性；标签保持正常态。

        注意：不要给 ttk.Label 设 state="disabled" —— 那会让它回落到主题的
        disabled 配色（clam 下是浅底），在深色界面上显示成刺眼的白块。
        """
        state = "normal" if visible else "disabled"
        for w in (self.quality_box, self.dl_btn):
            try:
                w.configure(state=state)
            except tk.TclError:
                pass
        if not visible:
            self.video_title.configure(text="尚未解析视频")
            self.video_meta.configure(text="粘贴链接后点击「解析」")
            self.est_label.configure(text="")

    def toggle_settings(self):
        self._settings_visible = not self._settings_visible
        if self._settings_visible:
            self.settings.pack(fill="x", pady=(12, 0))
            self.browser_box.state(["readonly"] if self.opt_cookies.get() else ["disabled"])
        else:
            self.settings.pack_forget()

    def on_paste(self):
        try:
            data = self.clipboard_get()
        except tk.TclError:
            self.log_msg("剪贴板为空", "warn")
            return
        self.url_var.set(data.strip())
        u = extract_url(data)
        if u:
            self.url_var.set(u)
        self.on_parse()

    def _auto_paste(self):
        """启动时若剪贴板里正好是 B 站链接，自动填入"""
        try:
            data = self.clipboard_get()
        except tk.TclError:
            return
        u = extract_url(data)
        if u:
            self.url_var.set(u)
            self.log_msg("已从剪贴板识别到链接，点击「解析」即可", "info")

    def on_choose_dir(self):
        d = filedialog.askdirectory(initialdir=self.opt_out_dir.get())
        if d:
            self.opt_out_dir.set(d)
            self._save_config()

    # ---------------------------------------------------------- 日志
    def log_msg(self, text, tag="info"):
        self.msg_q.put(("log", text, tag))

    def set_status(self, text):
        self.msg_q.put(("status", text))

    def set_bar(self, pct, speed="", eta=""):
        self.msg_q.put(("bar", pct, speed, eta))

    def clear_log(self):
        self.msg_q.put(("clear",))

    def _poll_messages(self):
        try:
            while True:
                msg = self.msg_q.get_nowait()
                kind = msg[0]
                if kind == "log":
                    _, text, tag = msg
                    self.log.configure(state="normal")
                    self.log.insert("end", text + "\n", tag)
                    self.log.see("end")
                    self.log.configure(state="disabled")
                elif kind == "status":
                    self.status_label.configure(text=msg[1])
                elif kind == "bar":
                    _, pct, speed, eta = msg
                    if pct is None:
                        self.progress.configure(value=0)
                    else:
                        self.progress.configure(value=max(0, min(100, pct)))
                    bits = [b for b in (speed, ("剩余 " + eta) if eta else "") if b]
                    if bits:
                        self.status_label.configure(text=" · ".join(bits))
                elif kind == "clear":
                    self.log.configure(state="normal")
                    self.log.delete("1.0", "end")
                    self.log.configure(state="disabled")
                elif kind == "video":
                    self._apply_video(msg[1])
                elif kind == "dl_done":
                    self._on_download_done(msg[1])
                elif kind == "dl_error":
                    self._on_download_error(msg[1])
                elif kind == "dl_started":
                    self._on_download_started()
                elif kind == "enable_dl":
                    self.dl_btn.configure(state="normal")
        except queue.Empty:
            pass
        self.after(120, self._poll_messages)

    # ---------------------------------------------------------- 解析
    def on_parse(self):
        url = extract_url(self.url_var.get())
        if not url:
            self.log_msg("请输入有效的 B 站视频链接", "err")
            return
        self.url_var.set(url)
        self.cancel_flag.clear()
        self.parse_btn.configure(state="disabled")
        self.dl_btn.configure(state="disabled")
        self.progress.configure(value=0)
        self.set_status("解析中…")
        self.log_msg("解析：" + url, "cmd")

        cookies = self.opt_cookies.get() or None
        browser = self.opt_cookie_browser.get() if cookies else None

        def work():
            try:
                info = parse_video(url, browser)
                self.msg_q.put(("video", info))
            except ParseError as e:
                self.msg_q.put(("dl_error", str(e)))
            except Exception as e:
                self.msg_q.put(("dl_error", "解析异常：%s" % e))
            finally:
                self.parse_btn.configure(state="normal")

        threading.Thread(target=work, daemon=True).start()

    def _apply_video(self, info):
        self.current_video = info
        self._set_video_visible(True)

        title = info["title"]
        self.video_title.configure(text=title)
        bits = ["UP主：" + info["uploader"]]
        if info.get("duration"):
            bits.append("时长：" + human_duration(info["duration"]))
        if info.get("filesize"):
            bits.append("总体积：" + fmt_size(info["filesize"]))
        self.video_meta.configure(text="  ·  ".join(bits))

        labels = ["最高（合并最佳）"]
        for o in info["options"][:10]:
            labels.append("%s%s" % (o["label"],
                                    "  ~%s" % fmt_size(o["size"]) if o.get("size") else ""))
        if len(info["options"]) > 10:
            labels.append("其他…")
        self.quality_box.configure(values=labels, state="readonly")
        self.quality_box.current(0)
        self.est_label.configure(text="")
        self.dl_btn.configure(state="normal")
        self.set_status("解析完成")
        self.log_msg("解析成功：%s" % title, "ok")
        self.progress.configure(value=0)

    def _update_estimate(self):
        info = self.current_video
        if not info:
            return
        idx = self.quality_box.current()
        if idx <= 0:
            self.est_label.configure(text="自动选择最高可用")
        else:
            opts = info["options"][:10]
            if idx - 1 < len(opts):
                o = opts[idx - 1]
                self.est_label.configure(
                    text="预估 %s" % (fmt_size(o["size"]) if o.get("size") else "大小未知"))

    # ---------------------------------------------------------- 下载
    def on_download(self):
        info = self.current_video
        if not info:
            return
        out_dir = self.opt_out_dir.get().strip()
        if not out_dir:
            self.log_msg("请先在「设置」里选择保存目录", "err")
            return
        try:
            os.makedirs(out_dir, exist_ok=True)
        except OSError as e:
            self.log_msg("无法创建目录：%s" % e, "err")
            return

        idx = self.quality_box.current()
        fmt_sel = build_format_selector(None)
        if idx > 0:
            opts = info["options"][:10]
            if idx - 1 < len(opts):
                fmt_sel = build_format_selector(resolve_height(
                    info, opts[idx - 1]["label"]))

        limit_raw = str(self.opt_limit.get()).strip()
        try:
            limit_kb = int(float(limit_raw)) if limit_raw else 0
        except ValueError:
            limit_kb = 0

        cookies = self.opt_cookies.get() or None
        browser = self.opt_cookie_browser.get() if cookies else None

        self.cancel_flag.clear()
        self.msg_q.put(("dl_started",))
        self.log_msg("开始下载 → %s" % out_dir, "cmd")

        threading.Thread(
            target=self._download_worker,
            args=(info, out_dir, fmt_sel, limit_kb, browser),
            daemon=True).start()

    def _on_download_started(self):
        self.dl_btn.configure(state="disabled")
        self.parse_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.progress.configure(style="Bar.Horizontal.TProgressbar")
        self.set_status("下载中…")

    def _on_download_done(self, path):
        self.cancel_btn.configure(state="disabled")
        self.parse_btn.configure(state="normal")
        self.dl_btn.configure(state="normal")
        self.progress.configure(value=100)
        self.set_status("完成")
        self.log_msg("保存完成：" + path, "ok")
        self.status_bar.configure(text="完成 · " + path)
        if messagebox.askyesno("下载完成", "已保存到：\n%s\n\n是否打开所在文件夹？" % path):
            try:
                os.startfile(os.path.dirname(path))
            except OSError:
                pass

    def _on_download_error(self, msg):
        self.cancel_btn.configure(state="disabled")
        self.parse_btn.configure(state="normal")
        if self.current_video:
            self.dl_btn.configure(state="normal")
        self.progress.configure(value=0)
        self.set_status("失败")
        self.log_msg(msg, "err")
        messagebox.showerror("出错了", msg)

    def on_cancel(self):
        self.cancel_flag.set()
        self.log_msg("正在停止…", "warn")
        self.cancel_btn.configure(state="disabled")

    def _download_worker(self, info, out_dir, fmt_sel, limit_kb, browser):
        ydl_mod = import_ytdlp()
        if ydl_mod is None:
            self.msg_q.put(("dl_error", "yt-dlp 模块缺失"))
            return

        ffdir = find_ffmpeg_dir()

        def hook(d):
            if self.cancel_flag.is_set():
                raise ydl_mod.utils.DownloadError("用户取消")
            st = d.get("status")
            if st == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                done = d.get("downloaded_bytes") or 0
                pct = (done / total * 100) if total else None
                sp = fmt_speed(d.get("speed"))
                eta = fmt_eta(d.get("eta"))
                self.msg_q.put(("bar", pct, sp, eta))
            elif st == "finished":
                fn = d.get("filename") or ""
                self.log_msg("流完成：%s" % os.path.basename(fn), "info")

        opts = {
            "outtmpl": os.path.join(out_dir, "%(title).100s [%(id)s].%(ext)s"),
            "format": fmt_sel,
            "merge_output_format": "mp4",
            "noplaylist": True,
            "http_headers": {
                "Referer": "https://www.bilibili.com/",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
                ),
            },
            "progress_hooks": [hook],
            "retries": 5,
            "fragment_retries": 5,
            "concurrent_fragment_downloads": 4,
            "continuedl": True,
            "noprogress": True,
            "quiet": False,
            "no_warnings": False,
            "windowsfilenames": True,
            "trim_file_name": 120,
        }
        if ffdir:
            opts["ffmpeg_location"] = ffdir
        if browser:
            opts["cookiesfrombrowser"] = (browser,)
        if limit_kb and limit_kb > 0:
            opts["ratelimit"] = limit_kb * 1024

        try:
            with ydl_mod.YoutubeDL(opts) as y:
                self.msg_q.put(("video_progress",))
                res = y.download([info["webpage_url"]])
            final = None
            if isinstance(res, dict):
                for key in ("filepath", "_filename"):
                    if res.get(key):
                        final = res[key]
                        break
                if not final:
                    dl = res.get("requested_downloads") or []
                    if dl and isinstance(dl[0], dict):
                        final = dl[0].get("filepath")
            if not final:
                final = self._guess_latest(out_dir, info["title"])
            self.msg_q.put(("dl_done", final or out_dir))
        except Exception as exc:
            text = str(exc)
            if "用户取消" in text:
                self.msg_q.put(("dl_error", "已停止下载。\n未完成的片段可能保留为 .part 文件。"))
            else:
                if not ffdir:
                    text += ("\n\n提示：未找到 ffmpeg，高清晰度音视频分离流无法合并。"
                             "请重新下载完整压缩包，确保 ffmpeg 文件夹与 exe 同级。")
                self.msg_q.put(("dl_error", text))

    def _guess_latest(self, out_dir, title):
        try:
            files = [f for f in os.listdir(out_dir)
                     if f.endswith((".mp4", ".mkv", ".flv", ".webm"))]
        except OSError:
            return None
        if not files:
            return None
        files.sort(key=lambda f: os.path.getmtime(os.path.join(out_dir, f)))
        return os.path.join(out_dir, files[-1])

    def on_open_dir(self):
        d = self.opt_out_dir.get() or default_out_dir()
        try:
            os.makedirs(d, exist_ok=True)
            os.startfile(d)
        except OSError:
            pass

    def on_close(self):
        self._save_config()
        self.cancel_flag.set()
        self.destroy()


# ---------------------------------------------------------------- 无界面模式

def run_cli(argv):
    """无界面下载：--url <链接> [--out <目录>] [--quality <720|best>] [--cookies chrome]

    用途：脚本 / 批处理调用，以及在打包后做端到端验证
    （--noconsole 的 GUI 程序无法用常规方式确认内部可用性）。
    退出码：0 成功，1 失败。
    """
    import argparse

    ap = argparse.ArgumentParser(prog="bili_downloader", add_help=True)
    ap.add_argument("--url", required=True, help="B站视频链接")
    ap.add_argument("--out", default=None, help="保存目录")
    ap.add_argument("--quality", default="best",
                    help="清晰度：best 或 720 / 480 / 360 等")
    ap.add_argument("--cookies", default=None,
                    help="从该浏览器读取 Cookie（chrome/edge/firefox 等）")
    args = ap.parse_args(argv)

    url = extract_url(args.url)
    if not url:
        print("[错误] 无效的 B 站链接：%s" % args.url)
        return 1

    out_dir = args.out or default_out_dir()
    os.makedirs(out_dir, exist_ok=True)

    print("[1/3] 解析中…")
    try:
        info = parse_video(url, args.cookies)
    except ParseError as e:
        print("[错误] %s" % e)
        return 1

    height = resolve_height(info, args.quality)
    fmt_sel = build_format_selector(height)
    print("      标题：%s" % info["title"])
    print("      UP主：%s" % info["uploader"])
    print("      时长：%s" % human_duration(info.get("duration")))
    print("      清晰度：%s" % ("最高可用" if height is None else "%dP" % height))
    print("      保存到：%s" % out_dir)

    ydl_mod = import_ytdlp()
    if ydl_mod is None:
        print("[错误] yt-dlp 模块缺失")
        return 1

    ffdir = find_ffmpeg_dir()
    if not ffdir:
        print("[警告] 未找到 ffmpeg，音视频分离流可能无法合并")

    state = {"last": 0.0}

    def hook(d):
        st = d.get("status")
        if st == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            now = time.time()
            if now - state["last"] < 1.0:
                return
            state["last"] = now
            if total:
                pct = done / total * 100
                print("      进度 %.1f%%  %s  %s" % (
                    pct, fmt_size(done), fmt_speed(d.get("speed"))), end="\r")
            else:
                print("      已下载 %s" % fmt_size(done), end="\r")
        elif st == "finished":
            print(" " * 60, end="\r")

    opts = {
        "outtmpl": os.path.join(out_dir, "%(title).100s [%(id)s].%(ext)s"),
        "format": fmt_sel,
        "merge_output_format": "mp4",
        "noplaylist": True,
        "http_headers": {
            "Referer": "https://www.bilibili.com/",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
            ),
        },
        "progress_hooks": [hook],
        "retries": 5,
        "fragment_retries": 5,
        "concurrent_fragment_downloads": 4,
        "continuedl": True,
        "noprogress": True,
        "quiet": True,
        "no_warnings": True,
        "windowsfilenames": True,
        "trim_file_name": 120,
    }
    if ffdir:
        opts["ffmpeg_location"] = ffdir
    if args.cookies:
        opts["cookiesfrombrowser"] = (args.cookies,)

    print("[2/3] 下载中…")
    try:
        with ydl_mod.YoutubeDL(opts) as y:
            res = y.download([info["webpage_url"]])
    except Exception as e:
        print("\n[错误] 下载失败：%s" % e)
        return 1

    final = None
    if isinstance(res, dict):
        final = res.get("filepath") or res.get("_filename")
        if not final:
            dl = res.get("requested_downloads") or []
            if dl and isinstance(dl[0], dict):
                final = dl[0].get("filepath")

    if not final or not os.path.isfile(final):
        try:
            files = [f for f in os.listdir(out_dir)
                     if f.endswith((".mp4", ".mkv", ".flv", ".webm"))]
            files.sort(key=lambda f: os.path.getmtime(os.path.join(out_dir, f)))
            final = os.path.join(out_dir, files[-1]) if files else None
        except OSError:
            final = None

    if final and os.path.isfile(final):
        print("[3/3] 完成：%s（%s）" % (final, fmt_size(os.path.getsize(final))))
        print("RESULT_OK")
        return 0
    print("[错误] 未找到输出文件")
    return 1


def main():
    """唯一入口。注意：本文件末尾**只能有这一个**入口块。

    历史教训：曾因重构残留，文件里出现了第二个 `def main()` + `if __name__ == "__main__"`
    块。Python 会顺序执行——第一个入口跑完（用户关窗、mainloop 返回）后，
    紧接着又执行第二个入口，于是**又构造了一个 App，弹出第二个窗口**。
    表现为"关掉程序后又自动弹出一个新窗口"。
    """
    argv = sys.argv[1:]
    if "--url" in argv:
        sys.exit(run_cli(argv))
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
