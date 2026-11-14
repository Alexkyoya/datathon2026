"""Allocation-only projection onto the ordered reference polyline.

The metric is unweighted Euclidean distance in four fractional class weights.
Equal squared distances within 1e-12 choose the earliest (lower-score) segment.
No risk thresholds or historical returns enter this calculation.
"""
import json
import math
from pathlib import Path
from .contracts import CLASSES, PROFILE_ORDER, number

TIE_TOLERANCE = 1e-12


def load_references():
    return json.loads((Path(__file__).resolve().parents[1] /
                       'config/reference_portfolios.json').read_text())


def build_references(config, assets, policy):
    if not isinstance(config.get('version'), str) or not config['version']:
        raise ValueError('Reference portfolio version is required')
    if config.get('method') != 'unweighted_euclidean_allocation_projection':
        raise ValueError('Unsupported spectrum method')
    if config.get('asset_class_order') != list(CLASSES):
        raise ValueError('Reference class order mismatch')
    portfolios = config['portfolios']
    if [p['category'] for p in portfolios] != list(PROFILE_ORDER):
        raise ValueError('Five ordered reference categories are required')
    built = []
    for portfolio in portfolios:
        weights = portfolio['ticker_weights']
        if not weights or not math.isclose(math.fsum(
                number(w, 'reference weight') for w in weights.values()), 1,
                rel_tol=0, abs_tol=1e-10):
            raise ValueError('Reference ticker weights must total 100%')
        vector = dict.fromkeys(CLASSES, 0.0)
        for ticker, weight in weights.items():
            if ticker == 'CASH_CAD':
                cls = 'cash'
            else:
                if ticker not in assets:
                    raise ValueError(f'Unmapped reference asset: {ticker}')
                cls = assets[ticker]['asset_class']
            if cls not in CLASSES:
                raise ValueError('Unknown reference class')
            vector[cls] += weight
        for cls, weight in vector.items():
            low, high = policy['profiles'][portfolio['category']]['asset_class_ranges'][cls]
            if not low-1e-10 <= weight <= high+1e-10:
                raise ValueError(f"Reference {portfolio['category']} violates {cls} range")
        built.append({**portfolio, 'class_weights': vector})
    for a, b in zip(built, built[1:]):
        if math.fsum((a['class_weights'][c]-b['class_weights'][c])**2 for c in CLASSES) <= 1e-20:
            raise ValueError('Adjacent reference vectors must differ')
    return built


def project(weights, references):
    if set(weights) != set(CLASSES) or not math.isclose(math.fsum(
            number(w, 'portfolio class weight') for w in weights.values()), 1,
            rel_tol=0, abs_tol=1e-10):
        raise ValueError('Class vector must be nonnegative and total 100%')
    best = None
    for index, (left, right) in enumerate(zip(references, references[1:])):
        start = left['class_weights']
        delta = {c: right['class_weights'][c]-start[c] for c in CLASSES}
        length_sq = math.fsum(v*v for v in delta.values())
        if length_sq <= 1e-20:
            raise ValueError('Degenerate spectrum segment')
        t = max(0.0, min(1.0, math.fsum(
            (weights[c]-start[c])*delta[c] for c in CLASSES)/length_sq))
        if t < 1e-12:
            t = 0.0
        elif t > 1-1e-12:
            t = 1.0
        point = {c: start[c]+t*delta[c] for c in CLASSES}
        distance_sq = math.fsum((weights[c]-point[c])**2 for c in CLASSES)
        if best is None or distance_sq < best[0]-TIE_TOLERANCE:
            best = (distance_sq, index, t, point)
    distance_sq, index, t, point = best
    categories = [references[index]['category'], references[index+1]['category']]
    return {'categories': categories,
            'membership_weights': {categories[0]: 1-t, categories[1]: t},
            'score': index+t, 'normalized_score': (index+t)/4,
            'distance': math.sqrt(distance_sq),
            'projected_class_weights': point,
            'residual_class_weights': {c: weights[c]-point[c] for c in CLASSES}}


def classify_transition(before, after, target_profile, config, assets, policy):
    references = build_references(config, assets, policy)
    b, a = project(before, references), project(after, references)
    target = PROFILE_ORDER.index(target_profile)
    return {'reference_portfolio_version': config['version'],
            'method': config['method'], 'class_order': list(CLASSES),
            'score_range': [0, 4], 'target_profile': target_profile,
            'target_score': target, 'references': references,
            'before': b, 'after': a,
            'movement': {'score_delta': a['score']-b['score'],
                         'distance_to_target_before': abs(b['score']-target),
                         'distance_to_target_after': abs(a['score']-target),
                         'change_in_distance_to_target': abs(a['score']-target)-abs(b['score']-target)},
            'distance_unit': 'Euclidean distance in fractional asset-class weights',
            'tie_break': 'Squared distances within 1e-12 choose the earliest segment',
            'interpretation': 'Memberships are interpolation weights, not probabilities or a comprehensive risk measure. Distance measures reference mismatch; no match-quality cutoff is assumed.',
            'source': config['source'], 'assumptions': config['assumptions']}
