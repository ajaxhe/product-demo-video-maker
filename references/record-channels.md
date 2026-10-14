# 其他素材来源：Web 录制 / 截图 / Mac 代录

目录：A Web 产品 · B 截图 · C Mac 手动/代理 · C+ Mac 后台长录 + 分批确定性驱动 · 分镜规范

仅在用户没有自录屏、而是给 URL / 截图 / 要求 AI 代为操作时使用。默认流程见 edit-recording.md。

## A Web 产品（无头，不占屏幕）

```bash
$PY $SK/rec_web.py explore "<url>" --work <work>                 # 可交互元素 + explore.png
$PY $SK/rec_web.py login "<url>" --state <work>/state.json       # 需要登录时
$PY $SK/rec_web.py dry <work>/storyboard.json                     # 逐步校验选择器，全部 [ok] 再录
$PY $SK/rec_web.py record <work>/storyboard.json                  # raw.mp4 + events.json
$PY $SK/render.py <work>/events.json
```

动作：`click`/`dblclick`（sel）、`type`（sel,text,delay）、`hover`、`highlight`（只聚光+推近）、`scroll`（dy）、`press`（key）、`wait`（ms）、`goto`/`js`。
每步可选：`caption`、`concept`、`zoom`、`pre`、`post`、`hold`、`hold_zoom`、`narration`。顶层：`viewport`（[1440,900]）、`dpr`（2）、`state`、`init_js`、`voice`、`style`。
范例：`assets/web_storyboard.json`（url 换成 `file://<assets>/demo_site.html` 可自测）。

## B 截图

```bash
$PY $SK/from_shots.py init <截图目录> --work <work>     # 分镜骨架 + grid/*.grid.png 坐标网格
# Read grid 图读坐标，填 storyboard.json（范例 assets/shots_storyboard.json）
$PY $SK/from_shots.py build <work>/storyboard.json && $PY $SK/render.py <work>/events.json
```

每镜：`image`、`dur`、`caption`、`click:[x,y]`、`box:[x,y,w,h]`、`kind`（click/hover/none）、`concept`、`zoom`。同一张图可连用两镜（先高亮再点击）。

## C Mac 手动 / 代理

开录前告知：录主显示器全屏、期间别动鼠标键盘、断开副屏、开勿扰。

```bash
$PY $SK/rec_mac.py --work <work> --duration 60 --app "备忘录"                        # C1 手动，touch STOP 提前停
$PY $SK/rec_mac.py --work <work> --agent "任务描述" --max-steps 30 --app "备忘录"     # C2 youtu-gui-agent 代操作
$PY $SK/inspect_tool.py clicks <work>/events.json      # 点击后局部截图联系表，看图补 caption
```

youtu-gui-agent 鉴权（静默，不展示 token）：`<youtu> auth --status`；失效则 `connect_cloud_service` 取 `tempToken` → `<youtu> auth --save-token`。
youtu 路径：`~/.workbuddy/skills/lucwei-1782700541103768418-dd050fc3__knot/youtu-gui-agent`；`run` 只有 `-v`/`--max-steps` 参数。

## C+ Mac 后台长录 + 分批确定性驱动（AI 代录正式镜头用）

实测 youtu-gui-agent 约 5s/步、会误点、会误判完成，只适合探路。正式镜头用 `ui.py`（Quartz CGEvent）：

```bash
$PY $SK/ui.py shot <work>/u1.jpg ; $PY $SK/ui.py click X Y        # 探路：截图读坐标（pt）
$PY $SK/rec_mac.py --work <work> --duration 2400 --countdown 0 --app WorkBuddy   # run_in_background 长录
$PY $SK/ui.py run <work>/b1.json --log <work>/b1.log.json          # 分批执行，每批后 shot 检查
#   等待段 {"do":"wait","s":90,"speed":12,"caption":"..."}
# Write 工具写 <work>/STOP 停录
$PY $SK/bursts_to_events.py <work> <work>/b1.log.json ... && $PY $SK/render.py <work>/events.json --style assets/style_plain_ui.json
```

带 `caption` 的动作开启一条字幕直到下一条带 caption 的动作；批与批之间的空档自动剪掉。

## 分镜规范（A/B/C 写 storyboard 时）

1. 开场 `wait` 2 秒交代全貌。
2. 一步一个动作一句字幕，≤20 字。
3. 概念卡（用户要求时才加）放在界面稳定的步骤，不放在会弹窗/跳转的 click 步。
4. 强调结果时，点击后加 `highlight` 对准结果。
5. 会落库的操作先确认不会污染真实数据。
