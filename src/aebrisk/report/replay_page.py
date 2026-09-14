"""Traditional Chinese document shell around the native replay controls."""

from html import escape
from string import Template

PAGE = Template("""<!doctype html>
<html lang="zh-TW">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>情境重播 · $heading · AEB</title>
<style>
:root{color:#142033;background:#f3f5f6;font-family:"Microsoft JhengHei","PingFang TC","Segoe UI",sans-serif;font-size:18px;}
*{box-sizing:border-box;}body{margin:0;line-height:1.65;}
.replay-shell{max-width:1320px;margin:0 auto;padding:24px 32px 36px;}
.page-heading{display:flex;align-items:center;justify-content:space-between;gap:24px;padding:18px 24px;background:#10243a;color:#fff;border-radius:12px;}
h1{font-size:30px;line-height:1.3;font-weight:700;letter-spacing:.04em;margin:0;}
.scenario-name{margin:4px 0 0;font-size:16px;color:#cbdde8;overflow-wrap:anywhere;}
.project-name{font-size:17px;color:#cbdde8;white-space:nowrap;}
.scenario-bar{display:flex;align-items:center;gap:16px;padding:16px 0;}
h2{font-size:18px;line-height:1.5;margin:0;font-weight:700;}
.scenario-bar h2{flex:none;}
.configuration{list-style:none;padding:0;margin:0;display:flex;flex-wrap:wrap;gap:8px;}
.configuration li{font-size:16px;line-height:1.4;color:#254c62;background:#fff;border:1px solid #cddbe3;border-radius:4px;padding:5px 10px;overflow-wrap:anywhere;}
.replay-workspace{background:white;border:1px solid #cddbe3;border-radius:12px;}
.scene-key{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:14px 24px;background:#f8fafc;border-bottom:1px solid #dfe8ed;border-radius:12px 12px 0 0;flex-wrap:wrap;}
.scene-key p{margin:0;color:#425d70;font-size:16px;}
.legend{list-style:none;padding:0;margin:0;display:flex;flex-wrap:wrap;gap:14px 24px;}
.legend li{display:flex;align-items:center;gap:10px;font-size:16px;}
.swatch{width:24px;height:12px;flex:none;border:2px solid #254fc4;background:rgba(37,79,196,.22);}
.swatch.observed{border-color:#087f8c;background:rgba(8,127,140,.14);}
.swatch.unobserved{border-color:#b65a17;border-style:dashed;background:transparent;}
.replay-canvas{min-width:0;padding:0 12px 12px;overflow-x:auto;border-radius:0 0 12px 12px;}
.replay-status{display:flex;flex-wrap:wrap;gap:10px 32px;padding:10px 24px 0;}
.replay-status div{display:flex;align-items:baseline;flex-wrap:wrap;gap:4px 10px;min-width:150px;}
.replay-status dt{font-size:16px;color:#425d70;}
.replay-status dd{margin:0;font-size:20px;font-variant-numeric:tabular-nums;color:#142033;}
.replay-canvas .gtitle{visibility:hidden;}
.replay-context{display:grid;grid-template-columns:1fr 1fr;gap:24px 48px;padding:24px 0;color:#425d70;}
.replay-context p{font-size:16px;margin:8px 0 0;max-width:64ch;}
.replay-context h2{color:#142033;}
details{font-size:16px;grid-column:1/-1;border-top:1px solid #cddbe3;padding-top:16px;}summary{cursor:pointer;color:#254c62;}
summary:focus-visible{outline:3px solid #087f8c;outline-offset:4px;}
code{display:block;margin-top:12px;white-space:normal;overflow-wrap:anywhere;font-size:15px;}
footer{border-top:1px solid #cddbe3;padding-top:18px;color:#425d70;font-size:15px;}
footer p{max-width:90ch;margin:0;}
@media(max-width:700px){.replay-shell{padding:20px 14px 28px;}.page-heading{align-items:flex-start;flex-direction:column;gap:12px;}.project-name{font-size:16px;}.scenario-bar{align-items:flex-start;flex-direction:column;gap:8px;}.scene-key{padding:16px;}.replay-context{grid-template-columns:1fr;gap:20px;}.replay-canvas{padding:0 0 8px;}h1{font-size:28px;}}
</style>
</head>
<body><main class="replay-shell">
<header class="page-heading"><div><h1>情境重播</h1><p class="scenario-name">$heading</p></div><div class="project-name">Perception error → AEB</div></header>
<section class="scenario-bar" aria-label="故障設定"><h2>故障設定</h2><ul class="configuration">$configuration</ul></section>
<div class="replay-workspace">
<div class="scene-key"><ul class="legend">
<li><span class="swatch" aria-hidden="true"></span>自車 Ego</li>
<li><span class="swatch observed" aria-hidden="true"></span>已觀測軌跡</li>
<li><span class="swatch unobserved" aria-hidden="true"></span>未觀測軌跡</li>
</ul><p>重播範圍 $start&ndash;$end s · 單位 m</p></div>
<dl class="replay-status" aria-label="目前重播狀態" style="margin:0">
<div><dt>時間 / AEB</dt><dd id="replay-time">載入中</dd></div>
<div><dt>速度</dt><dd id="replay-speed">載入中</dd></div>
<div><dt>TTC</dt><dd id="replay-ttc">載入中</dd></div>
</dl>
<section class="replay-canvas" aria-label="情境幾何與重播控制">$plot</section>
</div>
<aside class="replay-context" aria-label="重播說明">
<section><h2>操作說明</h2><p>按「播放」或拖曳時間軸&#xff0c;查看位置、速度與 AEB 狀態。滑鼠移到車輛上可查看 track ID&#xff1b;按「軌跡」展開清單&#xff0c;可切換個別軌跡的顯示。</p></section>
<section><h2>如何判讀</h2><p>TTC 是預估碰撞時間&#xff1b;「無可用數值」表示該幀未提供 TTC。虛線標示未觀測軌跡&#xff0c;座標是 scenario-local frame&#xff0c;不代表攝影機影像或道路地圖。</p></section>
<details><summary>查看完整 scenario ID 與授權原文</summary><code>$identity</code><p>$notice</p></details>
</aside>
<footer><p>此頁為衍生幾何視覺化&#xff0c;不含感測器影像或地圖資料。nuPlan 衍生幾何依 CC BY-NC-SA 4.0 與 Motional 條款分享&#xff1b;Plotly.js 採 MIT 授權。</p></footer>
</main></body></html>
""")


def replay_page(plot: str, title: str, start: float, end: float, notice: str) -> str:
    """Keep contextual text escaped and the scientific identifier unchanged."""
    scenario, _, configuration = title.partition(" / ")
    labels = (configuration or "未指定").removeprefix("coalition-").split("+")
    return PAGE.substitute(
        plot=plot,
        heading=escape(scenario),
        configuration="".join(f"<li>{escape(label)}</li>" for label in labels),
        identity=escape(title),
        start=f"{start:.1f}",
        end=f"{end:.1f}",
        notice=escape(notice),
    )
