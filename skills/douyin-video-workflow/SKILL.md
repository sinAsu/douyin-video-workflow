---
name: douyin-video-workflow
description: Use when the user wants to download long Douyin videos at the highest available resolution or in H.264, resume interrupted downloads, or batch crop local videos symmetrically to 4:3 while preserving height, frame rate, duration and audio.
---

# 抖音长视频下载与 4:3 裁切

将解析、下载、裁切分为可独立恢复的阶段。输入是用户指定的抖音链接或本地 MP4；不绑定某台电脑的盘符、视频数目或显卡。所有辅助脚本位于本技能的 `scripts/`。首先明确本次只下载、只裁切，还是执行完整流程。

## 输入与约束

- 原片始终保留，成品写到不同目录。不要在已有原片上覆盖编码。
- 默认按分辨率优先选择平台返回的完整 MP4 版本，不把高码率的 720p 排在 1080p 前面。用户要求 H.264 时只在 H.264 中选择；没有匹配版本就说明，不偷偷转码或改用 HEVC。不要宣称工具返回的最高档等于平台绝对最高档。
- 裁切严格遵守：左右以中心轴对称裁掉多余画面，上下不动、不缩放、不拉伸；保持原高度、原帧率、原帧数、播放时长和音轨。原有上下黑边、字幕、水印不做额外处理。边缘文字可能被裁掉，在预览中明确指出。
- 输出 H.264 MP4，音轨 `-c:a copy`。默认 `source-bitrate` 模式，目标**视频**平均码率接近原版；实际码率不保证逐字节相同。只有用户明确优先画质时使用 `quality` 模式，并说明它可能产生 2～3 倍体积，不会恢复原片已有的细节。

## 环境与执行入口

使用现有 Python 3.10+。在本任务独立的虚拟环境安装 `requirements.txt`；不要改动用户其他项目依赖。FFmpeg 优先 `FFMPEG` 环境变量或 PATH，其次 `imageio-ffmpeg` 自带版本。不要把第三方可执行文件、Cookie、账号信息、媒体直链或视频上传到仓库。

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r "<技能目录>\requirements.txt"
.venv\Scripts\python "<技能目录>\scripts\video_crop.py" doctor
```

`doctor` 的编码器列表只表示二进制包含支持，不能证明显卡或驱动能工作。`run --encoder auto` 会对实际输入做短编码测试：NVENC → QSV → AMF → libx264。不凭“有独显”推断可用编码器或速度。Apple 硬件编码不包含在此自动顺序中，可使用 CPU 路径。

## 1. 解析与下载

读 [references/download.md](references/download.md)。优先复用用户已登录的 Douzy，由其页面桥取得完整详情；登录与网站验证由用户操作。也可使用当前可用的浏览器工具或下载器取得同样的 `aweme_detail` JSON，再使用 `workflow.py select`。解析器可能随平台更新失效，不能反复调用同一失败路径。

先列出实际可选的编码、分辨率、视频码率和文件大小，再按用户需求选择。解析成功是已经拿到有效详情/媒体版本，**不需要等下载任务失败**。不为了得到日志让数 GB 视频不断经历 300 秒总超时。

下载使用稳定视频 ID **加版本标识**作为身份；H.264、HEVC 和不同清晰度是不同版本。同一版本改名后，通过大小和内容指纹识别，避免重复下载。逐项完成并验证后登记，不等整批成功才登记。

`workflow.py download` 使用标准 HTTP Range 续传，每次网络读有超时，没有按视频长度设总超时。401/403/过期必须重新解析同一版本，再重跑；保留 `.part`，不要把拒绝访问当成完成。不要打印签名直链。

## 2. 预览并确认裁切范围

```powershell
python "<技能目录>\scripts\video_crop.py" preview --input "D:\Videos" --output "D:\CropPreview" --at 600
```

展示脚本生成的 `*_comparison.jpg`：左边原图红框是保留区域，右边是实际裁切图，两边等高。不同剧集各选代表画面；必要时换时间点检查边缘字幕和人物。首次处理新的源文件，先取得用户对预览的确认；若本次已确认同样范围，不重复索要确认。

精确公式：`crop_width = height × 4 / 3`，`x = (width - crop_width) / 2`。例如 1140×720 → 960×720，左右各 90；1728×1080 → 1440×1080，左右各 144。脚本拒绝无法在 YUV420 网格上精确对称裁切的尺寸，不悄悄取整改变比例。宽度不足、非方形像素、旋转画面、HDR/10-bit 等情况先探测并指出约束冲突，不能套用普通 8-bit、SAR 1:1 的流程。

## 3. 试编码与整批处理

用户确认预览后，以其编码偏好运行；默认保原视频平均码率。需要比较质量时，先在单独目录制作短样片，说明速度和预计容量再处理长片。使用真实测得的视频时长/编码速度估算时间，忽略中途停止的闲置时间；不要许诺换显卡一定提升多少。

```powershell
python "<技能目录>\scripts\video_crop.py" run --input "D:\Videos" --output "D:\Videos_4x3" --mode source-bitrate --encoder auto --background
python "<技能目录>\scripts\video_crop.py" status --output "D:\Videos_4x3"
```

不得加入 `-r`、`-shortest`、拉伸、静音填充或音频重编码来掩盖时长差异。不要在未确认时切换成质量模式。后台任务启动后核实子进程和新进度都存在，再报告“正在处理”。脚本使用目录锁，不要重复启动相同目录的编码。

## 4. 恢复、监控与完成核验

读 [references/recovery.md](references/recovery.md) 处理长任务异常、MP4 音轨时长及恢复。每个输出在 `.crop-state/jobs.json` 绑定源文件内容指纹和参数，参数变化需新输出目录。完整的 `.partial.mp4` 先校验再改名；中途编码失败不具备视频断点续编能力，重试保留旧片到 `interrupted` 后重编码该部，不能拼接残片假称完成。

脚本比较比例、高度、视频帧数/时长、音轨数/编码/采样数/播放时长，抽查首中尾可解码。原片的 `mdhd` 时长与真实播放时长不一致时使用 edit list，并比较复制音轨的数据 SHA-256，不能只放宽容差。

只有全部文件核验成功才报告整批完成，并提供输出目录。用户明确要求定时监控时，用 Codex 内置自动化在本聊天设置检查；正常推进时安静，异常排查并通知，全部完成后停止监控。没有自动化工具时明确说明不能定时唤醒，不用脚本伪造通知能力。
