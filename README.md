# DanmakuStudio

图形化弹幕/LRC 批量压制工具。它可以将直播录像与弹幕 XML/LRC 合成，以从右往左滚动的半透明文本形式将弹幕永久烧录到视频中。

## 特性

- **图形化批量处理** - 可多选视频或文件夹，自动递归查找文件夹内视频
- **自动匹配字幕** - 自动寻找同目录下同名 XML/LRC 字幕；XML 和 LRC 同时存在时优先选择 XML
- **XML/LRC 支持** - XML 支持普通弹幕与会员弹幕；LRC 支持时间轴文本，并尽量识别常见导出格式中的用户名
- **开头弹幕删除** - 可删除开头 N 条弹幕（如直播开始前的测试弹幕）；pipeline 工作台还支持从前 40 条中参照选择一条，删除其之前的全部弹幕
- **滚动弹幕** - 弹幕从视频右端向左匀速滚动，多行车道布局，长文本自动折行
- **文本样式** - 字体颜色/字体类型/大小可调，支持描边与文本阴影（颜色+不透明度）；会员与普通用户样式可完全分开调整
- **Emoji 图片加载** - 本地 `assets/emoji/` PNG 与 YouTube 平台表情（由 `json2xml.py` 在 XML 中内嵌图片 URL，压制时按需下载缓存到 `~/.danmakustudio_emoji_cache/`）均可显示，不透明度、大小、间距独立可调
- **字体回退** - 多字体族链式匹配，未覆盖字符自动尝试系统字体回退
- **FFmpeg 编码** - 支持自动检测 GPU / QSV / CPU 编码路径；自动模式下无可用硬件编码时回退到 CPU
- **自定义压缩参数** - `encode.extra_args` / `--ffmpeg-args` 可追加任意 ffmpeg 参数，完全接管压缩设置
- **一键全流程** - `danmakustudio pipeline` 集成：yt-dlp 下载直播回放+弹幕 → 转 XML → 预览图 → 1 分钟测试片段 → 确认 → 全量压缩
- **预览图与测试片段** - 压制前可用 `preview` 命令生成预览图、`--test-clip` 只压 1 分钟片段确认效果

## 环境要求

### 源码运行

- Python >= 3.13
- FFmpeg / ffprobe 可在命令行中直接运行
- 依赖包：`lxml` `PySide6` `loguru` `tqdm` `pyyaml`
- `yt-dlp`（可选）：仅 `pipeline` 命令需要，用于下载直播回放视频与弹幕 JSON（`pip install yt-dlp`）

### EXE 运行

打包后的 `DanmakuStudio.exe` 不需要用户额外安装 Python，但需要：

- Windows 系统
- 保留整个 `dist\DanmakuStudio\` 文件夹，不要只复制单个 exe
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

### Windows 一键初始化（推荐）

```bash
git clone https://github.com/febr30th/DanmakuStudio.git
cd DanmakuStudio
```

在 PowerShell 中运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1
```

脚本会优先寻找当前用户通过 python.org 安装的 Python 3.13+，创建 `.venv`、升级其中的 pip，并安装项目运行依赖与测试/检查工具。安装完成前还会验证 PySide6/Qt 能否正常导入。需要指定 Python 路径时，可增加 `-Python "C:\path\to\python.exe"`。

FFmpeg 和 ffprobe 是外部运行依赖，不会下载到仓库；如果它们不在 `Path` 中，安装结束时会显示警告。

完成初始化后，可以分别用一条命令运行测试或 GUI：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\test.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\run_gui.ps1
```

`test.ps1` 和 `run_gui.ps1` 在干净 clone 中发现 `.venv` 不存在时，也会自动先调用 `setup.ps1`。测试临时文件写入仓库内已忽略的 `.pytest-tmp/`，不会依赖系统临时目录权限。

### 手动安装

使用 pip 安装运行依赖：

```bash
python -m pip install -e .
```

如果已经安装 `uv`，也可以使用：

```bash
uv sync
```

## 快速开始

### 图形界面

Windows 推荐使用仓库脚本：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\run_gui.ps1
```

也可以在已安装依赖或已激活虚拟环境后直接运行模块：

```bash
python -m danmakustudio.gui
```

安装项目后还可以使用命令入口：

```bash
danmakustudio-gui
```

也可以无参数运行 `danmakustudio` 打开图形界面：

```bash
danmakustudio
```

在图形界面中可以选择视频文件或文件夹。选择文件夹时会递归查找其中支持的视频文件，并自动匹配同目录下的同名字幕文件。

支持的视频扩展名：

```text
.mp4 .flv .mkv .avi .mov .ts .webm
```

支持的字幕扩展名：

```text
.xml .lrc
```

