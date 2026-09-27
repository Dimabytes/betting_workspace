# train-luna — training, evaluation, inference parity
Status: FINAL

## Summary

- **S2 — research evaluation reuses its early-stopping set.** The 613-map backtest manifest names the research catalog and the same validation dataset used to choose each member’s tree count. This makes the backtest a selected-on-validation result. An inner-split refit found no measurable forecast-metric inflation on the latest maps, but I did not run a full strategy backtest to measure dollar impact.
- **S3 — the fair is high by about 1.9¢ when Radiant market probability is at least 0.85.** This is 11,027 labeled seconds across 80 maps and 67 event series. The fair is used as a lower bound on the SELL quote, so it can leave Radiant inventory resting above the observed five-minute market value when that bound is active. The resulting fill/PnL effect is unmeasured.
- **No S1 found** in target construction, source-lag joins, member sampling, metadata hashes, split identity, or model-server prediction parity.
- After market second 600, research-model bias is small and not statistically distinguishable from zero, but forecast improvement over a no-move estimate falls to 0.093¢ for seconds 600–900 (event-cluster 95% CI −0.045¢ to +0.238¢). The data do not show a material late fair bias; they show weaker late-game edge.

## Findings table

| ID | Severity | Layer | Title | Confidence | Estimated impact |
|---|---|---|---|---|---|
| train-luna-F1 | S2 | Train / evaluation / backtest | Validation labels select the research trees and score the same backtest maps | Verified mechanism; measured metric optimism not detected | Dollar impact unknown; the published validation score is not an untouched estimate. Latest-map forecast-score difference was −0.004¢/row, 95% event-cluster CI −0.028¢ to +0.020¢. |
| train-luna-F2 | S3 | Model calibration / live SELL pricing | High-probability Radiant fair overstates the 300-second midpoint | Verified in research artifacts; production diagnostic is in-sample | +1.916¢ mean fair residual for 3.2% of scored rows; actual quote binding and PnL impact were not measured. |

## Findings detail

### train-luna-F1 — validation labels select the research trees and score the same backtest maps

**Where.** `src/shared/utils/gbm.py:174-190` applies early stopping to the supplied validation set. `src/train_model/train_model.py:188-212` passes the fixed validation rows into every research member fit, then predicts and scores those same rows. The current Dota LIVE seed-0 manifest names research model `20260924T183856Z`, selects 613 matches, and points at the same validation parquet and hash (`data/backtests/dota_maker/validation_join_delta02_x015_cut480_p4_archive-s3-20260924/seed0/manifest.json:40-57`).

**Mechanism.** Each member’s best tree count is selected by validation L1 loss. The model’s saved metrics and the research-model backtest then reuse those outcomes. This does not put validation labels into feature rows, but it makes the final evaluation adaptive to the evaluation set. Thresholds and trading outcomes can be more sensitive to this reuse than aggregate forecast MAE.

**Evidence.** Running the current research catalog through the training metric path reproduced `model.json` exactly: 342,444 labeled rows, 7.8075¢ no-move MAE, 7.5645¢ model MAE, +0.2430¢ MAE gain, +1.6178¢ directional markout, and +0.1168¢ model bias. The MAE-gain 95% interval was +0.1512¢ to +0.3380¢.

I also ran a small chronological inner-split fit, with 1,250 earlier train maps for fitting, 313 later train maps for tree selection, and a refit on each member’s canonical 90% sample of all 1,563 train maps. The 10 member counts averaged 58.6 trees (42–84), versus 39.4 (33–62) in the published research ensemble. On the latest 40% of validation maps, the published ensemble gained +0.3009¢ per labeled row and the inner-split refit gained +0.2965¢. The paired difference, honest refit minus published, was −0.0044¢ with a 95% event-cluster interval of −0.0277¢ to +0.0203¢. This small experiment found no positive forecast-metric optimism in that slice; it cannot show that backtest trading PnL is unbiased.

**Impact.** The 613-map backtest is not an untouched estimate of strategy performance, and the saved research metrics should not be treated as fully independent evidence for promotion. I cannot give a dollar estimate without a strategy-level replay; no full backtest was run under the audit rules.

**Confirm or fix.** Select tree counts on a chronological inner split drawn only from the training period, freeze the model and policy, then report one score on later maps that were not used for selection. Keep the current inner-split comparison as a diagnostic, not as a substitute for that untouched forward evaluation.

