# Two-sided + merge market making for Dota: theory and quoting rule
Status: FINAL

Scope: brief `briefs/swe-theory.md`. All code repos read-only. Empirical numbers
computed from `esports-trader/data` tapes by scripts in `work/swe-theory/` (paths
and commands in each section). "t" = ticks ($0.01). Prices in $.

## Summary

- Verified on 365 Dota map tapes (`market_seconds`): 10 s mid move p50 = 0.5 t,
  p90 = 3.5 t, p95 = 5 t, p99 = 12.5 t; P(|Δ10s| ≥ 5 t) ≈ 5.5% ≈ one jump per
  3 min unconditional, 7× clustered in high-vol regimes (0.72/min vs 0.10/min).
  60 s move: p50 = 2.5 t, p90 = 9.6 t, p99 = 25.5 t.
- Verified on 40 map markets (`onchain_fills`, Sept–Oct 2026): aggressor flow
  during active minutes p25 = $170/min, p50 = $612/min, p75 = $1.3k/min,
  p90 = $2.8k/min; ~84% of aggressor volume is BUY-side — a two-BUY quote
  captures buy flow on *both* tokens through mint-matching.
- The structural identity (Polymarket docs + on-chain tape): BUY NO at `q` IS
  an ask on YES at `1−q`; pair cost `b+q<1` locked at the two execution prices;
  merge strictly dominates selling a pair by ≥ full spread + taker fee.
- Theory gives three quote components: reservation skew `−g·net` (Avellaneda–
  Stoikov / GLFT), half-spread `h = h0 + c_σ·σ̂ + c_jump·J` (Cartea–Jaimungal–
  Ricci: MMs without a short-term-alpha signal are adversely selected out of
  the market — our Δ̂ is exactly that signal), and a binary-settlement fix:
  remaining variance saturates at `p(1−p)`, not `σ²(T−t)` (Feil–Nendel 2026).
- Edge model: `E/share = h − α(1−f) + ρ`, ρ ≈ 0.19 t at p = 0.5. With α ≈
  1.5–2.5 t all-in adverse cost, h = 1 t needs f > ~0.4–0.6 (unlikely without
  kill gate + delta skew); h = 2–3 t is profitable across most of the grid at
  f = 0–0.25. Expected: +0.4…+2.4 ¢ per $1 of buy volume at p = 0.5 — vs
  Follow300's measured +4.0 t/bought-share (~$48/map net, seed0 LIVE backtest)
  on gated single-side buys.
- Trader calibration (his data): median market 110 fills / 40 min / $1.2k cost,
  ~3 fills/min, avg fill ~$12; pair_avg_cost < 1 in 68.8% of markets — the
  31% >1 are Glosten–Milgrom adverse-selection losses realized at market level.
- Recommended start: h0 = 1–2 t + vol/jump adders, band p ∈ [0.10, 0.90] per
  token, g = 2e-4·4p(1−p) $/share skew, s = 20–50 shares/side, N_max = 100–300
  shares, merge at min(yes,no) ≥ 20, kill gate = cancel victim bid (keep the
  winner-side bid — it is the *good* side of a jump), lift the 480 s cutoff.

## Findings

### 1. Theory: what the MM literature says, mapped to our setting

#### 1.1 Classical stack

**Glosten–Milgrom (1985)** — "Bid, ask and transaction prices in a specialist
market with heterogeneously informed traders", JFE 14(1):71–100.
DOI 10.1016/0304-405X(85)90044-3. Core result for us: a positive spread is
required even with *zero* inventory risk and *zero* processing cost, because a
fraction of fills come from informed traders; the competitive spread equals the
expected loss to the informed conditional on trade direction ("markout"). In
our market the informed flow is literal: takers reacting to a kill/objective
that our GRID feed shows 1–10 s late. → Spread must exceed the
fill-conditioned adverse move; kill gate + delta skew are tools to shrink that
conditional move.

**Kyle (1985)** — "Continuous auctions and insider trading", Econometrica
53(6):1315–1335. DOI 10.2307/1913210. Price impact λ is linear in signed
volume; for a quoter this says adverse selection scales with order size — one
more reason our per-level size s stays small (20–50 shares) and sizes shrink
with inventory rather than "defending" a level.

**Avellaneda–Stoikov (2008)** — "High-frequency trading in a limit order book",
Quantitative Finance 8(3):217–224. DOI 10.1080/14697680701381228, PDF
math.nyu.edu/~avellane/HighFrequencyTrading.pdf. Setup: mid follows diffusion
with vol σ, exponential utility γ, order arrivals Poisson with intensity
Λ(δ) = A·e^(−κδ) falling in distance-to-mid δ, horizon T. Results:

- reservation price `r = s − q·γσ²(T−t)` — inventory q shifts the whole quote
  pair linearly; the shift coefficient `g = γσ²(T−t)` has units $/share² and
  *decays to 0 at the horizon*;
- optimal half-spread `δ* = γσ²(T−t)/2 + (1/γ)·ln(1 + γ/κ)` — a risk term that
  shrinks toward settlement plus a liquidity term `≈1/κ` that does not;
- practical content: skew ≠ spread widening. To offload inventory you move the
  midpoint of your quotes, not their width.

**Guéant–Lehalle–Fernandez-Tapia (2013)** — "Dealing with the inventory risk:
a solution to the market making problem", Math. & Financial Economics
7:477–507. arXiv:1105.3115. Solves AS with a hard inventory bound Q: the HJB
reduces to a linear ODE system; asymptotic closed form for quotes; near the
bound the adding-side quote vanishes (their closed form δ(q) blows up → our
"pull the adding side at |n| ≥ N_max" is the discrete version of their smooth
result). Also: with hard caps, optimal quotes are *not* symmetric — the
reducing side stays tight to attract offsetting flow.

**Cartea–Jaimungal–Penalva (2015)** — "Algorithmic and High-Frequency Trading",
Cambridge UP, ch. 10 (market making): adds (i) market-order-induced midprice
jumps — optimal spread widens by the jump variance contribution, and quotes
must be repriced *on the event*, not after; (ii) a short-horizon drift signal
("alpha") entering the reservation price — quotes skew away from the predicted
move, exactly our `fair = mid + k·Δ`.

**Cartea–Jaimungal–Ricci (2018)** — "Algorithmic Trading, Stochastic Control,
and Mutually-Exciting Processes", SIAM Review 60(3). PDF:
oxford-man.ox.ac.uk (Algorithmic-Trading-Stochastic-Control-and-Mutually-
Exciting-Processes.pdf). Hawkes-excited market orders + short-term alpha.
Headline result: "HF traders who do not include predictors of short-term-alpha
in their strategies are driven out of the market because they are adversely
selected" — the single most relevant paper for us: our kill gate + Δ̂ are the
alpha predictors; without them two-sided quoting in a jumpy market is a known
losing structure. Also: arrival intensities cluster (self-exciting), matching
our measured 7× jump clustering — spread adders should be event-driven, not
constant.

#### 1.2 Prediction-market / settlement-specific work

**Feil & Nendel (2026)** — "Optimal Market Making in Prediction Markets",
arXiv:2607.17991v1 (20 Jul 2026). The closest model to ours: latent belief
diffusion mapped (logistically) into price ∈ (0,1), binary settlement at T
with a terminal penalty on leftover inventory. Their results (§4.3, Fig. 1–3,
Table 2):

- optimal spread *decreases* over most of the market's life but **widens near
  settlement for p near 0.5** — settlement variance p(1−p) is maximal there and
  there is no time to re-pair inventory;
- skew (quote mid − price) is positive below p = 0.5, negative above in their
  arrival-intensity specification — a pure flow-asymmetry effect, no inventory
  needed (sign is model-dependent; the magnitude lesson transfers: near the
  rails, symmetric quoting is not symmetric risk);
- skew is linear-ish in inventory and **vanishes as p(1−p) → 0** near the
  boundaries (risk literally disappears when price is pinned);
- inventory-aware quoting cut terminal |q| from 49 → 15 shares and VaR₅%
  from 32 → 4 vs a myopic spread-chaser, at ~1% PnL cost.

The consequence for our design: the inventory-skew coefficient should scale
with the *remaining binary variance* `p(1−p)`, not with `σ²(T−t)` — for a
settling martingale `Var[X_T|F_t] = p(1−p)` at all times; AS's `σ²(T−t)` is the
diffusion approximation that must saturate at that bound. Concretely:
`g(p) = g0·4p(1−p)` gives max skew at mid-price and ~zero at the rails.

**Othman–Sandholm family** — Hanson LMSR (2003/2007); Othman, Pennock, Reeves,
Sandholm, "A practical liquidity-sensitive automated market maker", ACM TEAC
1(3):14, 2013 (cs.cmu.edu/~sandholm/liquidity-sensitive market maker.EC10.pdf).
Cost-function AMMs are two-sided quoters whose price schedule is the gradient
of a convex cost — i.e., an inventory-skewed quoter with bounded worst-case
loss. Their bounded-loss boundary = our merge invariant: keep pair cost < $1
and the *paired* book can never lose. Also Brahma et al. 2012, "A Bayesian
Market Maker" (cs.rpi.edu/~magdon/ps/conference/predMarkets.pdf): a binary-mm
that learns beliefs from trade direction; documented tradeoff — fast jump
adaptation vs low equilibrium loss — argues for a separate jump channel (our
c_jump·J) rather than folding everything into vol.

