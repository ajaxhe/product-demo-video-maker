# 实测踩坑记录（product-demo-video-maker）

> 只记「工具 / 环境 / 渲染」层面的坑，格式：症状 → 根因 → 解法。
> 剪辑原则（节奏、字幕、推近取舍、过渡页处理）不写在这里，写进 `edit-recording.md` 对应章节。
> 新增前先查本文件与 edit-recording.md 有没有同类条目：有就改写合并，没有再加到对应分组，不要堆在末尾。
> 目录：A 本机环境 · B Web 录制 · C Mac 录制 · D 渲染 · E Mac 代录 · F 剪辑用户录屏

## A 本机环境

1. **输出里一堆 `Blocked by Security Center rules: delete ...`**
   根因：本机安全中心拦截所有删除/重命名（含 Chromium 临时 profile、Python tempfile 探测文件）。
   解法：忽略；脚本内不做 delete/rename；每次输出写新文件/新版本号。残留的随机名小文件是 tempfile 探测留下的，可手动拖进废纸篓。
2. **`Error in sitecustomize ... No usable temporary directory`**：tempfile 探测要求「建+删」都成功，删被拦。噪音，忽略；但必须 `cd <work>/_tmp && TMPDIR=$PWD` 运行，否则随机文件会丢进 skill 目录或工作区根目录。
3. **前台 `命令 | tail` 跑长任务，直接 SIGTERM 137** → `run_in_background: true`，输出重定向到 log。
4. **非沙箱命令里 `mkdir -p` 不生效**，后续 `> file` 报 no such file → 先用 Write 工具在目标目录写占位文件来建目录。点 dot 目录（`.tmp`）建不出来 → 用 `_tmp`。
5. **非沙箱命令里 `cp` 静默失败**（目标不存在、无报错）→ `cat src > dst`。
6. **zsh 不做变量分词**：`T="1 2 3"; cmd $T` 会把整串当一个参数 → 静默无效 / 抽帧 0 张。→ 参数逐个写死，或用 `${=T}`。
7. **缺 cv2 / pillow，analyze 报 ModuleNotFoundError** → `python3 -m pip install --user opencv-python pillow numpy`。
8. **`find /` 全盘找字体 2 分钟超时** → 直接查 `/Applications/Microsoft *.app/Contents/Resources/DFonts`。PIL 加载 PingFang.ttc 报 `cannot open resource` → 用 Hiragino / 微软雅黑。
8a. **PPT 的底图在页面 XML 里找不到**：大图在版式 / 母版里（`ppt/slideLayouts/media/`），不在 `ppt/slides/media/`；也没装 LibreOffice，没法整页渲染。→ `story.py ppt` 直接解 zip，导出版式图片并出联系表。PPT 里的腾讯体等字体可能已装在 `~/Library/Fonts/`（`fc-list | grep -i tencent`），其余方正等字体本机多半没有，用微软雅黑代替。
9. **Read 工具读 .jpg 只返回「请用 ViewImage」** → 看关键帧、抽帧图一律用 ViewImage，query 写清要读的内容（时间戳、文字、坐标）。

## B Web 录制（rec_web.py）

10. **抓到的帧只有 1440×900，推近发糊**：无头 Chromium 的 `Page.startScreencast` 无视 `device_scale_factor`。→ 启动参数 `--force-device-scale-factor=2`（已内置），帧变 2880×1800。
11. **步骤全部 [ok] 之后进程卡死不退**：screencast 回调内 ack 与 `stopScreencast` 并发死锁；`browser.close()` 删 profile 被拦也会卡。→ 收尾置 `stopping` 停止 ack，不调 stop/close，直接退出 `with sync_playwright()`。
12. **点击「新建/创建」后弹窗出现或关闭，镜头还对着按钮推近、聚光灯压暗弹窗**
    → ①聚光灯：普通点击只亮到点击后 0.35s；②镜头：点击后 0.6s 拉远（`click_hold`）；③录制时 `elementFromPoint` 检测点击后元素被遮罩盖住/消失 → `point.changed=true`，render 点击瞬间收聚光灯并拉远，不与下一镜合并平移。概念卡放在弹窗里的 type/highlight 步，不放在会弹窗的 click 步。
13. **静态页面 screencast 只来 1 帧**：正常，帧按内容变化推送；脚本用每帧时间戳 + concat duration 还原成 30fps CFR。

## C Mac 录制（rec_mac.py）

14. **ffmpeg 录出 582 字节、0 帧**：A `-copyts` 与 `-t` 同用，`-t` 按 epoch 计算 → 立即结束，改为向 stdin 发 `q` 停止；B avfoundation 不支持 yuv420p 输入 → `-pixel_format nv12`。
15. **`h264_videotoolbox` 编码时安全中心拦截 Metal 缓存删除** → libx264 ultrafast。
16. **点击与视频帧对齐**：`-use_wallclock_as_timestamps 1 -copyts` 输出 mkv，ffprobe 的 `start_time` 即首帧 epoch；点击用 `time.time()` 记录，相减即视频内时间（首帧比 Popen 晚 ~0.44s）。转 raw.mp4 时 `setpts=PTS-STARTPTS -fps_mode cfr -r 30`。
17. **点击追踪不需要辅助功能权限**：`NSEvent.mouseLocation()` + `pressedMouseButtons()` 120Hz 轮询即可。坐标：点、左下原点 → 像素 `(x*s, (屏高-y)*s)`，Retina s=2；副屏点击越界已丢弃。
18. **双显示器**：`Capture screen 0` = 主显示器；GUI agent 只操作主屏。录前让用户把目标应用放主屏或断开副屏。

