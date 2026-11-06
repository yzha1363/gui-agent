"""Export human-readable per-example outputs and CSVs from saved inference only."""

import argparse
import csv
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gui_data.pipeline import read_jsonl
from gui_training.evaluation import aggregate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    args = parser.parse_args(); directory = args.run
    summary = json.loads((directory / 'summary.json').read_text(encoding='utf-8'))
    rows = read_jsonl(directory / 'validation.jsonl') + read_jsonl(directory / 'test.jsonl')
    keys = list(dict.fromkeys((r['split'], r['variant'], r['prompt_variant']) for r in rows))
    lines = ['# Local VLM offline comparison', '',
             'These are actual generated outputs. Annotation agreement is not live GUI success.', '',
             f'Primary prompt: `{summary["primary_prompt"]}`. Validation-selected prompt: `{summary["selected_prompt"]}`.', '',
             '| Split | Weights | Prompt | Schema valid | Types | Other parameters | Pointer targets | Annotation match |',
             '|---|---|---|---|---|---|---|---|']
    metrics_rows = []
    for split, variant, prompt in keys:
        group = aggregate([r for r in rows if (r['split'], r['variant'], r['prompt_variant']) == (split, variant, prompt)])
        names = ('schema_valid', 'action_types_match', 'parameters_match', 'pointer_targets_match', 'annotation_match')
        values = [group['metrics'][name] for name in names]
        lines.append('| ' + ' | '.join([split, variant, prompt] + [f'{v["correct"]}/{v["total"]}' if v['total'] else 'N/A' for v in values]) + ' |')
        for name, value in group['metrics'].items():
            metrics_rows.append({'split': split, 'variant': variant, 'prompt': prompt, 'metric': name, **value})
    lines.extend(['', '## Raw examples', ''])
    for index, row in enumerate(rows, 1):
        result = Path(row['result_path'])
        lines.extend([f'### {index}. {row["split"]} / {row["variant"]} / {row["prompt_variant"]}', '',
                      f'Sample: `{row["sample_id"]}`', '', f'![Actual fixed input screenshot]({(result.parent / "input.png").as_posix()})', '',
                      'Reference annotation (never placed in the model input):', '', '```json',
                      json.dumps(row['target'], ensure_ascii=False, indent=2), '```', '', 'Raw generated response:', '', '```text',
                      row['response'], '```', '', f'Error: `{row["score"]["error_category"]}`; tokens: {row["new_tokens"]}; '
                      f'token limit hit: {row["token_limit_hit"]}; generation seconds: {row["generation_seconds"]}.', '',
                      f'[Full case, scores, prompt and generated tokens]({result.as_posix()})', ''])
    (directory / 'comparison.md').write_text('\n'.join(lines), encoding='utf-8')
    with (directory / 'metrics.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=['split', 'variant', 'prompt', 'metric', 'correct', 'total', 'rate'])
        writer.writeheader(); writer.writerows(metrics_rows)
    with (directory / 'cases.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['split', 'variant', 'prompt', 'sample_id', 'schema_valid', 'types_match', 'parameters_match', 'annotation_match', 'error', 'new_tokens', 'token_limit_hit', 'generation_seconds', 'result'])
        for r in rows:
            s = r['score']; writer.writerow([r['split'], r['variant'], r['prompt_variant'], r['sample_id'], s['schema_valid'], s['action_types_match'],
                s['parameters_match'], s['annotation_match'], s['error_category'], r['new_tokens'], r['token_limit_hit'], r['generation_seconds'], r['result_path']])
    print(directory / 'comparison.md')


if __name__ == '__main__': main()
