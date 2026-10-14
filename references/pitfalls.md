# 实测踩坑记录（product-demo-video-editor）

> 格式：症状 → 根因 → 解法。新坑追加到末尾并标日期。
> 目录：本机环境 1-5b · Web 录制 6-10 · Mac 录制 11-15 · 渲染 16-18 · Mac 代录 C+ 19-25 · 剪辑用户录屏 26-36

## 本机环境（2026-10-13）

1. **命令输出里一堆 `Blocked by Security Center rules: delete ...`**
   根因：本机安全中心拦截所有删除/重命名（含 Chromium 临时 profile、Python tempfile 探测文件）。
   解法：忽略这些提示；脚本内不做 delete/rename；每次录制写新目录 `frames_HHMMSS/`；无音频时直接编码到成片路径。
   残留的随机名小文件（如 `x9jfray3`）是 Python tempfile 探测留下的，可手动拖进废纸篓。

2. **`Error in sitecustomize ... No usable temporary directory`**
   根因：tempfile 探测要求「建文件+删文件」都成功，删被拦。
   解法：噪音，忽略；但必须 `cd <work>/_tmp && TMPDIR=$PWD` 运行，否则随机文件会丢进 skill 目录或工作区根目录。

3. **前台 `命令 | tail` 跑录制，直接 SIGTERM 137**
   解法：用 `run_in_background: true`，输出重定向到 log，完成后读 log。

4. **非沙箱命令里 `mkdir -p` 不生效，后续 `> file` 报 no such file**
   解法：先用 Write 工具在目标目录写一个占位文件来建目录。

5. **点 dot 目录（`.tmp`）建不出来** → 用 `_tmp`。

5b. **非沙箱命令里 `cp` 静默失败（目标文件不存在、无报错）** → 用 `cat src > dst` 复制。

## Web 录制（rec_web.py）

6. **抓到的帧只有 1440×900，推近发糊**
   根因：无头 Chromium 的 `Page.startScreencast` 无视 context 的 `device_scale_factor`（Emulation 覆写也无效）。
   解法：启动参数 `--force-device-scale-factor=2`（已内置），帧变 2880×1800。

7. **步骤全部 [ok] 之后进程卡死不退**
   根因：screencast 回调内 `cdp.send(ack)` 与 `stopScreencast` 并发死锁；`browser.close()` 在本机也会卡（删 profile 被拦）。
   解法：收尾置 `stopping` 标志停止 ack，不调 stop/close，直接退出 `with sync_playwright()`。

8. **点击「新建」后，镜头还对着按钮推近，弹窗在画面外/被聚光灯压暗**（v2）
   解法：①聚光灯：普通点击只亮到点击后 0.35s；②镜头：普通点击后 0.6s 拉远（`click_hold`）；
   ③录制时用 `elementFromPoint` 检测点击后元素是否被遮罩盖住/消失 → `point.changed=true`，render 点击瞬间就收聚光灯并拉远，且不与下一镜合并平移。
   分镜层面：概念卡不要放在会弹窗的 click 步，放到弹窗里的 type/highlight 步。

9. **点击「创建」后弹窗瞬间关闭，镜头对着空白区域推近 + 一个空高亮框**（v3）→ 同第 8 条的 `changed` 机制。

10. **静态页面 screencast 只来 1 帧**：正常。帧按「内容变化」推送，脚本用每帧时间戳 + concat duration 还原成 30fps CFR。

## Mac 录制（rec_mac.py）

11. **ffmpeg 录出 582 字节、0 帧的文件**
    根因 A：`-copyts` 与 `-t 3` 同时用，`-t` 按 epoch 时间戳计算 → 立即结束。解法：不用 `-t`，向 stdin 发 `q` 停止。
    根因 B：avfoundation 不支持 yuv420p 输入。解法：`-pixel_format nv12`。

12. **`h264_videotoolbox` 编码时报安全中心拦截 Metal 缓存删除** → 改用 libx264 ultrafast。

13. **鼠标点击与视频帧对齐**：`-use_wallclock_as_timestamps 1 -copyts` 输出 mkv，ffprobe 的 `start_time` 即首帧 epoch 时间；
    点击用 `time.time()` 记录，相减即视频内时间。实测首帧比 Popen 晚 ~0.44s。转 raw.mp4 时 `setpts=PTS-STARTPTS -fps_mode cfr -r 30`。

