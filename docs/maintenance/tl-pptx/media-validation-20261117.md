# 条件音视频能力迁移证据

本次维护使用上游 `7e0df6705de0cd24345210d31f07fcb5a32b6900`，来源和目标 SHA-256 见同目录 `media-source-map-20261117.json`。运行文件均小于 1000 行，单行不超过 200 字符。没有真实联网 TTS、读取真实凭据、启动 Office、下载或加载模型。

## 保留和拆分

- 迁入 notes_to_audio、6 个 TTS provider 与共用发布 helper、PowerPoint 原生视频导出、字幕校准、音效同步、narration_sync、video_sound_mix。
- narration_sync 拆为 base（常量、dataclass、原门禁调用）、helpers（字幕及动画计划）、media（音频时间校准）、原名入口；video_sound_mix 拆为 base、probe、cues、原名入口。原函数与类 AST 全部一致，显式再导出保留原符号。
- config 保留配置 API，CANVAS_FORMATS 引用已迁入的同源静态常量。dotenv 不再搜索 cwd、仓库或 home；只有显式 env_path 才读取。notes CLI 在参数/provider 选择后，且有 --env-file 时仅加载该 provider 的键前缀。
- OpenAI backend 将可选 aiohttp 缺失转为明确 RuntimeError；WAV 写入使用等价 Wave_write 构造器，解决静态分析将 wave.open(wb) 误判为读对象。音频算法未变。
- 上游 configure_utf8_stdio 和分发门禁调用保留。现有 console 门禁兼容由主代理协调，未在本子任务中覆盖、禁用或模拟门禁。

## 条件依赖和平台

| 能力 | 条件 |
| --- | --- |
| Edge TTS | edge-tts >=7.2.8、网络及具体发送授权 |
| OpenAI Realtime | aiohttp、网络、明确 provider/账户及凭据授权 |
| Qwen/MiniMax/CosyVoice/ElevenLabs | 标准库 HTTP；网络、provider 凭据及发送授权；服务返回格式需真实验收 |
| 原生视频 | Windows、PowerPoint COM、PowerShell；macOS/Linux 不声称可运行 |
| 音频时间校准/混音校验 | numpy >=1.20、ffmpeg、ffprobe、合法媒体和实际工具版本验收 |
| 字幕对齐 | stable-ts（导入 stable_whisper）、其 torch/Whisper 依赖、已部署且授权的模型、ffmpeg；模型名不等于已经下载 |
| 音效库同步 | 显式 sound index/许可资源；默认 templates/sounds 尚未作为已部署资源验收 |

这些为按功能启用的依赖，不应默认扩展核心 SVG→PPTX 安装。没有为本批安装上述可选组件。外部服务、模型和 Office 的可用性都未验证。

## 实际验证

macOS，隔离维护环境 Python 3.14.10。`scripts/test/test_media_backends.py`：5/5 PASS。测试分别验证 mock Qwen 请求参数、缺音频 URL 失败、合成音频/字幕成对本地发布、合成 word boundaries 到 SRT、合成 PCM16 到 WAV 及逐字稿不一致拒绝。所有网络边界均 mock，不是服务验收。

`test_media_control_flow.py` 新增 3 个测试（PowerPoint 参数、mock 字幕模型调用、显式 dotenv fixture），**未执行**，等待完整性门禁就绪；测试不 mock 门禁。不能将其存在表述为控制流已通过。

运行时和已执行测试 Pylint E/F 专项为 0；全量检查 exit 28，仍有 336 Convention、84 Refactor、117 Warning，不宣称全量规范通过。主要为保真再导出、拆分 base 的共享导入、原函数复杂度与缺 docstring。已复核有意读取 parsed.port（用于非法端口报错）、固定三个音频数组被误判的解包和 provider 失败边界的大异常捕获；未批量禁用规则。C0301/C0321 为 0。

本轮未验证 Linux、Windows、真实 PowerPoint/播放器、视觉和听觉、实时 provider、模型结果、宿主与 DeepAgents、最终独立包闭包及公共安全入口。源 API 的直接文件/子进程/网络动作须经上层授权、路径/输入预算、私有任务工作区、发布与生命周期约束后才对业务任务开放。
