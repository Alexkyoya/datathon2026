"""Placing a client: questionnaire -> risk profile (stored, never inferred from holdings).

Second Look classifies clients by risk tolerance, not by past performance. A short
questionnaire (horizon, capacity for loss, willingness to take risk, objective,
experience) gives a score. The score maps to one of the five NBF profiles, then two
caps apply: a short horizon or a low capacity for loss limits the profile no matter
how much risk the client says they want.

Everything here is a TEAM ASSUMPTION except the profile names and their asset-class
ranges, which come from National Bank Financial (config/policy.json). The output is a
stored client in the component's format; the rule engine reads `profile` from that
store and never from a trade request.
"""
from .contracts import PROFILE_ORDER, identifier, number

QUESTIONNAIRE_VERSION = 'placement-1.0'

# Loss tolerance per profile, mirrors data/profiles.json "assumptions.loss_tolerance".
LOSS_TOLERANCE = {'Income': 0.10, 'Conservative': 0.15, 'Balanced': 0.20,
                  'Growth': 0.30, 'Maximum growth': 0.40}

OBJECTIVES = {'preserve': ['capital_preservation'], 'income': ['income', 'stability'],
              'balanced': ['balanced_growth'], 'growth': ['long_term_growth'],
              'max_growth': ['maximum_growth']}

QUESTIONS = [
    {'key': 'horizon_years', 'kind': 'years',
     'text': 'In how many years do you expect to start withdrawing a significant part of this money?',
     'scores': [(3, 1), (5, 2), (10, 3), (15, 4), (None, 5)],   # upper bound (exclusive) -> score
     'labels': {1: 'Less than 3 years', 2: '3 to 5 years', 3: '5 to 10 years',
                4: '10 to 15 years', 5: '15 years or more'}},
    {'key': 'capacity', 'kind': 'choice',
     'text': 'If this portfolio lost 20% of its value, how would it affect your plans?',
     'options': {'severe': 1, 'significant': 2, 'moderate': 3, 'minor': 4, 'none': 5},
     'labels': {'severe': 'I would have to change my plans immediately',
                'significant': 'It would significantly delay my goals',
                'moderate': 'Uncomfortable but manageable',
                'minor': 'Minor impact; I have other income and savings',
                'none': 'No impact on my plans'}},
    {'key': 'willingness', 'kind': 'choice',
     'text': 'Which statement best describes your attitude to short-term losses?',
     'options': {'avoid': 1, 'small': 2, 'moderate': 3, 'large': 4, 'any': 5},
     'labels': {'avoid': 'I want to avoid losses, even at the cost of lower returns',
                'small': 'I can accept small, temporary losses',
                'moderate': 'I can accept moderate losses for better long-term returns',
                'large': 'I can accept large swings for higher growth',
                'any': 'Volatility does not concern me over the long run'}},
    {'key': 'objective', 'kind': 'choice',
     'text': 'What is the main objective for this money?',
     'options': {'preserve': 1, 'income': 2, 'balanced': 3, 'growth': 4, 'max_growth': 5},
     'labels': {'preserve': 'Preserve capital', 'income': 'Generate income with some stability',
                'balanced': 'Balance growth and stability', 'growth': 'Grow capital over the long term',
                'max_growth': 'Maximise long-term growth'}},
    {'key': 'experience', 'kind': 'choice',
     'text': 'How would you describe your investment experience?',
     'options': {'none': 1, 'basic': 2, 'intermediate': 3, 'advanced': 4, 'professional': 5},
     'labels': {'none': 'None', 'basic': 'Basic (savings, GICs, a few funds)',
                'intermediate': 'Intermediate (stocks, ETFs, understands volatility)',
                'advanced': 'Advanced (follows markets, understands risk measures)',
                'professional': 'Professional or equivalent'}},
]

# Five questions scored 1..5 -> total 5..25 -> band.
SCORE_BANDS = [(5, 8, 'Income'), (9, 12, 'Conservative'), (13, 16, 'Balanced'),
               (17, 20, 'Growth'), (21, 25, 'Maximum growth')]

# Caps. The horizon cap follows the horizon assumptions in data/profiles.json
# (Income < 3y, Conservative 3-5y, Balanced 5-12y, Growth 10y+, Maximum growth 15y+).
HORIZON_CAP = {1: 'Income', 2: 'Conservative', 3: 'Balanced', 4: 'Growth', 5: 'Maximum growth'}
# A client who cannot absorb a 20% loss is not placed above Conservative, whatever they say.
CAPACITY_CAP = {'severe': 'Income', 'significant': 'Conservative'}

ASSUMPTIONS = [
    'Placement uses the sum of five 1-5 scores, then horizon and loss-capacity caps (team assumption).',
    'Profile names and asset-class ranges are NBF (config/policy.json); bands and caps are ours.',
    'Loss tolerance fractions per profile mirror data/profiles.json and are context for agents only.',
    'Holdings never influence placement: the profile is stored, and the rule engine reads it from the store.',
]


