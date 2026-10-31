"""Read template evidence and mixed-encoding attributes; never certify a template."""

import hashlib
import xml.etree.ElementTree as ET

from .contracts import ContractError, canonical_json, digest, positive, require, seal


def read_attributes(data):
    """Preserve generic fields separately from per-element XML formatting."""
    fields, content = {}, {}
    for raw in data.splitlines():
        if not raw.strip():
            continue
        key_bytes, separator, payload = raw.partition(b' ')
        require(bool(separator), 'ATTRIBUTE_LINE', 'Expected field and value')
        key = key_bytes.decode('ascii')
        require(key not in fields, 'ATTRIBUTE_DUPLICATE', key)
        payload = payload.strip()
        is_xml = b'<?xml' in payload
        try:
            text = payload.decode('utf-8' if is_xml else 'gb18030')
        except UnicodeError as error:
            raise ContractError('ATTRIBUTE_ENCODING', key) from error
        fields[key] = text
        # View-label placeholders such as << Mark >> are not valid standalone
        # XML. Preserve them, but parse only the part/bolt-mark role vocabulary.
        role_key = key.removeprefix('wdraw_dial.')
        if not is_xml or not role_key.startswith(('dpm.ContentString', 'dsm.ContentString')):
            continue
        require(text.startswith('"') and text.endswith('"'), 'ATTRIBUTE_XML', key)
        xml = text[1:-1].replace('\x01', '')
        require('<!ENTITY' not in xml, 'ATTRIBUTE_XML', 'Entity declarations are unsupported')
        try:
            root = ET.fromstring(xml)
        except ET.ParseError as error:
            raise ContractError('ATTRIBUTE_XML', key) from error
        formats = []
        for element in root.iter('Format'):
            height = positive(float(element.attrib['height']), key + '.height')
            require(bool(element.attrib.get('font')), 'ATTRIBUTE_FONT', key)
            formats.append({'font': element.attrib['font'], 'height': height,
                            'attributes': dict(element.attrib)})
        content[key] = formats
    require(bool(fields), 'ATTRIBUTE_EMPTY', 'No attribute fields')
    return seal({'schemaVersion': 'TemplateAttributesStudy.v1',
                 'bytesSha256': hashlib.sha256(data).hexdigest(),
                 'fields': fields, 'contentFormats': content,
                 'productionEligible': False})


def _nodes(node):
    yield node
    for child in node.get('Primitives', []):
        yield from _nodes(child)


def _style(font):
    require(isinstance(font.get('Name'), str) and bool(font['Name']), 'FONT_MISSING', 'Font name')
    return {'font': font['Name'], 'height': positive(font['Height'], 'font height')}


def _unique(values):
    return [value for _, value in sorted({canonical_json(v): v for v in values}.items())]


def _attribute_differences(attribute_study, sheet):
    fields = attribute_study['fields']
    prefixes = [prefix for prefix in ('', 'wdraw_dial.')
                if any(prefix + 'dl.' + name in fields for name in ('width', 'height'))]
    require(len(prefixes) <= 1, 'ATTRIBUTE_NAMESPACE_AMBIGUOUS',
            'Do not merge prefixed and unprefixed layout fields')
    prefix = prefixes[0] if prefixes else ''
    differences = []
    for field, actual in zip(('width', 'height'), sheet):
        key = prefix + 'dl.' + field
        if key not in fields or positive(float(fields[key]), key) != actual:
            differences.append(key)
    return differences


def _display_styles(presentation, role_styles):
    styles = _unique({'font': n['Font'], 'height': positive(n['Height'], 'display height')}
                     for v in presentation for n in _nodes(v)
                     if n.get('type', '').endswith('.TextPrimitive'))
    for values in role_styles.values():
        require(all(style in styles for style in values), 'STYLE_DISPLAY_MISMATCH',
                'An effective SDK role style is absent from actual display text')
    return styles


