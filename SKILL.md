---
name: product-demo-video-maker
description: 产品演示视频制作（剪辑为主，可扩展自动录屏）。默认流程：用户自己静音录好产品界面（.mov/.mp4），本 skill 看关键帧理解操作，剪掉空档与误操作、Agent 等待段高倍速压缩、核心点击推近加涟漪、敏感团队信息打码、合并补录片段、配帧级字幕（微软雅黑，烧入成片 + 可改 .srt + 无字幕版），用户改 srt 后一键重烧。也支持从 Web URL（无头录制）、一组截图、或由 AI 代操作 Mac 应用来生成素材。触发词：剪辑录屏、演示视频剪辑、配字幕、加字幕、录屏加推近、打码、补录合进去、改字幕重烧、控制在 1 分钟、产品演示视频、录个 demo、截图做视频、product demo video。
---

# 产品演示视频剪辑

流水线：素材 → 统一时间线 `events.json` → `render.py` 做全部后期（剪辑/倍速/推近/涟漪/打码/字幕）。

```
用户录屏 ──from_video.py──┐   （默认，详见 references/edit-recording.md）
Web URL ──rec_web.py──────┤
截图    ──from_shots.py────┼──> events.json ──render.py──> 成片.mp4 + .srt
Mac 代录 ─rec_mac.py/ui.py─┘   （详见 references/record-channels.md）
```

```bash
SK=<本技能目录>/scripts
PY=python3   # 需安装 opencv-python pillow playwright pyobjc
```

## 第 0 步：本机执行约束（不遵守必翻车）

1. 所有命令非沙箱运行（`dangerouslyDisableSandbox: true`），固定前缀 `cd <work>/_tmp && export TMPDIR=$PWD;`。`_tmp` 用 Write 工具写占位文件来创建（非沙箱 `mkdir` 不生效）。
2. 本机安全中心拦截删除；不覆盖旧文件，成片版本号递增。复制用 `cat src > dst`（`cp` 静默失败）。
3. 输出里的 `sitecustomize ... temporary directory`、`Blocked by Security Center` 是噪音，只看脚本的 `[ok]` / `[x]`。
4. 渲染超过 2 分钟的任务用 `run_in_background: true`；不要前台 `| tail` 等长任务。
5. zsh 不做变量分词：时间点、坐标等参数逐个写进命令，别塞进一个变量再展开。

## 默认偏好

- 素材来自真实产品界面，后期只加字幕 + 核心点击推近。不加片头片尾、过渡页、背景、概念卡，除非用户明确要求。
- 成片尺寸 = 录屏原尺寸，不强制 1080p；时长按用户要求（常为 ≤60s）。
- 字幕：微软雅黑粗体，白字黑底垂直居中，每条 ≤20 字；不解读 sample 数据，产物出来直接进展示/上传。
- 对外视频：非演示团队名等敏感信息打码，全片扫一遍，拿不准的问用户。
- 每次交付三件：`_vN.mp4`、`_vN.srt`、`_vN_无字幕.mp4`。

## 选通道

| 用户给的 | 做法 |
|---|---|
| 自己录好的录屏 | **默认**，读 `references/edit-recording.md` 按 §1 流程走 |
| 补录片段 | 先拼接再剪，`references/edit-recording.md` §7 |
| 产品 URL / 截图 / 要求 AI 代操作 Mac 应用 | 读 `references/record-channels.md` |

## 默认流程速览（用户录屏）

```bash
$PY $SK/from_video.py analyze <录屏> --work <work>   # keyframes_*.jpg + analysis.json + edit.json 草稿
# Read 关键帧 → 写 captions_src.srt（源时间）→ 改 edit.json：cut / speed / focus（推近）/ blur（打码）/ style（字体）
$PY $SK/from_video.py build <work> --nosub
$PY $SK/from_video.py reburn <work> --nosub         # 用户改完 _vN.srt 后
```

`edit.json` 结构、读坐标方法、打码检查清单、补录合并命令都在 `references/edit-recording.md`。
样式参数表在 `references/style.md`；默认样式 `assets/style_plain_ui.json`。

## 验收（交付前必做；看不到视频，靠抽帧）

```bash
$PY $SK/inspect_tool.py frames <成片.mp4> 1.8 5.1 8.5 ...   # → <成片>_frames.jpg，Read 查看
```

- 每条字幕中点：字幕与画面动作对应，无错别字；流式输出的文字按实际出现时刻对位。
- 每个推近时刻 +0.15s：已推近到目标，涟漪在目标上。
- 打码段：原尺寸裁剪放大核对，无漏打、无误挡保留项。
- 时长达标；每条字幕至少 3s 可读。

同一问题改 3 次仍不过，停下把抽帧图给用户看并问方向。
交付：`present_files` 给 mp4 + srt + 无字幕版；回复写清时长、字幕条数、推近/打码处理了哪些、待用户确认的项。

## 遇到问题

先查 `references/pitfalls.md`（36 条实测坑，按环境/录制/渲染/剪辑分组）；新坑按「症状 → 根因 → 解法」追加到末尾并标日期。