**History.** `git blame` attributes the current early-stopping helper to `d17f0d5f`; the same-validation fit/score path is present in the current research trainer (the member call is attributed to `74d96e2a`).

### train-luna-F2 — high-probability Radiant fair overstates the 300-second midpoint

**Where.** The research fair is `clip(current_mid + predicted_delta)` (`src/shared/utils/gbm.py:150-164`); live inference applies the same construction (`src/trader/model_server.py:167-177`). When the selected token’s best ask is below its fair, SELL pricing raises the quote to the fair (`src/strategy/quoting.py:519-529`).

**Mechanism.** For validation rows with current `market_p_radiant >= 0.85`, the research model’s Radiant fair exceeded the realized five-minute midpoint by a mean 1.916¢. The model therefore has a persistent positive fair residual in this regime. The normal BUY entry cap is below 0.85, so the direct strategy exposure is chiefly an existing Radiant position whose fair bound raises its SELL ask, if the bound is above the current ask.

**Evidence.** The research evaluation subset had 11,027 labeled rows from 80 maps / 67 event series, 3.2% of the 342,444 labeled rows. The event-cluster 95% interval for fair-minus-future bias was +0.877¢ to +3.085¢. MAE gain over the no-move estimate was only +0.209¢, with a 95% event-cluster interval of −0.333¢ to +0.754¢, so a forecast-error benefit in this regime was not established. The separately fit research no-XP artifact showed the same direction (+1.776¢ bias) on the same validation set. Production and production-no-XP diagnostics on these validation rows were also positive (+1.516¢ and +1.438¢), but those rows contributed to production training and are not independent evidence.

The calibration script used `market_scenario_report`’s definition: predicted fair minus the 300-second realized midpoint, with row-weighted estimates and event-series resampling. Source: `src/shared/utils/market_scenario_report.py:128-192,195-256`; sell-price mechanism: `src/strategy/quoting.py:519-529`.

**Impact.** If the model fair binds over the book ask, Radiant SELL quotes can be roughly 1.9¢ too high on average in this subset. That can delay exits or leave inventory resting; the validation dataset has no order-book ask or fills to measure how often this happened or its cash cost. The subset is bounded, so this is S3 rather than a demonstrated broad loss.

**Confirm or fix.** On an untouched map set, measure how often the fair bound overrides best ask for held Radiant inventory and compare fill/settlement outcomes. Only then consider regime calibration or reducing the fair floor for this state; do not calibrate against these same rows.

## Architecture / performance / debuggability notes

1. **Research and production have distinct roles.** Research trains on 1,563 maps and publishes quality metrics; production trains on the union of train and validation minute rows (2,225 maps here) and publishes `metrics: null`. `src/prepare_dataset/prepare_dataset.py:274-285` constructs that union. This final retrain is intentional; the limitation is that the deployed production artifacts have no independent quality score. `src/train_model/train_model.py:269-317,349-382` uses the rounded mean research tree count as the fixed production count: XP 39 (research mean 39.4), no-XP 32 (mean 32.5, Python `round`). The Dota primary catalog is production XP; production no-XP is the Oddin satellite (`src/trader/game_profile.py:54-67`). Do not present research metrics as a holdout score for either production model.
2. **Validation metrics are second-weighted, with event-series CIs.** The point estimates average eligible market seconds, so maps with more eligible seconds contribute more. The 2,000-replicate intervals resample `event_id` clusters while retaining row weights, which accounts for within-map and within-series dependence (`src/shared/utils/market_scenario_report.py:60-61,128-192`). `model.json` stores an MAE-gain CI but only a directional-markout point estimate; the companion `validation_scenarios.csv` stores the directional CI. Its full-window row gives +1.233¢ to +2.028¢.
3. **Training and evaluation use different time granularities.** Training states are minute boundaries from −60 through 540 (`src/prepare_dataset/stratz_seconds.py:200-224`); validation reconstructs exact-second states and joins market second `M` to game state `M−10` (`src/prepare_dataset/prepare_dataset.py:192-220`). The latest source time in scored validation rows is therefore much finer than the training grid. Live inputs come from Steam/GRID/Oddin; empirical feed-distribution parity is outside this brief.
4. **Training is reproducible and map-grouped.** The objective is L1 regression on raw `future_mid_300s − current_mid`; there are no explicit sample weights. Each ensemble member samples 90% of distinct match IDs without replacement, then includes all selected map rows (`src/shared/utils/gbm.py:26-34,150-154,216-245`). `radiant_win` is present for outcome reporting but is not in the 12-feature list (`src/shared/utils/gbm.py:37-53`).

