"""Design tokens + global CSS for the dashboard.

Palette: cool neutrals and one deep-teal accent; the four risk colours are the only warm tones on screen.
Type: Geist for the interface, Instrument Serif for display numbers and titles, Geist Mono for small
      labels and figures. System fallbacks when offline.
Motion: page rise on navigation, bars grow; all off under prefers-reduced-motion.
"""

# ---------------------------------------------------------------- tokens
BG = "#FAFAF9"          # page
SURFACE = "#FFFFFF"     # panels, inputs
SUBTLE = "#F4F4F5"      # tracks, hover fills
INK = "#18181B"         # primary text
INK_2 = "#52525B"       # secondary text  (7.6:1 on BG)
INK_3 = "#71717A"       # captions        (4.7:1 on BG)
LINE = "#E9E9EC"        # hairlines
LINE_2 = "#D4D4D8"      # input borders
BRAND = "#0B6A80"       # deep teal: nav, focus, sliders, forecast line, "our model"
BRAND_SOFT = "#E3F1F4"
RAIN, RAIN_SOFT = BRAND, "#B5D5DE"
RAISE, LOWER = "#D0453A", BRAND   # driver bars: pushes risk up / down

# bg = solid fill, fg = text on bg, soft/text = badge background + label
TIER = {
    "Low": {"bg": "#1E8449", "fg": "#FFFFFF", "soft": "#E7F5EC", "text": "#166A3A"},
    "Medium": {"bg": "#F2B01E", "fg": INK, "soft": "#FDF3D6", "text": "#8A5300"},
    "High": {"bg": "#F07C2E", "fg": INK, "soft": "#FDECDF", "text": "#A8400B"},
    "Critical": {"bg": "#C9302C", "fg": "#FFFFFF", "soft": "#FCE7E6", "text": "#A8201D"},
}
# Heatmap fills: same hues, but Low is a light tint so the risky weeks carry the weight
HEAT = {"Low": "#B7E0C4", "Medium": "#F5C451", "High": "#F07C2E", "Critical": "#C9302C"}

FONT = "Geist, ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
SERIF = "'Instrument Serif', 'Iowan Old Style', Georgia, serif"
MONO = "'Geist Mono', ui-monospace, SFMono-Regular, Menlo, monospace"
R_PANEL, R_CONTROL = "18px", "10px"
SHADOW_XS = "0 1px 2px rgba(16,24,40,.05)"
SHADOW_POP = "0 18px 40px -12px rgba(16,24,40,.22), 0 4px 10px -4px rgba(16,24,40,.08)"
EASE = "cubic-bezier(.2,.8,.2,1)"

CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Geist:wght@300..700&family=Geist+Mono:wght@400..600&family=Instrument+Serif:ital@0;1&display=swap');

/* ================================================================ base */
:root {{
  --bg:{BG}; --surface:{SURFACE}; --subtle:{SUBTLE}; --ink:{INK}; --ink2:{INK_2}; --ink3:{INK_3};
  --line:{LINE}; --line2:{LINE_2}; --brand:{BRAND}; --brand-soft:{BRAND_SOFT};
  --r-panel:{R_PANEL}; --r-control:{R_CONTROL}; --ease:{EASE};
}}
.stApp, .stApp *:not([data-testid="stIconMaterial"]):not(.material-symbols-rounded):not(.dw-serif):not(.dw-mono) {{ font-family:{FONT} !important; }}
.stApp .dw-serif {{ font-family:{SERIF} !important; font-weight:400 !important; font-feature-settings:"lnum" 1; }}
.stApp .dw-mono {{ font-family:{MONO} !important; }}
.stApp {{ background:var(--bg); color:var(--ink); -webkit-font-smoothing:antialiased;
         font-feature-settings:"tnum" 1, "ss01" 1; font-variant-numeric:tabular-nums; }}
.stApp p, .stApp li {{ font-size:14px; line-height:1.5; }}
h1, h2, h3, p {{ color:var(--ink); }}
.stMain {{ overflow-x:clip; }}

