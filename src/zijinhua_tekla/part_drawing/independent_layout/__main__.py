"""Offline stage-0 commands with fresh output directories and no Tekla calls."""

import argparse
import json
import sys
from pathlib import Path

from .contracts import ContractError, read_json


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    source = commands.add_parser('sources', help='Reaudit native JSON and freeze source envelopes')
    source.add_argument('--evidence', type=Path, required=True)
    source.add_argument('--model-tolerance-mm', type=float, default=0.1)
    source.add_argument('--extend-cutouts', action='store_true', help='Reprove supported blocked cutters on independent route')
    expressions = commands.add_parser('expressions', help='Adapt source batches and audit semantic responsibilities')
    expressions.add_argument('--sources', type=Path, required=True)
    expressions.add_argument('--complete-cutouts', action='store_true', help='Add independent inner-loop machining dimensions')
    preparation = commands.add_parser('prepare-input', help='Join one admitted source/expression to template studies and list missing gates')
    preparation.add_argument('--source', type=Path, required=True)
    preparation.add_argument('--expression', type=Path, required=True)
    preparation.add_argument('--template-study', type=Path, action='append', required=True)
    template = commands.add_parser('template-study', help='Inspect read-only captures, not production approval')
    template.add_argument('--study', type=Path, required=True)
    template.add_argument('--attributes', type=Path)
    geometry = commands.add_parser('check-observation', help='Minimal independent geometry check')
    geometry.add_argument('--observation', type=Path, required=True)
    commands.add_parser('environment', help='Check pinned OR-Tools with ten deterministic runs')
    for command in commands.choices.values():
        command.add_argument('--out', type=Path, required=True, help='Must be a new directory')
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        if args.out.exists():
            raise ContractError('OUTPUT_EXISTS', str(args.out))
        if args.command == 'sources':
            from .sources import freeze_native_sources
            result = freeze_native_sources(read_json(args.evidence), args.model_tolerance_mm, args.extend_cutouts)
        elif args.command == 'expressions':
            from .expressions import adapt_expression_sources
            result = adapt_expression_sources(read_json(args.sources), args.complete_cutouts)
        elif args.command == 'prepare-input':
            from .preparation import prepare_layout_input
            result = prepare_layout_input(read_json(args.source),read_json(args.expression),
                                          [read_json(p) for p in args.template_study])
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
        counts = result.get('counts', {})
        if (result.get('status') in ('GEOMETRY_CONFLICT','INPUT_BLOCKED') or counts.get('INPUT_BLOCKED', 0)
                or counts.get('UPSTREAM_CONTRACT_GAP', 0)):
            return 1
        return 0
    except (ValueError, KeyError, TypeError, OSError, ImportError) as error:
        print(f'{type(error).__name__}: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