## Checked and OK

- **Label and timing:** `build_market_second_rows` anchors the current quote at the game-state timestamp and looks up the future quote at +300 seconds (`src/market_data/build_market_data.py:113-134`; `src/shared/utils/telonex_book.py:268-277`). Minute training uses market second `S+10`; validation uses state second `M−10`; `TRAIN_LAG_SECONDS` and all four catalog metadata fields are 10 (`src/prepare_dataset/prepare_dataset.py:137-166,192-220`; `src/shared/constants/dataset.py:20-23`). No train/serve lag discrepancy found.
- **Tree count and training artifact:** the published XP research member trees are `[33,35,30,41,35,37,38,42,41,62]`; production uses 39 fixed trees/member. No-XP research counts average 32.5; production uses 32. This follows `GbmPredictor.num_trees()` and `train_model.main`, rather than an independently fitted production stop set.
- **Hashes and map identity:** `train_dataset_sha256` matches the current training parquet for all four catalogs; both research `validation_dataset_sha256` values match the current validation parquet. Production validation hashes are null by design. Train and validation have 1,563 and 717 unique match IDs, with no ID or start-time overlap. The 3,207-row catalog has unique `match_id`, `condition_id`, and `market_slug`; exact full-sequence hashes found no duplicate maps within either dataset. An aligned cross-split comparison of training minute rows to validation rows at the matching `S+10` market second found zero identical map signatures.
- **Features and inference:** all train and production training features and labels are finite. Validation feature rows with `market_status=ok` are finite. I loaded research, production, research-no-XP, and production-no-XP with both `gbm.load_predictor` and `model_server.load_model`, then compared 512 identical rows per catalog. Feature order, mean-of-members aggregation, float64 input, and clipping matched; max absolute delta difference was `1.39e-17`, max fair difference `1.11e-16`. No decision-scale inference skew found.
- **CIs and saved metrics:** recomputed research metrics match `model.json` and the final full-window scenario row. The MAE-gain CI and directional CI resample event series, not individual seconds. The stored directional CI is present in the CSV even though it is omitted from `model.json`.
- **Regimes and late fair:** on research rows at market seconds 0–540, predicted versus realized mean delta was +0.352¢ versus +0.237¢; MAE gain was +0.265¢ (95% event CI +0.162¢ to +0.371¢). Pre-horn −60–0 gain was +0.010¢ (CI −0.060¢ to +0.084¢), so no pre-horn edge was established. At 540–900, predicted versus realized mean delta was +0.591¢ versus +0.363¢, bias +0.228¢ (CI −0.510¢ to +0.983¢), and gain +0.118¢ (CI −0.009¢ to +0.255¢). At 600–900, predicted versus realized was +0.583¢ versus +0.383¢, bias +0.200¢ (CI −0.618¢ to +1.013¢), gain +0.093¢ (CI −0.045¢ to +0.238¢). Production diagnostic bias in 600–900 was −0.014¢, but production is trained on validation maps. No strong late bias is demonstrated; useful late edge is weak/uncertain.
- **Net-worth regimes:** the upper and lower radiant-NW-advantage deciles each improved on no-move: +0.704¢ for NW advantage 1,211–8,004 (34,237 rows / 277 maps) and +0.592¢ for −7,937 to −1,266 (34,249 rows / 276 maps). The extreme absolute tail `|radiant_nw_adv| >= 5,000` was sparse (1,107 rows / 28 maps); its −0.753¢ MAE gain had a 95% event-cluster CI of −1.551¢ to +0.316¢ and 50.2% direction accuracy. Treat that tail as inconclusive, not a proven failure.
- **No duplicate-series issue:** series IDs can span date-based map splits; that is not treated as the same map. Exact map identity checks above found no duplicate.

## Open questions / Needs from VPS

- **Needs from VPS:** none.
- To convert the high-probability fair residual into money, inspect held-position SELL rows where `fair > best ask`, and compare fills and terminal outcomes on maps excluded from all model selection. This audit did not run tests, a full backtest, training publication, or any VPS action.
- `uv run` initially hit a sandbox read error in the default user cache. The analysis was completed from the product repo with `UV_CACHE_DIR` inside this audit work directory and `uv run --offline --no-sync`; no dependency download or network access was used.