例如：

```text
视频.mp4
视频.xml
视频.lrc
```

如果同名 XML 和 LRC 同时存在，程序会优先选择 XML。

也可以在任务区点击 **"JSON 转 XML…"** 按钮，选择 yt-dlp 导出的聊天 JSON 直接转换为同名 XML；若同目录检测到同名视频，会询问是否直接加入任务队列。

### 命令行

```bash
danmakustudio source/视频.mp4 source/弹幕.xml
danmakustudio source/视频.mp4 source/字幕.lrc
```

指定编码模式和输出路径：

```bash
danmakustudio source/视频.mp4 source/弹幕.xml --encode gpu -o output.mp4 -f
```

删除开头 N 条弹幕（如直播开始前的测试弹幕）：

```bash
danmakustudio source/视频.mp4 source/弹幕.xml --drop-first 5
```

使用自定义配置文件：

```bash
danmakustudio source/视频.mp4 source/弹幕.xml -c danmakustudio.yaml
```

### 预览图（压制前确认样式）

```bash
danmakustudio preview source/视频.mp4 source/弹幕.xml [-c 配置] [-t 10,300,600] [-o 输出目录] [--drop-first N] [--yes]
```

默认自动挑选 3 个时间点（首条弹幕处 + 弹幕分布中点 + 末尾）；时间点没有可见弹幕时会自动向后微调。生成后打印 PNG 路径并询问是否用系统图片查看器打开。

### 测试片段（先压 1 分钟确认效果）

```bash
danmakustudio source/视频.mp4 source/弹幕.xml --test-clip [--clip-start 秒] [--clip-duration 60]
```

默认从**首条弹幕处**开始压 60 秒，输出自动命名为 `xxx.test-clip.mp4`；片段起点晚于 0 时弹幕从画面中部继续滚动，与完整压制完全一致。确认效果后去掉 `--test-clip` 重跑即可完成全量压制。

### 自定义 ffmpeg 压缩参数

```bash
danmakustudio source/视频.mp4 source/弹幕.xml --ffmpeg-args "-preset faster -crf 26 -maxrate 4M -bufsize 8M"
```

参数追加在 ffmpeg 命令末尾（后出现的参数优先生效），可覆盖默认压缩设置；也可写入配置文件 `encode.extra_args`（列表形式）。

### 弹幕压制工作台（菜单选择步骤，无固定顺序）

```bash
# source 可以是 YouTube 直播回放链接，也可以是本地视频文件
danmakustudio pipeline "https://www.youtube.com/watch?v=xxxx" [选项]
danmakustudio pipeline source/视频.mp4 [选项]
```

进入交互菜单，**自由选择要执行的步骤**（可反复执行、任意顺序、q 退出）：

```text
[1] 下载直播回放（yt-dlp，仅 URL 源）
[2] 下载视频封面（yt-dlp，转 PNG）
[3] 弹幕 JSON 转 XML
[4] 删除开头弹幕（前 N 条 / 前 40 条参照选择）
[5] 弹幕渲染配置摘要
[6] 生成预览图
[7] 展示 ffmpeg 压缩命令
[8] 测试压缩片段（先确认效果）
[9] 完整视频压缩
```

以本地视频为源时，自动匹配同目录同名 JSON（`xxx.live_chat.json` 或 `xxx.json`）；以 URL 为源时先执行步骤 1 下载。

步骤 4 用于去掉直播开始前发出的测试弹幕等：直接输入 N 删除开头 N 条（0 撤销删除）；输入 `r` 会列出前 40 条弹幕（序号/时间/用户/内容），选择保留第几条，其之前的弹幕全部删除。设置作用于之后的预览图、测试片段与完整压缩，重复执行步骤 4 可覆盖。

常用选项：`-c 配置`、`--out-dir 目录`（下载与中间产物）、`--encode auto|gpu|qsv|cpu`、`--lead-in 秒`（首条弹幕时间）、`--drop-first N`（删除开头 N 条弹幕，跳过交互步骤 4）、`--clip-duration 秒`（测试片段时长，默认 60）、`--ffmpeg-args "..."`（自定义压缩参数）、`--yes`（跳过"是否打开预览"等询问）、`--format "..."`（yt-dlp 下载格式）。下载需 yt-dlp 已安装且可访问 YouTube。

### 弹幕 JSON → XML（单独转换）

```bash
python json2xml.py [输入.json] [输出.xml] [--lead-in 秒]
```

默认输入 `chat.json`、输出 `video.xml`、首条弹幕 0 秒出现；工作台步骤 2 与图形界面的"JSON 转 XML…"按钮内部均调用同一转换。

## 配置

通过 `danmakustudio.yaml` 调整行为。

