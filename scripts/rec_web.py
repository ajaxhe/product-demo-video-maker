#!/usr/bin/env python3
"""rec_web.py — Web 产品录制（默认无头：不抢屏幕、不动真实鼠标，可后台跑）

原理：Playwright 执行分镜动作；CDP Page.startScreencast 抓高清帧（dpr=2，带时间戳）；
每个动作记录「时间 + 元素框 + 点击坐标」→ events.json；光标/涟漪/推近/字幕全部交给 render.py 后期合成。
（避开了「CDP 合成事件不动真实光标」那个坑——我们根本不需要真实光标。）

子命令:
  explore <url> [--work DIR] [--state S]   列出可交互元素(文本/建议选择器/坐标) + 截图 explore.png
  login   <url> --state state.json         弹出有头浏览器，手动登录后**关闭窗口**即保存登录态
  dry     <storyboard.json>                快速预演（不录），逐步校验选择器
  record  <storyboard.json> [--headed]     正式录制 → <work>/raw.mp4 + events.json
"""
import argparse
import base64
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import FFMPEG, ensure_dir, load_json, save_json  # noqa: E402

from playwright.sync_api import sync_playwright  # noqa: E402

DEF = {"pre": 0.8, "post": 1.6, "type_delay": 70}
INIT_JS = """
window.alert = (m)=>console.log('[alert]', m);
window.confirm = ()=>true;
window.prompt = (m, d)=> d || '';
"""


def launch(p, sb, headed=False):
    # 实测坑：无头模式下 screencast 无视 context 的 device_scale_factor，只输出 1x 帧；
    # 必须用启动参数 --force-device-scale-factor 才能拿到 2x 高清帧（推近时不糊）
    dpr_flag = f"--force-device-scale-factor={sb.get('dpr', 2)}"
    br = p.chromium.launch(headless=not headed, args=["--hide-scrollbars", "--no-sandbox", dpr_flag])
    vw, vh = sb.get("viewport", [1440, 900])
    kw = dict(viewport={"width": vw, "height": vh}, device_scale_factor=sb.get("dpr", 2), locale="zh-CN")
    st = sb.get("state")
    if st and Path(st).exists():
        kw["storage_state"] = st
    ctx = br.new_context(**kw)
    ctx.add_init_script(INIT_JS + (sb.get("init_js") or ""))
    return br, ctx


def locate(page, sel, timeout=8000):
    loc = page.locator(sel).first
    loc.wait_for(state="visible", timeout=timeout)
    loc.scroll_into_view_if_needed(timeout=timeout)
    return loc


