# perception-error-to-aeb

**在同一批 nuPlan 場景與固定 AEB policy 下，dropout、定位與形狀誤差、延遲及 track instability 會如何傳遞成碰撞與煞車行為？**

[English](README.en.md) · [證據導覽](docs/README.md)

這個研究不訓練模型。它固定場景、初始狀態、路徑、名義控制器、模擬頻率與終止規則，只改變 AEB 收到的觀測。正式模擬與衍生證據已完成；repository 尚未公開發布。

## 觀察結果

共同有效 cohort 的 `common_valid_tokens` = 344。<!-- claim: p3.evaluation.common_valid_tokens --> 每個設定都使用同一批 token；表內的 scenario-replicates 是重複執行，不是獨立樣本。

| 設定 | Scenario-replicates | 計入碰撞 | `contacts_not_at_fault` | 實測曝光（秒） |
| --- | ---: | ---: | ---: | ---: |
| no AEB | `scenarios` = 1032 <!-- claim: p3.baseline.scenarios.no_aeb --> | `collisions` = 204 <!-- claim: p3.baseline.collisions.no_aeb --> | `contacts_not_at_fault` = 261 <!-- claim: p3.baseline.contacts_not_at_fault.no_aeb --> | `simulated_seconds` = 10945.5 <!-- claim: p3.baseline.simulated_seconds.no_aeb --> |
| oracle AEB | `scenarios` = 1032 <!-- claim: p3.baseline.scenarios.oracle_aeb --> | `collisions` = 39 <!-- claim: p3.baseline.collisions.oracle_aeb --> | `contacts_not_at_fault` = 1095 <!-- claim: p3.baseline.contacts_not_at_fault.oracle_aeb --> | `simulated_seconds` = 14901.900000000001 <!-- claim: p3.baseline.simulated_seconds.oracle_aeb --> |
| 全通道 medium coalition | `scenarios` = 1032 <!-- claim: p3.coalition.scenarios.coalition-dropout-localization_shape-latency-track_instability --> | `collisions` = 0 <!-- claim: p3.coalition.collisions.coalition-dropout-localization_shape-latency-track_instability --> | `contacts_not_at_fault` = 1150 <!-- claim: p3.coalition.contacts_not_at_fault.coalition-dropout-localization_shape-latency-track_instability --> | `simulated_seconds` = 15395.8 <!-- claim: p3.coalition.simulated_seconds.coalition-dropout-localization_shape-latency-track_instability --> |

Shapley 的 collision game 中，localization/shape 的觀察平均貢獻為 `collision_indicator` = -0.027374031007751935。<!-- claim: p3.shapley.collision_indicator-values-localization_shape -->

Shapley 的 intervention-duration game 中，同一 channel 的觀察平均貢獻為 `intervention_duration_s` = 4.0670219638242875。<!-- claim: p3.shapley.intervention_duration_s-values-localization_shape -->

這兩個值使用不同單位與尺度，只是 full-minus-empty `coalition-none` 的觀察平均分解，不能相加、比較高低或解讀成顯著性。零嚴重度 tracker 仍以位置差分估計速度，因此 `coalition-none` 不是 oracle。現有證據沒有 Shapley 或設定差值的信賴區間，也不支持 channel 排名。

`bicycle_or_vru` 的 `valid_tokens` = 44 <!-- claim: p3.family-interventions.bicycle_or_vru.oracle_aeb.valid_tokens -->，低於 protocol 的 `evaluation_per_family` = 100。<!-- claim: p3.family-interventions.evaluation_per_family --> 這兩個值只說明樣本不足，不是 efficacy claim；首頁不對該 family 的介入率提出正式結論。

## 圖表與回放

- [Shapley 觀察平均貢獻](docs/figures/shapley-contributions.svg)把 collision indicator 與 intervention duration 分成不同單位的 panel。
- [各 family 介入事件率](docs/figures/intervention-rates-by-family.svg)以 scenario-replicates 為分母，沒有借用全體 cohort 的 bootstrap error bar。
- 預先固定的 median-nearest 展示各有 oracle、empty coalition 與 full coalition 的 replicate-zero 版本：

