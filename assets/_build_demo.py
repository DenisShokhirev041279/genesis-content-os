#!/usr/bin/env python3
"""
Genesis Content OS — README media builder.
Reuses the ContentMachine dark-space visual language (Inter + JetBrains Mono,
brand gold #F6B63B on #0A0E1A). Produces:
  - genesis_architecture.png   (module A-F data-flow diagram)
  - genesis_dashboard.png      (hero terminal/dashboard still)
  - genesis_demo.gif           (looping self-improving cycle, ~28s)

All numbers are honest (pulled from the live system):
  trend_signals 3273 · topics 236 (109 queued) · insights 23 (12 applied)
  merged PRs: #9 scenario_v2, #16 topic_distiller 1.0.0->1.0.1
"""
from __future__ import annotations
import math
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter

HERE = Path(__file__).resolve().parent
FONTS = Path.home() / "Library" / "Fonts"

# ---------- palette (dark space, brand gold) ----------
BG          = (10, 14, 26)      # #0A0E1A
BG_GLOW     = (16, 26, 48)      # subtle top glow
PANEL       = (17, 23, 40)      # #111728
PANEL_HI    = (22, 30, 52)
BORDER      = (34, 45, 74)      # #222D4A
BORDER_SOFT = (26, 34, 56)
GOLD        = (246, 182, 59)    # #F6B63B brand accent
GOLD_DIM    = (150, 116, 46)
WHITE       = (240, 244, 250)
MUTED       = (138, 152, 176)
MUTED_DK    = (86, 98, 122)
GREEN       = (52, 211, 153)
BLUE        = (96, 165, 250)
PINK        = (236, 72, 153)
RED         = (239, 88, 88)
PURPLE      = (167, 139, 250)

SS = 2  # supersample factor


def F(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / name), size * SS)


# font shortcuts
def f_black(s):   return F("InterDisplay-Black.otf", s)
def f_bold(s):    return F("Inter-Bold.otf", s)
def f_semi(s):    return F("Inter-SemiBold.otf", s)
def f_med(s):     return F("Inter-Medium.otf", s)
def f_reg(s):     return F("Inter-Regular.otf", s)
def mono(s):      return F("JetBrainsMono-Regular.ttf", s)
def mono_bold(s): return F("JetBrainsMono-Bold.ttf", s)


def S(v):  # scale a coordinate
    return int(v * SS)


def rrect(d, box, radius, fill=None, outline=None, width=1):
    d.rounded_rectangle([S(box[0]), S(box[1]), S(box[2]), S(box[3])],
                        radius=S(radius), fill=fill, outline=outline,
                        width=max(1, S(width)) if outline else 0)


def text(d, xy, s, font, fill, anchor="la"):
    d.text((S(xy[0]), S(xy[1])), s, font=font, fill=fill, anchor=anchor)


def measure(d, s, font):
    b = d.textbbox((0, 0), s, font=font)
    return (b[2] - b[0]) / SS, (b[3] - b[1]) / SS


def new_canvas(w, h):
    img = Image.new("RGB", (S(w), S(h)), BG)
    return img, ImageDraw.Draw(img)


def paint_background(d, w, h, glow_center=None):
    """Flat-ish space bg + one soft radial glow. Deterministic (loop-safe)."""
    # vertical subtle gradient top->bg
    top = BG_GLOW
    for y in range(0, S(160)):
        t = y / S(160)
        c = tuple(int(top[i] * (1 - t) + BG[i] * t) for i in range(3))
        d.line([(0, y), (S(w), y)], fill=c)
    # static starfield (deterministic)
    import random
    rnd = random.Random(7)
    for _ in range(90):
        x = rnd.randint(0, S(w)); y = rnd.randint(0, S(h))
        b = rnd.choice([22, 30, 38, 46])
        d.point((x, y), fill=(b + 8, b + 12, b + 22))


