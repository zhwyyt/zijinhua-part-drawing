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
    preparation.add_argument('--load-study', type=Path, action='append', default=[])
    preparation.add_argument('--bundle-load-study', type=Path)
    loads = commands.add_parser('template-load-study', help='Recheck detached SDK loads and immutable file snapshots')
    loads.add_argument('--probe', type=Path, required=True, help='Directory containing probe.json and snapshots')
    loads.add_argument('--template-study', type=Path, required=True)
    bundle = commands.add_parser('template-bundle', help='Build local namespaced copies of converted attributes')
    bundle.add_argument('--manifest', type=Path, required=True)
    bundle.add_argument('--template-study', type=Path, required=True)
    bundle_load = commands.add_parser('template-bundle-study', help='Verify all three isolated native loads')
    bundle_load.add_argument('--probe', type=Path, required=True)
    bundle_load.add_argument('--bundle', type=Path, action='append', required=True)
    bundle_load.add_argument('--template-study', type=Path, action='append', required=True)
    pages = commands.add_parser('page-study', help='Audit new calibration pages and measured text after two reopens')
    pages.add_argument('--job', type=Path, required=True)
    pages.add_argument('--probe', type=Path, required=True)
    glyphs = commands.add_parser('glyph-study', help='Measure embedded PDF ink and bind native sample IDs')
    glyphs.add_argument('--capture', type=Path, required=True)
    glyphs.add_argument('--pdf', type=Path, required=True)
    glyphs.add_argument('--receipt', type=Path, required=True, help='Single export receipt with PDF hash and transform log')
    glyphs.add_argument('--align-frame', action='store_true', help='Bind actual PDF mapping to independent inner/outer frame edges')
    glyphs.add_argument('--export-page', type=Path, help='Export-before native page snapshot; required with --align-frame')
    primitives = commands.add_parser('primitive-study', help='Audit synthetic short dimensions and leaders after two reopens')
    primitives.add_argument('--job', type=Path, required=True)
    primitives.add_argument('--probe', type=Path, required=True)
    primitives.add_argument('--template', required=True)
    primitives.add_argument('--ink', type=Path, required=True, help='Frame-aligned glyph-study report')
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
                                          [read_json(p) for p in args.template_study],
                                          [read_json(p) for p in args.load_study],
                                          read_json(args.bundle_load_study) if args.bundle_load_study else None)
        elif args.command == 'template-load-study':
            from .template_loads import study_template_load
            result = study_template_load(read_json(args.probe / 'probe.json'),
                                         read_json(args.template_study),args.probe)
        elif args.command == 'template-bundle':
            from .template_bundles import build_template_bundle
            manifest = read_json(args.manifest)
            def input_path(value):
                return args.manifest.parent / value
            dependencies = manifest['dependencies']
            if len({f['name'] for f in dependencies})!=len(dependencies):
                raise ContractError('BUNDLE_DUPLICATE', 'Duplicate manifest dependency')
            result = build_template_bundle(read_json(args.template_study),manifest['attributeName'],
                input_path(manifest['original']).read_bytes(),input_path(manifest['converted']).read_bytes(),
                {f['name']:input_path(f['source']).read_bytes() for f in dependencies})
        elif args.command == 'template-bundle-study':
            from .template_bundles import study_bundle_load
            result = study_bundle_load(read_json(args.probe/'probe.json'),
                [read_json(p) for p in args.bundle],[read_json(p) for p in args.template_study],args.probe)
        elif args.command == 'page-study':
            from .page_studies import study_native_pages
            job = read_json(args.job)
            captures = {identity:[read_json(args.probe/(identity+'-'+stage+'.json'))
                         for stage in ('before','reopen1','reopen2')] for identity in ('零件图10','零件图20','零件图50')}
            receipts = {t['templateId']:read_json(args.probe/(t['templateId']+'-view-control.json'))
                        for t in job['templates'] if t.get('viewControl')}
            result = study_native_pages(job,read_json(args.probe/'probe.json'),captures,
                                        read_json(args.probe/'ownership-before.json'),read_json(args.probe/'ownership-after.json'),receipts)
        elif args.command == 'glyph-study':
            from .glyph_occupancy import study_glyph_occupancy
            result = study_glyph_occupancy(read_json(args.capture),args.pdf.read_bytes(),read_json(args.receipt),args.align_frame,
                                           read_json(args.export_page) if args.export_page else None)
        elif args.command == 'primitive-study':
            from .primitive_studies import study_primitives
            templates=[t for t in read_json(args.job)['templates'] if t['templateId']==args.template]
            if len(templates)!=1 or args.template not in ('零件图10','零件图20','零件图50'):
                raise ContractError('PRIMITIVE_TEMPLATE', 'Expected one known template')
            captures=[read_json(args.probe/(args.template+'-'+stage+'.json')) for stage in ('before','reopen1','reopen2')]
            result=study_primitives(templates[0]['primitives'],captures,read_json(args.ink))
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
        if args.command == 'template-bundle':
            from .template_bundles import stage_bundle_files
            entry = stage_bundle_files(result,read_json(args.template_study),args.out/'files')
            (args.out/'template-job-entry.json').write_text(json.dumps(entry,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
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