## D 渲染（render.py）

19. **概念卡标签是个灰色空洞**：PIL 在 RGBA 图上画半透明色是替换像素不是混合。→ 预先算好不透明浅色（accent×0.14 + 白×0.86）。
20. **推近时画面抖动**：裁剪坐标取整导致。→ `cv2.warpAffine` 亚像素仿射一次完成缩放+平移；缩小倍率 <0.92 先 INTER_AREA 预缩防锯齿。
21. **镜头运动机制**：临界阻尼弹簧（`spring`=7）+ 对数缩放插值，无过冲；推近窗口间隔 <`zoom_hold_gap`(1.2s) 才保持推近直接平移，否则先拉远再推近 → 用户感知为"一会放大一会缩小"。取舍原则见 edit-recording §6。
22. **box 推近不生效 / 目标被裁**：box 推近倍数 = min(zoom, 0.8×屏宽/w, 0.8×屏高/h)，<1.15 直接不推；横跨整屏的元素因此推不动。目标被裁多半是坐标读成了显示像素（见 F29）。
23. **长录素材（~30 分钟 3K）渲染慢** → VideoSource 遇到大段剪切直接 seek，不逐帧解码。
23a. **对成片再裁（去浏览器顶栏 / 改 16:9），推近画面顶部被切掉**：成片里推近已经烘进画面，再裁切的是放大后的画面。→ 从子项目 `edit.json` 重渲并加 `style.crop`（`story.py chapters` 已这样做），render.py 会把推近坐标一起平移。顶栏下沿：取源帧顶部 320px，在地址栏空白列逐行看颜色突变（Chrome 3024 宽 Retina 约在 y=166–167）。
23b. **合集里字幕叠在深色故事卡上 / 淡化帧上**：字幕时间按片段算，交叉淡化时会伸进卡片里。→ 进入片段的字幕从淡化结束开始，离开片段的字幕在下一张卡开始淡入时结束（story.py `plan` 已处理）；截短后重新检查 ≥3s。
23c. **story.py / 自写合成脚本输出 `Broken pipe` / `Error muxing a packet`**：读片段的 ffmpeg 解码进程在读够帧数后被主动关掉，属于噪音。看最后的 `[ok]` 和时长即可。

## E Mac 代录（ui.py / GUI agent）

24. **GUI agent 正式镜头不可用**：约 5s/步，会误点，会把未完成判成完成 → 只用来探路，正式镜头用 ui.py 确定性脚本。
25. **osascript / System Events 报 -10004 权限违例** → 激活应用用 `open -a <App>`。
26. **中文输入**：CGEvent unicode 在部分输入框无效 → `paste`（pbcopy + ⌘V）；部分应用回车是换行，要点发送按钮。
27. **下拉列表 / 连接器列表顺序会变** → 每次先截图再点；企业版连接器可能不能手动解绑（管理员统一开通），可改为演示对话内「开关启用」。
28. **演示需要特殊数据状态**（如"已过期"文档，但有效期只能选未来日期）→ 提前准备好数据，不在录制中现造。

## F 剪辑用户录屏（from_video.py / inspect_tool.py）

29. **读坐标读错，推近/打码框偏移**：Read/ViewImage 显示的图会被缩放（如 3024 宽源 → 1512 宽网格图 → 显示 1080 宽），按显示像素估坐标会偏 2~3 倍。→ 用 `inspect_tool.py gridframes`，网格标签即源像素，只读标签。
30. **用户自录屏默认没有推近**：from_video 没有点击数据，zoom=0。用户默认期望核心点击有推近 → 读坐标手写 focus。
31. **源文件名中途被改 / 素材漏发** → ffprobe 报 No such file 时 `ls` 目录按修改时间找；ffprobe 一次只能传一个输入，多个文件要循环。
32. **多段 60fps 录屏拼接后时间漂移、offset 差半帧** → 用 `from_video.py concat`（各段先 fps=30 归一，`-count_frames` 数帧算 offset）；手写 filter 时每段 `setpts=PTS-STARTPTS,fps=30`。
33. **打码框错位**：补录与主录的列表排序不同，保留项夹在中间 → 按可见区间分段写 box，保留项上下拆两块；渲染后原尺寸裁剪核对。
34. **字幕偏上 / 粗体不对**：`msyh.ttc` 的 index 1 是 YaHei UI 不是粗体 → `font_bold` 指定 msyhbd.ttc；字幕按「国」字墨迹 bbox 居中，不加固定偏移。
35. **连续两次抽帧互相覆盖，读到旧图**：`frames` 已改为自动编号（`_frames_2.jpg`…），看 [ok] 打印的实际文件名。
36. **改完 srt 再跑 `plan`，用户的字幕改动丢了**：`plan` 会按 plan.json 重写 captions_src.srt → 用户只改字幕时用 `reburn`；还要改剪辑就先把字幕同步回 plan.json。
37. **字幕在过渡页淡入时被截短（<3s）**：`story.py` 规定字幕不压在卡上，后面接卡的字幕要提前 0.5s 收掉。→ `story.py plan` 看 `<3s` 告警，把该字幕所在保留段放慢或多留 0.5s（改 plan.json 再重渲章节）。
38. **`story.py chapters` 里 `"project": "."`**：相对路径按项目根目录解析，指向本项目自己；章节素材读的是 `work/edit.json`，改了 plan.json 要先跑 `from_video.py plan` 再 `chapters`，否则卡里是旧剪辑。
