"""Controlled review decisions, approval snapshots and native CRM merge."""

import hashlib
import json
import logging
import uuid

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.fields import Domain

_logger = logging.getLogger(__name__)
MANAGER_GROUP = 'gt_crm_duplicate_review.group_duplicate_review_manager'


def _check_wizard_case_access(env, vals):
    """Resolve the form's default case without elevating its access checks."""
    case_id = vals.get('case_id') or env.context.get('default_case_id')
    case = env['gt.crm.duplicate.case'].browse(case_id).exists()
    if not case:
        raise AccessError(_('This duplicate case is not available.'))
    case.check_access('read')
    case._check_source_access()
    return case


class DuplicateEvent(models.Model):
    _name = 'gt.crm.duplicate.event'
    _description = 'CRM Duplicate Review Audit Event'
    _order = 'event_at, id'
    _check_company_auto = True

    case_id = fields.Many2one('gt.crm.duplicate.case', required=True, ondelete='restrict', index=True, check_company=True)
    company_id = fields.Many2one('res.company', required=True, index=True)
    event_type = fields.Selection([
        ('reviewed', 'Candidate Reviewed'), ('not_duplicate', 'Not Duplicate'),
        ('reopened', 'Review Reopened'), ('approval', 'Merge Approved'),
        ('approval_withdrawn', 'Approval Withdrawn'), ('approval_invalidated', 'Approval Invalidated'),
        ('confirmation', 'Merge Confirmation'), ('merged', 'Merge Completed'),
        ('merge_failed', 'Merge Failed'), ('invalidated', 'Case Invalidated'),
        ('rechecked', 'Evidence Rechecked'),
    ], required=True, index=True)
    actor_id = fields.Many2one('res.users', required=True, readonly=True)
    event_at = fields.Datetime(required=True, readonly=True, default=fields.Datetime.now)
    previous_status = fields.Char(readonly=True)
    new_status = fields.Char(readonly=True)
    reason = fields.Text(readonly=True)
    source_fingerprint = fields.Char(readonly=True)
    approval_fingerprint = fields.Char(readonly=True)
    original_lead_a_id = fields.Integer(readonly=True)
    original_lead_b_id = fields.Integer(readonly=True)
    survivor_id = fields.Integer(readonly=True)
    event_ref = fields.Char(readonly=True)
    metadata = fields.Json(readonly=True, default=dict)

    @api.model_create_multi
    def create(self, vals_list):
        # The event writer injects server-derived identity; public RPC has no ACL.
        if not self.env.su:
            raise AccessError(_('Audit events can only be written by workflow methods.'))
        for vals in vals_list:
            vals['actor_id'] = self.env.uid
            vals['event_at'] = fields.Datetime.now()
        return super().create(vals_list)

    def write(self, vals):
        raise AccessError(_('Audit events are append-only.'))

    def unlink(self):
        raise AccessError(_('Audit events cannot be deleted.'))

    @api.model
    def _search(self, domain, offset=0, limit=None, order=None, *, active_test=True, bypass_access=False):
        if not self.env.su and not bypass_access:
            visible_cases = self.env['gt.crm.duplicate.case']
            domain = Domain('case_id', 'in', visible_cases._search([])) & Domain('company_id', 'in', self.env.companies.ids) & Domain(domain)
        return super()._search(domain, offset, limit, order, active_test=active_test, bypass_access=bypass_access)

    def _check_case_read_access(self):
        if self and not self.env.su:
            # Resolve only event company/case links internally first. This
            # avoids Odoo's generic company-switch suggestion on direct IDs.
            rows = self.sudo().read(['company_id', 'case_id'])
            active_company_ids = set(self.env.companies.ids)
            if any((row['company_id'] and row['company_id'][0] not in active_company_ids)
                   for row in rows):
                raise AccessError(_('This review history is not available in the active companies.'))
            case_ids = [row['case_id'][0] for row in rows if row['case_id']]
            cases = self.env['gt.crm.duplicate.case'].browse(case_ids)
            cases.check_access('read')
            cases._check_source_access()

    def read(self, fields=None, load='_classic_read'):
        self._check_case_read_access()
        return super().read(fields=fields, load=load)

    def web_read(self, specification):
        self._check_case_read_access()
        return super().web_read(specification)