配置优先级：

```text
命令行指定的配置文件 > 当前目录 danmakustudio.yaml > 用户目录 danmakustudio.yaml > 内置默认值
```

如果显式指定的配置文件不存在或结构不合法，程序会直接报错，不会继续回退到其它配置文件。

源码运行时，程序会读取当前工作目录下的 `danmakustudio.yaml`；EXE 运行时，图形界面会优先使用 exe 同目录下的 `danmakustudio.yaml`，用户可以直接编辑。

下表中，“随项目配置值”来自当前仓库中的 `danmakustudio.yaml`；删除该文件或删除某个字段后，对应字段会回退到“内置默认值”。

### 布局样式 (style)

| 参数                    | 随项目配置值 | 内置默认值 | 说明                    |
| --------------------- | ------ | ----- | --------------------- |
| `danmaku_x`           | 20     | 20    | 弹幕起始 X 坐标（像素）         |
| `layer_width_extra`   | 100    | 100   | 已废弃：渲染层始终延伸至视频最右端，保留仅为兼容旧配置 |
| `row_vertical_gap`    | 1      | 1     | 弹幕行之间的垂直间距（像素）       |
| `multiline_row_gap`   | 1      | 1     | 多行文本行间距（像素）           |
| `font_family`         | "Microsoft YaHei" | "Microsoft YaHei" | 字体类型，置于回退链最前，空=仅内置字体族 |
| `font_size`           | 25     | 25    | 字体大小（pt），全局共用         |

`text` 与 `member_text` 分别为普通用户与会员的文本样式，结构相同、可分开调整：

| 参数               | text 随项目值 | member_text 随项目值 | 内置默认值 | 说明                     |
| ---------------- | -------- | ---------------- | ----- | ---------------------- |
| `color`          | "#FFFFFF" | "#45dd7f"       | "#FFFFFF" / "#45dd7f" | 字体颜色                  |
| `opacity`        | 0.7      | 0.7             | 0.7   | 文本不透明度（含描边）0~1         |
| `outline_color`  | "#246542" | "#246542"      | "#246542" | 描边颜色，空字符串表示禁用      |
| `outline_width`  | 0.7      | 0.7            | 0.7   | 描边厚度（像素，可为小数），0 表示禁用    |
| `shadow_color`   | "#000000" | "#000000"     | "#000000" | 阴影颜色，空字符串表示禁用      |
| `shadow_opacity` | 0.7      | 0.7            | 0.7   | 阴影不透明度 0~1             |

阴影偏移固定为右下 2 像素；阴影会在弹幕缓存图右/下边缘处被轻微裁剪。

`emoji` 为表情样式（普通与会员共用）：

| 参数            | 随项目配置值 | 内置默认值 | 说明                    |
| ------------- | ------ | ----- | --------------------- |
| `opacity`     | 1.0    | 1.0   | 表情不透明度 0~1            |
| `size_ratio`  | 1.0    | 1.0   | 表情大小比例（相对行高），1.0=与行高一致 |
| `spacing`     | 4      | 4     | 表情间距（像素）              |

### 布局比例 (ratio)

| 参数                 | 随项目配置值 | 内置默认值 | 说明         |
| ------------------ | ------ | ----- | ---------- |
| `max_text_rows`    | 20     | 4     | 文本弹幕最多显示行数（自动按可见高度钳制） |
| `text_width_ratio` | 0.8    | 0.8   | 文本弹幕最大宽度比例 |
| `top_margin`       | 12     | 12    | 顶部边距（像素），弹幕不会贴住画面上边缘 |
| `bottom_margin`    | 12     | 22    | 底部边距（像素）   |

### 动画参数 (animation)

| 参数                      | 随项目配置值 | 内置默认值 | 说明            |
| ----------------------- | ------ | ----- | ------------- |
| `first_danmaku_at`     | 0      | null  | 第一条弹幕对齐到该秒（0=视频一开始就出现）；null=保持字幕文件原时间轴 |

弹幕严格按字幕文件中的原始时间出现（无发射间隔/批次节流）；单帧行数超出 `max_text_rows` 时按行数预算顺延，最多延迟一帧。

车道分配带**防重叠探测**：同速滚动的弹幕同车道内相对位置不变，出生时若与前车间距小于前车宽度（会全程重叠）则改投其它空闲车道；多行弹幕占用连续车道；全部车道忙碌时退回哈希车道（保证按原时间发射）。

### 编码参数 (encode)

当前 `danmakustudio.yaml` 中的编码参数处于注释状态，因此使用内置默认值。

