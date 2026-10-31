# LiveDanmaku · 直播弹幕工坊

YouTube 直播弹幕一体化工具：直播录制（自动监控开播）/ 视频与弹幕下载 → 弹幕转换与高潮点分析 → 样式定制与预览 → 压制烧录成片。图形化批量压制弹幕/LRC，以从右往左滚动的半透明文本形式将弹幕永久烧录到视频中。

## 特性

- **图形化批量处理** - 可多选视频或文件夹，自动递归查找文件夹内视频
- **自动匹配字幕** - 自动寻找同目录下同名 XML/LRC/JSON 弹幕；多种同时存在时优先选择 XML
- **XML/LRC/JSON 支持** - XML 支持普通弹幕与会员弹幕；LRC 支持时间轴文本；JSON（yt-dlp 聊天导出，含 `xxx.live_chat.json`）可直接压制，无需先转 XML
- **开头弹幕删除** - 可删除开头 N 条弹幕（如直播开始前的测试弹幕）；支持参照选择一条（默认展示前 50 条，条数可调整），删除其之前的全部弹幕
- **滚动弹幕** - 弹幕从视频右端向左匀速滚动，多行车道布局；**弹幕流速**可自行调整（像素/秒，界面或配置 `animation.scroll_speed`）
- **文本样式** - 字体颜色/字体类型/大小可调（字体列表支持按名称搜索、可收藏字体置顶），支持描边与文本阴影（颜色+不透明度）；会员与普通用户样式可完全分开调整
- **Emoji 图片加载** - 本地 `assets/emoji/` PNG 与 YouTube 平台表情（转换时在 XML 中内嵌图片 URL，压制时按需下载缓存到 `~/.livedanmaku_emoji_cache/`）均可显示，不透明度、大小、间距独立可调
- **字体回退** - 多字体族链式匹配，未覆盖字符自动尝试系统字体回退
- **FFmpeg 编码** - 支持自动检测 GPU / QSV / CPU 编码路径；自动模式下无可用硬件编码时回退到 CPU；QSV 自动钉定真实 Intel 显卡（多适配器机器上避免会话落在虚拟显示适配器，`encode.qsv_vendor_id` 可配置）
- **编码进度与背压** - 按 ffmpeg 实际编码帧数节流渲染产出，长视频压制内存占用有界；编码器无进展自动回退 CPU 重试
- **自定义压缩参数** - `encode.extra_args` 与界面「自定义参数」输入框可追加任意 ffmpeg 参数，完全接管压缩设置
- **实时录制直播** - 录制进行中的直播（支持从开头录制），可监控频道开播自动开始录制，录制中随时保存已录制片段而不中断；可同步录制弹幕 JSON
- **下载指定片段** - 下载页可填起止时间（如 5:01 ~ 10:43），基于 yt-dlp --download-sections 只下载指定片段
- **下载代理** - 下载页可填显式代理（清理环境残留代理，ffmpeg 切片同步走代理）；视频与弹幕并行下载互不阻塞
- **直播高潮点分析** - 根据弹幕 JSON 的时间戳统计发送频率：高潮点 = 相对前后区段明显更密集（或明显高于全片中位水平），单段最长 5 分钟，可自定义统计前 N 名
- **JSON 转 XML** - 界面内把 yt-dlp 导出的聊天 JSON 转换为弹幕 XML；检测到同名视频可直接加入任务队列
- **预览图与测试片段** - 压制前用「生成预览图…」确认样式，或勾选「只导出测试片段」先压一段确认效果
- **视频转 GIF** - 「视频转 GIF」页把视频片段转为高质量 GIF：可选取片段、调整播放倍速（0.25~4 倍）、帧率（5~30 fps）、分辨率（原始/1080p/720p/480p/360p）与调色板档位（palettegen/paletteuse 调色板+抖动），转换可随时停止

## 环境要求

### 源码运行

- Python >= 3.12（3.12 实测全量测试通过，开发验证于 3.14；PySide6 需要 64 位 Python）
- FFmpeg / ffprobe 可在命令行中直接运行（**唯一需要手工安装的外部依赖**，见下文「外部依赖怎么处理」）
- Python 依赖包（`pip install -e .` 自动安装）：`lxml` `PySide6` `loguru` `tqdm` `pyyaml`
- `yt-dlp`：界面「下载内容」「直播录制」两页需要。属可选依赖组，`pip install -e ".[download]"` 或 `setup.ps1` 会自动装进 venv，GUI 启动时自动把 venv 的 Scripts 目录前置到 PATH，无需手工配置