/* ================================================================ chrome */
header[data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"], [data-testid="stAppDeployButton"],
[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"], [data-testid="stMainMenu"], #MainMenu, footer
{{ display:none !important; }}
[data-testid="stMainBlockContainer"] {{ max-width:1248px; padding:0 24px 72px !important; }}
[data-testid="stMainBlockContainer"] > [data-testid="stVerticalBlock"], .st-key-view {{ gap:20px; }}
[data-testid="stMainBlockContainer"] [data-testid="stHorizontalBlock"] {{ gap:20px; }}

/* ================================================================ top bar + nav */
[data-testid="stLayoutWrapper"]:has(> .st-key-topbar) {{ position:sticky; top:0; z-index:90; }}
.st-key-topbar {{ height:64px !important; min-height:64px; position:relative; flex-wrap:nowrap !important; }}
.st-key-topbar::before {{
  content:""; position:absolute; z-index:-1; top:0; bottom:0; left:calc(50% - 50vw); right:calc(50% - 50vw);
  background:rgba(250,250,249,.8); backdrop-filter:saturate(1.6) blur(14px); -webkit-backdrop-filter:saturate(1.6) blur(14px);
  border-bottom:1px solid var(--line);
}}
.dw-brand {{ display:flex; align-items:center; gap:10px; white-space:nowrap; }}
.dw-brand .mark {{ width:28px; height:28px; border-radius:8px; background:var(--brand); display:grid; place-items:center;
                  box-shadow:inset 0 -1px 0 rgba(0,0,0,.15); }}
.dw-brand b {{ font-size:15px; font-weight:600; letter-spacing:-.01em; color:var(--ink); }}
.dw-brand span {{ font-size:13px; color:var(--ink3); padding-left:10px; border-left:1px solid var(--line2); }}

/* segmented controls: equal-width pills on a track with a sliding thumb (pure CSS) */
.st-key-page [role="radiogroup"], .st-key-h [role="radiogroup"] {{
  margin:0 !important; display:grid; grid-auto-flow:column; grid-auto-columns:1fr; gap:0; position:relative; isolation:isolate;
  padding:3px; background:#EFEFF1; border-radius:999px;
}}
.st-key-page [role="radiogroup"]::before, .st-key-h [role="radiogroup"]::before {{
  content:""; position:absolute; z-index:0; top:3px; bottom:3px; left:3px; width:calc((100% - 6px) / 3);
  border-radius:999px; transition:transform .32s var(--ease);
}}
.st-key-page [role="radiogroup"]:has(> button:nth-of-type(2)[aria-checked="true"])::before,
.st-key-h [role="radiogroup"]:has(> button:nth-of-type(2)[aria-checked="true"])::before {{ transform:translateX(100%); }}
.st-key-page [role="radiogroup"]:has(> button:nth-of-type(3)[aria-checked="true"])::before,
.st-key-h [role="radiogroup"]:has(> button:nth-of-type(3)[aria-checked="true"])::before {{ transform:translateX(200%); }}
.st-key-page [role="radiogroup"] > button, .st-key-h [role="radiogroup"] > button {{
  position:relative; z-index:1; min-width:0; min-height:0 !important; margin:0 !important;
  background:transparent !important; border:none !important; box-shadow:none !important; border-radius:999px !important;
  color:var(--ink2) !important; transition:color .2s ease;
}}
.st-key-page [role="radiogroup"] > button p, .st-key-h [role="radiogroup"] > button p
{{ font-size:13.5px; font-weight:500; color:inherit !important; white-space:nowrap; }}
.st-key-page [role="radiogroup"] > button:hover, .st-key-h [role="radiogroup"] > button:hover {{ color:var(--ink) !important; }}
/* nav: solid dark thumb */
.st-key-page [role="radiogroup"]::before {{ background:var(--ink); box-shadow:0 1px 2px rgba(16,24,40,.25), 0 2px 6px rgba(16,24,40,.12); }}
.st-key-page [role="radiogroup"] > button {{ height:32px; padding:0 18px !important; }}
.st-key-page [role="radiogroup"] > button[aria-checked="true"] {{ color:#fff !important; }}
/* weeks ahead: white raised thumb, same height as inputs */
.st-key-h {{ width:100% !important; }}
.st-key-h [data-testid="stButtonGroup"] {{ width:100%; }}
.st-key-h [role="radiogroup"] {{ height:40px; margin:0 !important; border-radius:var(--r-control); }}
.st-key-h [role="radiogroup"]::before {{ border-radius:8px; background:var(--surface); box-shadow:0 1px 2px rgba(16,24,40,.08), 0 1px 3px rgba(16,24,40,.06); }}
.st-key-h [role="radiogroup"] > button {{ height:34px; padding:0 8px !important; border-radius:8px !important; }}
.st-key-h [role="radiogroup"] > button[aria-checked="true"] {{ color:var(--ink) !important; }}

/* ================================================================ inputs */
[data-testid="stWidgetLabel"] {{ min-height:20px; height:20px; margin-bottom:6px !important; align-items:center; justify-content:flex-start; gap:4px; }}
[data-testid="stWidgetLabel"] > div {{ flex:0 0 auto; width:auto; }}
.st-key-page [data-testid="stWidgetLabel"], .st-key-yearpick [data-testid="stWidgetLabel"] {{ display:none !important; }}
[data-testid="stWidgetLabel"] p {{ color:var(--ink2) !important; font-size:12.5px !important; font-weight:500; line-height:20px; }}
[data-testid="stWidgetLabel"] [data-testid="stTooltipIcon"], [data-testid="stWidgetLabel"] [data-testid="stTooltipHoverTarget"]
{{ height:20px; display:flex; align-items:center; }}
[data-testid="stWidgetLabel"] [data-testid="stTooltipHoverTarget"] button {{
  width:16px; height:16px; padding:0; border:none !important; background:none !important; color:var(--ink3);
  display:grid; place-items:center; border-radius:999px; min-height:0;
}}
[data-testid="stWidgetLabel"] [data-testid="stTooltipHoverTarget"] svg {{ width:13px; height:13px; }}
[data-testid="stWidgetLabel"] [data-testid="stTooltipHoverTarget"] button:hover {{ color:var(--ink); }}

[data-testid="stSelectbox"] [role="group"], [data-testid="stDateInputField"] {{
  height:40px; min-height:40px; background:var(--surface) !important; border:1px solid var(--line2) !important;
  border-radius:var(--r-control) !important; box-shadow:{SHADOW_XS}; transition:border-color .15s ease, box-shadow .15s ease;
}}
[data-testid="stSelectbox"] [role="group"]:hover, [data-testid="stDateInputField"]:hover {{ border-color:#98A2B3 !important; }}
[data-testid="stSelectbox"] [role="group"]:focus-within, [data-testid="stDateInputField"]:focus-within
{{ border-color:var(--brand) !important; box-shadow:0 0 0 3px var(--brand-soft); }}
[data-testid="stSelectbox"] [role="group"]:has(:focus-visible), [data-testid="stDateInputField"]:has(:focus-visible)
{{ outline:2px solid var(--brand); outline-offset:2px; }}
[data-testid="stSelectbox"] [role="group"] :focus-visible, [data-testid="stDateInputField"] :focus-visible
{{ outline:none !important; }}
[data-testid="stSelectbox"] input {{ font-size:14px !important; font-weight:500; color:var(--ink) !important; padding-left:12px !important; cursor:pointer; }}
[data-testid="stSelectbox"] [role="group"] button {{ color:var(--ink3); }}
[data-testid="stDateInputField"] [role="spinbutton"] {{ font-size:14px; font-weight:500; color:var(--ink); }}
[data-testid="stDateInputField"] [role="spinbutton"]:focus {{ background:var(--brand-soft); color:var(--brand); }}
[data-testid="stDateInputCalendarButton"] {{ color:var(--ink3) !important; }}

/* menus, calendars and other floating layers */
[role="listbox"] {{ border-radius:12px !important; }}
[role="listbox"] [role="option"] {{ border-radius:8px; font-size:14px; }}
[data-testid="stSelectboxVirtualDropdown"], [data-baseweb="popover"] > div, [data-baseweb="calendar"],
.react-aria-Popover {{ border-radius:12px !important; box-shadow:{SHADOW_POP} !important; border:1px solid var(--line) !important; }}

/* ================================================================ buttons */
.stButton button, [data-testid="stPopoverButton"] {{
  height:40px; min-height:40px; padding:0 14px; border-radius:var(--r-control) !important;
  font-size:14px; font-weight:500; box-shadow:{SHADOW_XS};
  transition:background .15s ease, border-color .15s ease, color .15s ease, box-shadow .15s ease;
}}
.stButton button[kind="secondary"], [data-testid="stPopoverButton"] {{
  background:var(--surface) !important; border:1px solid var(--line2) !important; color:var(--ink) !important;
}}
.stButton button[kind="secondary"]:hover, [data-testid="stPopoverButton"]:hover {{ background:#F9FAFB !important; border-color:#98A2B3 !important; }}
.stButton button[kind="primary"] {{ background:var(--ink) !important; border:1px solid var(--ink) !important; color:#fff !important; }}
.stButton button[kind="primary"]:hover {{ background:#1E293B !important; }}
.stButton button:active, [data-testid="stPopoverButton"]:active {{ transform:translateY(.5px); }}
.stButton button p, [data-testid="stPopoverButton"] p {{ font-size:14px; font-weight:500; color:inherit !important; }}
[data-testid="stPopoverButton"] [data-testid="stIconMaterial"] {{ color:var(--ink3); }}
:focus-visible {{ outline:2px solid var(--brand) !important; outline-offset:2px; }}

/* popover body */
[data-testid="stPopoverBody"] {{
  min-width:320px; padding:20px 20px 16px !important; background:var(--surface) !important;
  border:1px solid var(--line) !important; border-radius:14px !important; box-shadow:{SHADOW_POP} !important;
  animation:dwPop .18s var(--ease) both;
}}
[data-testid="stPopoverBody"] [data-testid="stVerticalBlock"] {{ gap:14px; }}
[data-testid="stPopoverBody"] [data-testid="stSlider"] [data-testid="stWidgetLabel"] {{ margin-bottom:16px !important; }}
[data-testid="stPopoverBody"] [data-testid="stSliderTickBar"] {{ display:none; }}
[data-testid="stPopoverBody"] .dw-head {{ margin-bottom:12px; }}
@keyframes dwPop {{ from {{ opacity:0; transform:translateY(-4px) scale(.98); }} }}

/* ================================================================ sliders */
[data-testid="stSlider"] [role="group"] > div > div:first-child {{ height:4px; border-radius:999px; }}
[data-testid="stSlider"] [role="group"] > div > div:nth-child(2) {{
  width:18px; height:18px; background:var(--surface) !important; border:2px solid var(--brand);
  box-shadow:0 1px 3px rgba(16,24,40,.18), 0 0 0 0 var(--brand-soft); transition:box-shadow .15s ease;
}}
[data-testid="stSlider"] [role="group"] > div > div:nth-child(2):hover,
[data-testid="stSlider"] [role="group"] > div > div:nth-child(2):focus-within {{ box-shadow:0 1px 3px rgba(16,24,40,.18), 0 0 0 5px var(--brand-soft); }}
[data-testid="stSliderThumbValue"] {{ color:var(--ink) !important; font-size:12.5px; font-weight:600; }}
[data-testid="stSliderTickBar"] p, [data-testid="stSliderTickBar"] {{ color:var(--ink3) !important; font-size:12px !important; }}

/* ================================================================ panels */
[class*="st-key-panel"] {{
  background:var(--surface); border:1px solid var(--line) !important; border-radius:var(--r-panel) !important;
  padding:22px 24px !important; box-shadow:0 1px 2px rgba(16,24,40,.03); gap:12px;
}}
.dw-head {{ display:flex; align-items:flex-start; justify-content:space-between; gap:12px; margin-bottom:6px; }}
.dw-head h3 {{ font-size:15px; font-weight:600; line-height:22px; letter-spacing:-.015em; margin:0; padding:0; color:var(--ink); }}
.dw-head p {{ font-size:12.5px !important; color:var(--ink3) !important; margin:2px 0 0 !important; line-height:1.45 !important; }}
.dw-title {{ padding:24px 0 8px; display:flex; align-items:flex-end; justify-content:space-between; gap:16px; flex-wrap:wrap; }}
.dw-title h1 {{ font-size:46px; line-height:1; letter-spacing:-.01em; margin:0; padding:0; }}
.dw-title p {{ font-size:11.5px !important; color:var(--ink3) !important; margin:10px 0 0 !important; letter-spacing:.06em; text-transform:uppercase; }}
.dw-caption {{ font-size:12.5px !important; color:var(--ink3) !important; margin:0 !important; line-height:1.5; }}

.st-key-yearpick {{ max-width:120px; margin-left:auto; }}

/* controls row */
.st-key-controls [data-testid="stHorizontalBlock"] {{ gap:12px !important; }}
.st-key-controls {{ padding-bottom:4px; }}

/* info tooltip (keyboard + hover) */
.dw-info {{ position:relative; display:inline-grid; place-items:center; width:18px; height:18px; flex:none; margin-top:2px;
           border-radius:999px; color:var(--ink3); cursor:help; }}
.dw-info:hover, .dw-info:focus-visible {{ color:var(--ink); }}
.dw-info [role="tooltip"] {{
  position:absolute; right:-6px; top:calc(100% + 8px); z-index:20; width:max-content; max-width:260px;
  padding:8px 10px; border-radius:8px; background:var(--ink); color:#fff; font-size:12.5px; line-height:1.45; font-weight:400;
  box-shadow:{SHADOW_POP}; opacity:0; transform:translateY(-2px); pointer-events:none; transition:opacity .15s ease, transform .15s ease;
}}
.dw-info:hover [role="tooltip"], .dw-info:focus-visible [role="tooltip"] {{ opacity:1; transform:none; }}

/* ================================================================ components */
/* legends */
.dw-legend {{ display:flex; gap:6px 18px; flex-wrap:wrap; font-size:12.5px; color:var(--ink2); line-height:18px; }}
.dw-legend span {{ display:inline-flex; align-items:center; gap:7px; white-space:nowrap; }}
.dw-legend i {{ display:inline-block; width:10px; height:10px; border-radius:3px; flex:none; }}
.dw-legend i.line {{ height:2.5px; width:14px; border-radius:2px; }}
.dw-legend i.dot {{ width:7px; height:7px; border-radius:50%; }}

/* tier badge */
.dw-tier {{ display:inline-flex; align-items:center; gap:7px; height:26px; padding:0 11px 0 9px; border-radius:999px;
           font-size:13px; font-weight:600; white-space:nowrap; }}
.dw-tier::before {{ content:""; width:7px; height:7px; border-radius:50%; background:currentColor; }}
.dw-chip {{ display:inline-flex; align-items:center; gap:6px; height:26px; padding:0 10px; border-radius:999px;
           font-size:12.5px; font-weight:500; color:var(--brand); background:var(--brand-soft); white-space:nowrap; }}


/* risk summary */
.dw-sum-top {{ display:flex; gap:8px; flex-wrap:wrap; align-items:center; }}
.dw-big {{ font-size:96px; line-height:.9; letter-spacing:-.02em; margin:22px 0 10px; color:var(--ink); }}
.dw-big-sub {{ font-size:14px; color:var(--ink2); line-height:1.45; }}
.dw-kv {{ display:grid; grid-template-columns:auto 1fr; margin:20px 0 0; font-size:13.5px; }}
.dw-kv dt, .dw-kv dd {{ padding:9px 0; border-top:1px solid var(--line); margin:0; }}
.dw-kv dt {{ color:var(--ink3); padding-right:20px; font-size:11.5px; letter-spacing:.06em; text-transform:uppercase; display:flex; align-items:center; }}
.dw-kv dd {{ font-size:13px; text-align:right; color:var(--ink); }}
.dw-sim {{ margin-top:14px; font-size:12.5px; color:var(--brand); }}

/* driver rows */
.dw-drvs {{ display:grid; grid-template-columns:max-content minmax(90px,1fr) auto; column-gap:16px; }}
.dw-drv {{ grid-column:1 / -1; display:grid; grid-template-columns:subgrid; align-items:center;
          padding:9px 0; font-size:13.5px; color:var(--ink); }}
.dw-drv + .dw-drv {{ border-top:1px solid #F2F4F7; }}
.dw-drv .track {{ position:relative; height:8px; }}
.dw-drv .track::before {{ content:""; position:absolute; left:50%; top:-7px; bottom:-7px; width:1px; background:var(--line2); }}
.dw-drv .bar {{ position:absolute; top:0; height:8px; border-radius:999px; animation:dwGrowX .6s var(--ease) both; }}
.dw-drv .bar.up {{ transform-origin:left; }} .dw-drv .bar.down {{ transform-origin:right; }}
.dw-drv .val {{ min-width:44px; text-align:right; color:var(--ink2); font-weight:500; white-space:nowrap; }}

/* tier mix */
.dw-need {{ display:flex; align-items:baseline; gap:10px; flex-wrap:wrap; margin-bottom:14px; }}
.dw-need b {{ font-size:48px; letter-spacing:-.01em; line-height:1; }}
.dw-need span {{ font-size:14px; color:var(--ink2); }}
.dw-mix {{ display:flex; height:10px; border-radius:999px; overflow:hidden; gap:3px; margin-bottom:12px; }}
.dw-mix div {{ height:100%; border-radius:999px; animation:dwGrowX .6s var(--ease) both; transform-origin:left; }}

/* city ranking */
.dw-ranks {{ columns:2; column-gap:40px; }}
.dw-rank {{ break-inside:avoid; display:grid; grid-template-columns:22px 64px 1fr 44px 46px 64px; align-items:center; gap:10px;
           height:38px; border-bottom:1px solid #F2F4F7; font-size:13.5px; }}
.dw-rank .n {{ color:var(--ink3); font-size:12px; text-align:right; }}
.dw-rank .w {{ font-weight:500; white-space:nowrap; }}
.dw-rank .lane {{ height:6px; background:var(--subtle); border-radius:999px; position:relative; overflow:hidden; }}
.dw-rank .fill {{ position:absolute; inset:0 auto 0 0; border-radius:999px; animation:dwGrowX .7s var(--ease) both; transform-origin:left; }}
.dw-rank .num {{ text-align:right; font-weight:500; }}
.dw-rank .rng {{ text-align:right; color:var(--ink3); font-size:12px; white-space:nowrap; }}
.dw-rank .t {{ font-size:12px; font-weight:600; text-align:right; white-space:nowrap; }}

/* stats */
.dw-stats {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:16px; }}
.dw-stats > div {{ background:var(--surface); border:1px solid var(--line); border-radius:var(--r-panel); padding:20px 22px;
                  box-shadow:0 1px 2px rgba(16,24,40,.03); }}
.dw-stats b {{ display:block; font-size:48px; line-height:1; letter-spacing:-.01em; color:var(--ink); }}
.dw-stats b.brand {{ color:var(--brand); }}
.dw-stats span {{ display:block; font-size:13px; line-height:1.45; color:var(--ink2); margin-top:8px; }}

/* steps */
.dw-steps {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:24px; counter-reset:s; margin-top:6px; }}
.dw-steps > div {{ counter-increment:s; position:relative; font-size:13.5px; color:var(--ink2); line-height:1.5; }}
.dw-steps > div::before {{ content:counter(s); display:grid; place-items:center; width:26px; height:26px; border-radius:999px;
                          background:var(--brand-soft); color:var(--brand); font-size:12.5px; font-weight:600; margin-bottom:12px; }}
.dw-steps > div:not(:last-child)::after {{ content:""; position:absolute; top:13px; left:36px; right:-14px; height:1px; background:var(--line); }}
.dw-steps b {{ display:block; color:var(--ink); font-weight:600; font-size:14px; margin-bottom:4px; }}

/* expander */
[data-testid="stExpander"] details {{ background:var(--surface); border:1px solid var(--line) !important; border-radius:var(--r-panel) !important;
                                      overflow:hidden; box-shadow:0 1px 2px rgba(16,24,40,.03); }}
[data-testid="stExpander"] summary {{ padding:14px 20px !important; background:transparent !important; border-radius:0 !important;
                                      transition:background .15s ease; }}
[data-testid="stExpander"] summary:hover {{ background:#F9FAFB !important; }}
[data-testid="stExpander"] summary p {{ font-size:14px; font-weight:600; color:var(--ink) !important; }}
[data-testid="stExpander"] summary [data-testid="stIconMaterial"] {{ color:var(--ink3); }}
[data-testid="stExpanderDetails"] {{ border:none !important; border-top:1px solid var(--line) !important; padding:18px 20px 20px !important; }}
[data-testid="stDataFrameResizable"] {{ border-radius:12px !important; border-color:var(--line) !important; }}

/* ================================================================ charts */
[data-testid="stPlotlyChart"] {{ margin:0 -4px; }}
.stPlotlyChart .modebar-container {{ display:none !important; }}

/* ================================================================ motion */
@keyframes dwRise {{ from {{ opacity:0; transform:translateY(8px); }} to {{ opacity:1; transform:none; }} }}
@keyframes dwGrowX {{ from {{ transform:scaleX(0); }} to {{ transform:scaleX(1); }} }}

/* ================================================================ responsive */
@media (max-width: 1024px) {{
  .dw-stats {{ grid-template-columns:repeat(2,minmax(0,1fr)); }}
  .dw-ranks {{ columns:1; }}
}}
@media (max-width: 640px) {{
  [data-testid="stMainBlockContainer"] {{ padding:0 16px 56px !important; }}
  [data-testid="stMainBlockContainer"] > [data-testid="stVerticalBlock"] {{ gap:16px; }}
  .st-key-topbar {{ height:56px !important; min-height:56px; }}
  [data-testid="stPopoverButton"] {{ padding:0 10px; }}
  [data-testid="stPopoverButton"] > div > div > span > span:has(> [data-testid="stIconMaterial"]) {{ display:none; }}
  .dw-brand span {{ display:none; }}
  .st-key-page [role="radiogroup"] > button {{ padding:0 12px !important; }}
  .dw-title {{ padding-top:20px; }}
  .dw-title h1 {{ font-size:38px; }}
  /* controls: two per row, segmented control full width */
  .st-key-controls [data-testid="stHorizontalBlock"] {{ flex-wrap:wrap; }}
  .st-key-controls [data-testid="stColumn"] {{ flex:1 1 calc(50% - 6px) !important; min-width:calc(50% - 6px) !important; width:auto !important; }}
  .st-key-controls [data-testid="stColumn"]:has(.st-key-h) {{ flex-basis:100% !important; min-width:100% !important; order:-1; }}
  .st-key-controls [data-testid="stColumn"]:not(:has(input, button)) {{ display:none; }}
  [class*="st-key-panel"] {{ padding:18px 16px !important; }}
  .dw-stats {{ gap:12px; }}
  .dw-stats > div {{ padding:16px; }}
  .dw-stats b {{ font-size:38px; }}
  .dw-steps {{ grid-template-columns:1fr; gap:18px; }}
  .dw-steps > div {{ padding-left:40px; }}
  .dw-steps > div::before {{ position:absolute; left:0; top:-2px; }}
  .dw-steps > div:not(:last-child)::after {{ top:30px; bottom:-14px; left:13px; right:auto; width:1px; height:auto; }}
  .dw-big {{ font-size:76px; }}
  .dw-drvs {{ grid-template-columns:minmax(0,max-content) minmax(60px,1fr) auto; column-gap:10px; }}
  .dw-drv {{ font-size:13px; }}
  .dw-rank {{ grid-template-columns:18px 58px 1fr 34px 56px; gap:8px; }}
  .dw-rank .rng {{ display:none; }}
}}
@media (prefers-reduced-motion: reduce) {{
  *, *::before, *::after {{ animation:none !important; transition:none !important; }}
}}
</style>
"""