class DuplicateCaseWorkflow(models.Model):
    _inherit = 'gt.crm.duplicate.case'

    def write(self, vals):
        protected = {
            'status', 'reviewer_id', 'reviewed_at', 'decision_reason', 'review_reason',
            'approval_fingerprint', 'approved_by_id', 'approved_at', 'approval_reason',
            'merge_survivor_id', 'merged_at', 'merged_by_id', 'merge_event_ref',
            'merge_in_progress',
            'lead_a_original_id', 'lead_b_original_id', 'lead_a_original_type', 'lead_b_original_type',
        }
        if protected.intersection(vals) and not self.env.su:
            raise AccessError(_('Review decisions and protected evidence must use the authorized workflow.'))
        return super().write(vals)


    def _require_manager(self):
        if not self.env.user.has_group(MANAGER_GROUP):
            raise AccessError(_('Only Duplicate Review Managers may perform this action.'))

    def _lock_case_and_sources(self):
        self.ensure_one()
        self.env.cr.execute('SELECT id FROM gt_crm_duplicate_case WHERE id = %s FOR UPDATE', (self.id,))
        if not self.env.cr.fetchone():
            raise UserError(_('This duplicate case is no longer available.'))
        lead_ids = sorted({x for x in (self.lead_a_id.id, self.lead_b_id.id) if x})
        if lead_ids:
            self.env.cr.execute('SELECT id FROM crm_lead WHERE id = ANY(%s) ORDER BY id FOR UPDATE', (lead_ids,))
            locked = {row[0] for row in self.env.cr.fetchall()}
            if locked != set(lead_ids):
                raise UserError(_('A source CRM record changed or was removed. Recheck this case.'))
        self.invalidate_recordset()
        self.lead_a_id.invalidate_recordset()
        self.lead_b_id.invalidate_recordset()

    def _validate_pair(self, require_write=False):
        self.ensure_one()
        a, b = self.lead_a_id.exists(), self.lead_b_id.exists()
        if not a or not b:
            raise UserError(_('Both CRM records must still exist. Recheck this case.'))
        if not self.company_id or a.company_id != self.company_id or b.company_id != self.company_id:
            raise UserError(_('These CRM records belong to different companies and cannot be merged.'))
        if a.id >= b.id:
            raise ValidationError(_('The CRM source pair is not in canonical order.'))
        if not a.active or not b.active or a.lost_reason_id or b.lost_reason_id:
            raise UserError(_('Archived or lost CRM records are not eligible for a duplicate-review merge.'))
        if a.won_status == 'won' or b.won_status == 'won':
            raise UserError(_('Won CRM records cannot be merged through this workflow.'))
        if not self.env.user.has_group(MANAGER_GROUP) or self.company_id not in self.env.user.company_ids:
            raise AccessError(_('You are not authorized for this company.'))
        for lead in (a, b):
            lead.check_access('read')
            if require_write:
                lead.check_access('write')
                lead.check_access('unlink')
        return a, b

    def _fresh_evaluation(self):
        from .matching import evaluate_pair
        a, b = self._validate_pair()
        result = evaluate_pair(a, b)
        if not result:
            raise UserError(_('The records no longer have actionable matching evidence. Recheck this case.'))
        fingerprint = self._fingerprint(a.id, b.id, result['match_type'], result['fingerprint_key'])
        return a, b, result, fingerprint

    @api.model
    def _approval_digest(self, a, b):
        # Material source fields relevant to identity, ownership, conflicts and survivor ranking.
        snapshot = []
        for lead in sorted((a, b), key=lambda record: record.id):
            snapshot.append({
                'id': lead.id, 'company': lead.company_id.id, 'active': lead.active,
                'type': lead.type, 'email': lead.gt_review_email_key,
                'phone': lead.gt_review_phone_key, 'mobile': lead.gt_review_mobile_key,
                'partner': lead.partner_id.id, 'name': lead.name or '',
                'user': lead.user_id.id, 'team': lead.team_id.id, 'stage': lead.stage_id.id,
                'revenue': lead.expected_revenue, 'probability': lead.probability,
                'priority': lead.priority, 'lost_reason': lead.lost_reason_id.id,
                'won_status': lead.won_status, 'description': lead.description or '',
                'tags': sorted(lead.tag_ids.ids), 'campaign': lead.campaign_id.id,
                'source': lead.source_id.id, 'medium': lead.medium_id.id,
                'deadline': fields.Date.to_string(lead.date_deadline) if lead.date_deadline else False,
            })
        payload = json.dumps(snapshot, sort_keys=True, separators=(',', ':'), default=str)
        return hashlib.sha256(payload.encode()).hexdigest()

    def _append_event(self, event_type, old_status=None, new_status=None, reason=None,
                      source_fingerprint=None, approval_fingerprint=None,
                      survivor_id=None, event_ref=None, metadata=None):
        self.ensure_one()
        self.env['gt.crm.duplicate.event'].sudo().create({
            'case_id': self.id, 'company_id': self.company_id.id, 'event_type': event_type,
            'previous_status': old_status, 'new_status': new_status, 'reason': reason,
            'source_fingerprint': source_fingerprint or self.source_fingerprint,
            'approval_fingerprint': approval_fingerprint or self.approval_fingerprint,
            'original_lead_a_id': self.lead_a_original_id,
            'original_lead_b_id': self.lead_b_original_id,
            'survivor_id': survivor_id, 'event_ref': event_ref, 'metadata': metadata or {},
        })

    def action_open_review_wizard(self):
        self.ensure_one()
        self._require_manager()
        self._check_source_access()
        return {
            'type': 'ir.actions.act_window', 'name': _('Review Duplicate Candidate'),
            'res_model': 'gt.crm.duplicate.review.wizard', 'view_mode': 'form',
            'target': 'new', 'context': {'default_case_id': self.id},
        }

    def action_recheck(self):
        self.ensure_one()
        self._require_manager()
        self._lock_case_and_sources()
        old_status = self.status
        if old_status == 'merged' or old_status == 'invalid':
            raise UserError(_('This case is terminal and cannot be rechecked.'))
        try:
            a, b = self._validate_pair()
        except UserError:
            self.sudo().write({'status': 'invalid', 'last_evaluated': fields.Datetime.now()})
            self._append_event('invalidated', old_status, 'invalid', _('Sources are unavailable or no longer eligible for review.'))
            return True
        from .matching import evaluate_pair
        result = evaluate_pair(a, b)
        if not result:
            self.sudo().write({'status': 'invalid', 'last_evaluated': fields.Datetime.now()})
            self._append_event('invalidated', old_status, 'invalid', _('Sources are unavailable or no actionable match remains.'))
            return True
        fingerprint = self._fingerprint(a.id, b.id, result['match_type'], result['fingerprint_key'])
        new_status = old_status
        values = {
            'match_type': result['match_type'], 'match_score': result['match_score'],
            'confidence_band': result['confidence_band'], 'matched_signals': result['matched_signals'],
            'conflict_indicators': result['conflict_indicators'], 'shared_contact': result['shared_contact'],
            'match_explanation': '; '.join(result['matched_signals']),
            'source_fingerprint': fingerprint, 'last_evaluated': fields.Datetime.now(),
        }
        if old_status == 'not_duplicate' and fingerprint != self.source_fingerprint:
            new_status = 'pending'
            values.update({'status': new_status, 'review_reason': False})
        elif old_status == 'merge_approved':
            approval_digest = self._approval_digest(a, b)
            if approval_digest != self.approval_fingerprint:
                new_status = 'stale'
                values['status'] = new_status
                self._append_event('approval_invalidated', old_status, new_status, _('Source data changed after approval.'))
        elif old_status in ('pending', 'stale'):
            new_status = 'pending'
            values['status'] = new_status
        self.sudo().write(values)
        event = 'reopened' if old_status == 'not_duplicate' and new_status == 'pending' else 'rechecked'
        self._append_event(event, old_status, new_status, _('Matching evidence was re-evaluated.'), source_fingerprint=fingerprint)
        return True

    def _decide_not_duplicate(self, reason_code, reason):
        self.ensure_one()
        self._require_manager()
        self._lock_case_and_sources()
        if reason_code not in {'different_requirements', 'different_contacts', 'shared_phone', 'generic_email', 'separate_opportunities', 'false_positive', 'other'}:
            raise ValidationError(_('Choose a supported Not Duplicate reason.'))
        if len((reason or '').strip()) < 8:
            raise ValidationError(_('A meaningful review reason is required (at least 8 characters).'))
        if self.status not in ('pending', 'stale'):
            raise UserError(_('Only a pending or stale case can be marked Not Duplicate.'))
        a, b, result, fingerprint = self._fresh_evaluation()
        if fingerprint != self.source_fingerprint:
            raise UserError(_('Matching evidence changed. Recheck the case before recording a decision.'))
        old_status = self.status
        self.sudo().write({
            'status': 'not_duplicate', 'reviewer_id': self.env.user.id,
            'reviewed_at': fields.Datetime.now(), 'decision_reason': reason.strip(),
            'review_reason': reason.strip(),
        })
        self._append_event('not_duplicate', old_status, 'not_duplicate', f'{reason_code}: {reason.strip()}', fingerprint)
        return True

    def _approve_merge(self, reason):
        self.ensure_one()
        self._require_manager()
        self._lock_case_and_sources()
        if self.status != 'pending':
            raise UserError(_('Only a current pending case can be approved for merge.'))
        a, b = self._validate_pair(require_write=True)
        result = self._fresh_evaluation()[2:]
        evaluated, fingerprint = result
        if fingerprint != self.source_fingerprint:
            raise UserError(_('These CRM records have changed. Recheck them before approval.'))
        approval_digest = self._approval_digest(a, b)
        reason = (reason or '').strip()
        if len(reason) < 8:
            raise ValidationError(_('Enter a meaningful approval reason (at least 8 characters).'))
        self.sudo().write({
            'status': 'merge_approved', 'approved_by_id': self.env.user.id,
            'approved_at': fields.Datetime.now(), 'approval_reason': reason,
            'approval_fingerprint': approval_digest, 'reviewer_id': self.env.user.id,
            'reviewed_at': fields.Datetime.now(), 'decision_reason': reason,
        })
        self._append_event('approval', 'pending', 'merge_approved', reason,
                           source_fingerprint=fingerprint, approval_fingerprint=approval_digest)
        return True

    def action_open_merge_confirmation(self):
        self.ensure_one()
        self._require_manager()
        if self.status != 'merge_approved':
            raise UserError(_('This case has not been approved for merge.'))
        self._check_source_access()
        return {
            'type': 'ir.actions.act_window', 'name': _('Confirm CRM Merge'),
            'res_model': 'gt.crm.duplicate.merge.confirm.wizard', 'view_mode': 'form',
            'target': 'new', 'context': {'default_case_id': self.id},
        }

    def action_withdraw_merge_approval(self, reason):
        self.ensure_one()
        self._require_manager()
        self._lock_case_and_sources()
        if self.status != 'merge_approved':
            raise UserError(_('Only an approved merge can be withdrawn.'))
        if len((reason or '').strip()) < 8:
            raise ValidationError(_('Enter a meaningful reason for withdrawing approval.'))
        self.sudo().write({'status': 'pending', 'approval_fingerprint': False,
                           'approved_by_id': False, 'approved_at': False,
                           'approval_reason': False, 'review_reason': reason.strip()})
        self._append_event('approval_withdrawn', 'merge_approved', 'pending', reason.strip())
        return True

    def _execute_approved_merge(self, acknowledged):
        self.ensure_one()
        self._require_manager()
        if not acknowledged:
            raise ValidationError(_('You must explicitly acknowledge the irreversible CRM merge.'))
        self._lock_case_and_sources()
        if self.status != 'merge_approved':
            raise UserError(_('This duplicate case is not approved for merge.'))
        a, b = self._validate_pair(require_write=True)
        current_digest = self._approval_digest(a, b)
        if current_digest != self.approval_fingerprint:
            old = self.status
            self.sudo().write({'status': 'stale'})
            self._append_event('approval_invalidated', old, 'stale', _('Source data changed after approval.'), approval_fingerprint=self.approval_fingerprint)
            return {
                'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'title': _('Approval is stale'),
                           'message': _('These CRM records have changed since the merge was approved. Review and approve them again.'),
                           'type': 'warning', 'sticky': True},
            }
        _source_a, _source_b, result, fingerprint = self._fresh_evaluation()
        if fingerprint != self.source_fingerprint:
            raise UserError(_('Matching evidence is stale. Recheck and approve this case again.'))
        lead_model = self.env['crm.lead']
        sources = lead_model.browse(sorted((a.id, b.id)))
        ref = str(uuid.uuid4())
        old_status = self.status
        self._append_event('confirmation', old_status, old_status, _('Explicit irreversible merge confirmation supplied.'),
                           source_fingerprint=fingerprint, approval_fingerprint=current_digest,
                           event_ref=ref, metadata={'source_ids': sources.ids})
        try:
            # A savepoint ensures native partial writes are rolled back if it raises.
            with self.env.cr.savepoint():
                self.sudo().write({'merge_in_progress': True})
                survivor = sources.merge_opportunity(auto_unlink=True)
                survivor.invalidate_recordset()
                if len(survivor) != 1 or not survivor.exists() or survivor._name != 'crm.lead':
                    raise UserError(_('The native CRM merge returned no verifiable survivor.'))
                if survivor.company_id != self.company_id:
                    raise UserError(_('The native CRM merge produced a record outside the case company.'))
                remaining = lead_model.sudo().browse(sources.ids).exists()
                if remaining.ids != survivor.ids:
                    raise UserError(_('The native CRM merge did not consolidate exactly the approved source pair.'))
                # Native merge order is verified from crm.lead._sort_by_confidence_level.
                # Save original IDs and retain a link to the surviving source only.
                self.sudo().write({
                    'lead_a_id': survivor.id, 'lead_b_id': False,
                    'merge_survivor_id': survivor.id, 'status': 'merged',
                    'merged_at': fields.Datetime.now(), 'merged_by_id': self.env.user.id,
                    'merge_event_ref': ref, 'last_evaluated': fields.Datetime.now(),
                    'merge_in_progress': False,
                })
                self._append_event('merged', old_status, 'merged', _('Native CRM merge completed and survivor verified.'),
                                   source_fingerprint=fingerprint, approval_fingerprint=current_digest,
                                   survivor_id=survivor.id, event_ref=ref,
                                   metadata={'source_ids': sources.ids, 'survivor_id': survivor.id,
                                             'result_type': survivor.type, 'expected_revenue': survivor.expected_revenue})
        except Exception as error:
            _logger.exception('Native duplicate-review merge failed for case %s', self.id)
            if isinstance(error, (AccessError, UserError, ValidationError)):
                raise
            raise UserError(_('The native CRM merge could not be completed. No successful merge has been recorded.')) from error
        return {
            'type': 'ir.actions.act_window', 'name': _('Merged CRM Record'),
            'res_model': 'crm.lead', 'res_id': survivor.id, 'view_mode': 'form',
            'target': 'current',
        }