### EXE 运行

打包后的 `LiveDanmaku.exe` 不需要用户额外安装 Python，但需要：

- Windows 系统
- 保留整个 `dist\LiveDanmaku\` 文件夹，不要只复制单个 exe
- `ffmpeg.exe` 和 `ffprobe.exe` 可被程序找到

推荐把 FFmpeg 的 `bin` 目录加入系统环境变量 `Path`。可以用下面命令验证：

```powershell
ffmpeg -version
ffprobe -version
```

如果要使用 NVIDIA GPU 编码，还需要显卡驱动正常，并且 FFmpeg 支持 `h264_nvenc`：

```powershell
ffmpeg -hide_banner -encoders | findstr nvenc
```

如果没有可用硬件编码，自动模式会回退到 CPU 编码；手动选择 GPU 或 QSV 时，硬件/FFmpeg 不支持会报错。

## 格式限制说明

当前版本主要面向常见 SDR 8-bit 恒定帧率视频。程序会按检测到的单一帧率生成弹幕帧，并输出 H.264 8-bit 视频（CPU/NVENC 为 `yuv420p`，QSV 为 `nv12`）。

对于 VFR（可变帧率）视频，长时间压制可能出现弹幕时间轻微偏移。对于 HDR、10-bit 或宽色域视频，输出可能丢失 HDR、高位深或色彩元数据，画面亮度与色彩观感可能发生变化。

如需尽量稳定的结果，建议先将素材转为 SDR 8-bit CFR 视频后再压制。

## 安装

### Windows 

**第 1 步：安装 Python 3.12 或更新版本**（64 位；无需手动装 yt-dlp，见第 3 步）

```powershell
winget install --id Python.Python.3.13 -e
```

或从 [python.org](https://www.python.org/downloads/) 下载安装包，安装时勾选
**"Add python.exe to PATH"**。已有 3.12+ 可直接复用。安装后重开终端验证：

```powershell
python --version    # 应显示 3.12.x 或更高
```

**第 2 步：获取代码**

```bash
git clone https://github.com/lilililinyuu/LiveDanmaku.git
cd LiveDanmaku
```

**第 3 步：一键初始化项目环境**（创建 `.venv`、装好全部 Python 依赖、
装好 yt-dlp、校验 PySide6 可导入；`.venv` 已存在时可重复执行修复）

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1
```

