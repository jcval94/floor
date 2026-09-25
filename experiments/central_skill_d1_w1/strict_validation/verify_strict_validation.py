from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

import pandas as pd


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--dataset', required=True)
    ap.add_argument('--phase1-runner', required=True)
    ap.add_argument('--strict-runner', required=True)
    ap.add_argument('--d1-champion', required=True)
    ap.add_argument('--w1-champion', required=True)
    ap.add_argument('--d1-competition', required=True)
    ap.add_argument('--w1-competition', required=True)
    ap.add_argument('--output', required=True)
    args = ap.parse_args()

    phase1 = load_module('phase1_verify', Path(args.phase1_runner))
    strict = load_module('strict_verify', Path(args.strict_runner))
    dataset_path = Path(args.dataset)
    df, _ = phase1.load_dataset(dataset_path)
    raw = json.loads(dataset_path.read_text(encoding='utf-8'))['rows']
    elig = pd.DataFrame({
        'timestamp': [r.get('timestamp') for r in raw],
        'symbol': [r.get('symbol') for r in raw],
        'split_eligible_d1': [r.get('split_eligible_d1') for r in raw],
        'split_eligible_w1': [r.get('split_eligible_w1') for r in raw],
    })
    elig['date'] = pd.to_datetime(elig['timestamp'], utc=True).dt.normalize()
    df = df.merge(elig[['date','symbol','split_eligible_d1','split_eligible_w1']], on=['date','symbol'], how='left')

    output = {'incumbent_parity': {}, 'split_integrity': {}}
    for h in ('d1', 'w1'):
        artifact = json.loads(Path(getattr(args, f'{h}_champion')).read_text(encoding='utf-8'))
        competition = json.loads(Path(getattr(args, f'{h}_competition')).read_text(encoding='utf-8'))
        frame = phase1.prepare_horizon(df, h)

        validation = frame[(frame['split'] == 'validation') & (frame[f'split_eligible_{h}'] == True)].copy()
        pf, pc = strict.predict_incumbent(artifact, validation)
        m = phase1.prediction_metrics(validation, pf, pc)
        expected = float(competition['existing_current_validation_score'][0])
        delta = float(m['mae_spread_pct'] - expected)
        output['incumbent_parity'][h] = {
            'validation_rows': int(len(validation)),
            'computed_mae_spread_pct': float(m['mae_spread_pct']),
            'competition_existing_score': expected,
            'absolute_delta': abs(delta),
            'pass': abs(delta) < 1e-9,
        }

        test = frame[frame['split'] == 'test'].copy()
        test_start = test['date'].min()
        target_col = f'target_end_date_{h}'
        dev = frame[frame['split'].isin(['train', 'validation'])].copy()
        dev = dev[dev[target_col] < test_start].copy()
        max_target = dev[target_col].max()
        output['split_integrity'][h] = {
            'development_rows': int(len(dev)),
            'test_rows': int(len(test)),
            'max_development_target_end': str(max_target.date()),
            'test_start': str(test_start.date()),
            'strictly_before_test': bool(max_target < test_start),
        }

    ok = all(x['pass'] for x in output['incumbent_parity'].values()) and all(
        x['strictly_before_test'] for x in output['split_integrity'].values()
    )
    output['overall_pass'] = ok
    Path(args.output).write_text(json.dumps(output, indent=2, sort_keys=True), encoding='utf-8')
    print(json.dumps(output, indent=2, sort_keys=True))
    if not ok:
        raise SystemExit(1)


if __name__ == '__main__':
    main()