class CrmLeadReviewHooks(models.Model):
    _inherit = 'crm.lead'

    _MATERIAL_REVIEW_FIELDS = frozenset({
        'company_id', 'active', 'type', 'email_from', 'phone', 'mobile',
        'gt_review_email_key', 'gt_review_phone_key', 'gt_review_mobile_key',
        'partner_id', 'name', 'user_id', 'team_id', 'stage_id',
        'expected_revenue', 'probability', 'priority', 'lost_reason_id', 'won_status',
    })

    def _review_cases(self):
        return self.env['gt.crm.duplicate.case'].sudo().search([
            '|', ('lead_a_id', 'in', self.ids), ('lead_b_id', 'in', self.ids),
        ])

    def write(self, vals):
        result = super().write(vals)
        changed = self._MATERIAL_REVIEW_FIELDS.intersection(vals)
        if changed:
            for case in self._review_cases():
                if case.merge_in_progress or case.status in ('merged', 'invalid', 'not_duplicate'):
                    continue
                old_status = case.status
                if old_status in ('pending', 'merge_approved'):
                    case.sudo().write({'status': 'stale'})
                    if old_status == 'merge_approved':
                        case._append_event('approval_invalidated', old_status, 'stale',
                                           _('A matching or merge-relevant source field changed after approval.'))
        return result

    def unlink(self):
        cases = self._review_cases()
        for case in cases:
            if case.merge_in_progress or case.status in ('merged', 'invalid'):
                continue
            old_status = case.status
            case.sudo().write({'status': 'invalid'})
            case._append_event('invalidated', old_status, 'invalid',
                               _('A source CRM record was removed outside a verified duplicate-review merge.'))
        return super().unlink()


