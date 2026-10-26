# render.py 样式参数

写在 `edit.json.style`、`storyboard.style` 或 `events.json.style`；只改样式不用重录，直接重渲。调样式加 `--preview 12` 只渲前 12 秒。

| 参数 | 默认 | 说明 |
|---|---|---|
| `font` / `font_bold` | 苹方 | 常规/粗体字体文件。默认用微软雅黑 msyh.ttc / msyhbd.ttc |
| `caption_size` / `caption_bottom` / `caption_alpha` | 40 / 30 / — | 字幕字号 / 距底边 / 黑底透明度（按 1920 宽基准，`out_size:source` 时自动等比放大） |
| `caption_highlight` / `caption_highlight_color` | [] / `[255,214,102]` | 全片通用的突出词（一般不用）；单条字幕里用 `**词**` 标记更常用，见 edit-recording.md §5。`caption_highlight_map` 由 `plan` 自动生成，别手写 |
| `out_w`/`out_h`/`fps` | 1920/1080/30 | 输出规格 |
| `out_size` | null | `"source"`＝成片尺寸跟随（裁切后的）源画面 |
| `crop` | null | `[x,y,w,h]` 源像素，或 `"window"`（rec_mac `--app` 记录的窗口） |
| `fit` | contain | `cover`＝铺满输出画面，多余裁掉（`crop_anchor_y`） |
| `background` | 浅色渐变 | `none`＝无边框满屏（默认偏好） |
| `padding`/`radius`/`shadow` | 56/18/true | 有背景时的边距、圆角、阴影 |
| `zoom` | 1.8 | 默认推近倍数 |
| `zoom_lead` / `click_hold` / `zoom_hold_gap` | 0.7/0.6/1.2 | 提前推近秒数 / 点击后保持 / 两次推近间隔小于此值则平移 |
| `spotlight` | 0.45 | 框外压暗，0 关闭（默认偏好 0） |
| `ripple` | true | 点击涟漪 |
| `accent` | `#2F6BFF` | 高亮/涟漪主色 |
| `keep` | null | `[[t0,t1,倍速],...]` 只保留这些区间（from_video 自动生成） |
| `cut` | null | `[[t0,t1],...]` 剪掉的源时间段 |
| `speedup` | null | `{"factor":4,"idle_min":1.5}` 空闲段自动加速（C2） |
| `blur` | null | `[{"t0","t1","box","cell"}]` 马赛克打码，推近前作用于源帧 |
| `intro` / `outro` | null | 片头片尾卡（用户要求时才加）：`{"kicker","title","subtitle","columns":[...],"dur"}`，可为列表 |
| `theme` | light | `dark` 深色主视觉（仅片头片尾/概念卡生效） |
| `bgm` | null | `{"file":"x.mp3","volume":0.12}` |

样式预设：`assets/style_plain_ui.json`（默认：纯界面 + 微软雅黑字幕）、`assets/style_summit_dark.json`（深色大会卡片，用户要求、且没有参考 PPT 时）。

多个功能片串成故事线、用参考 PPT 做章节过渡页时不走 intro/outro，用 `scripts/story.py`（配置 `work/story.json`，见 `references/story-cards.md`、`assets/story_example.json`）。
