---
name: product-demo-video-maker
description: 产品演示视频制作（剪辑为主，可扩展自动录屏）。默认流程：用户自己静音录好产品界面（.mov/.mp4，可多段），本 skill 看关键帧理解操作，按故事节拍拼接多段录屏、剪掉空档/加载/误操作、等待段高倍速压缩、核心点击推近加涟漪、敏感信息打码、合并补录片段、配帧级字幕（微软雅黑，烧入成片 + 可改 .srt + 无字幕版），按目标时长（如 30s/60s）分配节奏，用户改 srt 后一键重烧。也支持从 Web URL（无头录制）、一组截图、或由 AI 代操作 Mac 应用来生成素材。触发词：剪辑录屏、演示视频剪辑、配字幕、加字幕、录屏加推近、打码、补录合进去、多段录屏合成、改字幕重烧、控制在 30 秒/1 分钟、产品演示视频、录个 demo、截图做视频、product demo video。
---

# 产品演示视频剪辑

流水线：素材 → 统一时间线 `events.json` → `render.py` 做全部后期（剪辑/倍速/推近/涟漪/打码/字幕）。

```
用户录屏(可多段) ─concat→plan.json─from_video.py─┐   （默认，详见 references/edit-recording.md）
Web URL ──rec_web.py─────────────────────────────┤
截图    ──from_shots.py───────────────────────────┼──> events.json ──render.py──> 成片.mp4 + .srt
Mac 代录 ─rec_mac.py/ui.py────────────────────────┘   （详见 references/record-channels.md）
```

```bash
SK=<本技能目录>/scripts
PY=python3   # 需安装 opencv-python pillow；Web 录制另需 playwright，Mac 代录需 pyobjc
```

## 第 0 步：本机执行约束（不遵守必翻车）

1. 所有命令非沙箱运行（`dangerouslyDisableSandbox: true`），固定前缀 `cd <work>/_tmp && export TMPDIR=$PWD;`。`_tmp` 用 Write 工具写占位文件来创建（非沙箱 `mkdir` 不生效）。
2. 本机安全中心拦截删除；不覆盖旧文件，成片版本号递增。复制用 `cat src > dst`（`cp` 静默失败）。
3. 输出里的 `sitecustomize ... temporary directory`、`Blocked by Security Center` 是噪音，只看脚本的 `[ok]` / `[x]` / `[!]`。
4. 渲染超过 2 分钟的任务用 `run_in_background: true`；不要前台 `| tail` 等长任务。
5. zsh 不做变量分词：时间点、坐标等参数逐个写进命令，别塞进一个变量再展开。
6. 看图（关键帧、抽帧、网格图）用 ViewImage，Read 读图片只返回路径。

## 默认偏好

- 素材来自真实产品界面，后期只加字幕 + 核心点击推近。不加片头片尾、过渡页、背景、概念卡，除非用户明确要求。
- 成片尺寸 = 录屏原尺寸，不强制 1080p；时长按用户要求（常为 30s / ≤60s），差 1s 以内算达标。
- 字幕：微软雅黑粗体，白字黑底垂直居中，每条 ≤20 字、≥3s；只写画面里看得见的，不解读 sample 数据。
- 对外视频：非演示团队名等敏感信息打码，全片扫一遍，拿不准的问用户。
- 每次交付三件：`_vN.mp4`、`_vN.srt`、`_vN_无字幕.mp4`。

## 剪辑原则（通用，详见 edit-recording.md）

1. **先排故事再剪**：入口 → 操作 → 价值页 → 细节 → 落地；多段素材按节拍拼，不按文件名。（§2）
2. **时间给价值页**：操作/输入/等待压缩，结果页、概览、答案放慢；凑时长降倍速，不加内容。（§4）
3. **过渡页**：开场留 1–2s 入口；中段刷新、白屏、加载、空态页直接剪掉；无关操作哪怕 1 秒也剪。（§4）
4. **推近宁少勿多**：只推核心点击和局部组件，整屏宽元素不推；同屏连续关注点一次推近 + 平移，避免来回晃。（§6）
5. **交付时说清缺口**：素材没拍到的效果不写进字幕，建议补录。（§10）

## 选通道

| 用户给的 | 做法 |
|---|---|
| 自己录好的录屏（一段或多段） | **默认**，读 `references/edit-recording.md` 按 §1 流程走 |
| 补录 / 漏发的片段 | 重新 `concat` 或插入合并，`references/edit-recording.md` §8 |
| 产品 URL / 截图 / 要求 AI 代操作 Mac 应用 | 读 `references/record-channels.md` |

## 默认流程速览（用户录屏）

```bash
$PY $SK/from_video.py analyze <录屏> --work <work>/v1                     # 每段：keyframes_*.jpg + analysis.json
$PY $SK/from_video.py concat <work>/combined.mp4 <录屏1> <录屏2> ...       # 多段时：→ combined.sources.json
$PY $SK/inspect_tool.py gridframes <源录屏> t1 t2 ... --out-dir <work>    # 读推近/打码坐标（标签=源像素）
# 写 <work>/plan.json：保留段 [源序号,t0,t1,倍速,字幕] + focus + blur
$PY $SK/from_video.py plan <work> --target 30                            # → edit.json + captions_src.srt + 时长/字幕告警
$PY $SK/from_video.py build <work> --nosub
$PY $SK/from_video.py reburn <work> --nosub                              # 用户只改了 _vN.srt 时
```

plan.json 格式、倍速参考、读坐标方法、打码检查清单、补录合并命令都在 `references/edit-recording.md`。
样式参数表在 `references/style.md`；默认样式 `assets/style_plain_ui.json`。

## 验收（交付前必做；看不到视频，靠抽帧）

```bash
$PY $SK/inspect_tool.py frames <成片.mp4> 1.8 5.1 8.5 ...   # → <成片>_frames[_N].jpg，ViewImage 查看
```

- 每条字幕中点：字幕与画面对应，无错别字；流式输出的文字按实际出现时刻对位。
- 每个推近时刻 +0.15s：已推近到目标、目标未被裁；推近密集段相邻帧无来回缩放。
- 中段无白屏/刷新/加载帧，跨段计数与状态不矛盾；打码段原尺寸裁剪核对。
- 时长达标；每条字幕至少 3s 可读。

同一问题改 3 次仍不过，停下把抽帧图给用户看并问方向。
交付：`present_files` 给 mp4 + srt + 无字幕版；回复写清时长、字幕条数、节拍/字幕表、推近/打码与剪掉的内容、素材缺口与待确认项。

## 遇到问题 / 沉淀经验

- 先查 `references/pitfalls.md`（工具/环境/渲染层面的实测坑，按 A–F 分组）。
- 沉淀新经验时**不要直接追加到文件末尾**：先判断它是剪辑原则（→ edit-recording.md 对应章节，必要时同步本文件「剪辑原则」一行）、工具坑（→ pitfalls.md 对应分组）还是录制通道问题（→ record-channels.md）；已有同类条目就改写合并，写成跨产品通用的表述，不写具体项目名。
