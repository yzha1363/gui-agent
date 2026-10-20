"""Select unambiguous text targets using the screenshot's physical coordinates."""

import math
import re
import unicodedata


class TargetError(ValueError):
    pass


class TargetNotFound(TargetError):
    """A fresh observation may find a target that has not appeared yet."""


class AmbiguousTarget(TargetError):
    """Selection needs an explicit occurrence rather than an automatic retry."""


def normalized_text(text):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', text)).casefold()


def select_text(report, query, min_confidence=0.5, contains=False, occurrence=None):
    """Return a physical desktop target from a screen OCR report.

    Matching ignores whitespace, case and full-width variants. Multiple
    matches require a 1-based occurrence, ordered from top to bottom then left
    to right. Missing and ambiguous targets use separate TargetError subclasses.
    """
    query = normalized_text(query)
    if not query:
        raise TargetError('Target text cannot be empty.')
    if not 0 <= min_confidence <= 1:
        raise TargetError('Confidence must be between 0 and 1.')
    source = report.get('source', {})
    if source.get('kind') != 'screen' or source.get('coordinate_space') != 'physical-desktop-pixels':
        raise TargetError('Click targeting requires a live screen capture, not an image file.')
    region = source['region']
    matches = []
    for result in report['results']:
        text = normalized_text(result['text'])
        confidence = float(result['confidence'])
        matched = query in text if contains else query == text
        if not matched or not math.isfinite(confidence) or confidence < min_confidence:
            continue
        left, top, right, bottom = map(float, result['bbox'])
        if not all(math.isfinite(n) for n in (left, top, right, bottom)):
            raise TargetError('Invalid OCR bounding box.')
        if not (0 <= left < right <= region['width'] and 0 <= top < bottom <= region['height']):
            raise TargetError('OCR bounding box falls outside the screenshot.')
        # Derive from original-image geometry rather than trusting a cached click point.
        x, y = (left + right) / 2 + region['left'], (top + bottom) / 2 + region['top']
        matches.append({'id': result['id'], 'text': result['text'], 'confidence': confidence,
                        'desktop_center': [round(x), round(y)],
                        'desktop_bbox': [left + region['left'], top + region['top'],
                                         right + region['left'], bottom + region['top']]})
    matches.sort(key=lambda item: (item['desktop_bbox'][1], item['desktop_bbox'][0]))
    if not matches:
        raise TargetNotFound(f'No text target matched: {query}')
    if occurrence is None and len(matches) != 1:
        raise AmbiguousTarget(f'{len(matches)} targets matched; specify a 1-based occurrence. Candidates: {matches}')
    index = 0 if occurrence is None else occurrence - 1
    if not 0 <= index < len(matches):
        raise TargetError(f'Occurrence must be in 1..{len(matches)}.')
    return matches[index]