class DuplicateReviewWizard(models.TransientModel):
    _name = 'gt.crm.duplicate.review.wizard'
    _description = 'Review CRM Duplicate Candidate'

    case_id = fields.Many2one('gt.crm.duplicate.case', required=True, readonly=True)
    match_type = fields.Selection(related='case_id.match_type', readonly=True)
    match_score = fields.Integer(related='case_id.match_score', readonly=True)
    conflict_summary = fields.Text(related='case_id.conflict_summary', readonly=True)
    status = fields.Selection(related='case_id.status', readonly=True)
    reason_code = fields.Selection([
        ('different_requirements', 'Different commercial requirements'),
        ('different_contacts', 'Different contacts in the same company'),
        ('shared_phone', 'Shared office phone number'), ('generic_email', 'Shared generic email'),
        ('separate_opportunities', 'Separate sales opportunities'),
        ('false_positive', 'False-positive similarity'), ('other', 'Other'),
    ])
    reason = fields.Text()

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.user.has_group(MANAGER_GROUP):
            raise AccessError(_('Only Duplicate Review Managers may create review wizards.'))
        for vals in vals_list:
            _check_wizard_case_access(self.env, vals)
        return super().create(vals_list)

    def write(self, vals):
        if 'case_id' in vals:
            raise AccessError(_('The case attached to a review wizard cannot be changed.'))
        return super().write(vals)

    def action_not_duplicate(self):
        self.ensure_one()
        self.case_id._decide_not_duplicate(self.reason_code, self.reason)
        return {'type': 'ir.actions.act_window_close'}

    def action_approve_merge(self):
        self.ensure_one()
        self.case_id._approve_merge(self.reason)
        return {'type': 'ir.actions.act_window_close'}

    def action_recheck(self):
        self.ensure_one()
        self.case_id.action_recheck()
        return {'type': 'ir.actions.act_window_close'}

    def action_withdraw_approval(self):
        self.ensure_one()
        self.case_id.action_withdraw_merge_approval(self.reason)
        return {'type': 'ir.actions.act_window_close'}


