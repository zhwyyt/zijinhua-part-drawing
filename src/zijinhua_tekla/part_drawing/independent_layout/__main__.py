"""Offline stage-0 commands with fresh output directories and no Tekla calls."""

import argparse
import json
import sys
from pathlib import Path

from .contracts import ContractError, read_json


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    source = commands.add_parser('sources', help='Reaudit native JSON and freeze source envelopes')
    source.add_argument('--evidence', type=Path, required=True)
    source.add_argument('--model-tolerance-mm', type=float, default=0.1)
    template = commands.add_parser('template-study', help='Inspect read-only captures, not production approval')
    template.add_argument('--study', type=Path, required=True)
    template.add_argument('--attributes', type=Path)
    geometry = commands.add_parser('check-observation', help='Minimal independent geometry check')
    geometry.add_argument('--observation', type=Path, required=True)
    commands.add_parser('environment', help='Check pinned OR-Tools with ten deterministic runs')
    for command in commands.choices.values():
        command.add_argument('--out', type=Path, required=True, help='Must be a new directory')
    args = parser.parse_args(argv)
    try:
        if args.out.exists():
            raise ContractError('OUTPUT_EXISTS', str(args.out))
        if args.command == 'sources':
            from .sources import freeze_native_sources
            result = freeze_native_sources(read_json(args.evidence), args.model_tolerance_mm)
        elif args.command == 'template-study':
            from .templates import study_template
            capture, objects, presentation = [read_json(args.study / name) for name in
                                             ('capture.json', 'objects.json', 'presentation.json')]
            result = study_template(capture, objects, presentation,
                                    args.attributes.read_bytes() if args.attributes else None)
        elif args.command == 'check-observation':
            from .validation import validate_observation
            result = validate_observation(read_json(args.observation))
        else:
            from .environment import check_environment
            result = check_environment()
        args.out.mkdir(parents=True, exist_ok=False)
        (args.out / 'report.json').write_text(json.dumps(result, ensure_ascii=False, allow_nan=False,
                                                        indent=2) + '\n', encoding='utf-8')
        print(json.dumps({'command': args.command, 'out': str(args.out),
                          'status': result.get('status', result.get('counts'))}, ensure_ascii=False))
        if result.get('status') == 'GEOMETRY_CONFLICT' or result.get('counts', {}).get('INPUT_BLOCKED', 0):
            return 1
        return 0
    except (ValueError, KeyError, TypeError, OSError, ImportError) as error:
        print(f'{type(error).__name__}: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