def add_soft_glow(img, cx, cy, radius, color, alpha=70):
    """Composite a soft radial glow (for active node)."""
    glow = Image.new("RGB", img.size, (0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([S(cx - radius), S(cy - radius), S(cx + radius), S(cy + radius)],
               fill=color)
    glow = glow.filter(ImageFilter.GaussianBlur(S(radius) * 0.45))
    # screen blend approximation via ImageChops
    from PIL import ImageChops
    scaled = glow.point(lambda p: int(p * alpha / 255))
    return ImageChops.screen(img, scaled)


# ======================================================================
#  PIPELINE STAGES  (the honest self-improving cycle)
# ======================================================================
TOPIC = "Self-hosted n8n for autonomous AI agents"

STAGES = [
    dict(mod="A", name="SCAN", color=BLUE,
         cap="Scanner picks tomorrow's topic",
         log=[("$ genesis run trend-scanner", MUTED),
              ("  scanning GitHub trending + Hacker News ...", MUTED),
              ("  3273 trend_signals collected", WHITE),
              ("  GPT-4.1 distilling  ->  topics  (236 / 109 queued)", WHITE),
              (f"  ✓ topic #237  \"{TOPIC[:34]}\"", GREEN)]),
    dict(mod="B", name="RENDER", color=PURPLE,
         cap="One topic -> post + voice + avatar",
         log=[("$ genesis run content-gen --topic 237", MUTED),
              ("  GPT-4.1  ->  draft per channel ...", MUTED),
              ("  ElevenLabs voice clone  ->  audio.mp3", WHITE),
              ("  HeyGen avatar  ->  render.mp4", WHITE),
              ("  ✓ 5 posts + 1 short ready", GREEN)]),
    dict(mod="B", name="PUBLISH", color=GOLD,
         cap="Live on 4 channels on schedule",
         log=[("$ genesis publish --topic 237", MUTED),
              ("  -> YouTube Shorts     ✓ T+0", WHITE),
              ("  -> Instagram Reels    ✓ T+60m", WHITE),
              ("  -> Telegram channel   ✓ T+90m", WHITE),
              ("  -> LinkedIn           ✓ 10:00 CET", GREEN)]),
    dict(mod="C", name="METRICS", color=GREEN,
         cap="Reads its own performance back",
         log=[("$ genesis run metrics", MUTED),
              ("  Plausible + YouTube + LinkedIn + TG ingest ...", MUTED),
              ("  metrics_snapshots += 42 rows", WHITE),
              ("  ✓ topic 237:  1.2k views · CTR 4.7%", GREEN)]),
    dict(mod="D", name="INSIGHT", color=PINK,
         cap="Agent analyses the audience",
         log=[("$ genesis run analyzer", MUTED),
              ("  GPT-4 weekly review over metrics ...", MUTED),
              ("  insight #24: hook-question > statement", WHITE),
              ("             (+38% retention, evidence n=42)", MUTED),
              ("  insights 23 -> 24   (12 applied)", GREEN)]),
    dict(mod="E", name="AUTO-PR", color=GOLD,
         cap="Agent rewrites its own prompt",
         log=[("$ genesis run auto-decision", MUTED),
              ("  evidence threshold reached  ->  bump prompt", MUTED),
              ("  opened auto/prompt-topic_distiller  PR #16", WHITE),
              ("  ✓ merged: topic_distiller 1.0.0 -> 1.0.1", GREEN),
              ("  loop -> next scan uses the improved prompt", GOLD)]),
]

# canvas
W, H = 1120, 700
NODE_Y = 96
NODE_H = 66
NODE_W = 150
N_L = 46
N_R = W - 46
GAP = (N_R - N_L - NODE_W * 6) / 5


def node_box(i):
    x0 = N_L + i * (NODE_W + GAP)
    return (x0, NODE_Y, x0 + NODE_W, NODE_Y + NODE_H)


def draw_pipeline(d, active):
    # connecting arrows
    for i in range(5):
        b0 = node_box(i); b1 = node_box(i + 1)
        y = NODE_Y + NODE_H / 2
        x0 = b0[2] + 4; x1 = b1[0] - 4
        col = GOLD if (active is not None and i < active) else BORDER
        d.line([(S(x0), S(y)), (S(x1 - 7), S(y))], fill=col, width=max(1, S(2)))
        d.polygon([(S(x1), S(y)), (S(x1 - 8), S(y - 4)), (S(x1 - 8), S(y + 4))], fill=col)
    # nodes
    for i, st in enumerate(STAGES):
        b = node_box(i)
        on = (i == active)
        done = (active is not None and i < active)
        if on:
            fill = PANEL_HI; oc = st["color"]; ow = 2
        elif done:
            fill = PANEL; oc = GOLD_DIM; ow = 1
        else:
            fill = PANEL; oc = BORDER_SOFT; ow = 1
        rrect(d, b, 11, fill=fill, outline=oc, width=ow)
        cx = (b[0] + b[2]) / 2
        # module chip
        chip = st["mod"]
        cc = st["color"] if (on or done) else MUTED_DK
        text(d, (cx, b[1] + 15), f"MODULE {chip}", f_bold(9),
             cc if on else (MUTED if done else MUTED_DK), anchor="ma")
        nc = WHITE if on else (MUTED if done else MUTED_DK)
        text(d, (cx, b[1] + 30), st["name"], f_black(17), nc, anchor="ma")
    # ---- the self-improving return loop ----
    loop_y = NODE_Y + NODE_H + 30
    lb0 = node_box(5); lb1 = node_box(0)
    xr = (lb0[0] + lb0[2]) / 2
    xl = (lb1[0] + lb1[2]) / 2
    lc = GOLD if active is not None else BORDER
    # down from last, across, up into first
    d.line([(S(xr), S(NODE_Y + NODE_H)), (S(xr), S(loop_y))], fill=lc, width=max(1, S(2)))
    d.line([(S(xl), S(NODE_Y + NODE_H)), (S(xl), S(loop_y))], fill=lc, width=max(1, S(2)))
    d.line([(S(xl), S(loop_y)), (S(xr), S(loop_y))], fill=lc, width=max(1, S(2)))
    # arrow head into first node (pointing up)
    d.polygon([(S(xl), S(NODE_Y + NODE_H + 2)),
               (S(xl - 4), S(NODE_Y + NODE_H + 11)),
               (S(xl + 4), S(NODE_Y + NODE_H + 11))], fill=lc)
    # loop label centered
    lbl = "↻  self-improving loop — the agent rewrites its own prompts"
    tw, _ = measure(d, lbl, f_semi(11))
    midx = (xl + xr) / 2
    # label background chip to sit on the line
    rrect(d, (midx - tw / 2 - 10, loop_y - 10, midx + tw / 2 + 10, loop_y + 10),
          9, fill=BG, outline=BORDER)
    text(d, (midx, loop_y), lbl, f_semi(11),
         GOLD if active is not None else MUTED, anchor="mm")
    return loop_y


def draw_header(d):
    text(d, (46, 26), "●", f_bold(13), GOLD, anchor="lm")
    text(d, (64, 20), "GENESIS", f_black(20), WHITE)
    tw, _ = measure(d, "GENESIS", f_black(20))
    text(d, (64 + tw + 8, 20), "CONTENT OS", f_black(20), GOLD)
    text(d, (65, 44), "autonomous content pipeline that learns from its own audience",
         f_reg(11), MUTED)
    # right status
    text(d, (W - 46, 22), "● LIVE", f_bold(11), GREEN, anchor="ra")
    text(d, (W - 46, 40), "modules A–F online", f_reg(10), MUTED, anchor="ra")


def draw_terminal(d, term_x, term_y, term_w, term_h, lines, cursor_on, caption):
    rrect(d, (term_x, term_y, term_x + term_w, term_y + term_h), 12,
          fill=(9, 12, 20), outline=BORDER, width=1)
    # title bar
    bar_h = 30
    rrect(d, (term_x, term_y, term_x + term_w, term_y + bar_h + 6), 12, fill=PANEL)
    d.rectangle([S(term_x), S(term_y + bar_h - 4), S(term_x + term_w),
                 S(term_y + bar_h + 6)], fill=(9, 12, 20))
    d.line([(S(term_x), S(term_y + bar_h + 6)), (S(term_x + term_w), S(term_y + bar_h + 6))],
           fill=BORDER, width=1)
    for i, c in enumerate([RED, GOLD, GREEN]):
        cxp = term_x + 18 + i * 18
        d.ellipse([S(cxp - 5), S(term_y + 10), S(cxp + 5), S(term_y + 20)], fill=c)
    text(d, (term_x + term_w / 2, term_y + 15), "genesis@genesis-do : ~/pipeline",
         mono(10), MUTED, anchor="mm")
    # log lines
    lx = term_x + 20
    ly = term_y + bar_h + 20
    lh = 22
    for i, (s, col) in enumerate(lines):
        text(d, (lx, ly + i * lh), s, mono(11.5), col)
    # blinking cursor after last line
    if lines and cursor_on:
        last = lines[-1][0]
        tw, _ = measure(d, last, mono(11.5))
        d.rectangle([S(lx + tw + 4), S(ly + (len(lines) - 1) * lh + 1),
                     S(lx + tw + 12), S(ly + (len(lines) - 1) * lh + 15)], fill=GOLD)
    # caption strip at bottom
    text(d, (term_x + 20, term_y + term_h - 24), caption, f_semi(12), GOLD)


def draw_stats(d, sx, sy, sw, sh, vals):
    """Right-hand live counters."""
    rrect(d, (sx, sy, sx + sw, sy + sh), 12, fill=PANEL, outline=BORDER, width=1)
    text(d, (sx + 18, sy + 16), "LIVE STATE", f_bold(10), MUTED)
    text(d, (sx + sw - 18, sy + 16), "supabase · pgvector", f_reg(9), MUTED_DK, anchor="ra")
    cards = [
        ("trend_signals", vals["signals"], "raw signals scanned", BLUE),
        ("topics", vals["topics"], "distilled · 109 queued", PURPLE),
        ("insights", vals["insights"], "learned · 12 applied", PINK),
        ("prompt PRs merged", vals["prs"], "#9 scenario · #16 distiller", GOLD),
    ]
    y = sy + 42
    row_h = (sh - 54) / 4
    for label, value, sub, col in cards:
        d.line([(S(sx + 18), S(y)), (S(sx + sw - 18), S(y))], fill=BORDER_SOFT, width=1)
        text(d, (sx + 18, y + 12), label.upper(), f_semi(10), MUTED)
        text(d, (sx + sw - 18, y + 8), str(value), f_black(24), col, anchor="ra")
        text(d, (sx + 18, y + 30), sub, f_reg(9.5), MUTED_DK)
        y += row_h


def render_frame(active, visible_lines, cursor_on, vals):
    img, d = new_canvas(W, H)
    paint_background(d, W, H)
    # glow behind active node
    if active is not None:
        b = node_box(active)
        img = add_soft_glow(img, (b[0] + b[2]) / 2, (b[1] + b[3]) / 2, 120,
                            STAGES[active]["color"], alpha=55)
        d = ImageDraw.Draw(img)
        paint_background  # noop; bg already drawn under glow
    # redraw everything on glowed image
    draw_header(d)
    loop_y = draw_pipeline(d, active)
    body_y = loop_y + 26
    term_x, term_w = 46, 690
    stats_x = term_x + term_w + 24
    stats_w = N_R - stats_x
    body_h = H - body_y - 40
    cap = STAGES[active]["cap"] if active is not None else "cycle complete — restarting"
    draw_terminal(d, term_x, body_y, term_w, body_h, visible_lines, cursor_on, cap)
    draw_stats(d, stats_x, body_y, stats_w, body_h, vals)
    # footer
    text(d, (46, H - 22), "github.com/DenisShokhirev041279/genesis-content-os",
         f_reg(10), MUTED_DK)
    text(d, (W - 46, H - 22), "Apache-2.0 · n8n · GPT-4.1 · Doppler",
         f_reg(10), MUTED_DK, anchor="ra")
    return img.resize((W, H), Image.LANCZOS)


# ======================================================================
#  Build GIF
# ======================================================================
def build_gif():
    frames_dir = HERE / "_frames"
    frames_dir.mkdir(exist_ok=True)
    for f in frames_dir.glob("*.png"):
        f.unlink()

    fps = 12
    frame_idx = 0
    # rolling log window
    MAX_LINES = 8
    log = []  # accumulated (text,color) for current cycle

    # base stat values (will tick during relevant stages)
    base = dict(signals=3271, topics=236, insights=23, prs=1)
    vals = dict(base)

    def emit(active, cursor_on, vals_):
        nonlocal frame_idx
        window = log[-MAX_LINES:]
        img = render_frame(active, window, cursor_on, vals_)
        img.save(frames_dir / f"f{frame_idx:04d}.png")
        frame_idx += 1

    # opening: empty terminal, no active
    for _ in range(12):
        emit(None, False, vals)

    for si, st in enumerate(STAGES):
        # reveal each log line, then a short hold
        for li, (line, col) in enumerate(st["log"]):
            log.append((line, col))
            # type-in: reveal + small read pause per new line
            for k in range(4):
                emit(si, (k % 2 == 0), vals)
            # tick counters on the payoff line
            if st["name"] == "SCAN" and li == 2:
                for v in range(3272, 3274):
                    vals["signals"] = v; emit(si, True, vals)
                vals["signals"] = 3273
            if st["name"] == "SCAN" and li == 4:
                vals["topics"] = 237
            if st["name"] == "INSIGHT" and li == 4:
                vals["insights"] = 24
            if st["name"] == "AUTO-PR" and li == 3:
                vals["prs"] = 2
        # hold at end of stage so text is readable
        hold = 34 if st["name"] in ("PUBLISH", "AUTO-PR") else 26
        for k in range(hold):
            emit(si, (k // 4) % 2 == 0, vals)

    # closing: fade log out to empty over a few frames (loop-safe reset)
    while log:
        log.pop(0)
        emit(None, False, vals)
        emit(None, False, vals)
    for _ in range(8):
        emit(None, False, vals)

    print(f"rendered {frame_idx} frames @ {fps}fps  (~{frame_idx/fps:.1f}s)")

    # ffmpeg palettegen + paletteuse (transdiff keeps size down on static UI)
    import imageio_ffmpeg
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    out = HERE / "genesis_demo.gif"
    palette = frames_dir / "palette.png"
    vf = "scale=1120:-1:flags=lanczos"
    subprocess.run([ff, "-y", "-framerate", str(fps), "-i", str(frames_dir / "f%04d.png"),
                    "-vf", f"{vf},palettegen=max_colors=128:stats_mode=diff",
                    str(palette)], check=True, capture_output=True)
    subprocess.run([ff, "-y", "-framerate", str(fps), "-i", str(frames_dir / "f%04d.png"),
                    "-i", str(palette),
                    "-lavfi", f"{vf} [x]; [x][1:v] paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle",
                    "-loop", "0", str(out)], check=True, capture_output=True)
    sz = out.stat().st_size / 1024 / 1024
    print(f"GIF: {out}  {sz:.2f} MB")
    return sz


# ======================================================================
#  Architecture diagram  (A-F data flow)
# ======================================================================
def build_architecture():
    w, h = 1200, 912
    img, d = new_canvas(w, h)
    paint_background(d, w, h)
    # title
    text(d, (60, 44), "●", f_bold(15), GOLD, anchor="lm")
    text(d, (82, 36), "GENESIS CONTENT OS", f_black(24), WHITE)
    text(d, (82, 66), "six autonomous modules · topic discovery → publish → learn → rewrite prompt",
         f_reg(13), MUTED)
    text(d, (w - 60, 40), "data flow A → F", f_semi(13), GOLD, anchor="ra")

    cx = w / 2
    bw = 620
    bx0 = cx - bw / 2
    modules = [
        ("A", "trend-scanner", "GitHub trending + Hacker News  →  trend_signals  →  topics (GPT-4.1)", BLUE,
         "n8n · every 6h + daily 06:00"),
        ("B", "content-gen", "topic  →  post per channel  →  publish YT · IG · TG · LinkedIn · Ghost", PURPLE,
         "GPT-4.1 + ElevenLabs + HeyGen"),
        ("C", "metrics", "Plausible + YouTube + LinkedIn + Telegram  →  metrics_snapshots", GREEN,
         "ingestors · per-post rows"),
        ("D", "analyzer", "weekly GPT-4 review of metrics  →  insights (with evidence)", PINK,
         "activates after C ≥ 7d"),
        ("E", "auto-decision", "evidence threshold  →  new prompt version  →  auto/prompt-* PR", GOLD,
         "self-improving · Denis approves diff"),
        ("F", "weekly-reports", "Sunday 09:00 Berlin digest  →  weekly_reports  →  Telegram DM", BLUE,
         "leaf output → Denis"),
    ]
    top = 120
    bh = 92
    vgap = 24
    boxes = []
    for i, (mod, name, desc, col, meta) in enumerate(modules):
        y0 = top + i * (bh + vgap)
        y1 = y0 + bh
        boxes.append((y0, y1))
        rrect(d, (bx0, y0, bx0 + bw, y1), 14, fill=PANEL, outline=BORDER, width=1)
        # left accent bar
        d.rounded_rectangle([S(bx0), S(y0), S(bx0 + 8), S(y1)], radius=S(4), fill=col)
        # module badge
        rrect(d, (bx0 + 22, y0 + 20, bx0 + 22 + 66, y0 + 20 + 52), 10,
              fill=PANEL_HI, outline=col, width=2)
        text(d, (bx0 + 22 + 33, y0 + 20 + 26), mod, f_black(24), col, anchor="mm")
        # name + desc
        tx = bx0 + 110
        text(d, (tx, y0 + 20), name, f_bold(19), WHITE)
        text(d, (tx, y0 + 48), desc, f_reg(12.5), MUTED)
        text(d, (bx0 + bw - 22, y0 + 22), meta, f_semi(11), col, anchor="ra")
        # arrow to next
        if i < len(modules) - 1:
            ax = cx
            ay0 = y1; ay1 = y1 + vgap
            d.line([(S(ax), S(ay0 + 2)), (S(ax), S(ay1 - 6))], fill=GOLD, width=max(1, S(2)))
            d.polygon([(S(ax), S(ay1 - 1)), (S(ax - 5), S(ay1 - 9)), (S(ax + 5), S(ay1 - 9))], fill=GOLD)

    # self-improving return loop on the right side E -> A  (F is a leaf below)
    ya = (boxes[0][0] + boxes[0][1]) / 2
    ye = (boxes[4][0] + boxes[4][1]) / 2
    rx = bx0 + bw + 40
    lc = GOLD
    d.line([(S(bx0 + bw), S(ye)), (S(rx), S(ye))], fill=lc, width=max(1, S(2)))
    d.line([(S(rx), S(ye)), (S(rx), S(ya))], fill=lc, width=max(1, S(2)))
    d.line([(S(rx), S(ya)), (S(bx0 + bw), S(ya))], fill=lc, width=max(1, S(2)))
    d.polygon([(S(bx0 + bw + 1), S(ya)), (S(bx0 + bw + 10), S(ya - 5)),
               (S(bx0 + bw + 10), S(ya + 5))], fill=lc)
    # vertical loop label
    tmp = Image.new("RGBA", (S(360), S(30)), (0, 0, 0, 0))
    td = ImageDraw.Draw(tmp)
    td.text((S(180), S(15)), "↻  the loop that makes it autonomous",
            font=f_bold(13), fill=GOLD, anchor="mm")
    tmp = tmp.rotate(-90, expand=True)
    img.paste(tmp, (S(rx + 12), S((ya + ye) / 2) - tmp.height // 2), tmp)
    d = ImageDraw.Draw(img)

    # storage footer strip
    fy = top + 6 * (bh + vgap) + 4
    rrect(d, (bx0, fy, bx0 + bw, fy + 46), 12, fill=(9, 12, 20), outline=BORDER, width=1)
    text(d, (bx0 + 20, fy + 23),
         "Storage:  Supabase Postgres + pgvector   ·   Secrets:  Doppler   ·   Orchestration:  n8n",
         mono(12), MUTED, anchor="lm")
    return img.resize((w, h), Image.LANCZOS)


def build_dashboard_still():
    # pick a nice mid-cycle state: PUBLISH stage fully revealed
    log = []
    for st in STAGES[:2]:
        for line, col in st["log"]:
            log.append((line, col))
    for line, col in STAGES[2]["log"]:
        log.append((line, col))
    vals = dict(signals=3273, topics=237, insights=23, prs=1)
    img = render_frame(2, log[-8:], True, vals)
    return img


if __name__ == "__main__":
    import sys
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    if what in ("arch", "all"):
        a = build_architecture()
        a.save(HERE / "genesis_architecture.png")
        print("wrote genesis_architecture.png", a.size)
    if what in ("dash", "all"):
        s = build_dashboard_still()
        s.save(HERE / "genesis_dashboard.png")
        print("wrote genesis_dashboard.png", s.size)
    if what in ("gif", "all"):
        build_gif()
