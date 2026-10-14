# product-demo-video-editor

[Agent Skill](https://agentskills.io) / [Claude Skill](https://www.anthropic.com/news/skills) 形式的**产品演示视频剪辑**工具：把一段自己录制的产品界面录屏，交给 AI 看关键帧理解操作，产出一条带字幕、带点击推近的演示成片。

```
用户录屏 ──from_video.py──┐
Web URL ──rec_web.py──────┤
截图    ──from_shots.py────┼──> events.json ──render.py──> 成片.mp4 + .srt
Mac 代录 ─rec_mac.py/ui.py─┘
```

## 它做什么

- **剪辑**：自动识别画面活动段，剪掉空档与误操作，Agent 执行/生成的等待段高倍速压缩（实测 7 分钟压到 3 秒）
- **字幕**：看关键帧理解操作后逐条配字幕，微软雅黑、黑底白字、垂直居中；同时产出烧入版、可编辑 `.srt`、无字幕版三种
- **推近**：录屏没有点击数据时，AI 从画面里读出光标位置，给核心点击加推近 + 涟漪
- **打码**：对非演示用的团队名、人名等敏感信息打马赛克（镜头推近后依然覆盖）
- **改了重烧**：用户改完 `.srt`（文字或时间）一键重新合成，样式不变；也可合并补录片段

## 快速开始

```bash
# 1 分析录屏：生成关键帧联系表 + 剪辑建议
python3 scripts/from_video.py analyze your_recording.mov --work ./work

# 2 AI 阅读工作目录里的 keyframes_*.jpg，写 work/captions_src.srt，并修订 work/edit.json
#   （cut 剪段 / speed 倍速 / focus 推近 / blur 打码 / style 样式）

# 3 渲染：成片_v1.mp4 + 可编辑成片_v1.srt + 成片_v1_无字幕.mp4
python3 scripts/from_video.py build ./work --nosub

# 4 改完成片 .srt 后重烧下一版
python3 scripts/from_video.py reburn ./work --nosub
```

依赖：Python 3.9+，`pip install opencv-python pillow`；`rec_web.py` 另需 `playwright`，`rec_mac.py` 需 macOS + ffmpeg。

## 目录结构

```
SKILL.md                     # Skill 入口（触发条件、约束、流程速览）
references/
  edit-recording.md          # 默认流程：剪辑用户录屏（edit.json 结构、读坐标、打码清单、补录合并）
  record-channels.md         # 其他素材来源：Web 无头录制 / 截图 / Mac 代录
  style.md                   # render.py 样式参数表
  pitfalls.md                # 36 条实测踩坑（症状 → 根因 → 解法）
scripts/                     # from_video / render / rec_web / rec_mac / from_shots / ui / inspect_tool ...
assets/                      # 分镜与样式示例、自测用模拟页面
```

## 说明

- 为个人工作流打磨的 skill：默认偏好（纯界面 + 字幕、原尺寸输出、≤60s）等已内置，可按需调整。
- 详细用法见 `SKILL.md` 与 `references/`。