**第 4 步：安装 FFmpeg / ffprobe**（唯一的系统级外部依赖；若已装可跳过）
见下文[外部依赖怎么处理](#外部依赖怎么处理上传-github-后他人如何立即使用)，
推荐 `winget install --id Gyan.FFmpeg -e`，装完重开终端 `ffmpeg -version` 验证。

**第 5 步（可选）：创建个人配置** —— 复制模板后按需修改（不复制则全部用内置默认值）：

```powershell
copy livedanmaku.example.yaml livedanmaku.yaml
```

**第 6 步：启动**

```powershell
# 日常：双击项目根目录的 启动GUI.bat（无控制台黑窗）
# 或命令行：
powershell -NoProfile -ExecutionPolicy Bypass -File .\run_gui.ps1
```

**第 7 步（可选）：验证安装** —— 跑一遍测试套件（257+ 用例，约 1 分钟）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\test.ps1
```

`test.ps1` 和 `run_gui.ps1` 在干净 clone 中发现 `.venv` 不存在时，也会自动先调用 `setup.ps1`。测试临时文件写入仓库内已忽略的 `.pytest-tmp/`，不会依赖系统临时目录权限。

环境就绪后，日常启动直接双击 **`启动GUI.bat`**；需要看控制台日志时改用：

```powershell
.venv\Scripts\python.exe -m livedanmaku.gui
```

### 手动安装

使用 pip 安装运行依赖（含 yt-dlp 下载支持）：

```bash
python -m pip install -e ".[download]"
```

**系统 PATH 上已有 yt-dlp、不想重复安装**：去掉 `.[download]` 只装运行依赖即可
（或 `setup.ps1 -NoDownload`）。GUI 优先用 venv 内的 yt-dlp，没有时自动回退
调用系统 PATH 上的版本——已装的全局 yt-dlp 无需任何额外配置：

```bash
python -m pip install -e .
```

如果已经安装 `uv`，也可以使用：

```bash
uv sync
```

### 外部依赖怎么处理

依赖分两类，处理方式不同：

**1. Python 依赖包 —— 全自动，无需手工处理。**
`pyproject.toml` 已声明全部依赖（PySide6、lxml、loguru、pyyaml、tqdm）；
`yt-dlp` 在可选组 `[download]` 中。任何人 clone 后执行 `setup.ps1`
（或 `pip install -e ".[download]"`）即可装好一切，包括 yt-dlp。
GUI 启动时会自动把 venv 的 `Scripts` 目录前置到子进程 PATH，
pip 装的 `yt-dlp.exe` 直接可用，不需要用户配置环境变量。
**系统已装 yt-dlp 的用户**：可 `setup.ps1 -NoDownload`（或 `pip install -e .`）
跳过 venv 内安装；查找顺序为 venv 内优先、系统 PATH 兜底，两份共存不冲突
（想让 GUI 用最新版 yt-dlp 时，在 venv 里 `pip install -U yt-dlp` 即可）。
需要精确复现开发环境版本时用仓库内的 `requirements-lock.txt`：

```bash
python -m pip install -r requirements-lock.txt && python -m pip install -e ".[download]" --no-deps
```

**2. FFmpeg / ffprobe —— 系统级二进制，不能随 pip 分发，需要装一次。**
任选一种方式安装（Windows）：

```powershell
# 方式 A：winget（推荐）
winget install --id Gyan.FFmpeg -e

# 方式 B：scoop
scoop install ffmpeg

# 方式 C：choco
choco install ffmpeg

# 方式 D：手动下载（本项目开发用的 BtbN 构建）
# 从 https://github.com/BtbN/FFmpeg-Builds/releases 下载
# ffmpeg-master-latest-win64-gpl.zip，解压后把 bin 目录加入系统 Path
```

安装后重开终端验证：

```powershell
ffmpeg -version
ffprobe -version
yt-dlp --version
```

FFmpeg 就位后压制（含 GPU/QSV 硬编码）、封面下载、预览图全部可用；
`setup.ps1` 结束时若检测不到会打印警告提示。yt-dlp 已随 pip 安装进
venv，无需单独处理（也可自行安装最新版到系统 PATH，功能相同）。

**3. 打包成 EXE 分发给完全小白用户（可选）。**
`build_exe.ps1` 用 PyInstaller 产出 `dist\LiveDanmaku\`，exe 内置
Python 与全部 Python 依赖，用户只需另备 ffmpeg.exe（放同目录或
PATH）。详见下方「打包」章节。

## 快速开始

**本项目只有图形界面（GUI），没有弹幕压制的命令行用法。**
`livedanmaku` / `livedanmaku-gui` 两个命令入口、`python -m livedanmaku.cli` /
`python -m livedanmaku.gui` 都只是启动同一个 GUI 窗口，便于不同启动方式选用；
任务日志、进度、预览、错误信息全部在窗口内展示。

### 启动 GUI

日常使用（`.venv` 就绪后）直接**双击项目根目录的 `启动GUI.bat`**，无控制台黑窗。

需要看控制台日志排障时，任选其一：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\run_gui.ps1   # 仓库脚本
.venv\Scripts\python.exe -m livedanmaku.gui                          # 已装依赖时
livedanmaku-gui                                                      # 已 pip install -e 时
```

### 界面布局

左侧导航栏（可点击 « 收起）在五个功能页之间切换；下半部分固定显示任务日志，
上下面板之间可拖动分割条自由调节高度。

在「压制设置」页可选择视频文件或文件夹：选择文件夹时会递归查找其中支持的视频文件，
并自动匹配同目录下的同名字幕文件。支持的视频扩展名：

```text
.mp4 .flv .mkv .avi .mov .ts .webm
```

支持的弹幕扩展名：

```text
.xml .lrc .json
```

同名 XML/LRC/JSON 同时存在时优先选择 XML；yt-dlp 导出的
`xxx.live_chat.json` 也直接识别。

界面为侧边导航布局：左侧导航栏（可点击 « 收起）在 **压制设置**、**弹幕样式**、**JSON 转 XML**、**下载内容**、**直播录制**、**视频转 GIF** 六个功能页之间切换；下半部分固定显示任务队列与日志，上下面板之间可拖动分割条自由调节高度。

### 各页面用法

**「压制设置」页 —— 主工作流，从视频+弹幕到成片：**

1. 点 **“选择视频或文件夹”** 加入任务（可多选；文件夹会递归查找视频并自动配对同目录同名弹幕）
2. 确认编码模式（自动 / QSV / GPU / CPU）、输出目录、配置文件（留空用默认）
3. 按需调整选项：**首条弹幕时间**（对齐视频开头）、**弹幕流速**（默认 300 px/s）、**删除开头弹幕**（可点 **“参照选择…”** 从开头弹幕里指定保留哪一条，其之前的全部删除）、**测试片段**（只压一段确认效果）、**自定义参数**（追加任意 ffmpeg 参数）
4. 任务区点 **“生成预览图…”** 可用真实视频帧渲染弹幕预览 PNG
5. 点 **“开始压制 →”**，进度条与日志实时显示；QSV/GPU 编码器异常时自动回退 CPU 重试

**「弹幕样式」页 —— 样式即改即用：** 字体/字号/颜色/描边/阴影（普通与会员分开）、表情不透明度/大小/间距、渲染精度（超采样）都在此调整，预览区实时刷新；压制时自动套用本页参数，点 **“保存样式到 YAML…”** 可把当前样式写进配置文件。

**「JSON 转 XML」页：** 第一个按钮 **“删除开头弹幕…”** 可直接生成删除开头 N 条后的弹幕文件（JSON/XML 均可，支持参照选择；可选覆盖原文件或生成新文件）。填入 yt-dlp 导出的聊天 JSON（`xxx.live_chat.json` 自动按 `xxx.xml` 命名）点转换；检测到同名视频会询问加入任务队列。点 **“分析高潮点”** 按发送频率找出直播最热闹的时间段（单段最长 5 分钟，可自定义统计前 N 名）。

**「下载内容」页：** 输入视频链接，勾选要下载的内容（视频 / 弹幕 JSON / 封面，可组合），可选填起止时间只下载指定片段、显式代理；yt-dlp 在后台运行，视频与弹幕并行下载互不阻塞，进度实时显示在任务日志。

### 典型流程

1. 「下载内容」页输入链接下载直播回放视频与弹幕（或直接使用已有视频和 JSON）
2. 「JSON 转 XML」页转换弹幕，检测到同名视频时一键加入任务队列；也可点 **“分析高潮点”** 查看直播最热闹的时间段
3. 「压制设置」页调整编码模式、首条弹幕时间、删除开头弹幕、测试片段等
4. 「弹幕样式」页调整样式，右侧预览实时可见；需要时点 **“生成预览图…”** 用真实视频帧确认
5. 勾选 **“只导出测试片段”** 先压一段确认效果，确认无误后取消勾选，点击 **“开始压制 →”** 完成全量压制

**「视频转 GIF」页 —— 把视频片段做成 GIF：**

1. 填入视频路径，点 **“读取信息”** 查看分辨率与时长（时间范围会自动按时长钳制）
2. 设置 **片段范围**（开始/结束秒数），或保持默认从头到尾
3. 调整 **倍速**（0.25~4 倍，>1 加速）、**帧率**（默认 15 fps）、**分辨率**（original / 1080p / 720p / 480p / 360p，高度按视频比例缩放）与 **调色板**（high / medium / low，映射调色板规模与抖动算法）
4. 输出路径留空 = 视频同目录 `<名字>-<起>-<止>s.gif`；点 **“开始转换”**，转换中可随时 **“停止转换”**（半成品自动清理）

### 直播录制

「直播录制」页基于 yt-dlp 的直播录制能力：

- **录制进行中的直播**：填入直播/视频地址，点 **"开始录制"**。格式默认 `bestvideo+bestaudio/best`（优先双流、结束时自动合并；直播无双流时 yt-dlp 回退单文件），默认携带 `--live-from-start` 从直播开头录制（不支持回放的直播自动忽略）。
- **监控频道开播自动录制**：填入频道链接（`@handle` 或 `/channel/…`），点 **"监控此频道开播（自动录制）"**，程序按配置间隔探测，开播后自动开始录制。
- **随时保存已录制片段**：录制中点 **"保存当前片段"**，把已录制的部分复制到 `输出目录/已保存/`（文件名自动加时间戳），**不中断录制**。
- **同步录制弹幕 JSON**：默认勾选，录制的同时抓取 live_chat 弹幕；录制结束可一键转 XML 加入压制队列。
- **停止录制**：向 yt-dlp 发送优雅停止信号等待其收尾，超时强制终止（含 ffmpeg 子进程）；结束后自动重封装直播流为规范 mp4（修音画同步）并补全弹幕回放。

录制相关默认参数（代理、PO Token 路径、格式、探测间隔等）在配置文件的 `recorder:` 段调整。弹幕说明：录制中的弹幕 JSON 只能实时捕获「开始录制之后」的发言；录制结束后程序会自动下载 YouTube 的整场弹幕回放并替换（从直播第一秒开始）。停止录制时若直播尚未结束，将保留已捕获的部分。「弹幕样式」页的预览卡片提供「渲染精度」（对应配置 `style.render_scale`）：先按倍率放大渲染文字再缩回视频尺寸，>1 时文字边缘更细腻，调整后预览区实时更新效果。

| 参数 | 内置默认值 | 说明 |
| --- | --- | --- |
| `proxy` | ""（不使用） | 录制/探测代理。注意 ffmpeg 不支持 SOCKS，录制直播必须用 http 形式 |
| `download_proxy` | ""（不使用） | 下载页代理（下载页也可在界面单独填） |
| `pot_token_script` | ""（不使用） | bgutil-pot 脚本/可执行文件路径，经 --extractor-args 传入；遇 YouTube 发质询时需要 |
| `fmt` | 同左 | 录制格式：优先音视频分离流并在结束时合并，直播无双流时回退单文件 |
| `output_template` | 同左 | 输出文件名模板 |
| `live_from_start` | true | true=用 --live-from-start 从直播开头录制（DVR 回放追帧，不支持时 yt-dlp 自动忽略）；false=从当前直播位置实时录制 |
| `record_chat` | true | 同步录制弹幕 JSON |
| `monitor_interval` | 60.0 | 频道开播探测间隔（秒） |
| `sync_mode` | auto | 音画同步整理方式：auto=流拷贝重封装（秒级）；exact=精确同步，重编码并重算时间戳（修正累积漂移，整理耗时与录制时长相当） |
| `av_offset` | 0.0 | 音画补偿秒数：整理时平移音频流（>0 音频延后）。录制的文件音频比视频快 N 秒时填 +N |

## 配置

通过 `livedanmaku.yaml` 调整行为。仓库提供模板 `livedanmaku.example.yaml`：
复制为 `livedanmaku.yaml` 后按需修改；不创建该文件则全部使用内置默认值。

配置优先级：

```text
界面「配置文件」框指定的 YAML > 当前目录 livedanmaku.yaml > 用户目录 livedanmaku.yaml > 内置默认值
```

如果显式指定的配置文件不存在或结构不合法，程序会直接报错，不会继续回退到其它配置文件。

EXE 运行时，图形界面会优先使用 exe 同目录下的 `livedanmaku.yaml`，用户可以直接编辑；也可以在界面「保存样式到 YAML…」中把当前样式写出为新配置。

下表为各参数的内置默认值与说明。

### 布局样式 (style)

| 参数                    | 内置默认值 | 说明                    |
| --------------------- | ----- | --------------------- |
| `danmaku_x`           | 20    | 弹幕起始 X 坐标（像素）         |
| `layer_width_extra`   | 100   | 已废弃：渲染层始终延伸至视频最右端，保留仅为兼容旧配置 |
| `row_vertical_gap`    | 1     | 弹幕行之间的垂直间距（像素）       |
| `multiline_row_gap`   | 1     | 多行文本行间距（像素）           |
| `font_family`         | "Microsoft YaHei" | 字体类型，置于回退链最前，空=仅内置字体族 |
| `font_size`           | 25    | 字体大小（pt），全局共用         |

`text` 与 `member_text` 分别为普通用户与会员的文本样式，结构相同、可分开调整：

| 参数               | 内置默认值 | 说明                     |
| ---------------- | ----- | ---------------------- |
| `color`          | "#FFFFFF" / "#45dd7f" | 字体颜色                  |
| `opacity`        | 0.7   | 文本不透明度（含描边）0~1         |
| `outline_color`  | "#246542" | 描边颜色，空字符串表示禁用      |
| `outline_width`  | 0.7   | 描边厚度（像素，可为小数），0 表示禁用    |
| `shadow_color`   | "#000000" | 阴影颜色，空字符串表示禁用      |
| `shadow_opacity` | 0.7   | 阴影不透明度 0~1             |

阴影偏移固定为右下 2 像素；阴影会在弹幕缓存图右/下边缘处被轻微裁剪。

`emoji` 为表情样式（普通与会员共用）：

| 参数            | 内置默认值 | 说明                    |
| ------------- | ----- | --------------------- |
| `opacity`     | 1.0   | 表情不透明度 0~1            |
| `size_ratio`  | 1.0   | 表情大小比例（相对行高），1.0=与行高一致 |
| `spacing`     | 4     | 表情间距（像素）              |

`render_scale` 为渲染超采样倍率（样式页预览卡片可调）：

| 参数             | 内置默认值 | 说明 |
| -------------- | ----- | --- |
| `render_scale` | 1.0   | 弹幕渲染超采样倍率（1.0~4.0）：先放大渲染文字再缩回视频尺寸，>1 时文字边缘更细腻，内存/CPU 占用略增 |

### 布局比例 (ratio)

| 参数                 | 内置默认值 | 说明         |
| ------------------ | ----- | ---------- |
| `max_text_rows`    | 4     | 文本弹幕最多显示行数（自动按可见高度钳制） |
| `text_width_ratio` | 0.8   | 文本弹幕最大宽度比例 |
| `top_margin`       | 12    | 顶部边距（像素），弹幕不会贴住画面上边缘 |
| `bottom_margin`    | 22    | 底部边距（像素）   |

### 动画参数 (animation)

| 参数                      | 内置默认值 | 说明            |
| ----------------------- | ----- | ------------- |
| `first_danmaku_at`     | null  | 第一条弹幕对齐到该秒（0=视频一开始就出现）；null=保持字幕文件原时间轴 |
| `scroll_speed`          | 300   | 弹幕水平流速（像素/秒）；越大滚得越快、单条停留越短，也影响车道分配的相撞计算。取值 (0, 2000] |

弹幕严格按字幕文件中的原始时间出现（无发射间隔/批次节流）；单帧行数超出 `max_text_rows` 时按行数预算顺延，最多延迟一帧。

车道分配带**防重叠探测**：同速滚动的弹幕同车道内相对位置不变，出生时若与前车间距小于前车宽度（会全程重叠）则改投其它空闲车道；多行弹幕占用连续车道；全部车道忙碌时退回哈希车道（保证按原时间发射）。

### 编码参数 (encode)

| 参数                        | 内置默认值    | 说明                                 |
| ------------------------- | -------- | ---------------------------------- |
| `gpu_preset`              | "p5"     | NVIDIA NVENC 预设（p1~p7，越大越慢、质量越好）   |
| `gpu_cq`                  | 15       | NVIDIA NVENC QP 质量（0~51，越小越好、文件越大） |
| `qsv_preset`              | "slow"   | Intel QSV 预设（越慢通常质量越好）             |
| `qsv_quality`             | 18       | Intel QSV 质量（0~51，越小越好、文件越大）       |
| `qsv_vendor_id`           | 0x8086   | QSV 绑定的显卡 PCI vendor id；多适配器机器（虚拟显示适配器 + 真实显卡）上把 QSV 会话钉定到真实 Intel 显卡，避免会话落在虚拟适配器上停转；0 = 走 ffmpeg 自动选择 |
| `cpu_preset`              | "medium" | libx264 预设（越慢压缩效率越好）               |
| `cpu_crf`                 | 18       | libx264 CRF 质量（0~51，越小越好、文件越大）     |
| `cpu_min_reserve_threads` | 2        | CPU 编码保留线程数                        |
| `extra_args`              | []       | 自定义 ffmpeg 参数列表（追加在命令末尾，可覆盖默认压缩参数） |

示例：

```yaml
encode:
  extra_args: ["-maxrate", "4M", "-bufsize", "8M"]
```

### 系统参数 (system)

| 参数                      | 内置默认值    | 说明                          |
| ----------------------- | -------- | ----------------------------- |
| `pipe_buffer_size`      | 10000000 | 管道缓冲区大小（字节）                 |
| `pipe_queue_size`       | 16       | 异步写入队列大小                    |
| `ffmpeg_timeout`        | 10       | FFmpeg 启动超时（秒）              |
| `stderr_thread_timeout` | 5        | 错误日志线程超时（秒）                 |
| `video_alignment`       | 2        | 视频尺寸对齐到偶数，避免 1080 被放大到 1088 |
| `max_queue_frames`      | 64       | 异步写入队列最大帧数                  |

## 打包

项目根目录提供一键打包脚本。干净 clone 中执行时，脚本会创建独立的 `.venv-build`、安装项目和 PyInstaller，然后根据 `LiveDanmaku.spec` 打包：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build_exe.ps1
```

如果只想验证打包依赖和入口是否可用、但不生成 exe：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build_exe.ps1 -CheckOnly
```

打包完成后，可执行文件位于：

```text
dist\LiveDanmaku\LiveDanmaku.exe
```

发布时请保留整个 `dist\LiveDanmaku\` 文件夹。程序仍依赖系统中的 FFmpeg / ffprobe。

不要运行 `build\LiveDanmaku\LiveDanmaku.exe`；它是 PyInstaller 的中间产物。最终程序是 `dist\LiveDanmaku\LiveDanmaku.exe`。

## 处理流程

```text
字幕解析 -> 资源加载 -> 视频信息获取 -> 弹幕布局计算 -> 逐帧渲染 -> FFmpeg 合成
```

1. 解析 XML/LRC/JSON 弹幕；XML 会提取 `<d>` 标签（以及 `<e name url>` 表情元数据），LRC 会解析时间轴文本，JSON（聊天导出）直接解析消息与表情
2. 加载 Emoji 图片资源（本地 assets 或按 `<e>` URL 下载缓存），按需加载字体
3. 通过 ffprobe 获取视频宽高、帧率、总帧数
4. 预创建弹幕对象，计算布局参数
5. 构建 FFmpeg 编码命令，自动检测 GPU / QSV / CPU 编码路径
6. 逐帧渲染滚动弹幕图层，通过管道送入 FFmpeg，输出压制视频

## 项目结构

```text
LiveDanmaku/
├── src/livedanmaku/        # 核心包
│   ├── cli.py                # 图形界面启动入口（控制台脚本 livedanmaku）
│   ├── gui.py                # 图形界面入口
│   ├── batch.py              # 批量文件发现与字幕匹配
│   ├── config/               # 配置模块
│   ├── core/                 # 压制流程编排（burner/preview）、yt-dlp 下载辅助（pipeline）、高潮点分析（highlight）、直播录制与监控（recorder/monitor）
│   ├── encode/               # FFmpeg 进程管理
│   ├── input/                # XML/LRC 字幕解析、YouTube 聊天 JSON 解析与转换（chat2xml）
│   ├── layout/               # 布局计算、活跃弹幕状态
│   ├── render/               # 文本渲染、图片资源加载（emoji_fetch 远程表情下载）
│   ├── ui/                   # 图形界面（主窗口/样式面板/下载与转换页/后台线程）
│   └── utils/                # 工具函数与输入输出校验
├── tests/                    # 测试
├── LiveDanmaku.spec        # PyInstaller 打包配置
├── setup.ps1                 # 一键安装运行与开发依赖（-NoDownload 跳过 yt-dlp）
├── test.ps1                  # 一键运行测试
├── run_gui.ps1               # 一键启动 GUI
├── 启动GUI.bat               # 双击启动 GUI（pythonw，无控制台窗口）
├── build_exe.ps1             # 一键打包 / 打包前检
├── scripts/_common.ps1       # PowerShell 公共辅助函数
├── scripts/relocate.ps1      # 项目目录重命名 + venv 重建（手动调用）
├── livedanmaku.yaml        # 默认配置文件
├── requirements-lock.txt     # 开发环境精确版本清单（迁移/复现用）
├── pyproject.toml
├── uv.lock
├── LICENSE
└── README.md
```

`assets/` 目录为可选资源目录，通常用于存放 Emoji 图片或字体文件；当前干净仓库不包含具体素材资源。

## 许可证

本项目沿用 GPLv3 发布，详见仓库根目录的 `LICENSE` 文件。由于本项目基于 GPLv3 代码修改，分发修改版时也应保留 GPLv3、保留版权与许可声明，并公开对应源代码。



## 致谢

本项目基于 [febr30th/DanmakuStudio](https://github.com/febr30th/DanmakuStudio) 修改，感谢原项目作者的弹幕渲染与压制实现。
