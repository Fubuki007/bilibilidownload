# B站视频下载器

一个 Windows 桌面小工具：粘贴 B 站链接 → 解析 → 选清晰度 → 下载到本地。

## 特性

- **三种链接形态都能识别**：完整链接、`b23.tv` 短链、以及从 App 复制出来的裸编号 `BV1mZH26vEYC` / `av123456`
- **整段分享文案也能用**：会自动从文本里挑出第一条链接
- **剪贴板自动填充**：启动时若剪贴板里就是链接，直接填好
- **图形界面**：深色主题，带解析信息、清晰度选择、实时进度 / 速度 / 剩余时间、运行日志
- **可暂停续传**：中断后重新下载会接着传
- **可选 Cookie**：需要 1080P 及以上或大会员视频时，可从本机浏览器读取 Cookie（只在下载时读取，不落盘）
- **命令行模式**：可被脚本调用，也能用于自动化验收

## 下载产物

- 容器统一为 `mp4`
- 编码固定优先 **H.264 (avc1)**，其次 H.265，最后才回退其他
  > B 站同一清晰度会同时提供 avc1 / hvc1 / av01 三种编码。yt-dlp 默认会挑 AV1，但 AV1 在老播放器、VLC 及多数剪辑软件里兼容性很差，因此这里锁死 H.264。
- 音视频分离流由 ffmpeg 自动合并

## 使用

1. 双击 `B站视频下载器.exe`
2. 粘贴链接（或直接用剪贴板里已有的）
3. 点「解析」
4. 选清晰度，点「开始下载」

默认保存到 `E:\B站下载`，可在「设置」里更改。

### 关于清晰度

不登录时 B 站只提供到 720P（接口返回的实际高度是 718 / 478 / 358，属正常现象）。
想下 1080P 及以上或大会员专享内容，在「设置」中勾选「从浏览器读取 Cookie」并选择浏览器，
且该浏览器需已登录 B 站。

### 命令行

```bat
B站视频下载器.exe --url "BV1mZH26vEYC" --out "E:\B站下载" --quality 720
```

| 参数 | 说明 |
| --- | --- |
| `--url` | 链接 / 短链 / 裸 BV 号，必填 |
| `--out` | 保存目录，默认 `E:\B站下载` |
| `--quality` | `best` 或 `720` / `480` / `360` 等 |
| `--cookies` | `chrome` / `edge` / `firefox` 等 |

退出码：`0` 成功，`1` 失败；成功时会输出 `RESULT_OK`。

## 从源码运行

前置条件：

- Windows
- Python 3.9+，且**带 tkinter**
- `pip install -U yt-dlp`
- 一个 `ffmpeg.exe`（放在程序同级的 `ffmpeg/` 目录，或加入 PATH）

```bat
python bili_downloader.py
```

## 构建 exe

```bat
python tools/make_icon.py     :: 生成 app.ico / app_icon.png
python build.py               :: 产出 dist/B站视频下载器.exe
```

`build.py` 内部使用 PyInstaller（`--onefile --noconsole`），
并把 `app.ico` / `app_icon.png` 一并打进包里，用于运行时窗口图标。

发布时把 `B站视频下载器.exe` 与 `ffmpeg/` 放在同一层目录即可，`ffmpeg` 必须随包分发。

## 项目结构

```
bili_downloader.py     主程序（GUI + CLI）
build.py               打包脚本
使用说明.txt            给最终用户的说明
tools/make_icon.py     图标生成脚本（纯代码绘制，可复现）
app.ico                图标（由 make_icon.py 生成）
app_icon.png           图标 PNG 版
ffmpeg/ffmpeg.exe      音视频合并（需自行放置，不纳入版本控制）
```

## 实现要点

### Tcl/Tk 库路径

当 Python 解释器与它的 Tcl/Tk 数据目录不在同一层级时（例如基础解释器装在 D 盘、venv 建在 E 盘），
Tcl 会按解释器所在盘符去猜 `<盘符>/lib/tcl8.6`，猜错就抛：

```
_tkinter.TclError: Can't find a usable init.tcl in the following directories
```

这会导致 tkinter 完全不可用，并且 **PyInstaller 会据此判定「tkinter 损坏」而把它从包里剔除**——
打包过程零报错，运行时却报 `No module named 'tkinter'`。

程序里的 `ensure_tcl_tk_env()` 会主动探测真实数据目录并设置 `TCL_LIBRARY` / `TK_LIBRARY`，
**必须早于 `import tkinter`**。构建时也需要导出这两个环境变量，否则 PyInstaller 的探测同样会失败。

### 真实分辨率

B 站 DASH 返回的分辨率常是 718 / 478 / 358 这类非标准值。界面上向上取整显示为 720P / 480P / 360P，
但**筛选时必须用真实高度**，否则用 720 去匹配会一个流都选不到。

## 声明

仅供个人学习与备份使用，请遵守 B 站用户协议及相关版权规定，不要用于传播或商业用途。