| Family | Oracle | Empty coalition | Full medium coalition |
| --- | --- | --- | --- |
| lead/stopping | [開啟](docs/evidence/nuplan_aeb_v2/replays/lead_or_stopping--oracle_aeb.html) | [開啟](docs/evidence/nuplan_aeb_v2/replays/lead_or_stopping--coalition-none.html) | [開啟](docs/evidence/nuplan_aeb_v2/replays/lead_or_stopping--coalition-dropout+localization_shape+latency+track_instability.html) |
| cut-in/crossing | [開啟](docs/evidence/nuplan_aeb_v2/replays/cut_in_or_crossing--oracle_aeb.html) | [開啟](docs/evidence/nuplan_aeb_v2/replays/cut_in_or_crossing--coalition-none.html) | [開啟](docs/evidence/nuplan_aeb_v2/replays/cut_in_or_crossing--coalition-dropout+localization_shape+latency+track_instability.html) |
| pedestrian/crosswalk | [開啟](docs/evidence/nuplan_aeb_v2/replays/pedestrian_or_crosswalk--oracle_aeb.html) | [開啟](docs/evidence/nuplan_aeb_v2/replays/pedestrian_or_crosswalk--coalition-none.html) | [開啟](docs/evidence/nuplan_aeb_v2/replays/pedestrian_or_crosswalk--coalition-dropout+localization_shape+latency+track_instability.html) |
| bicycle/VRU | [開啟](docs/evidence/nuplan_aeb_v2/replays/bicycle_or_vru--oracle_aeb.html) | [開啟](docs/evidence/nuplan_aeb_v2/replays/bicycle_or_vru--coalition-none.html) | [開啟](docs/evidence/nuplan_aeb_v2/replays/bicycle_or_vru--coalition-dropout+localization_shape+latency+track_instability.html) |

回放是從指定 replicate 重新建構的衍生 timeline，使用 scenario-local 原點與匿名 actor ID；它不是 raw trajectory export、地圖畫面或 nuBoard log。每個重建結果先與凍結 formal record 共有的碰撞、接觸、曝光與介入量測逐項比對；formal schema 不保存最終 pose 或 speed，兩者改以同一次 fresh simulator outcome 檢查。

## 解釋邊界

碰撞分類會排除 ego 已停止，或位於 ego 後方且物件速度大小較大的接觸。這是研究內的 operational rule，不是 longitudinal closing-velocity 判定、完整 nuPlan 等價證明或法律責任認定。零個計入碰撞不等於零接觸、較佳感知或實車安全。

protocol 要求的 maximum horizon 是 15 秒上限；到碰撞、路徑結束、資料結束或上限即停止，所以表中逐設定列實測曝光。logged actors 不會對 ego 反應。本版沒有 sensor pixels、地圖畫面、nuBoard log、每距離碰撞率，也只測一種固定 AEB policy。每 100 km 碰撞率為未提供。

## 重現

所有 Python 指令都在固定的 Linux container 中執行：

```powershell
$env:NUPLAN_DATA_ROOT='D:/datasets/nuplan'
docker compose run --rm dev uv run --frozen aeb-risk summarize-families --results-dir artifacts/formal/nuplan_aeb_v2 --manifest artifacts/manifests/nuplan_aeb_v2/evaluation.json --protocol configs/protocols/nuplan_aeb_v2.yaml --output-dir docs/evidence/nuplan_aeb_v2
docker compose run --rm dev uv run --frozen aeb-risk figures --evidence-dir docs/evidence/nuplan_aeb_v2 --output-dir docs/figures
docker compose run --rm dev uv run --frozen aeb-risk report --claims docs/claims.yaml --artifacts-dir docs/evidence/nuplan_aeb_v2 --output-dir site
docker compose run --rm dev uv run --frozen python -m aebrisk.dev verify
```

完整 provenance、hash 與解釋限制見[分析重現紀錄](docs/verification/analysis-reproduction.md)。資料受 nuPlan/Motional 條款與 [CC BY-NC-SA 4.0](docs/evidence/nuplan_aeb_v2-NOTICE.md) 規範；原始碼使用 MIT license。

同一 portfolio 的 [P1 driving-risk-metrics](https://github.com/kuotunyu/driving-risk-metrics)提供評估與不確定性工具；[P2 bev-calibration-lab](https://github.com/kuotunyu/bev-calibration-lab)研究相機/LiDAR calibration fault，目前尚未公開發布。