**Practice / Polymarket-specific.** warproxxx/poly-maker (github.com/warproxxx/
poly-maker): two-sided BUY-YES+BUY-NO around microprice with inventory skew and
CTF merge — the exact structure we propose; the v1 README adds "in today's
market, this bot is not profitable" — naive two-sided quoting without an alpha
or a rewards edge is a documented failure mode; the fix per Cartea–Jaimungal–
Ricci is the alpha/feed gating we already have. Polymarket's own docs (accessed
2026-10-07) describe the same pattern for human MMs
(docs.polymarket.com/trading/market-making): "Buying NO at 0.48 is economically
equivalent to selling YES at 0.52", plus split/merge/redeem workflows
(docs.polymarket.com/trading/positions/manage).

### 2. Buy-both + merge vs sell — the formal equivalence

Setting: binary market, YES pays $1 iff event, NO pays $1 iff not; unified
CLOB; both our orders are post-only maker BUYs (we never cross).

**State-contingent equivalence.** Selling 1 YES at price `a` (requires holding
YES): cash `+a`, payoff `(a−1, a)` in states (YES wins, YES loses). Buying
1 NO at price `q = 1−a`: cash `−q`, payoff `(−q, −q+1) = (a−1, a)`.
Identical in every state. ∎ A NO-bid at `q` *is* a YES-ask at `1−q`.

So "BUY YES at b, BUY NO at q" = quoting bid `b` / ask `a = 1−q` on YES, with
effective spread `a − b = 1 − (b+q)`. If both legs fill at quote, holding
(yes=no=1) merges to exactly $1: locked margin `1 − (b+q) = 2h` for
half-spread `h` when quotes are symmetric around r.

**Why the flat maker can *only* quote the ask via BUY NO.** A resting SELL YES
locks YES shares, not cash; with zero YES inventory it cannot be posted.
Alternative: `splitPosition` ($1 → 1 YES + 1 NO, docs.polymarket.com/trading/
positions/manage) then sell the YES — but that (a) locks a full $1 per level
vs `q ≈ 0.5` for the NO-bid, (b) leaves you holding the NO = instant 1-share
imbalance — exactly the position you were trying not to take. So for a
market maker that starts flat, the ask side is *forced* to be a NO bid; this
is a collateral-model fact, not a convention. Capital check: resting BUY YES
locks `b`/share, resting BUY NO locks `q`/share; a symmetric quote pair locks
`b+q = 1−2h ≈ $0.94–0.98` per share-pair — slightly less than the $1 a
minted pair would need.

**Fees.** `sports_fees_v3`: taker pays `0.05·p(1−p)·qty` on the price of the
token *they* trade; maker pays 0 and receives rebate `0.15·0.05·p(1−p)·qty`
(verified in `esports-trader/src/backtest/postprocess.py:64-65,172-196` and
Gamma dumps: `feeSchedule {rate 0.05, rebateRate 0.15, takerOnly: true}`).
Both our legs are maker ⇒ each filled share earns `ρ(p) = 0.0075·p(1−p)`.
Per $1 deployed this is `0.0075·(1−p)` — *higher on cheap tokens* (0.68 ¢/$
at p = 0.10 vs 0.075 ¢/$ at p = 0.90): a small systematic tilt toward the
cheap-side bid, worth noting before anyone "fixes" an asymmetric fill mix.
In a mint-match (buy-vs-buy) the aggressor is the taker and pays the fee; the
resting order books the rebate — confirmed by the `mirrored` flag semantics in
`src/backtest/onchain_side.py:2-12` and `taker_fee` column in the tape.

**Merge vs sell (why merge is the natural exit).** Holding `y` YES at avg
`c_y` and `n` NO at avg `c_n`, `y ≥ n`:
- merge `n` pairs ⇒ cash `+n`, leftover `y−n` YES. Risk: none on the merged
  part, ever — $1/pair is contractually guaranteed at any game state.
- sell both at bids `b_y ≈ p − s/2`, `b_n ≈ 1−p − s/2` ⇒ cash
  `n·(b_y + b_n) = n·(1 − s)` — strictly worse than merge by `n·s` (the whole
  spread), *plus* taker fee `0.05·p(1−p)·(y+n)` if crossing as taker, *plus*
  you surrendered the residual's optionality to re-pair cheaply later.