14. **点击追踪需要辅助功能权限吗？** 不需要。`NSEvent.mouseLocation()` + `pressedMouseButtons()` 120Hz 轮询即可；只有 CGEventTap 才要权限。
    坐标：点、左下原点 → 像素 `(x*s, (屏高-y)*s)`，`s = 视频宽/主屏点宽`（Retina=2）。副屏上的点击坐标越界，已丢弃。

15. **双显示器**：`Capture screen 0` = 主显示器；youtu-gui-agent 只操作主屏。录前让用户把目标应用放主屏或断开副屏。

## 渲染（render.py）

16. **概念卡的「核心概念」标签是个灰色空洞**
    根因：PIL `ImageDraw` 在 RGBA 图上画半透明色是替换像素不是混合，挖出透明洞。
    解法：预先算好不透明浅色（accent×0.14 + 白×0.86）。

17. **推近时画面抖动**：裁剪坐标取整导致。解法：用 `cv2.warpAffine` 亚像素仿射一次完成缩放+平移；缩小倍率 <0.92 时先 INTER_AREA 预缩防锯齿。

18. **镜头运动**：临界阻尼弹簧（`spring`=7）+ 对数缩放插值，无过冲；相邻推近间隔 <1.2s 时保持推近直接平移，避免「拉远-推近」来回晃。

## Mac 代录 C+（2026-10-13）

19. **youtu-gui-agent 正式镜头不可用**：约 5s/步，会误点、会把未完成判成完成（状态 FAIL 但实际做了一半）。→ 只用来探路，正式镜头用 ui.py 确定性脚本。
20. **zsh 不做变量分词**：`for c in "click 1 2"; do $U $c; done`、`T="1 2 3"; cmd $T` 都会把整串当一个参数 → 静默无效 / 抽帧 0 张。→ 参数逐个写死，或用 `${=T}`。
21. **osascript / System Events 报 -10004 权限违例** → 激活应用用 `open -a <App>`。
22. **中文输入**：CGEvent unicode 在部分输入框无效 → `paste`（pbcopy + ⌘V）。部分应用输入框回车是换行，要点发送按钮。
23. **企业连接器的下拉列表顺序会变** → 每次先截图再点。企业版连接器可能不能手动解绑（管理员统一开通），演示「安装连接器」时可改为对话内「开关启用」。
24. **知识库文档有效期只能选未来日期**，无法把旧版文档设为「已过期」；需要「已过期」画面得提前准备。
25. **长录素材（~30 分钟 3K）渲染慢** → VideoSource 遇到大段剪切直接 seek，不逐帧解码。

## 剪辑用户录屏（2026-10-14）

26. **v1/v2 没有推近**：用户自录屏没有点击数据，from_video 默认 zoom=0。→ 看帧读坐标写 `focus`（edit-recording.md §5）。用户把推近当默认期望。
27. **读坐标读错**：Read 显示的图片会被缩放，直接按显示像素估坐标会偏。→ 帧上叠带数字标签的网格再读标签；源像素 = 读数 × 源宽/网格宽。
28. **字幕早于画面 3s**：Agent 回答 / 看板摘要是流式输出，按「开始滚动」时刻配字幕会抢跑。→ 逐 0.5s 抽帧找文字出现时刻。
29. **字幕解读 sample 数据被要求删掉**：用户要的是产品能力演示，不是数据结论。→ 产物出来直接进展示/上传环节。
30. **用户中途改了源文件名** → ffprobe 报 No such file；`ls` 目录按大小/修改时间找新名字。ffprobe 一次只能传一个输入，多个文件要循环。
31. **补录 concat 后时间漂移** → concat 滤镜里每段 `setpts=PTS-STARTPTS,fps=30`；时间统一用映射函数换算（edit-recording.md §7）。
32. **打码漏了一处**：用户只框了侧栏团队列表，上传弹窗里也有团队列表。→ 对外视频全片扫敏感信息，拿不准的列给用户确认。
33. **打码框错位**：补录与主录的团队列表排序不同；保留项夹在中间。→ 按可见区间分段写 box，保留项上下拆成两块；渲染后原尺寸裁剪核对。
34. **PIL 加载 PingFang.ttc 报 `cannot open resource`**（画网格标签时）→ 用 `ImageFont.load_default(size)`；字幕字体用 Office 自带微软雅黑。
35. **msyh.ttc 的 index 1 当粗体是错的**（那是 YaHei UI）→ `font_bold` 指定 msyhbd.ttc。字幕原来固定 -4px 偏上 → 改按「国」字墨迹 bbox 居中。
36. **`find /` 全盘找字体 2 分钟超时 SIGTERM** → 直接查 `/Applications/Microsoft *.app/Contents/Resources/DFonts`。
