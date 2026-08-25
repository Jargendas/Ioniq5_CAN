# Status 
Continue the task outlined in this document.

## Task
Write a script using cantools (in `~/main_venv/`) + `parsing_lib.py` + the
existing `*.ipynb` workflows to **highlight discrete changes in signals**,
verify it against the logs in `../CAN_logs/` using the file titles as metadata,
and modify `parsing_lib.py` as needed.

## Deliverables

### 1. `CAN_parsing/highlight_discrete_signals.py` (new)
- Loads and **merges** the three E-GMP DBCs by default
  (`ioniq5-2022.dbc`, `ev6-2024.dbc`, `ioniq6-2023-2025.dbc`) via cantools.
  - Works around a cantools bug: `BA_ "VFrameFormat" ...` lines crash the
    parser on extended-ID frames; they are stripped (the extended bit is
    already encoded in the frame id) and each `BO_` block is re-added
    first-wins so later DBCs only fill gaps.
- Two-tier discrete-change detection per log:
  - **DBC signal tier**: frames present in a DBC are decoded with cantools;
    signals with a small set of distinct values (`--threshold`, default 10)
    that actually transition are reported with their change timestamps and
    `VAL_` names (e.g. `Battery_Precond_State: Preparing (5) -> On (21)`).
  - **muid fallback tier**: frames *not* in the DBC are reported at
    whole-message granularity using parsing_lib's limited-payload search.
- Auto-detects log format (SavvyCAN vs panda CSV headers) and normalizes
  timestamps to seconds.
- `--clean-timestamps` is **off by default**: the notebooks only clean the
  button/AVN logs, and cleaning the preconditioning logs truncates the first
  segment (mid-log timestamp reset) and hides the real 0x0C7 toggle.
- Metadata verification driven by the file title: a keyword -> expected-frame
  table annotates each log with PASS/`!!` per expected frame, plus a one-line
  summary table and optional `--json` dump.
- Excludes ISO-TP (`ISOTP*`) and VIN frames from the signal tier (protocol /
  broadcast noise, not control signals).

### 2. `CAN_parsing/parsing_lib.py` (extended, backwards compatible)
Added three numpy-only helpers used by the script (no cantools dependency):
- `return_frame_series(dict_name, frame_id, bus=None)` — time-sorted
  (timestamps, messages) for one frame.
- `return_value_transitions(timestamps, values)` — indices/times/from/to where
  a series changes value between consecutive samples.
- `return_frame_IDs_with_limited_message_changes(dict_name, threshold, bus=None)`
  — control-signal-candidate frames (2..threshold distinct payloads),
  generalizing the notebook search for control vs physical-sensor frames.

## Verification results (all logs in ../CAN_logs/)

| Log | DBC signals | Expected frames (title metadata) |
|---|---|---|
| M-CAN_driving_with_nav_preconditioning_at_end_cleaned | 9 | all 5 OK (0x0c7,0x2ad,0x4cc,0x4e8,0x4ed) |
| M-CAN_driving_with_nav_to_school_no_preconditioning... | 5 | 0x4e8,0x4ed OK; precond state correctly absent |
| M-CAN_start_nav_to_EA_parked_in_D_preconditioning_cleaned | 11 | all 5 OK |
| M-CAN_start_nav_to_school_parked_in_D | 7 | 0x4e8 OK, 0x4ed no change (nav only, parked) |
| M-CAN_GV60_preconditioning_{concise_2,long_1} | 1 | extended 0xa82aa03 Battery_Precond_State OK (only expected frame) |
| M-CAN_GV60_already_preconditioning | 0 | correctly no discrete change (0xa82aa03 X, state stays On) |
| M-CAN_head_unit_only_climate_start_no_warmers_62f | 2 | 0x380/0x4f1/0x4a2 not present; only battery temp jitter |
| M-CAN_head_unit_only_nothing_happening | 1 | no expected frames (baseline) |
| I-CAN_car_in_D / ready_parked / nothing_on | 1 / 7 / 2 | 0x31b+0x38 OK in ready_parked; 0x38 constant in car_in_D |
| M-CAN_nav_start_to_* (bank/chicken/Fresno/school) | 7-8 | 0x4e8 distance flag OK |
| M-CAN_panda_nothing_on_climate_start_* | 5-7 | 0x380,0x4a2,0x4f1 OK (climate temps) |
| M-CAN_panda_nothing_on_remote_{lock,unlock} | 1-3 | 0x405,0x411 OK |
| M-CAN_panda_nothing_on / nothing_happening | 4 / 1 | no expected frames (baseline) |

Key findings verified: preconditioning logs show `Battery_Precond_State`
transitioning Off->Preparing->On and the 0x0C7 toggle; no-preconditioning logs
keep 0x2AD off; GV60 extended precond status decodes; climate-start logs show
temp setpoint changes; "nothing" logs show essentially no control signals.

## Remaining / known notes
- `M-CAN_head_unit_only_climate_start_no_warmers_62f.csv` has no 0x380/0x4f1/
  0x4a2 frames at all (head-unit-only capture); the only "discrete" hits are
  battery-temperature 7<->8 jitter. May warrant a jitter filter later.
- `0x4ed` (Navigation_Precond_Status) shows no change in `start_nav_to_school`
  / nav panda logs since it's nav-only with no preconditioning — flagged `X`
  but this matches the title.
- `verify_log` special-cases `no_preconditioning` to expect 0x2AD/0x4ED/0x4CC
  absence (else those would show false `MISSING`).
- Current status committed.

## This session (Aug 25 2026)
- Fixed DBC merge dropping `VAL_` tables: the trailing `VAL_` lines were being
  glued onto the wrong `BO_` block and silently ignored by cantools, so signal
  names never rendered. They are now grouped by message id and appended to
  their own block, so `Battery_Precond_State` reports
  `Off (1) -> Preparing (5) -> On (21)` instead of raw `1 -> 5 -> 21`.
- Fixed `verify_log` matching zero-padded hex strings against un-padded
  expected ids (`0x38` never matched `0x038`). Now compared numerically, so
  `I-CAN_car_in_ready_parked` correctly reports 0x38 OK
  (`Power_Status_Ready` transitions); 0x38 stays X in `car_in_D` (constant).
- GV60 logs now only expect `0xa82aa03` (keyword special-case): the standard
  M-CAN preconditioning ids don't exist on that platform, previously they
  showed as five noisy `MISSING` marks.
- `parsing_lib.py` unchanged this session (three helpers already committed).