Merge only loses to selling when `b_y + b_n > 1` (crossed book — an arb state
the CLOB resolves instantly). ⇒ Exit = merge; SELL is reserved for the
residual (unpaired) leg, and even that is cheaper via skewing quotes to attract
the offsetting fill than via crossing. This matches the trader: 22 SELLs vs
367,893 BUYs, 6,784 merges returning $9.24M — he virtually never sells; his
exits are merge + redeem ($0.61M residuals rode to settlement).

**Queue priority / mirrored matching.** Each resting order sits in its own
token's book at its own price; the operator mints when complementary BUYs sum
to ≥ $1 (docs.polymarket.com/concepts/prices-orderbook: "BUY YES at 0.60 +
BUY NO at 0.40 match; $1 collateral creates a complete set"). Priority is
price-time on the underlying orders (Polymarket US FIX spec states
price-time; the esports CLOB is documented the same way). A mirrored bid and a
native ask at the same effective YES price are different orders competing by
their own timestamps — no documented penalty for the mirrored representation
(likely, not explicitly documented; flag for live verification). Our
`onchain_fills` tape shows `mirrored` on ~50% of rows = mint-matches are half
of all fills — the mirror channel is first-class, not an edge case. Verified:
in one sample file, fills where the taker bought the sibling token make up
~51% of rows.

### 3. Edge decomposition and the expected-PnL table

Per filled share (one leg), in ticks:

```
E[pnl/share] = h  −  α·(1 − f)  +  ρ − c_ops
```

- `h` half-spread in ticks (quote at r ± h; merged pair margin = 2h);
- `α` all-in adverse cost per filled share = E[fair_move against us over the
  pairing horizon | fill] + residual-unwind drag on the fraction that never
  pairs. This is the Glosten–Milgrom term;
- `f` fraction of α removed by the forecast skew (`k·Δ`) and the kill gate;
- `ρ = 0.0075·p(1−p)` maker rebate = 0.1875 t/share at p = 0.5 (0.375 ¢/$);
- `c_ops` merge gas amortized ≈ $0.05/(M·$1) ≈ 0.05–0.25 t — drop for the
  envelope, keep in the live P&L.

Anchors for α:
- (verified) unconditional 10 s |Δmid|: mean 1.4 t, p90 3.5 t — but fills are
  *conditioned* on aggressors; conditional adverse drift is materially higher
  for a stale quote;
- (estimate) Feil–Nendel/myopic-MM logic + standard fill markouts put
  α ∈ 1–3 t for an ungated two-sided quoter;
- (verified) the trader's own margin: pair_avg_cost mean 0.975 ⇒ his realized
  `h_eff − α ≈ 1.25 t` per leg — consistent with h ≈ 2–3 t and α ≈ 1–1.7 t;
- (verified) **our own LIVE backtest (seed0, 848 maps, Follow300,
  fills.parquet — `seed0_fills.py`):** gated buy fills show markout_30s **+0.5 t**
  (qty-weighted) / **+1.0 t** (unweighted), markout_300s +3.0 t, mean rebate
  0.17 t/share (avg buy p ≈ 0.63 ⇒ formula checks out: 0.0075·0.63·0.37),
  net PnL 4.0 t/bought-share. I.e., on the fills the delta+kill gates *allow*,
  adverse selection is already fully removed and the drift is favorable — the
  gate is worth ≥ α + 0.5 t per share vs an ungated baseline;
- (verified) how much of short-horizon drift the 300 s model can see
  (`d300_vs_d60.py`,
  856k windows): corr(Δ_60s, Δ_300s) = 0.42, E[Δ_60s|Δ_300s] slope = 0.18,
  realized 300 s move explains ~17% of 60 s variance; sign agreement 62% when
  |Δ_300s| ≥ 2 t. So the forecast kills the *trend component* of α but not
  the jump component — the kill gate covers that part. Realistic combined
  f ≈ 0.25–0.5 for delta+gate conditioning, higher conditioned on |Δ̂| ≥ 2 t.

Table (per share, ticks; per-$ = ×2 at p = 0.5). Script: `edge_table.py`.

```
E[pnl/share, ticks]          alpha:   1.0    1.5    2.0    2.5    3.0
f=0.00  h=1                          +0.19  -0.31  -0.81  -1.31  -1.81
        h=2                          +1.19  +0.69  +0.19  -0.31  -0.81
        h=3                          +2.19  +1.69  +1.19  +0.69  +0.19
f=0.25  h=1                          +0.44  +0.06  -0.31  -0.69  -1.06
        h=2                          +1.44  +1.06  +0.69  +0.31  -0.06
        h=3                          +2.44  +2.06  +1.69  +1.31  +0.94
f=0.50  h=1                          +0.69  +0.44  +0.19  -0.06  -0.31
        h=2                          +1.69  +1.44  +1.19  +0.94  +0.69
        h=3                          +2.69  +2.44  +2.19  +1.94  +1.69
```

Break-even forecast fraction for h = 1 tick: `f* = 1 − (1+ρ)/α`:
α = 1.0/1.5/2.0/2.5/3.0 t ⇒ f* = −0.19 / 0.21 / 0.41 / 0.53 / 0.60.
**h = 1 needs f ≈ 0.4–0.6 in the realistic α range — achievable only if the
kill gate plus k·Δ skew remove half the adverse moves; do not count on it for
v1. h = 2 is the robust choice; h = 3 still positive nearly everywhere but
trades volume for edge.**

Two-side volume note: per-$ figures at p=0.5 double the per-share numbers
(2 shares/$). So h=2, α=2, f=0.25 ⇒ +1.38 ¢/$; the trader realizes ~2.9 ¢/$
gross on CS2/LoL — our per-$ is the right order of magnitude if we quote
inside a 3 t spread at h≈2 and keep α≈1.5.

Capacity (verified flow): active-minute taker flow p50 = $612/min ⇒ at 15–25%
capture we buy ~$90–150/min on hot maps, ~$25–40/min on median maps ⇒
~$1–5k buy volume per map ⇒ +0.4…+2.4 ¢/$ ⇒ **~$10–80/map expected** at the
plateau, scaling sub-linearly with size (queue share is the binding
constraint — ~30 competing makers, best-level depth ~$40).

### 4. The concrete quoting rule

State per market: YES book (token_0 = radiant), NO book, `m` = YES mid —
prefer **microprice** `(bid·a_sz + ask·b_sz)/(a_sz+b_sz)` whenever spread > 2 t
(p50 spread is 3 t, so microprice shifts fair by up to ~1.5 t toward the
heavier side — free information); `Δ̂` = model 300 s mid-forecast
(re-anchored 0.25 s), `n` = yes_shares − no_shares (signed, post-fill),
paired `u = min(yes,no)`, time/phase flags.

```
fair       = clip(m + k·Δ̂, ε, 1−ε)                       [k ∈ [0,1]]
g(p)       = g0 · 4·fair·(1−fair)      [settlement-variance-scaled skew]
r          = fair − g(p)·n                                [reservation]
σ̂_10s      = EWMA(|Δ1s mid|)·√10  (halflife 60 s)         [short vol, ticks]
J          = decaying indicator: 1 if any |Δ10s| ≥ 5 t in last 120 s,
             decay exp(−t/60 s)
h          = clip(h0 + c_σ·max(0, σ̂_10s − σ0) + c_jump·J,
                  h_min, h_max)                           [half-spread]
b_yes      = floor_tick(r − h)           [YES bid]
b_no       = floor_tick((1 − r) − h)     [NO bid  = YES ask at 1−b_no]
size_yes   = s · clip(1 − max(0, +n)/N_max, 0, 1)         [adding side shrinks]
size_no    = s · clip(1 − max(0, −n)/N_max, 0, 1)
gates:     quote token i only while its own price p_i ∈ [p_lo, p_hi]
           (per-token band, not a global fair band — see §5);
           kill gate: on radiant kill → CANCEL resting radiant-side bids,
           block new radiant bids ≤10 s (dire bid untouched);
           mid-spike −10 t/10 s → cancel all bids, 30 s cooloff;
           feed stale >16 s / pause flag / market halt → pull everything;
           game-end imminent (fair ∉ band and stays) → stop quoting, merge.
merge:     when u ≥ M → mergePositions u → cash += u, yes,no −= u.
flatten:   taker exit of residual only if E[adverse move] > c_taker
           (≈ half-spread + 0.05·p(1−p) ≈ 2.75 t at p=0.5) — i.e. |Δ̂
           against n| > ~3 t with confidence, or feed/pause emergency.
           Otherwise skew out: the reducing side is already at h_min.
```

Parameter table — start values and the experiment-zero grid.
Brief's grid was h ∈ {1,2,3}, g ∈ {0, 2e-4, 5e-4}, N_max ∈ {100, 300},
M = 20, s ∈ {20, 50}; my comments in the last column.

| param | start | grid / range | basis / note |
|---|---|---|---|
| h0 | 1 t | {1, 2, 3} — keep | but expect h_eff ≈ 2 t after adders; h=1 only survives if f≥0.4 |
| h_min, h_max | 1 t, 5 t | fixed | floor = tick; cap ≈ observed spread p90 |
| σ0 (vol ref) | 1.5 t | fixed | = measured mean 10 s \|Δ\| (1.42 t) |
| c_σ | 0.3 | {0.2, 0.3, 0.5} | +1 t when 10 s vol doubles to ~3 t |
| c_jump | 1 t | {0, 1, 2} | jump indicator adder; Q4-vol quartile runs 12% jump rate |
| J decay | 60 s | {30, 60, 120} | Hawkes clustering ~minutes scale |
| k (delta weight) | 0.5 | {0, 0.5, 1} | forecast weight on fair; also rescales adverse selection |
| g0 | 2e-4 $/sh | {0, 2e-4, 5e-4} — keep | ×4p(1−p) → at p=0.5, 100-sh imbalance shifts r by 2–5 t. AS conversion: g0 ≈ γ·σ²_rem with σ²_rem = min(σ̂²·(T−t), p(1−p)) ≈ 0.18–0.25 $² at mid-map mid-price (σ̂ ≈ 0.0095 $/√s measured) ⇒ grid ≈ γ ∈ [8e-4, 2e-3] $⁻¹ |
| s | 20 sh/side | {20, 50} — keep | his median fill ≈ 18 sh; keep ≤ ~$40 depth per side |
| N_max | 100 sh | {100, 300} — keep | pull adding side entirely at cap (GLFT boundary) |
| p_lo, p_hi | 0.10, 0.90 | {0.05–0.95, 0.10–0.90, 0.15–0.85} | per-token band; see §5 |
| M (merge) | 20 sh | {20, 50, 100} | gas ~$0.05 vs $20–100 freed; merge asap, holding a pair earns nothing |
| taker flatten | off | {off, Δ>3 t, emergency} | cost ≈ 2.75 t/sh at p=0.5; skew is cheaper |
| kill gate | reuse | mandatory | cancel victim bid; winner bid stays — it eats the benign side of the jump |
| 480 s cutoff | LIFT | — | merge needs no exit runway; trader's last fill ≈ map end. Keep stale-feed halt |
| pause | pull quotes | — | book freezes then gaps on unpause |

**Settlement-timing answer (brief asks: widen near settlement when
|fair−0.5| is large?):** the opposite — widen when fair is *near 0.5* late in
the map (max settlement variance, no re-pairing time; Feil–Nendel Fig. 1) and
let the per-token band handle the extremes. At |fair−0.5| → 0.5 the variance
shrinks, but the *expensive token's* bid inherits the comeback tail
(0.93 → 0.60 = −33 t on one fill) while the *cheap token's* bid stays benign
(fills via the mint channel from confident winner-buys; worst case −5 t). So:
quote token i only while its price p_i ∈ [p_lo, p_hi] = [0.10, 0.90] — keep
the 0.05–0.10 cheap-side bid running, retire the 0.90+ expensive-side bid.
Measured: mid sits in [0.05, 0.95] for 89% of map-seconds; extreme buckets
(<0.1, >0.9) hold ~20% of seconds with p90 10 s moves ~1.5 t.

**Interaction with kill gate (verified direction):** post-kill, informed flow
sells the victim token (hits our victim bid directly) AND buys the winner
token (fills our victim bid through the mirror mint — taker buys dire at a ⇒
mint pair ⇒ our radiant bid supplies the radiant leg). Both toxic channels
converge on the victim bid — cancel it. The winner-side bid is only hit by
*uninformed* winner-sells and dip-buyers — keep it. This is exactly
Follow300's victim-token block, lifted to the two-sided quote.

**Lift the 480 s cutoff:** Follow300's cutoff exists because a directional
episode needs ~300 s for Δ̂ to realize and a maker-SELL needs time to exit.
Two-sided + merge has no such constraint: the exit is merge (atomic, ~1 s) and
the residual's natural disposal is settlement. The trader's fill timing says
the same thing — his last-fill median is ~28:30 game time ≈ map end, 41% of
fills after min 20. Replace the time cutoff with the per-token price band and
the feed-staleness halt; that is also how we keep quoting through the "march
to 1" phase where cheap-side mint fills keep arriving.

**Turnover / merge math:** balanced fills grow `u` at ≈ min-side fill rate
`λ_min ≈ ½·fill_rate·(1 − imbalance_drift)`. At capture $60–120/min ≈ 120–240
sh/min split over two sides, `λ_min ≈ 30–90 sh/min` ⇒ M = 20 merges every
~0.3–0.7 min on hot maps, ~2–5 min on quiet ones (script `edge_table.py`).
Peak capital = resting quotes (2·s·p̄ ≈ $20–50) + unmerged pairs (≈ M/2·$1 avg)
+ residual (≤ N_max ≈ $50–150) ⇒ ~$150–300 working capital sustains the flow;
turnover = buys/capital ≈ 0.3–0.8×/min ⇒ 12–30× per map — matching the
trader's peak-unreturned-capital ≈ 8% of market volume ($16.4k on $204k).

### 5. Dota-specific risks vs CS2/LoL

| risk | Dota reality | design consequence |
|---|---|---|
| Map length | 30–60 min (measured: p10 24 min, p50 37, p90 53) | long exposure window; inventory half-life controlled by N_max + skew, not time |
| Fight clustering | jump rate 7× higher in hot quartile (verified: 12% vs 1.7% per 10 s) | c_jump·J adder is the load-bearing control; widen during fights, earn in lulls |
| Pauses | book freezes, feed freezes, price gaps on resume | pull all quotes on pause flag + stale feed; re-enter after first print |
| GRID lag | scoreboard median 1.35 s, p90 ~10 s (`.learnings/pandascore-fast-feed-20260928.md`) | kill-gate 10 s window covers the lag; anyone streaming the game directly beats it — treat jump-window fills as partially toxic (α term) |
| Flow / capacity | p50 $612/min active vs his CS2 markets ~$1–2k+/min; ~30 makers competing | our share 15–25% ⇒ small clips; capacity ceiling ~$5k/map — fine for a strategy, not a business alone |
| Settlement | binary 0/1, no redeem automation needed on residual | leftover residual is a lottery; keep E[\|n\| at end] small via N_max + late band |
| One more jump source | Roshan/towers, buyback plays — mid-spike gate (already live) | cancel-all + 30 s cooloff on non-kill spikes |

**`pair_avg_cost > 1` in ~31% of his markets** (verified: P(<1) = 68.8%,
mean 0.975): those are the markets where directional drift ran through his
fills — GM adverse selection realized at market granularity. His response is
*not* to stop quoting (he merges anyway when min ≥ threshold and redeems the
residual) — consistent with the theory: the pair margin is a payout you
already locked (negative), the residual is a bet you take because unwinding
costs more (2.75 t taker) than the expected drift. For us: the kill gate +
k·Δ skew exist precisely to shrink that 31% bucket — in the backtest, measure
the share of maps where `pair margin < 0` before merge.

Also note his taker mix: 22% of fill *records* but 49% of fill *dollars* are
taker (avg taker fill $58 vs maker $17) — likely pair-completion and
opportunistic cheap-side sweeps; keep "taker leg to complete an open pair" as
a candidate rule in the grid (flatten flag).

### 6. What the backtest must measure (acceptance gates)

1. **Fill-conditioned markout** — mid 10 s/60 s after each maker fill, signed
   by side; this is measured α. Gate: α_60s ≤ 1.5 t blended; report split by
   post-kill ≤10 s vs calm fills.
2. **Pair margin distribution** — per merged pair `1 − (vwap_y + vwap_n)`;
   gate: mean ≥ +1.5 t, P(<0) ≤ 25% (his realized: mean 2.5 t, 69% positive).
3. **PnL per $ of buy volume**, net of fees + rebates; gate: ≥ +0.8 ¢/$
   median-map, ≥ +1.5 ¢/$ aggregate (his ~2.9 ¢/$ gross).
4. **Residual share** — fraction of bought shares that never merge before
   settlement; gate: ≤ 30%, and residual PnL share of total ≤ 40% (else the
   strategy is secretly directional, not a maker).
5. **Inventory profile** — mean/p95 \|n\|, imbalance half-life, max residual
   at settlement; gate: p95 \|n\| ≤ N_max, E[\|n_end\|] ≤ 20 shares.
6. **Merge cadence & capital** — merges/map, freed $/map, peak deployed
   capital, turnover = volume/peak; gate: turnover ≥ 8×/map and merge count
   ≤ ~60/map (gas drag < 5% of edge at $0.05/tx).
7. **Spread captured vs quoted** — realized `a_eff − b` per pair vs the 2h
   target; gate: capture ≥ 70% of quoted edge (queue position honesty).
8. **PnL by regime** — calm vs jumpy quartile, early/mid/late map, by price
   band; gate: no regime contributes < −20% of total PnL.
9. **Fill share of flow** — our fills / on-chain taker $ in the window;
   gate: ≤ 30% sustained (more means our model of "flow" is wrong or we're
   the market).
10. **Toxic-window PnL** — PnL on fills within 10 s of a kill event vs rest;
    gate: kill-window PnL ≥ 0 (that is the kill gate's whole job).

Plus the standard: per-map PnL p10, max map drawdown, and the two-column
`format_terminal_report` totals.

## Open questions / what I could not verify

- **α is the whole game and I bounded it, not measured it.** The honest range
  1–3 t spans breakeven at h=2. grok-sim's fill-level replay measures the
  real markout; my table is the decision surface to read it against. Note the
  asymmetry of evidence: *gated* fills are measured (+0.5–1.0 t at 30 s);
  *ungated* two-sided fills are the unknown.
- Mirrored-vs-native queue tie-break at identical effective prices is not in
  public docs; assumed global price-time by arrival (likely). Verify live with
  a single probe order, or just don't lean on it.
- Whether esports map markets carry the *liquidity rewards* program on top of
  maker rebates — excluded (conservative; postprocess.py notes 171/328
  validation markets have no program); upside if present.
- Model Δ̂ quality itself (R² on its 300 s label) is not measured here; the
  corr(Δ_60, Δ_300) = 0.42 figure is realized-vs-realized — the model sees a
  noisy version of Δ_300, so its contribution to f is lower-bounded, not
  exact. Combine with kill-gate coverage empirically via the fill markout
  split in grok-sim.
- Oddin-feed maps (no XP model): Δ̂ quality differs; consider k=0 there (pure
  symmetric quoting + kill gate only).
- Pause handling needs a feed flag that exists in live data (`market_status`
  in market_seconds — check values; GRID pause flags exist per collector
  sidecars on VPS, verify field name before wiring).
- `markets_summary.json` cash_net vs realized PnL across maps is sol-policy's
  territory; I used only pair_avg_cost / fill-rate aggregates for calibration.

## Files

- `work/swe-theory/jump_stats.py` — 10 s/60 s mid-move distribution + regime
  clustering + price-band occupancy (run: `python jump_stats.py 400`).
- `work/swe-theory/dota_flow.py` — per-map aggressor $/min from one-token
  onchain_fills files (run: `python dota_flow.py 40`).
- `work/swe-theory/edge_table.py` — expected-PnL tables, f* break-evens,
  rebate-per-$ curve, merge cadence (run: `python edge_table.py`).
- `work/swe-theory/d300_vs_d60.py` — realized-300 s vs 10/60 s move
  correlation (run: `python d300_vs_d60.py`).
- `work/swe-theory/seed0_fills.py` — LIVE seed0 fills.parquet anchors:
  markouts, rebate/share, fill sizes (run: `python seed0_fills.py`).
- `work/swe-theory/flow_and_trader.py` — trader per-market aggregates from
  markets_summary.json + first-pass flow (superseded by dota_flow.py).
- Report: `reports/swe-theory.md` (this file).

## References

1. Glosten & Milgrom 1985, JFE 14(1):71–100. DOI 10.1016/0304-405X(85)90044-3.
2. Kyle 1985, Econometrica 53(6):1315–1335. DOI 10.2307/1913210.
3. Avellaneda & Stoikov 2008, Quantitative Finance 8(3):217–224.
   DOI 10.1080/14697680701381228; math.nyu.edu/~avellane/HighFrequencyTrading.pdf
4. Guéant, Lehalle, Fernandez-Tapia 2013, Math. & Financial Economics
   7:477–507. arXiv:1105.3115.
5. Cartea, Jaimungal, Penalva 2015, "Algorithmic and High-Frequency Trading",
   Cambridge UP, ch. 10 (market making with jumps/alpha).
6. Cartea, Jaimungal, Ricci 2018, "Algorithmic Trading, Stochastic Control,
   and Mutually-Exciting Processes", SIAM Review 60(3).
   oxford-man.ox.ac.uk PDF (accessed 2026-10-07).
7. Feil & Nendel 2026, "Optimal Market Making in Prediction Markets",
   arXiv:2607.17991v1 (20 Jul 2026) — settlement-risk MM; spread/skew/inventory
   results quoted from §4.3–4.4 (accessed 2026-10-07).
8. Othman, Pennock, Reeves, Sandholm 2013, "A practical liquidity-sensitive
   automated market maker", ACM TEAC 1(3):14.
   cs.cmu.edu/~sandholm/liquidity-sensitive market maker.EC10.pdf.
9. Brahma, Chakraborty, Das, Lavoie, Magdon-Ismail 2012, "A Bayesian Market
   Maker", cs.rpi.edu/~magdon/ps/conference/predMarkets.pdf.
10. Polymarket docs (accessed 2026-10-07): docs.polymarket.com/trading/
    market-making ("Buying NO at 0.48 ≡ selling YES at 0.52"),
    /concepts/prices-orderbook (mint-match of complementary buys),
    /concepts/order-lifecycle (post-only, GTC), /trading/positions/manage
    (split/merge/redeem via Router/CTF).
11. warproxxx/poly-maker, github.com/warproxxx/poly-maker — reference two-sided
    Polymarket maker with merge; v1 README notes it is unprofitable today
    without an edge.
12. Kalshi NBA in-game MM thesis (empirical Hawkes + queue-hazard approach),
    exa.ai/library/publication/zjbjh8j13fq — methodology precedent for fitting
    arrival/jump distributions to a sports CLOB.
