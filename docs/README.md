# 文件導覽

平常先讀研究首頁，再依目的選擇下列文件；不需要逐份閱讀全部驗證歷史。

| 目的 | 文件 |
| --- | --- |
| 看目前的分析證據 | [證據索引](evidence/README.md) |
| 重現聚合結果、查原始雜湊 | [分析重現](verification/analysis-reproduction.md) |
| 理解模擬上限、終止條件與碰撞分類 | [模擬契約](simulation-contract.md) |
| 理解資料選擇、限制與授權 | [資料卡](dataset-card.md)、[證據授權](evidence/nuplan_aeb_v2-NOTICE.md) |
| 追查每一個數值的來源 | [claims registry](claims.yaml) |

## 驗證紀錄何時需要讀

下列是特定時間、環境或階段的驗證證據，不是下一步施工指令。保留原有日期與結果，
避免把舊紀錄改寫成新的成功紀錄。

| 問題 | 紀錄 |
| --- | --- |
| 基底 image 的 digest 從哪裡來？ | [容器基底](verification/container-base.md) |
| 鎖定環境曾如何獨立重建？ | [環境重建](verification/reproducible-build.md) |
| nuPlan 資料掛載與篩選如何確認？ | [資料預檢](verification/nuplan-preflight.md) |
| 正式模擬前，真實資料 smoke 發現什麼？ | [模擬 smoke](verification/simulation-smoke.md) |
| 共同 cohort 技能如何測試？ | [RED](verification/skills/running-common-cohort-aeb-studies/red.md)、[GREEN](verification/skills/running-common-cohort-aeb-studies/green.md) |
| 歸因文字審核技能如何測試？ | [RED](verification/skills/auditing-aeb-error-attribution/red.md)、[GREEN](verification/skills/auditing-aeb-error-attribution/green.md) |

施工順序由此 repo 外的工作區主計畫管理，私有 handoff 不放入公開 repository。
`README.md` 與 `README.zh-TW.md` 的語言命名會在展示交付時統一，避免目前的連結被中途改名打斷。
