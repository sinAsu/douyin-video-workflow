# 抖音长视频下载与 4:3 裁切 Codex Skill

从抖音链接解析到续传下载、H.264 版本选择、截图预览、4:3 居中裁切和成品校验的一套可移植流程。支持 Windows 的 NVIDIA NVENC、Intel Quick Sync、AMD AMF；没有可用硬件编码时使用 CPU。编码器会实际试运行，不仅检查名称。

## 在另一台电脑安装

仓库已公开，安装技能无需登录 GitHub。安装 Codex 后，直接把下面这句话发给 Codex：

> 使用 skill-installer，从 https://github.com/sinAsu/douyin-video-workflow 仓库安装 skills/douyin-video-workflow 这个技能。

技能安装后在下一轮消息中调用。Python、FFmpeg 和 Douzy 不是 Codex 自带的必然组件，第一次运行时让 Codex 按技能说明检测并安装所需依赖。抖音账号登录和验证码由用户本人完成。

## 调用示例

> 使用 $douyin-video-workflow，把以下抖音链接下载到 D:\Videos，选择最高可用分辨率的 H.264 版。先制作 4:3 居中裁切截图给我看；确认后再批量处理。左右对称裁掉多余画面，上下不动，不拉伸，保持高度、帧率、时长，音轨直接复制。目标视频码率接近原版，原文件保留，成品放到 D:\Videos_4x3。链接如下：……

仅裁切本地文件：

> 使用 $douyin-video-workflow，处理 D:\Videos 的 MP4，先给我截图确认居中4:3裁切效果，再按原视频平均码率批量处理，输出到 D:\Videos_4x3。

可再补充“每小时监控，异常修复，全部完成后通知并停止监控”；技能会使用可用的 Codex 自动化工具，不会假称后台 Python 具有聊天定时唤醒能力。

## 默认裁切规则

- 只居中对称裁左右，原高度、上下黑边、帧率和时长保留。
- 1140×720 → 960×720，两侧各裁90像素；1728×1080 → 1440×1080，两侧各裁144像素。
- H.264 MP4，音轨直接复制。默认目标视频平均码率接近原版，实际大小可能略有差异。
- 可选质量优先模式；它可能让文件变大，并不增加原有细节。
- 完成记录绑定源文件及参数，改名去重、续传与恢复分阶段处理。

目前附带解析器适配已登录、正在运行且支持页面桥的 Windows Douzy。平台或 Douzy 改版可能影响解析，不能保证永久有效。也支持从其他已授权工具导出的详情 JSON 选择版本。DASH 分离音视频下载不由本版本脚本自动执行。

## 测试

```powershell
python -m pip install -r skills/douyin-video-workflow/requirements.txt
python -m unittest discover -s tests -v
```

仓库只包含技能、脚本和合成测试，不包含视频、用户账户、Cookie、下载日志或真实签名媒体地址。
