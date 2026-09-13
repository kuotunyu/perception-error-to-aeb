"""Accessible document shell around Plotly's native replay controls."""

from html import escape
from string import Template

PAGE = Template("""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>$heading — AEB scenario replay</title>
<style>
:root{color:#142033;background:#eef3f6;font-family:"Segoe UI",Arial,sans-serif;font-size:18px;}
*{box-sizing:border-box;}body{margin:0;line-height:1.55;}
.replay-shell{max-width:1160px;margin:0 auto;padding:28px 24px 40px;}
.page-heading{display:flex;align-items:baseline;justify-content:space-between;gap:24px;margin-bottom:24px;}
h1{font:700 clamp(28px,3.5vw,40px)/1.15 Georgia,serif;letter-spacing:-.025em;margin:0;overflow-wrap:break-word;}
.page-heading p{margin:0;color:#425d70;font-size:16px;white-space:nowrap;}
.replay-workspace{display:grid;grid-template-columns:minmax(0,1fr) 264px;gap:28px;align-items:start;}
.replay-canvas{min-width:0;background:#fff;border:1px solid #cddbe3;border-radius:12px;overflow:hidden;}
.replay-context{min-width:0;padding-top:8px;}
h2{font-size:20px;line-height:1.3;margin:0 0 14px;}
.context-section + .context-section{border-top:1px solid #cddbe3;margin-top:24px;padding-top:24px;}
.configuration{list-style:none;padding:0;margin:0;display:flex;flex-wrap:wrap;gap:8px;}
.configuration li{padding:5px 10px;background:#dde9ef;color:#183e54;font-size:16px;border-radius:4px;overflow-wrap:anywhere;}
.legend{list-style:none;padding:0;margin:0;display:grid;gap:14px;}
.legend li{display:flex;align-items:center;gap:12px;font-size:16px;}
.swatch{width:30px;height:14px;flex:none;border:2px solid #254fc4;background:rgba(37,79,196,.22);}
.swatch.observed{border-color:#087f8c;background:rgba(8,127,140,.14);}
.swatch.unobserved{border-color:#b65a17;border-style:dashed;background:transparent;}
.replay-context p{font-size:16px;color:#425d70;margin:12px 0 0;}
.window{font-variant-numeric:tabular-nums;color:#142033;font-weight:600;}
details{font-size:16px;margin-top:24px;}summary{cursor:pointer;color:#254c62;text-underline-offset:3px;}
summary:focus-visible{outline:3px solid #087f8c;outline-offset:4px;}
code{display:block;margin-top:12px;white-space:normal;overflow-wrap:anywhere;font-size:15px;}
footer{margin-top:24px;border-top:1px solid #cddbe3;padding-top:18px;color:#425d70;font-size:16px;max-width:78ch;}
footer p{margin:0;}
@media(max-width:850px){.page-heading{display:block;}.page-heading p{margin-top:10px;white-space:normal;}.replay-workspace{grid-template-columns:minmax(0,1fr);}.replay-context{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:24px;}.context-section + .context-section{border:0;margin:0;padding:0;}details{grid-column:1/-1;margin:0;}}
@media(max-width:520px){.replay-shell{padding:20px 12px 28px;}.replay-workspace{gap:20px;}.replay-context{grid-template-columns:minmax(0,1fr);}.page-heading{margin-bottom:18px;}}
</style>
</head>
<body><main class="replay-shell">
<header class="page-heading"><h1>$heading</h1><p>Perception error → AEB · Scenario replay</p></header>
<div class="replay-workspace">
<section class="replay-canvas" aria-label="Animated scenario geometry and playback controls">$plot</section>
<aside class="replay-context" aria-label="Replay context and reading guide">
<section class="context-section"><h2>Configuration</h2><ul class="configuration">$configuration</ul></section>
<section class="context-section"><h2>Read the scene</h2>
<ul class="legend">
<li><span class="swatch" aria-hidden="true"></span>Ego vehicle</li>
<li><span class="swatch observed" aria-hidden="true"></span>Observed track</li>
<li><span class="swatch unobserved" aria-hidden="true"></span>Unobserved track</li>
</ul><p>Hover a vehicle for its track ID. Select <strong>Tracks</strong> to show individual IDs and toggle their visibility.</p></section>
<section class="context-section"><h2>Playback</h2><div class="window">$start&ndash;$end s</div>
<p>Press <strong>Play</strong> or drag the timeline. Position, speed and AEB state update together.</p>
<p>TTC is time to collision. “Unavailable” means no TTC value is provided for that frame.</p></section>
<details><summary>Scenario identifier</summary><code>$identity</code></details>
</aside></div>
<footer role="contentinfo"><p>$notice</p></footer>
</main></body></html>
""")


def replay_page(plot: str, title: str, start: float, end: float, notice: str) -> str:
    """Keep scenario identity in normal document flow; escape all contextual text."""
    scenario, _, configuration = title.partition(" / ")
    labels = (configuration or "Not specified").removeprefix("coalition-").split("+")
    return PAGE.substitute(
        plot=plot,
        heading=escape(scenario.replace("_", " ").capitalize()),
        configuration="".join(f"<li>{escape(label.replace('_', ' '))}</li>" for label in labels),
        identity=escape(title),
        start=f"{start:.1f}",
        end=f"{end:.1f}",
        notice=escape(notice),
    )
