# product-demo-video-maker

English | [中文](README.zh.md)

An [Agent Skill](https://agentskills.io) / [Claude Skill](https://www.anthropic.com/news/skills) for **product demo video editing**: hand over a screen recording of your product UI, let the AI read keyframes to understand what is happening, and get back a finished demo with burned-in captions and click-zoom emphasis.

```
screen recording ──from_video.py──┐
web URL          ──rec_web.py────┤
screenshots      ──from_shots.py─┼──> events.json ──render.py──> demo.mp4 + .srt
Mac app (AI-driven) rec_mac.py/ui.py┘
```

## What it does

- **Editing**: detects active segments automatically; stitches multiple recordings into one timeline in story order; cuts dead air, loading screens and mis-clicks; compresses waiting stretches with high speed-up (measured: 7 minutes down to 3 seconds); estimates and allocates pacing against a target duration (e.g. 30s).
- **Captions**: reads keyframes to understand the interaction, then writes captions line by line — Microsoft YaHei bold, white on black, vertically centered; ships a burned-in version, an editable `.srt`, and a clean no-caption version.
- **Zoom**: when the recording carries no click data, the AI reads coordinates off frames rendered with a source-pixel grid and adds zoom + ripple to the key clicks, panning smoothly between consecutive points of interest on the same screen.
- **Redaction**: mosaics sensitive information such as non-demo team names or personal names — and keeps covering it after the camera zooms in.
- **Re-burn**: once you edit the `.srt` (text or timing), a single command recomposites with identical styling; pickup footage can be merged the same way.
- **Storyline compilations**: chains several finalized feature demos into one narrative video (e.g. a 2-minute loop for a booth). Chapter transition cards inherit the backdrop, logo and typeface from the reference slide deck, so the video matches the talk (`scripts/story.py`).

## Quick start

```bash
# 0 Create the project: ~/Movies/demo-videos/<project>/{source,work,output,scripts} + README + CHANGELOG
#   (PDVM_ROOT overrides the root). Recordings passed via --src are archived into source/ (--link symlinks instead).
python3 scripts/from_video.py init my-demo --src rec1.mov rec2.mov
cd ~/Movies/demo-videos/my-demo   # ./work below refers to this project's work/

# 1 Analyze a recording: emits a keyframe contact sheet + active segments
python3 scripts/from_video.py analyze rec1.mov --work ./work/analyze/rec1

# 2 Merge multiple recordings first (skip when there is only one)
python3 scripts/from_video.py concat ./work/combined.mp4 rec1.mov rec2.mov rec3.mov

# 3 The AI reads keyframes_*.jpg and writes work/plan.json
#   (kept segments + speed + captions + zoom + redaction), then generates edit parameters
python3 scripts/from_video.py plan ./work --target 30

# 4 Render into output/: demo_v1.mp4 + editable demo_v1.srt + demo_v1_无字幕.mp4 (no-caption)
python3 scripts/from_video.py build ./work --nosub

# 5 Re-burn the next version after editing the .srt
python3 scripts/from_video.py reburn ./work --nosub
```

Requirements: Python 3.9+, `pip install opencv-python pillow`. `rec_web.py` additionally needs `playwright`; `rec_mac.py` needs macOS + ffmpeg.

## Layout

```
SKILL.md                     # Skill entry point (triggers, constraints, editing principles, flow at a glance)
references/
  edit-recording.md          # Default flow: story beats, plan.json, pacing and duration, captions, zoom, redaction, merging, review
  record-channels.md         # Other material sources: headless web capture / screenshots / AI-driven Mac recording
  story-cards.md             # Storyline compilations: when to add transition cards, borrowing visuals from a reference deck, card layout, transitions and caption rules
  style.md                   # render.py style parameter reference
  pitfalls.md                # Field-tested tool/environment/render pitfalls (symptom -> root cause -> fix)
scripts/                     # from_video / render / story / rec_web / rec_mac / from_shots / ui / inspect_tool ...
assets/                      # Storyboard and style samples, mock pages used for self-testing
```

## Notes

- This skill is tuned for one person's workflow: the default preferences (UI only + captions, native resolution output, ≤60s) are baked in and can be adjusted as needed.
- See `SKILL.md` and `references/` for details.

## License

[MIT](LICENSE)
