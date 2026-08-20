# poly-maker

## Frozen — do not edit this repo

`../poly-maker` is a clean fork of `warproxxx/poly-maker` (our remote: `Dimabytes/poly-maker`).

Do not change it. Do not plan changes. Do not open PRs against this fork or against upstream.

Live Dota code patches Engine classes and methods from `dota_2_model` (`engine_seams.py`), before or after `Engine()`. If a fork limitation blocks a feature, tell the user in one sentence and wait for an explicit "change poly-maker" in this conversation.

## Setup

- Project path: `../poly-maker` (sibling of `betting_workspace/`)
- Fork of a Polymarket CLOB V2 maker bot (political markets). Our GitHub remote is `Dimabytes/poly-maker`.
- Python 3.12+, `uv`. `uv sync --extra dev`, `uv run polymaker --help`.
- `dota_2_model` does not place live orders; live/paper execution lives here.
