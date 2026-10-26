# product-demo-video-maker

[Agent Skill](https://agentskills.io) / [Claude Skill](https://www.anthropic.com/news/skills) 形式的**产品演示视频剪辑**工具：把一段自己录制的产品界面录屏，交给 AI 看关键帧理解操作，产出一条带字幕、带点击推近的演示成片。

```
用户录屏 ──from_video.py──┐
Web URL ──rec_web.py──────┤
截图    ──from_shots.py────┼──> events.json ──render.py──> 成片.mp4 + .srt
Mac 代录 ─rec_mac.py/ui.py─┘
```

## 它做什么

- **剪辑**：自动识别画面活动段；多段录屏按故事顺序拼成一条时间线；剪掉空档、加载页与误操作，等待段高倍速压缩（实测 7 分钟压到 3 秒）；按目标时长（如 30s）估算并分配节奏
- **字幕**：看关键帧理解操作后逐条配字幕，微软雅黑、黑底白字、垂直居中；同时产出烧入版、可编辑 `.srt`、无字幕版三种
- **推近**：录屏没有点击数据时，AI 从带源像素网格的抽帧里读出坐标，给核心点击加推近 + 涟漪，同屏连续关注点平移衔接
- **打码**：对非演示用的团队名、人名等敏感信息打马赛克（镜头推近后依然覆盖）
- **改了重烧**：用户改完 `.srt`（文字或时间）一键重新合成，样式不变；也可合并补录片段
- **故事线合集**：把几个已定稿的功能演示串成一条带客户故事的片子（如展区 2 分钟循环）；章节过渡页直接取参考 PPT 的底图、logo、字体，与演讲视觉统一（`scripts/story.py`）

## 快速开始

```bash
# 0 建项目目录：~/Movies/demo-videos/<项目名>/{source,work,output,scripts} + README + CHANGELOG（PDVM_ROOT 可改根目录）
#   --src 的录屏会复制归档到 source/（加 --link 则只建软链接）
python3 scripts/from_video.py init my-demo --src rec1.mov rec2.mov
cd ~/Movies/demo-videos/my-demo   # 以下 ./work 即项目的 work/

# 1 分析录屏：生成关键帧联系表 + 活动段
python3 scripts/from_video.py analyze rec1.mov --work ./work/analyze/rec1

# 2 多段录屏时先合并（单段跳过）
python3 scripts/from_video.py concat ./work/combined.mp4 rec1.mov rec2.mov rec3.mov

# 3 AI 阅读 keyframes_*.jpg，写 work/plan.json（保留段 + 倍速 + 字幕 + 推近 + 打码），生成剪辑参数
python3 scripts/from_video.py plan ./work --target 30

# 4 渲染到 output/：成片_v1.mp4 + 可编辑成片_v1.srt + 成片_v1_无字幕.mp4
python3 scripts/from_video.py build ./work --nosub

# 5 改完成片 .srt 后重烧下一版
python3 scripts/from_video.py reburn ./work --nosub
```

依赖：Python 3.9+，`pip install opencv-python pillow`；`rec_web.py` 另需 `playwright`，`rec_mac.py` 需 macOS + ffmpeg。

## 目录结构

```
SKILL.md                     # Skill 入口（触发条件、约束、剪辑原则、流程速览）
references/
  edit-recording.md          # 默认流程：故事节拍、plan.json、节奏与时长、字幕、推近、打码、多段合并、验收
  record-channels.md         # 其他素材来源：Web 无头录制 / 截图 / Mac 代录
  story-cards.md             # 故事线合集：何时加过渡页、按参考 PPT 取视觉、卡片版式、转场与字幕规则
  style.md                   # render.py 样式参数表
  pitfalls.md                # 工具/环境/渲染实测踩坑（症状 → 根因 → 解法）
scripts/                     # from_video / render / story / rec_web / rec_mac / from_shots / ui / inspect_tool ...
assets/                      # 分镜与样式示例、自测用模拟页面
```

## 说明

- 为个人工作流打磨的 skill：默认偏好（纯界面 + 字幕、原尺寸输出、≤60s）等已内置，可按需调整。
- 详细用法见 `SKILL.md` 与 `references/`。
