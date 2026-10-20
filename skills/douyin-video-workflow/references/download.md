# 解析和下载

## Douzy 本机桥

Douzy 官方项目：[jiji262/douyin-downloader](https://github.com/jiji262/douyin-downloader)。使用当前官方文档确认安装包、版本及平台支持；本技能不包含或修改 Douzy 软件，也不绕过其付费功能。现有下载功能可能对单 URL 或单任务设置固定总时限，超长视频会超时；解析成功和下载完成应分开判断。

`resolve_douzy.py` 适用于带本机页面桥的 Windows Douzy 版本。启动 Douzy、完成用户登录后，将完整的 `https://www.douyin.com/video/ID` 链接写入本地 UTF-8 文本。短分享链接先用浏览器展开。

```powershell
python "<技能目录>\scripts\resolve_douzy.py" --links "D:\Task\links.txt" --codec h264 --manifest "D:\Task\manifest.json"
python "<技能目录>\scripts\workflow.py" download --manifest "D:\Task\manifest.json" --output "D:\Videos"
```

`--codec any` 是所有完整 MP4 中分辨率优先，`h264` 是 H.264 内的最高分辨率。脚本打印选项，不打印签名 URL。它只读取当前 Douzy sidecar 进程中页面桥的地址和临时授权，在内存中调用固定的详情端点，不读取浏览器 Cookie 数据库、不关闭浏览器、不导出账号信息。页面桥必须是本机 `127.0.0.1`，不向外部地址发送该授权。找不到桥、无权读取进程或接口不兼容时停止此路径，改用正常浏览器解析/详情导出，不查找或转储任意进程内存。

长下载可给 `download` 加 `--background`。任务记录和 `worker.log` 保存在下载目录的 `.workflow-state`，进度可通过 `.part` 大小增长检查。启动消息不代表开始传输，要核对实际进程和文件增长；完成清单只在核验成功后更新。

桥协议随 Douzy 版本可能改变。只有本机明确授权的应用会话才能复用；不要尝试认证绕过。登录、验证码、风控验证需用户在原应用中完成。

## 详情 JSON 路径

当已有工具提供抖音详情响应时，将其保存在任务目录，支持 `aweme_detail`、`body.aweme_detail` 或直接详情对象：

```powershell
python "<技能目录>\scripts\workflow.py" select --detail "D:\Task\detail.json" --codec h264 --manifest "D:\Task\manifest.json"
```

`select` 为单视频生成并覆盖指定清单；多视频清单用解析脚本汇总，或由 agent 合并数组，并按 ID/版本检查重复。不能覆盖用户的其他任务清单。

完整 MP4 版本来自 `video.bit_rate` 中的 `format=mp4` 以及明确的 `play_addr_h264`/`play_addr_265`。不把 DASH 的视频轨作为完整音视频下载。需要 DASH 时应单独取得匹配音轨、下载并无损封装，再核验同步；本脚本目前不实现该路径，不得把它当成已经支持。

选中的清单字段包含：`id`、`title`、`variant`、`codec`、`width`、`height`、`bitrate`、`size`、`md5`、`urls`。其中 `urls` 是短期签名信息，只保存本机，不写入 Git/聊天。下载端仅接受 HTTPS 的 `*.douyinvod.com`。平台更换 CDN 时先核实归属再修改允许范围。

## 续传与完成身份

- 输出使用 ID、编码、分辨率和版本摘要命名；下载完成后可改名，`.workflow-state/downloads.json` 用内容指纹重新找到文件。
- 首次任务前已有用户改名文件且没有身份记录时，不猜测对应关系；核对源 ID、长度、视频参数/内容后才登记。
- `.part` 属于具体版本；新解析若返回另一资源版本，不混用旧片段。签名改变但版本和长度相同可继续。
- 服务器忽略 Range 返回 200 时从头写入，不能把完整响应追加到残片后。206 必须匹配请求偏移和总长。
- 网络错误有限重试；401/403/404/410 停止并重新解析。每次读取默认 60 秒超时，不对几个小时的视频设置 300/900 秒总时限。
- 完成后核对长度、平台 MD5（有则核对）、MP4 结构、实际编码和尺寸，再登记；失败保留原文件与残片。
- 初始解析很久后才下载的条目可能过期；重新解析目标条目再重跑即可，已完成版本会跳过。

单连接续传是可移植默认。如果需要更高速度，可安装官方 aria2，并使用其 `--continue=true`、有限连接数及无限制总时长；保留 `.aria2` 状态，仍按上述规则核验和登记。不要混用 HTTP `.part` 与 aria2 的文件布局。
