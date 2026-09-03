# perception-error-to-aeb

P3：在同一組 nuPlan 場景上，感知錯誤如何經由一個確定性的自動緊急煞車（AEB）
控制器傳遞成安全影響。

[English](README.md)

## 目前狀態

已有固定版本的容器、鎖定的環境、私有檔案防護與確定性的驗證閘門。**尚未存在任何
模擬、任何 cohort、任何結果。** 之後出現在這裡的每一個數字都必須追溯到凍結的
cohort manifest、run record 與 artifact 雜湊；在那之前，這裡沒有數字。

## 研究問題

在完全相同的 nuPlan 場景、初始狀態、路徑、名義控制器、模擬步長與終止條件下，
dropout、定位與形狀誤差、觀測延遲、追蹤斷裂，各自以及交互作用，如何改變 AEB 的
碰撞避免、漏煞與誤煞、舒適度與介入時長？

本專案刻意不訓練任何模型。學習式的規劃器會把本專案要計算的歸因混淆掉。

## 固定的執行環境

- 以 `python:3.9.19-slim-bookworm` 為基底的 Linux 容器，以 digest 固定。
- nuPlan devkit 固定在 commit `e9241677997dd86bfc0bcd44817ab04fe631405b`，
  以該 commit 的 URL 安裝。
- 只用世界狀態、資料庫與地圖。沒有感測器資料、沒有相機回放、沒有 GPU。
- 10 Hz 模擬、4 秒預測視野、閉迴路 ego 與不反應的紀錄代理人。

## 操作方式

所有指令都在容器內執行。

```bash
docker compose build
docker compose run --rm dev uv run --frozen python -m aebrisk.dev verify
docker compose run --rm dev aeb-risk --help
```

驗證閘門依序執行：私有檔案防護、格式檢查、lint、型別檢查、測試、100% 語句與
分支覆蓋率、JSON schema 契約、文件連結檢查。失敗時先修第一個失敗的階段，不要
跳過或降低門檻。

## 邊界

- nuPlan 資料授權給帳號持有人，不會進入本 repository。容器內的資料集掛載都是唯讀。
- AEB 門檻、錯誤嚴重度與場景家族對應由核准的協定固定。改動任何一項都是新的協定版本。