class DuplicateMergeConfirmWizard(models.TransientModel):
    _name = 'gt.crm.duplicate.merge.confirm.wizard'
    _description = 'Confirm Approved CRM Merge'

    case_id = fields.Many2one('gt.crm.duplicate.case', required=True, readonly=True)
    approved_by_id = fields.Many2one(related='case_id.approved_by_id', readonly=True)
    approved_at = fields.Datetime(related='case_id.approved_at', readonly=True)
    approval_reason = fields.Text(related='case_id.approval_reason', readonly=True)
    match_explanation = fields.Char(related='case_id.match_explanation', readonly=True)
    conflict_summary = fields.Text(related='case_id.conflict_summary', readonly=True)
    acknowledge_irreversible = fields.Boolean(required=True, default=False)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.user.has_group(MANAGER_GROUP):
            raise AccessError(_('Only Duplicate Review Managers may create merge confirmations.'))
        for vals in vals_list:
            _check_wizard_case_access(self.env, vals)
        return super().create(vals_list)

    def write(self, vals):
        if 'case_id' in vals:
            raise AccessError(_('The case attached to a merge confirmation cannot be changed.'))
        return super().write(vals)

    def action_confirm_merge(self):
        self.ensure_one()
        if not self.acknowledge_irreversible:
            raise ValidationError(_('Explicitly acknowledge the irreversible CRM merge to continue.'))
        return self.case_id._execute_approved_merge(self.acknowledge_irreversible)
