# 剪辑用户自录屏（通道 D，默认流程）

目录：1 流程 · 2 edit.json · 3 剪辑节奏 · 4 字幕 · 5 推近 focus · 6 打码 blur · 7 补录合并 · 8 验收 · 9 交付与迭代

## 1 流程

```bash
# 前缀（见 SKILL.md 第 0 步）：cd <work>/_tmp && export TMPDIR=$PWD; 非沙箱运行
$PY $SK/from_video.py analyze <录屏.mov> --work <work>      # 关键帧联系表 keyframes_XX.jpg + analysis.json + edit.json 草稿
# Read 全部 keyframes_*.jpg；看不清的段用 inspect_tool.py frames <录屏> t1 t2 ... 补抽
# 写 <work>/captions_src.srt（源时间）+ 改 edit.json（cut/speed/focus/blur/style）
$PY $SK/from_video.py build <work> --nosub                 # → <name>_vN.mp4 + _vN.srt + _vN_无字幕.mp4
$PY $SK/from_video.py reburn <work> [--srt 改过的.srt] --nosub   # 用户改完成片字幕后重烧下一版
```

- 先确认源文件存在：用户可能中途改名（实测发生过源文件中途被改名）。报 No such file 时 `ls` 目录按大小/修改时间找。
- 窗口录制（⌘⇧5 录所选窗口）不需要 crop；全屏录制才填 `crop`。
- 成片尺寸 = 录屏原尺寸（`out_size: source`），不强制 1080p。

## 2 edit.json

```json
{"video": "<源或 combined.mp4>", "crop": null, "name": "产品演示",
 "cut":   [[t0,t1], ...],
 "speed": [[t0,t1,倍速], ...],
 "focus": [{"t":点击时刻,"x":px,"y":px,"zoom":1.8,"hold":1.2},
           {"t0":..,"t1":..,"box":[x,y,w,h],"zoom":1.6}],
 "blur":  [{"t0":..,"t1":..,"box":[x,y,w,h],"cell":26,"note":"说明"}],
 "style": {"font":".../msyh.ttc","font_bold":".../msyhbd.ttc"}}
```

所有时间是**源视频时间**，坐标是**源像素**。推荐用「保留段列表」思路写：先列 `keep=[(t0,t1,倍速),...]`，再由程序生成 cut（保留段之间的空隙）和 speed（倍速≠1 的段），不易漏。

## 3 剪辑节奏（时长目标常为 ≤60s）

- 先按 analysis.json 的 `runs`（活动段）定保留段：每个操作保留「点击前 0.3s → 结果出现后 1~2s」。
- Agent 执行/生成过程（几分钟）一整段高倍速压到 3s 左右（实测 424s × 141 倍）；配一条覆盖全段的字幕。
- 误操作段（贴错图、走错页）直接 cut，不要只加速。
- 每条字幕在成片里至少 3s 可读；不够就降低该段倍速，而不是缩字幕。
- 核心点击所在段用 1× 速，否则推近一闪而过。
- 估算时长：`sum((b-a)/f for a,b,f in keep)`，超了先压等待段和重复操作（如勾选 6 个目录用 4×）。

## 4 字幕

- 每条 ≤20 字；关键步用「第 N 步：动作 + 对象」，其余讲价值（权限、可溯源、产出回流）。
- **不解读 sample 数据**：产物出来后直接进展示/上传，不写「达成 xx%、根因 xx」之类的数据解读（用户明确要求）。
- 流式输出的文字（Agent 回答、看板摘要）要按**文字实际出现的时刻**对位，逐 0.5s 抽帧确认（实测字幕早了 3s）。
- 字体微软雅黑：Office 自带 `/Applications/Microsoft Word.app/Contents/Resources/DFonts/msyh.ttc` + `msyhbd.ttc`；`msyh.ttc` 的 index 1 是 YaHei UI，不是粗体，必须用 `font_bold` 指定 msyhbd。
- 字幕已按「国」字墨迹垂直居中（render.py make_caption），不要再加固定偏移。

## 5 推近 focus（用户录屏没有点击数据，必须手工补）

1. 由 analysis.json 的活动段起点估点击时刻，抽「点击前 0.1s」的帧（光标还停在目标上）。
2. 帧缩到点宽（如 1512）后叠 100px 网格 + 坐标标签，再 Read；**读网格标签，不要读 Read 显示的像素**（Read 会缩放图片）。
3. 源像素 = 网格读数 × (源宽 / 网格宽)，Retina 常为 ×2。
4. 点击推近：`{"t":活动段起点-0.05,"x","y","zoom":1.8,"hold":1.0~1.2}`；需要展示一整块面板（如权限面板）用 box 区域推近 `zoom 1.5~1.6`。
5. 渲染后在每个 focus 时刻 +0.15s 抽帧，确认推近对准、涟漪在目标上。

核心点击一般是：入口导航、关键菜单、「添加到任务」、发送、上传、选择目标、确定。

## 6 打码 blur（对外视频必做全片检查）

- blur 作用在推近之前的源帧上，镜头放大后马赛克依然覆盖。
- **用户只框了一处时也要全片扫**：同类信息常在别处再出现（实测：用户框了侧栏团队列表，上传弹窗里还有一份团队列表）。
- 必查清单：知识库/团队列表（只保留演示用的团队）、上传/选择目标弹窗、快速访问/最近访问、人名、全屏水印、未发布的模型名、通知弹窗。拿不准的列出来问用户，不擅自处理。
- 可见区间：选一行稳定内容（如演示团队名那一行）做模板，逐帧比对灰度差，得到出现/消失时刻；按区间分段写 blur。
- 列表排序会变（补录与主录进入顺序不同 → 团队顺序不同），每段单独定 box，保留项可能夹在中间，要拆成上下两块。
- 渲染后**原分辨率裁剪放大**核对（缩略图看不出漏打/误挡）。

## 7 补录合并

用户补录一段插到主录屏某处时，先拼成一条素材再统一剪：

```bash
ffmpeg -i main.mov -i supp.mov -filter_complex \
 "[0:v]trim=0:6,setpts=PTS-STARTPTS,fps=30[a];[1:v]setpts=PTS-STARTPTS,fps=30[b];\
  [0:v]trim=start=6,setpts=PTS-STARTPTS,fps=30[c];[a][b][c]concat=n=3:v=1[v]" \
 -map "[v]" -c:v libx264 -preset ultrafast -crf 14 -pix_fmt yuv420p combined.mp4
```

- 规格须一致（ffprobe 看宽高、帧率）；`fps=30` 统一帧率，避免 60fps 录屏 concat 后时间漂移。
- 写时间用映射函数：主录 `m(t)= t if t<插入点 else t+补录时长`，补录 `s(t)= t+插入点`。focus/blur/字幕/keep 都经映射。
- 插入点选在画面状态与补录开头一致的位置（如「进入知识库列表后」）；替换掉主录里讲同一件事的段，避免重复。

## 8 验收

- 每条字幕中点抽帧：字幕与画面动作对应、无错别字。
- 每个 focus 时刻抽帧：推近到位。
- 打码段原尺寸裁剪核对。
- 成片时长达标。

## 9 交付与迭代

- 每次给三件：`_vN.mp4`（字幕烧入）、`_vN.srt`（可改）、`_vN_无字幕.mp4`（剪映用：导入字幕 → 选一条设样式 → 应用到全部）。
- 版本号只增不覆盖（本机禁删/改名）。
- 用户改 srt（文字或时间）→ `reburn`，样式不变；改剪辑/推近/打码 → 改 edit.json 后 `build`。
