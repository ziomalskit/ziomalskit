"""Test-only worker catalog, preserving the original hashed structural fixture.

The original fixture infers scalar types and merges dynamic branches. This
adapter catalog expresses their V3 dynamic-combo contract explicitly so the
real converter can exercise selection-dependent ordering without guessing.
"""
import copy
from tests.test_workflows import CATALOG


def adapter_catalog():
    catalog = copy.deepcopy(CATALOG)
    catalog['BasicScheduler']['input']['required']['denoise'][0] = 'FLOAT'
    for kind, selector, branches in (
        ('ResizeImageMaskAlt', 'resize_type', {
            'scale total pixels': ['aspect_ratio', 'megapixels', 'megapixel_priority', 'multiple_of', 'crop'],
            'match size': ['multiple_of', 'crop'],
        }),
        ('BlockSparseAttention', 'selection', {'sol-attn': ['tau'], 'sla': ['keep_percent']}),
    ):
        fields = catalog[kind]['input']['required']
        variants = [dict(key=key, inputs={'required': {name: fields[selector + '.' + name] for name in names}})
                    for key, names in branches.items()]
        catalog[kind]['input']['required'] = {
            name: (['COMFY_DYNAMICCOMBO_V3', {'options': variants}] if name == selector else spec)
            for name, spec in fields.items() if not name.startswith(selector + '.')
        }
    # CustomCombo's dynamically growing trailing rows are empty options, rather
    # than required data copied from a different instance of the same class.
    for name, spec in catalog['CustomCombo']['input']['required'].items():
        if name.startswith('option'):
            spec[1]['default'] = ''
    return catalog

ADAPTER_CATALOG = adapter_catalog()