def _horizon_score(years):
    years = number(years, 'horizon_years')
    for bound, score in QUESTIONS[0]['scores']:
        if bound is None or years < bound:
            return score
    raise AssertionError('unreachable')


def score_answers(answers):
    """Return per-question scores. Raises ValueError on a missing or unknown answer."""
    if not isinstance(answers, dict):
        raise ValueError('answers must be an object')
    missing = [q['key'] for q in QUESTIONS if q['key'] not in answers]
    if missing:
        raise ValueError(f'Missing answers: {", ".join(missing)}')
    scores = {}
    for q in QUESTIONS:
        value = answers[q['key']]
        if q['kind'] == 'years':
            scores[q['key']] = _horizon_score(value)
        else:
            if value not in q['options']:
                raise ValueError(f'Unknown answer {value!r} for {q["key"]}; options: {sorted(q["options"])}')
            scores[q['key']] = q['options'][value]
    return scores


def place_investor(answers):
    """Map questionnaire answers to a profile, with the reasoning needed to explain it."""
    scores = score_answers(answers)
    total = sum(scores.values())
    band = next(p for lo, hi, p in SCORE_BANDS if lo <= total <= hi)
    caps = {'horizon': HORIZON_CAP[scores['horizon_years']]}
    if answers['capacity'] in CAPACITY_CAP:
        caps['capacity'] = CAPACITY_CAP[answers['capacity']]
    profile = min([band, *caps.values()], key=PROFILE_ORDER.index)
    applied = {k: v for k, v in caps.items() if PROFILE_ORDER.index(v) < PROFILE_ORDER.index(band)}
    return {
        'questionnaire_version': QUESTIONNAIRE_VERSION,
        'scores': scores, 'total': total, 'band_profile': band,
        'caps': caps, 'caps_applied': applied, 'profile': profile,
        'loss_tolerance_fraction': LOSS_TOLERANCE[profile],
        'objectives': OBJECTIVES[answers['objective']],
        'explanation': _explain(total, band, applied, profile),
        'assumptions': ASSUMPTIONS,
    }


def _explain(total, band, applied, profile):
    text = f'Score {total}/25 places the client in the {band} band.'
    if applied:
        reasons = ' and '.join(f'{k} cap ({v})' for k, v in applied.items())
        text += f' The {reasons} lowers the placement to {profile}.'
    else:
        text += ' No cap applies.'
    return text + ' Holdings and past performance play no part in this placement.'


def build_client(client_id, name, age, answers, cash_cad, holdings, holdings_as_of,
                 preferences=None, fictional=True):
    """Stored client record in the component format (see examples/clients/*.json).

    holdings: {ticker: units}. cash_cad: cash ledger in CAD. preferences: optional
    {'excluded_tickers': [...], 'excluded_sectors': [...]}.
    """
    identifier(client_id)
    if not isinstance(name, str) or not name.strip():
        raise ValueError('name is required')
    number(age, 'age')
    number(cash_cad, 'cash_cad')
    if not isinstance(holdings, dict) or any(number(v, f'holding {t}') < 0 for t, v in holdings.items()):
        raise ValueError('holdings must map ticker to non-negative units')
    prefs = {'excluded_tickers': [], 'excluded_sectors': []}
    prefs.update(preferences or {})
    placement = place_investor(answers)
    return {
        'client_id': client_id, 'name': name.strip(), 'age': age,
        'profile': placement['profile'],
        'horizon_years': number(answers['horizon_years'], 'horizon_years'),
        'objectives': placement['objectives'],
        'loss_tolerance_fraction': placement['loss_tolerance_fraction'],
        'preferences': prefs,
        'cash_cad': float(cash_cad), 'holdings_as_of': holdings_as_of, 'holdings': holdings,
        'placement': {k: placement[k] for k in ('questionnaire_version', 'scores', 'total',
                                                 'band_profile', 'caps', 'caps_applied', 'explanation')},
        'questionnaire_answers': dict(answers),
        'fictional': bool(fictional),
        'source': 'questionnaire (second_look.questionnaire.build_client)',
    }


# Fictional answers for the three personas, derived from the case brief. Used by tests and
# scripts/place_client.py to show the questionnaire reproduces the stored profiles.
PERSONA_ANSWERS = {
    'robert': {'horizon_years': 4, 'capacity': 'significant', 'willingness': 'small',
               'objective': 'preserve', 'experience': 'advanced'},
    'mateo': {'horizon_years': 10, 'capacity': 'moderate', 'willingness': 'moderate',
              'objective': 'balanced', 'experience': 'intermediate'},
    'amira': {'horizon_years': 15, 'capacity': 'minor', 'willingness': 'large',
              'objective': 'growth', 'experience': 'intermediate'},
}