| 参数                        | 内置默认值    | 说明                                 |
| ------------------------- | -------- | ---------------------------------- |
| `gpu_preset`              | "p5"     | NVIDIA NVENC 预设（p1~p7，越大越慢、质量越好）   |
| `gpu_cq`                  | 15       | NVIDIA NVENC QP 质量（0~51，越小越好、文件越大） |
| `qsv_preset`              | "slow"   | Intel QSV 预设（越慢通常质量越好）             |
| `qsv_quality`             | 18       | Intel QSV 质量（0~51，越小越好、文件越大）       |
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

当前 `danmakustudio.yaml` 中的系统参数处于注释状态，因此使用内置默认值。

| 参数                      | 内置默认值    | 说明                          |
| ----------------------- | -------- | --------------------------- |
| `pipe_buffer_size`      | 10000000 | 管道缓冲区大小（字节）                 |
| `pipe_queue_size`       | 16       | 异步写入队列大小                    |
| `ffmpeg_timeout`        | 10       | FFmpeg 启动超时（秒）              |
| `stderr_thread_timeout` | 5        | 错误日志线程超时（秒）                 |
| `video_alignment`       | 2        | 视频尺寸对齐到偶数，避免 1080 被放大到 1088 |
| `max_queue_frames`      | 64       | 异步写入队列最大帧数                  |

## 打包

项目根目录提供一键打包脚本。干净 clone 中执行时，脚本会创建独立的 `.venv-build`、安装项目和 PyInstaller，然后根据 `DanmakuStudio.spec` 打包：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build_exe.ps1
```

如果只想验证打包依赖和入口是否可用、但不生成 exe：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build_exe.ps1 -CheckOnly
```

打包完成后，可执行文件位于：

```text
dist\DanmakuStudio\DanmakuStudio.exe
```

发布时请保留整个 `dist\DanmakuStudio\` 文件夹。程序仍依赖系统中的 FFmpeg / ffprobe。

不要运行 `build\DanmakuStudio\DanmakuStudio.exe`；它是 PyInstaller 的中间产物。最终程序是 `dist\DanmakuStudio\DanmakuStudio.exe`。

## 处理流程

```text
字幕解析 -> 资源加载 -> 视频信息获取 -> 弹幕布局计算 -> 逐帧渲染 -> FFmpeg 合成
```

1. 解析 XML/LRC 字幕；XML 会提取 `<d>` 标签（以及 `<e name url>` 表情元数据），LRC 会解析时间轴文本
2. 加载 Emoji 图片资源（本地 assets 或按 `<e>` URL 下载缓存），按需加载字体
3. 通过 ffprobe 获取视频宽高、帧率、总帧数
4. 预创建弹幕对象，计算布局参数
5. 构建 FFmpeg 编码命令，自动检测 GPU / QSV / CPU 编码路径
6. 逐帧渲染滚动弹幕图层，通过管道送入 FFmpeg，输出压制视频

## 项目结构

```text
DanmakuStudio/
├── src/danmakustudio/        # 核心包
│   ├── cli.py                # CLI 入口（encode/preview/pipeline 子命令）
│   ├── gui.py                # 图形界面入口
│   ├── batch.py              # 批量文件发现与字幕匹配
│   ├── config/               # 配置模块
│   ├── core/                 # 压制流程编排（burner/pipeline/preview）
│   ├── encode/               # FFmpeg 进程管理
│   ├── input/                # XML/LRC 字幕解析、YouTube 聊天 JSON 转换（chat2xml）
│   ├── layout/               # 布局计算、活跃弹幕状态
│   ├── render/               # 文本渲染、图片资源加载（emoji_fetch 远程表情下载）
│   └── utils/                # 工具函数与输入输出校验
├── tests/                    # 测试
├── DanmakuStudio.spec        # PyInstaller 打包配置
├── setup.ps1                 # 一键安装运行与开发依赖
├── test.ps1                  # 一键运行测试
├── run_gui.ps1               # 一键启动 GUI
├── build_exe.ps1             # 一键打包 / 打包前检
├── scripts/_common.ps1       # PowerShell 公共辅助函数
├── danmakustudio.yaml        # 默认配置文件
├── json2xml.py               # YouTube 弹幕 JSON → XML 转换脚本（薄封装）
├── pyproject.toml
├── uv.lock
├── LICENSE
└── README.md
```

`assets/` 目录为可选资源目录，通常用于存放 Emoji 图片或字体文件；当前干净仓库不包含具体素材资源。

## 许可证

本项目沿用 GPLv3 发布，详见仓库根目录的 `LICENSE` 文件。由于本项目基于 GPLv3 代码修改，分发修改版时也应保留 GPLv3、保留版权与许可声明，并公开对应源代码。



## 致谢

本项目基于[febr30th/DanmakuStudio](https://github.com/febr30th/DanmakuStudio))修改。
