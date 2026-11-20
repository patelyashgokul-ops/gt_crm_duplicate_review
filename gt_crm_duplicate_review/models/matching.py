"""Deterministic, conservative duplicate signals and contextual conflicts."""

import re
from difflib import SequenceMatcher


GENERIC_MAILBOXES = frozenset({'admin', 'contact', 'hello', 'info', 'office', 'sales', 'support', 'team'})


def _local(email):
    return email.split('@', 1)[0] if email and '@' in email else ''


def _edit_distance_one(left, right):
    if abs(len(left) - len(right)) > 1:
        return False
    i = j = edits = 0
    while i < len(left) and j < len(right):
        if left[i] == right[j]:
            i += 1
            j += 1
            continue
        edits += 1
        if edits > 1:
            return False
        if len(left) >= len(right):
            i += 1
        if len(right) >= len(left):
            j += 1
    edits += (len(left) - i) + (len(right) - j)
    return edits == 1


def _normalized_name(lead):
    return re.sub(r'[^a-z0-9]+', ' ', (lead.name or '').casefold()).strip()


def evaluate_pair(a, b):
    """Return one primary signal, bounded heuristic score, evidence and conflicts."""
    email_exact = bool(a.gt_review_email_key and a.gt_review_email_key == b.gt_review_email_key)
    phones_a = {x for x in (a.gt_review_phone_key, a.gt_review_mobile_key) if x}
    phones_b = {x for x in (b.gt_review_phone_key, b.gt_review_mobile_key) if x}
    phone_exact = bool(phones_a & phones_b)
    evidence = []
    shared = False
    if email_exact:
        evidence.append('Same valid normalized email')
        shared = _local(a.gt_review_email_key) in GENERIC_MAILBOXES
    if phone_exact:
        evidence.append('Same valid normalized phone/mobile number')
        shared = shared or bool(a.partner_id and b.partner_id and a.partner_id.commercial_partner_id == b.partner_id.commercial_partner_id)

    if email_exact and phone_exact:
        kind, score = 'email_phone', 95
    elif email_exact:
        kind, score = 'email_exact', 85
    elif phone_exact:
        kind, score = 'phone_exact', 80
    else:
        kind, score = False, 0
        left, right = a.gt_review_email_key, b.gt_review_email_key
        if left and right and left.rsplit('@', 1)[1] == right.rsplit('@', 1)[1]:
            la, lb = _local(left), _local(right)
            names = _normalized_name(a), _normalized_name(b)
            similar_names = bool(names[0] and names[1] and SequenceMatcher(None, *names).ratio() >= 0.75)
            same_customer = bool(a.partner_id and a.partner_id == b.partner_id)
            if (min(len(la), len(lb)) >= 7 and la not in GENERIC_MAILBOXES and lb not in GENERIC_MAILBOXES
                    and _edit_distance_one(la, lb) and (similar_names or same_customer or phone_exact)):
                kind, score = 'email_near', 65
                evidence.append('Email local parts differ by one character; same domain and supporting CRM context')

    if not kind:
        return False
    if shared:
        evidence.append('Shared contact warning: generic mailbox or shared customer contact may support distinct opportunities')

    conflicts = []
    if a.user_id != b.user_id:
        conflicts.append('These records are assigned to different salespeople.')
    if a.team_id != b.team_id:
        conflicts.append('These records belong to different sales teams.')
    if a.stage_id != b.stage_id:
        conflicts.append('These records are in different pipeline stages.')
    if a.expected_revenue != b.expected_revenue:
        conflicts.append('Expected revenue values are different. Verify the commercial requirement.')
    if (a.name or '').strip().casefold() != (b.name or '').strip().casefold():
        conflicts.append('The opportunity names are different; these may be separate deals.')
    if a.type != b.type:
        conflicts.append('One record is a lead and the other is an opportunity.')
    if a.partner_id.commercial_partner_id != b.partner_id.commercial_partner_id:
        conflicts.append('These records have different customer associations.')
    if a.active != b.active:
        conflicts.append('One source record is archived and the other is active.')
    if a.priority != b.priority:
        conflicts.append('These records have different priorities.')
    if shared and not any('Shared contact warning' in item for item in evidence):
        evidence.append('Shared Contact warning: these details may be shared by multiple opportunities.')
    band = 'strong' if score >= 90 else 'review' if score >= 75 else 'possible'
    return {
        'match_type': kind,
        'match_score': max(0, min(100, score)),
        'confidence_band': band,
        'matched_signals': evidence,
        'conflict_indicators': conflicts,
        'shared_contact': shared,
        'fingerprint_key': (
            a.gt_review_email_key if kind in ('email_exact', 'email_phone')
            else '|'.join(sorted(phones_a & phones_b)) if kind == 'phone_exact'
            else '|'.join(sorted((a.gt_review_email_key, b.gt_review_email_key)))
        ),
    }