def _observed_roles(objects):
    roles = {'dimension': [], 'partLabel': [], 'holeNote': []}
    views, unknown_marks = [], []
    for view in objects:
        v = view['view']
        scale = positive(v['Attributes']['Scale'], 'view scale')
        views.append({'scale': scale, 'origin': v['Origin'], 'frameOrigin': v['FrameOrigin'],
                      'size': [positive(v['Width'], 'view width'), positive(v['Height'], 'view height')],
                      'shortening': v['Attributes'].get('Shortening')})
        for obj in view['objects']:
            require(obj.get('refreshed') is True, 'OBJECT_UNREAD', str(obj.get('index')))
            data = obj['data']
            kind = data['type'].rsplit('.', 1)[-1]
            if kind == 'StraightDimensionSet':
                roles['dimension'].append(_style(data['Attributes']['Text']['Font']))
            elif kind == 'Mark':
                for item in data['Attributes']['Content']:
                    role = {'PART_POS': 'partLabel', 'albl_Size': 'holeNote'}.get(item.get('Name'))
                    if role:
                        roles[role].append(_style(item['Font']))
                    else:
                        unknown_marks.append(item)
    return roles, views, unknown_marks


def study_template(capture, objects, presentation, attributes=None):
    """Summarize sampled roles without inventing glyph bounds or missing roles."""
    for value in (capture, objects, presentation):
        canonical_json(value)
    require(capture.get('schemaVersion') == 'activeLayoutStudy.v1', 'CAPTURE_SCHEMA', 'Unsupported capture')
    for key in ('partMetadataUnchanged', 'workPlaneUnchanged', 'activeIdentityUnchanged'):
        require(capture.get(key) is True, 'CAPTURE_CHANGED', key)
    drawing = capture['drawing']
    require(drawing['Name'] in ('零件图10', '零件图20', '零件图50'), 'TEMPLATE_NOT_ALLOWED', drawing['Name'])
    size = drawing['Layout']['SheetSize']
    sheet = [positive(size[k], k) for k in ('Width', 'Height')]
    require(isinstance(objects, list) and len(objects) == capture['viewCount'] and bool(objects),
            'VIEWS_MISSING', 'Capture and view enumeration disagree')
    require(capture.get('presentation', {}).get('status') == 'READ' and bool(presentation),
            'PRESENTATION_MISSING', 'Actual display evidence is required')
    pending = ['ATTRIBUTE_LOAD_IDENTITY', 'DEPENDENCY_CLOSURE', 'UNIT_AND_SCALE_CALIBRATION',
               'GLYPH_AND_CLEARANCE_CALIBRATION', 'SAVE_REOPEN_STABILITY']
    roles, views, unknown_marks = _observed_roles(objects)
    role_styles = {key: _unique(value) for key, value in roles.items()}
    pending += ['ROLE_UNOBSERVED:' + key for key, values in role_styles.items() if not values]
    if unknown_marks:
        pending.append('MARK_ROLE_MAPPING')
    text_styles = _display_styles(presentation, role_styles)
    attribute_study, differences = None, []
    if attributes is not None:
        attribute_study = read_attributes(attributes)
        differences = _attribute_differences(attribute_study, sheet)
    else:
        pending.append('ATTRIBUTE_SNAPSHOT')
    return seal({'schemaVersion': 'TemplateStudy.v1', 'status': 'STUDY_ONLY',
                 'productionEligible': False, 'templateId': drawing['Name'],
                 'source': {'modelPath': capture['modelPath'], 'drawing': drawing,
                            'captureDigest': digest(capture), 'objectsDigest': digest(objects),
                            'presentationDigest': digest(presentation)},
                 'units': 'NATIVE_SHEET_UNIT', 'sheetSizeNative': sheet, 'views': views,
                 'observedRoleStyles': role_styles, 'displayTextStyles': text_styles,
                 'unmappedMarkElements': unknown_marks, 'attributes': attribute_study,
                 'attributeDifferences': differences, 'pending': sorted(pending),
                 'scope': 'READ_ONLY_SAMPLE_NOT_TEMPLATE_CAPABILITY'})