def run_steps(page, sb, rec=None, voice=None):
    """rec=None 时为预演；否则返回带时间信息的 steps"""
    out = []
    d = dict(DEF)
    d.update(sb.get("defaults") or {})
    for i, s in enumerate(sb["steps"]):
        sid = s.get("id") or f"s{i + 1}"
        act = s.get("action", "wait")
        t0 = time.time()
        box = point = None
        try:
            if s.get("sel"):
                loc = locate(page, s["sel"])
                page.wait_for_timeout(120)
                b = loc.bounding_box()
                box = [b["x"], b["y"], b["width"], b["height"]]
            pre = s.get("pre", d["pre"]) if rec else 0.05
            post = s.get("post", d["post"]) if rec else 0.2
            if act in ("click", "type", "hover", "dblclick"):
                cx, cy = box[0] + box[2] / 2, box[1] + box[3] / 2
                if s.get("offset"):
                    cx, cy = cx + s["offset"][0], cy + s["offset"][1]
                page.wait_for_timeout(int(pre * 1000))
                tp = time.time()
                if act == "click":
                    page.mouse.click(cx, cy)
                elif act == "dblclick":
                    page.mouse.dblclick(cx, cy)
                elif act == "hover":
                    page.mouse.move(cx, cy, steps=6)
                else:
                    page.mouse.click(cx, cy)
                    if s.get("clear", True):
                        page.keyboard.press("Meta+A")
                        page.keyboard.press("Backspace")
                    page.wait_for_timeout(150)
                    page.keyboard.type(s["text"], delay=s.get("delay", d["type_delay"]) if rec else 5)
                point = {"t": tp, "x": cx, "y": cy, "kind": "click" if act == "dblclick" else act}
                if act in ("click", "dblclick"):
                    # 点击后元素被遮罩盖住/消失（弹窗打开、弹窗关闭、跳转）→ 标记 changed，
                    # render 会在点击瞬间就收掉聚光灯并拉远，避免对着空白推近（v3 实测翻车）
                    page.wait_for_timeout(180)
                    try:
                        point["changed"] = loc.evaluate(
                            "(el,[x,y])=>{const t=document.elementFromPoint(x,y);return !(t&&(t===el||el.contains(t)))}",
                            [cx, cy], timeout=800)
                    except Exception:
                        point["changed"] = True
                    post = max(0.2, post - 0.18)
            elif act == "press":
                page.wait_for_timeout(int(pre * 500))
                page.keyboard.press(s["key"])
            elif act == "scroll":
                dy = s.get("dy", 500)
                n = max(1, abs(dy) // 40)
                for _ in range(int(n)):
                    page.mouse.wheel(0, 40 if dy > 0 else -40)
                    page.wait_for_timeout(16 if rec else 1)
            elif act == "goto":
                page.goto(s["url"], wait_until="networkidle")
            elif act == "js":
                page.evaluate(s["code"])
            elif act == "highlight":
                page.wait_for_timeout(int(pre * 1000))
                point = None
            if act == "wait":
                page.wait_for_timeout(int(s.get("ms", 1000) if rec else 50))
            else:
                page.wait_for_timeout(int(post * 1000))
            if rec:
                need = s.get("hold", 0)
                if voice and sid in voice:
                    need = max(need, voice[sid][1] + 0.45)
                left = need - (time.time() - t0)
                if left > 0:
                    page.wait_for_timeout(int(left * 1000))
            print(f"  [ok] {sid} {act} {s.get('sel', '')}")
        except Exception as e:
            print(f"  [x] {sid} {act} {s.get('sel', '')} 失败: {str(e).splitlines()[0]}")
            if rec:
                raise
        out.append({"id": sid, "t0": t0, "t1": time.time(), "box": box, "point": point, "src": s})
    return out


def cmd_explore(a):
    work = ensure_dir(a.work or ".")
    with sync_playwright() as p:
        br, ctx = launch(p, {"state": a.state, "viewport": [1440, 900], "dpr": 1})
        page = ctx.new_page()
        page.goto(a.url, wait_until="networkidle")
        page.wait_for_timeout(1500)
        page.screenshot(path=str(work / "explore.png"))
        items = page.evaluate("""() => {
          const q = 'a,button,input,textarea,select,[role=button],[role=tab],[role=menuitem],[onclick],[data-testid],li[class*=nav],div[class*=nav] > *';
          const seen = new Set(), res = [];
          for (const el of document.querySelectorAll(q)) {
            const r = el.getBoundingClientRect();
            if (r.width < 4 || r.height < 4 || r.bottom < 0 || r.top > innerHeight) continue;
            const txt = (el.innerText || el.value || el.placeholder || el.getAttribute('aria-label') || '').trim().replace(/\\s+/g,' ').slice(0,30);
            const id = el.id ? '#' + el.id : '';
            const tid = el.dataset && el.dataset.testid ? `[data-testid="${el.dataset.testid}"]` : '';
            const sel = tid || id || (txt ? `${el.tagName.toLowerCase()}:has-text("${txt.slice(0,16)}")` : el.tagName.toLowerCase());
            const key = sel + Math.round(r.x) + Math.round(r.y);
            if (seen.has(key)) continue; seen.add(key);
            res.push({tag: el.tagName.toLowerCase(), text: txt, sel, x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)});
          }
          return res;
        }""")
        save_json(work / "explore.json", items)
        for it in items[:120]:
            print(f"{it['tag']:8} {it['x']:5},{it['y']:<5} {it['w']:4}x{it['h']:<4} {it['sel']:40} | {it['text']}")
        print(f"\n[ok] {len(items)} 个元素 → {work / 'explore.json'}，截图 {work / 'explore.png'}")
        br.close()


def cmd_login(a):
    with sync_playwright() as p:
        br, ctx = launch(p, {"viewport": [1440, 900], "dpr": 1}, headed=True)
        page = ctx.new_page()
        page.goto(a.url)
        print("请在弹出的浏览器里完成登录，然后关闭该标签页/窗口……")
        page.wait_for_event("close", timeout=0)
        ctx.storage_state(path=a.state)
        print(f"[ok] 登录态已保存 {a.state}")
        br.close()


def cmd_dry(a):
    sb = load_json(a.storyboard)
    with sync_playwright() as p:
        br, ctx = launch(p, sb)
        page = ctx.new_page()
        page.goto(sb["url"], wait_until="networkidle")
        page.wait_for_timeout(800)
        run_steps(page, sb, rec=None)
        br.close()


def cmd_record(a):
    sb_path = Path(a.storyboard).resolve()
    sb = load_json(sb_path)
    work = ensure_dir(Path(sb.get("work") or sb_path.parent))
    # 每次录制用新目录：本机安全中心会拦截删除，复用旧目录会混入残帧
    frames_dir = ensure_dir(work / f"frames_{time.strftime('%H%M%S')}")

    voice = None
    if (sb.get("voice") or {}).get("enabled"):
        from tts import voice_steps
        voice = voice_steps(sb["steps"], work, sb["voice"])

    frames = []
    with sync_playwright() as p:
        br, ctx = launch(p, sb, headed=a.headed)
        page = ctx.new_page()
        page.goto(sb["url"], wait_until="networkidle")
        page.wait_for_timeout(int(sb.get("warmup", 1.5) * 1000))  # 预热，避免白屏进素材
        cdp = ctx.new_cdp_session(page)
        vw, vh = sb.get("viewport", [1440, 900])
        dpr = sb.get("dpr", 2)

        stopping = {"v": False}

        def on_frame(params):
            if stopping["v"]:
                return  # 收尾阶段不再 ack：stopScreencast 与回调内 send 并发会死锁（实测坑）
            ts = params.get("metadata", {}).get("timestamp") or time.time()
            fn = frames_dir / f"f{len(frames):06d}.jpg"
            fn.write_bytes(base64.b64decode(params["data"]))
            frames.append((ts, fn.name))
            try:
                cdp.send("Page.screencastFrameAck", {"sessionId": params["sessionId"]})
            except Exception:
                pass

        cdp.on("Page.screencastFrame", on_frame)
        cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 92, "maxWidth": int(vw * dpr),
                                          "maxHeight": int(vh * dpr), "everyNthFrame": 1})
        page.wait_for_timeout(600)
        # 静态页可能只来 1 帧：轻推一下触发合成
        page.evaluate("document.body.style.outline='0px solid transparent'")
        page.wait_for_timeout(int(sb.get("lead", 0.6) * 1000))
        steps = run_steps(page, sb, rec=True, voice=voice)
        page.wait_for_timeout(int(sb.get("tail", 1.0) * 1000))
        t_end = time.time()
        stopping["v"] = True
        # 不调 stopScreencast / br.close()：两者在本机都可能卡死，直接退出 with 块由驱动杀进程
    print("  [ok] 录制结束，合成 raw.mp4 …", flush=True)

    if not frames:
        sys.exit("[x] 没抓到任何帧")
    frames.sort()
    t_base = frames[0][0]
    from PIL import Image
    fw, fh = Image.open(frames_dir / frames[0][1]).size
    k = fw / vw
    lines = []
    for (ts, fn), nxt in zip(frames, frames[1:] + [(t_end, None)]):
        lines.append(f"file '{fn}'\nduration {max(0.001, nxt[0] - ts):.4f}")
    lines.append(f"file '{frames[-1][1]}'")
    (frames_dir / "list.txt").write_text("\n".join(lines) + "\n")
    subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(frames_dir / "list.txt"),
                    "-vf", f"scale={fw // 2 * 2}:{fh // 2 * 2}", "-fps_mode", "cfr", "-r", "30",
                    "-c:v", "libx264", "-preset", "fast", "-crf", "14", "-pix_fmt", "yuv420p",
                    str(work / "raw.mp4")], check=True)

    ev_steps = []
    for s in steps:
        src = s["src"]
        e = {"id": s["id"], "t0": round(s["t0"] - t_base, 3), "t1": round(s["t1"] - t_base, 3),
             "caption": src.get("caption", ""), "action": src.get("action")}
        if s["box"]:
            e["box"] = [round(v * k, 1) for v in s["box"]]
        if s["point"]:
            e["point"] = {"t": round(s["point"]["t"] - t_base, 3), "x": round(s["point"]["x"] * k, 1),
                          "y": round(s["point"]["y"] * k, 1), "kind": s["point"]["kind"]}
            if s["point"].get("changed"):
                e["point"]["changed"] = True
        for f in ("concept", "zoom", "highlight"):
            if f in src:
                e[f] = src[f]
        if src.get("action") in ("wait", "scroll", "press", "goto", "js") and "zoom" not in src:
            e["zoom"] = 0
        ev_steps.append(e)
    ev = {"kind": "web", "video": "raw.mp4", "src_w": fw, "src_h": fh, "duration": round(t_end - t_base, 3),
          "cursor_mode": "synthetic", "steps": ev_steps, "style": sb.get("style", {})}
    if voice:
        ev["audio"] = [{"t": e["t0"] + 0.15, "file": voice[e["id"]][0]} for e in ev_steps if e["id"] in voice]
    save_json(work / "events.json", ev)
    print(f"[ok] {len(frames)} 帧 {fw}x{fh} → {work / 'raw.mp4'}\n[ok] 时间线 → {work / 'events.json'}")
    print(f"下一步: python render.py {work / 'events.json'}")


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    e = sp.add_parser("explore"); e.add_argument("url"); e.add_argument("--work"); e.add_argument("--state")
    lg = sp.add_parser("login"); lg.add_argument("url"); lg.add_argument("--state", required=True)
    d = sp.add_parser("dry"); d.add_argument("storyboard")
    r = sp.add_parser("record"); r.add_argument("storyboard"); r.add_argument("--headed", action="store_true")
    a = ap.parse_args()
    {"explore": cmd_explore, "login": cmd_login, "dry": cmd_dry, "record": cmd_record}[a.cmd](a)


if __name__ == "__main__":
    main()
