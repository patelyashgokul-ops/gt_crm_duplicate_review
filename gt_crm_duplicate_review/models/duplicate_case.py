import hashlib
import logging

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError
from odoo.fields import Domain
from .matching import evaluate_pair


KEYS = ('gt_review_email_key', 'gt_review_phone_key', 'gt_review_mobile_key')
GROUP_LIMIT = 100
DEFAULT_BATCH_SIZE = 50
MAX_BATCH_SIZE = 200
SCAN_LOCK_NAMESPACE = 67209120
_logger = logging.getLogger(__name__)


class DuplicateCase(models.Model):
    _name = 'gt.crm.duplicate.case'
    _description = 'CRM Duplicate Review Case'
    _order = 'id desc'
    _check_company_auto = True

    company_id = fields.Many2one('res.company', required=True, index=True)
    lead_a_id = fields.Many2one('crm.lead', ondelete='set null', check_company=True)
    lead_b_id = fields.Many2one('crm.lead', ondelete='set null', check_company=True)
    lead_a_original_id = fields.Integer(index=True, readonly=True)
    lead_b_original_id = fields.Integer(index=True, readonly=True)
    lead_a_original_type = fields.Selection([('lead', 'Lead'), ('opportunity', 'Opportunity')], readonly=True)
    lead_b_original_type = fields.Selection([('lead', 'Lead'), ('opportunity', 'Opportunity')], readonly=True)
    match_type = fields.Selection([
        ('email_phone', 'Exact Email + Phone'), ('email_exact', 'Exact Email'),
        ('phone_exact', 'Exact Phone'), ('email_near', 'Near Email'),
    ], required=True)
    match_score = fields.Integer(required=True)
    confidence_band = fields.Selection([('strong', 'Strong Match'), ('review', 'Review Required'), ('possible', 'Possible Match')], required=True, default='review')
    matched_signals = fields.Json(default=lambda self: [])
    matched_signals_summary = fields.Text(compute='_compute_evidence_summaries')
    conflict_indicators = fields.Json(default=lambda self: [])
    conflict_summary = fields.Text(compute='_compute_evidence_summaries')
    conflict_count = fields.Integer(compute='_compute_conflict_count', store=True, index=True)
    shared_contact = fields.Boolean()
    last_evaluated = fields.Datetime()
    match_explanation = fields.Char(required=True)
    status = fields.Selection([
        ('pending', 'Pending'), ('stale', 'Stale'), ('not_duplicate', 'Not Duplicate'),
        ('merge_approved', 'Merge Approved'), ('merged', 'Merged'), ('invalid', 'Invalid'),
    ], required=True, default='pending', index=True)
    evidence_current = fields.Boolean(compute='_compute_evidence_current', store=True)
    reviewer_id = fields.Many2one('res.users')
    reviewed_at = fields.Datetime()
    decision_reason = fields.Text()
    source_fingerprint = fields.Char(required=True)
    approval_fingerprint = fields.Char(readonly=True)
    approved_by_id = fields.Many2one('res.users', readonly=True)
    approved_at = fields.Datetime(readonly=True)
    approval_reason = fields.Text(readonly=True)
    merge_survivor_id = fields.Many2one('crm.lead', ondelete='set null', readonly=True)
    merged_at = fields.Datetime(readonly=True)
    merged_by_id = fields.Many2one('res.users', readonly=True)
    merge_event_ref = fields.Char(readonly=True)
    merge_in_progress = fields.Boolean(default=False, readonly=True)
    event_ids = fields.One2many('gt.crm.duplicate.event', 'case_id', readonly=True)
    review_reason = fields.Text(readonly=True)
    lead_a_name = fields.Char(related='lead_a_id.name', string='Record A Name', readonly=True)
    lead_b_name = fields.Char(related='lead_b_id.name', string='Record B Name', readonly=True)
    lead_a_contact_name = fields.Char(related='lead_a_id.contact_name', string='Record A Contact Name', readonly=True)
    lead_b_contact_name = fields.Char(related='lead_b_id.contact_name', string='Record B Contact Name', readonly=True)
    lead_a_partner_id = fields.Many2one(related='lead_a_id.partner_id', string='Record A Customer', readonly=True)
    lead_b_partner_id = fields.Many2one(related='lead_b_id.partner_id', string='Record B Customer', readonly=True)
    lead_a_email = fields.Char(related='lead_a_id.email_from', string='Record A Email', readonly=True)
    lead_b_email = fields.Char(related='lead_b_id.email_from', string='Record B Email', readonly=True)
    lead_a_phone = fields.Char(related='lead_a_id.phone', string='Record A Phone', readonly=True)
    lead_b_phone = fields.Char(related='lead_b_id.phone', string='Record B Phone', readonly=True)
    lead_a_mobile = fields.Char(related='lead_a_id.mobile', string='Record A Mobile', readonly=True)
    lead_b_mobile = fields.Char(related='lead_b_id.mobile', string='Record B Mobile', readonly=True)
    lead_a_user_id = fields.Many2one(related='lead_a_id.user_id', string='Record A Salesperson', readonly=True)
    lead_b_user_id = fields.Many2one(related='lead_b_id.user_id', string='Record B Salesperson', readonly=True)
    lead_a_team_id = fields.Many2one(related='lead_a_id.team_id', string='Record A Sales Team', readonly=True)
    lead_b_team_id = fields.Many2one(related='lead_b_id.team_id', string='Record B Sales Team', readonly=True)
    lead_a_stage_id = fields.Many2one(related='lead_a_id.stage_id', string='Record A Stage', readonly=True)
    lead_b_stage_id = fields.Many2one(related='lead_b_id.stage_id', string='Record B Stage', readonly=True)
    currency_id = fields.Many2one(related='company_id.currency_id', readonly=True)
    lead_a_revenue = fields.Monetary(related='lead_a_id.expected_revenue', string='Record A Expected Revenue', currency_field='currency_id', readonly=True)
    lead_b_revenue = fields.Monetary(related='lead_b_id.expected_revenue', string='Record B Expected Revenue', currency_field='currency_id', readonly=True)
    lead_a_probability = fields.Float(related='lead_a_id.probability', string='Record A Probability', readonly=True)
    lead_b_probability = fields.Float(related='lead_b_id.probability', string='Record B Probability', readonly=True)
    lead_a_priority = fields.Selection(related='lead_a_id.priority', string='Record A Priority', readonly=True)
    lead_b_priority = fields.Selection(related='lead_b_id.priority', string='Record B Priority', readonly=True)
    lead_a_type = fields.Selection(related='lead_a_id.type', string='Record A Type', readonly=True)
    lead_b_type = fields.Selection(related='lead_b_id.type', string='Record B Type', readonly=True)
    lead_a_active = fields.Boolean(related='lead_a_id.active', string='Record A Active', readonly=True)
    lead_b_active = fields.Boolean(related='lead_b_id.active', string='Record B Active', readonly=True)
    lead_a_create_date = fields.Datetime(related='lead_a_id.create_date', string='Record A Created On', readonly=True)
    lead_b_create_date = fields.Datetime(related='lead_b_id.create_date', string='Record B Created On', readonly=True)
    lead_a_write_date = fields.Datetime(related='lead_a_id.write_date', string='Record A Last Updated', readonly=True)
    lead_b_write_date = fields.Datetime(related='lead_b_id.write_date', string='Record B Last Updated', readonly=True)

    _unique_pair = models.Constraint('unique(company_id, lead_a_id, lead_b_id)', 'This candidate pair already exists.')
    _unique_original_pair = models.Constraint('unique(company_id, lead_a_original_id, lead_b_original_id)', 'This historical candidate pair already exists.')
    _ordered_pair = models.Constraint('check(lead_a_id is null or lead_b_id is null or lead_a_id < lead_b_id)', 'Candidate pairs must contain two distinct leads in canonical order.')
    _distinct_original_pair = models.Constraint('check(lead_a_original_id is null or lead_b_original_id is null or lead_a_original_id < lead_b_original_id)', 'Historical source identifiers must be in canonical order.')

    @api.depends(
        'company_id', 'lead_a_id', 'lead_b_id', 'match_type', 'source_fingerprint',
        'lead_a_id.company_id', 'lead_b_id.company_id',
        'lead_a_id.name', 'lead_b_id.name', 'lead_a_id.partner_id', 'lead_b_id.partner_id',
        'lead_a_id.active', 'lead_b_id.active',
        'lead_a_id.gt_review_email_key', 'lead_b_id.gt_review_email_key',
        'lead_a_id.gt_review_email_domain', 'lead_b_id.gt_review_email_domain',
        'lead_a_id.gt_review_phone_key', 'lead_b_id.gt_review_phone_key',
        'lead_a_id.gt_review_mobile_key', 'lead_b_id.gt_review_mobile_key',
    )
    def _compute_evidence_current(self):
        for case in self:
            a, b = case.lead_a_id, case.lead_b_id
            current = bool(
                a and b and a.active and b.active and case.company_id
                and a.company_id == case.company_id and b.company_id == case.company_id
            )
            evaluated = evaluate_pair(a, b) if current else False
            case.evidence_current = bool(
                evaluated and evaluated['match_type'] == case.match_type
                and case.source_fingerprint == self._fingerprint(
                    a.id, b.id, case.match_type, evaluated['fingerprint_key'],
                )
            )

    @api.depends('conflict_indicators')
    def _compute_conflict_count(self):
        for case in self:
            case.conflict_count = len(case.conflict_indicators or [])

    @api.depends('matched_signals', 'conflict_indicators')
    def _compute_evidence_summaries(self):
        for case in self:
            case.matched_signals_summary = '\n'.join(f'• {signal}' for signal in case.matched_signals or [])
            case.conflict_summary = '\n'.join(f'• {conflict}' for conflict in case.conflict_indicators or [])

    @api.model
    def _fingerprint(self, a_id, b_id, kind, key):
        return hashlib.sha256(f'{a_id}:{b_id}:{kind}:{key}'.encode()).hexdigest()

    @api.constrains('company_id', 'lead_a_id', 'lead_b_id')
    def _check_pair_company(self):
        for case in self:
            if case.status != 'merged' and (not case.lead_a_id or not case.lead_b_id):
                raise ValidationError(self.env._('Both CRM records must exist for an unmerged case.'))
            if case.lead_a_id and (not case.lead_a_id.company_id or case.lead_a_id.company_id != case.company_id):
                raise ValidationError(self.env._('Both CRM records must belong to the case company.'))
            if case.lead_b_id and case.lead_b_id.company_id != case.company_id:
                raise ValidationError(self.env._('Both CRM records must belong to the case company.'))

    @api.model
    def _search(self, domain, offset=0, limit=None, order=None, *, active_test=True, bypass_access=False):
        if not self.env.su and not bypass_access:
            leads = self.env['crm.lead']
            readable = leads._search([])
            visible_pair = (
                Domain('lead_a_id', 'in', readable) & Domain('lead_b_id', 'in', readable)
            ) | (
                Domain('status', 'in', ['merged', 'invalid']) & (
                    (Domain('lead_a_id', 'in', readable) & Domain('lead_b_id', '=', False))
                    | (Domain('lead_a_id', '=', False) & Domain('lead_b_id', 'in', readable))
                    | (Domain('lead_a_id', '=', False) & Domain('lead_b_id', '=', False))
                )
            )
            domain = Domain(domain) & visible_pair & Domain('company_id', 'in', self.env.companies.ids)
        return super()._search(domain, offset, limit, order, active_test=active_test, bypass_access=bypass_access)

    def _check_source_access(self):
        if self and not self.env.su:
            # Read only the company metadata under narrowly scoped elevation so
            # Odoo's company-record-rule helper cannot suggest switching into
            # a hidden company after a forged direct-ID request.
            company_rows = self.sudo().read(['company_id'])
            active_company_ids = set(self.env.companies.ids)
            if any((row['company_id'] and row['company_id'][0] not in active_company_ids)
                   for row in company_rows):
                raise AccessError(self.env._('This duplicate case is not available in the active companies.'))
            source_ids = set(self.mapped('lead_a_id').ids + self.mapped('lead_b_id').ids)
            readable_ids = set(self.env['crm.lead'].search([('id', 'in', list(source_ids))]).ids)
            if source_ids != readable_ids:
                raise AccessError(self.env._('Both source CRM records must be readable to view a case.'))

    def read(self, fields=None, load='_classic_read'):
        self._check_source_access()
        return super().read(fields=fields, load=load)

    def web_read(self, specification):
        self._check_source_access()
        return super().web_read(specification)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError(self.env._('Candidate creation is reserved for the internal scanner.'))
        for vals in vals_list:
            a_id, b_id = vals.get('lead_a_id'), vals.get('lead_b_id')
            if a_id and b_id:
                vals.setdefault('lead_a_original_id', min(a_id, b_id))
                vals.setdefault('lead_b_original_id', max(a_id, b_id))
                leads = self.env['crm.lead'].sudo().browse([a_id, b_id]).exists()
                types = {lead.id: lead.type for lead in leads}
                vals.setdefault('lead_a_original_type', types.get(min(a_id, b_id)))
                vals.setdefault('lead_b_original_type', types.get(max(a_id, b_id)))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su:
            raise AccessError(self.env._('Candidate evidence is read only.'))
        return super().write(vals)

    def unlink(self):
        raise AccessError(self.env._('Candidate cases cannot be deleted.'))

    @api.model
    def _scan_notice(self, message, level='info'):
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': self.env._('CRM Duplicate Review'),
                'message': message,
                'type': level,
                'sticky': level == 'warning',
            },
        }

    def _open_source(self, field_name):
        self.ensure_one()
        self._check_source_access()
        lead = self[field_name]
        if not lead.exists():
            raise AccessError(self.env._('This CRM record is no longer available.'))
        lead.check_access('read')
        return {
            'type': 'ir.actions.act_window', 'name': lead.display_name,
            'res_model': 'crm.lead', 'res_id': lead.id, 'view_mode': 'form',
            'target': 'current', 'context': {'active_test': False},
        }

    def action_open_lead_a(self):
        return self._open_source('lead_a_id')

    def action_open_lead_b(self):
        return self._open_source('lead_b_id')

    def action_refresh_evidence(self):
        return self.action_recheck()

    @api.model
    def action_scan(self):
        if not self.env.user.has_group('gt_crm_duplicate_review.group_duplicate_review_manager'):
            raise AccessError(self.env._('Only Duplicate Review Managers may scan.'))
        company = self.env.company
        if company not in self.env.user.company_ids:
            raise AccessError(self.env._('You are not authorized to scan this company.'))
        return self._run_company_scan_batch(company, raise_if_locked=True)

    @api.model
    def _run_company_scan_batch(self, company, raise_if_locked=False):
        """Run one bounded batch for an explicitly authorized company."""
        self.env.cr.execute(
            'SELECT pg_try_advisory_xact_lock(%s, %s)',
            (SCAN_LOCK_NAMESPACE, company.id),
        )
        if not self.env.cr.fetchone()[0]:
            if raise_if_locked:
                raise ValidationError(self.env._('A duplicate scan is already running for this company.'))
            return self._scan_notice(self.env._('A duplicate scan is already running for this company.'), 'warning')
        cursor = self.env['gt.crm.duplicate.scan'].sudo().search([('company_id', '=', company.id)], limit=1)
        if not cursor:
            cursor = self.env['gt.crm.duplicate.scan'].sudo().create({'company_id': company.id})
        if cursor.state == 'blocked':
            cursor.write({'state': 'running', 'blocked_lead_id': 0, 'started_at': cursor.started_at or fields.Datetime.now()})
        elif cursor.state in ('complete', 'partial', 'failed'):
            cursor.write({
                'state': 'running', 'last_lead_id': 0, 'blocked_lead_id': 0,
                'fuzzy_truncated': False, 'incomplete_groups': 0,
                'records_evaluated': 0, 'candidate_pairs_evaluated': 0,
                'cases_created': 0, 'cases_refreshed': 0, 'cases_skipped': 0,
                'error_summary': False, 'started_at': fields.Datetime.now(), 'finished_at': False,
            })
        elif not cursor.started_at:
            cursor.started_at = fields.Datetime.now()
        size = self.env['ir.config_parameter'].sudo().get_int('gt_crm_duplicate_review.batch_size', DEFAULT_BATCH_SIZE)
        size = max(1, min(size, MAX_BATCH_SIZE))
        leads = self.env['crm.lead'].sudo().search([
            ('company_id', '=', company.id), ('active', '=', True), ('lost_reason_id', '=', False),
            ('id', '>', cursor.last_lead_id),
        ], order='id', limit=size)
        if not leads:
            partial = bool(cursor.incomplete_groups or cursor.fuzzy_truncated)
            cursor.write({
                'last_lead_id': 0, 'state': 'partial' if partial else 'complete',
                'finished_at': fields.Datetime.now(),
            })
            message = (self.env._('Scan is partial: %s oversized groups or fuzzy candidate sets were not fully evaluated.', cursor.incomplete_groups)
                       if partial else self.env._('The company scan is complete.'))
            return self._scan_notice(message, 'warning' if partial else 'info')
        for lead in leads:
            try:
                with self.env.cr.savepoint():
                    completed = self._scan_lead(lead, cursor)
            except Exception:
                _logger.exception('Duplicate scan batch failed for company %s at checkpoint %s', company.id, cursor.last_lead_id)
                cursor.write({
                    'state': 'failed', 'finished_at': fields.Datetime.now(),
                    'error_summary': self.env._('Batch failed; resume from the last committed CRM record.'),
                })
                return self._scan_notice(self.env._('Scan batch failed safely. The last checkpoint is retained; retry the scan to continue.'), 'danger')
            if not completed:
                return self._scan_notice(
                    self.env._('Scan paused at CRM record %s because one exact-match group exceeds the safe limit. Review and explicitly skip this group to continue.', lead.id),
                    'warning',
                )
            cursor.write({'last_lead_id': lead.id, 'records_evaluated': cursor.records_evaluated + 1})
        cursor.state = 'running'
        message = self.env._('Processed %s CRM records. Run the scan again to continue.', len(leads))
        if cursor.fuzzy_truncated:
            message += ' ' + self.env._('Near-email candidates were capped for at least one broad domain.')
        return self._scan_notice(message, 'warning' if cursor.fuzzy_truncated else 'info')

    @api.model
    def _scan_lead(self, lead, cursor):
        if not self.env.su and not self.env.user.has_group('gt_crm_duplicate_review.group_duplicate_review_manager'):
            raise AccessError(self.env._('Only Duplicate Review Managers may scan.'))
        for field in KEYS:
            key = lead[field]
            if not key:
                continue
            exact_domain = [('gt_review_email_key', '=', key)] if field == 'gt_review_email_key' else [
                '|', ('gt_review_phone_key', '=', key), ('gt_review_mobile_key', '=', key),
            ]
            base_domain = [
                ('company_id', '=', lead.company_id.id), ('active', '=', True),
                ('lost_reason_id', '=', False), ('id', '<', lead.id),
            ]
            exact = self.env['crm.lead'].sudo().search(
                base_domain + exact_domain, order='id', limit=GROUP_LIMIT + 1,
            )
            if len(exact) > GROUP_LIMIT:
                cursor.write({'state': 'blocked', 'blocked_lead_id': lead.id})
                return False
            candidates = {record.id: record for record in exact}
            if field == 'gt_review_email_key' and lead.gt_review_email_domain:
                near = self.env['crm.lead'].sudo().search(base_domain + [
                    ('gt_review_email_domain', '=', lead.gt_review_email_domain),
                    ('id', 'not in', list(candidates)),
                ], order='id', limit=GROUP_LIMIT + 1)
                if len(near) > GROUP_LIMIT:
                    cursor.fuzzy_truncated = True
                    cursor.incomplete_groups += 1
                else:
                    candidates.update({record.id: record for record in near})
            for other in candidates.values():
                cursor.candidate_pairs_evaluated += 1
                evaluated = evaluate_pair(lead, other)
                if not evaluated:
                    continue
                a, b = sorted((lead.id, other.id))
                digest = self._fingerprint(a, b, evaluated['match_type'], evaluated['fingerprint_key'])
                values = {
                    'match_type': evaluated['match_type'], 'match_score': evaluated['match_score'],
                    'confidence_band': evaluated['confidence_band'], 'matched_signals': evaluated['matched_signals'],
                    'conflict_indicators': evaluated['conflict_indicators'], 'shared_contact': evaluated['shared_contact'],
                    'match_explanation': '; '.join(evaluated['matched_signals']), 'source_fingerprint': digest,
                    'last_evaluated': fields.Datetime.now(),
                }
                case = self.sudo().search([('company_id', '=', lead.company_id.id), ('lead_a_original_id', '=', a), ('lead_b_original_id', '=', b)], limit=1)
                if case:
                    old_status = case.status
                    old_fingerprint = case.source_fingerprint
                    if old_status in ('pending', 'stale'):
                        values['status'] = 'pending'
                    elif old_status == 'not_duplicate' and old_fingerprint != digest:
                        values.update({'status': 'pending', 'review_reason': False})
                        case._append_event('reopened', old_status, 'pending', self.env._('Matching data changed during scan.'), digest)
                    elif old_status == 'merge_approved':
                        approval_digest = case._approval_digest(lead, other)
                        if approval_digest != case.approval_fingerprint:
                            values['status'] = 'stale'
                            case._append_event('approval_invalidated', old_status, 'stale', self.env._('Source data changed after approval.'), digest, approval_digest)
                    elif old_status in ('merged', 'invalid'):
                        cursor.cases_skipped += 1
                        continue
                    case.write(values)
                    cursor.cases_refreshed += 1
                else:
                    values.update({'company_id': lead.company_id.id, 'lead_a_id': a, 'lead_b_id': b})
                    self.sudo().create(values)
                    cursor.cases_created += 1
        return True

    @api.model
    def action_skip_blocked_group(self):
        if not self.env.user.has_group('gt_crm_duplicate_review.group_duplicate_review_manager'):
            raise AccessError(self.env._('Only Duplicate Review Managers may resume a blocked scan.'))
        company = self.env.company
        self.env.cr.execute(
            'SELECT pg_try_advisory_xact_lock(%s, %s)',
            (SCAN_LOCK_NAMESPACE, company.id),
        )
        if not self.env.cr.fetchone()[0]:
            raise ValidationError(self.env._('A duplicate scan is active for this company. Retry after it finishes.'))
        cursor = self.env['gt.crm.duplicate.scan'].sudo().search([('company_id', '=', company.id)], limit=1)
        if not cursor or cursor.state != 'blocked' or not cursor.blocked_lead_id:
            raise ValidationError(self.env._('There is no blocked candidate group to skip.'))
        cursor.write({
            'last_lead_id': cursor.blocked_lead_id,
            'blocked_lead_id': 0,
            'incomplete_groups': cursor.incomplete_groups + 1,
            'state': 'running',
        })
        return self._scan_notice(self.env._('Oversized group skipped. Run the scan again to continue; the final scan will be marked partial.'), 'warning')

    @api.model
    def _cron_scan_enabled_companies(self):
        if not (self.env.su or self.env.user.has_group('base.group_system')):
            raise AccessError(self.env._('Only a system administrator may run scheduled duplicate scans.'))
        companies = self.env['res.company'].sudo().search([('duplicate_review_scan_enabled', '=', True)])
        for company in companies:
            self.with_context(allowed_company_ids=[company.id])._run_company_scan_batch(company)
        return True


class DuplicateScan(models.Model):
    _name = 'gt.crm.duplicate.scan'
    _description = 'CRM Duplicate Scan Progress'

    company_id = fields.Many2one('res.company', required=True, index=True)
    last_lead_id = fields.Integer(default=0)
    blocked_lead_id = fields.Integer()
    fuzzy_truncated = fields.Boolean(default=False)
    started_at = fields.Datetime()
    finished_at = fields.Datetime()
    records_evaluated = fields.Integer(default=0)
    candidate_pairs_evaluated = fields.Integer(default=0)
    cases_created = fields.Integer(default=0)
    cases_refreshed = fields.Integer(default=0)
    cases_skipped = fields.Integer(default=0)
    incomplete_groups = fields.Integer(default=0)
    error_summary = fields.Char()
    state = fields.Selection([
        ('running', 'Running'), ('complete', 'Complete'), ('partial', 'Partial'),
        ('blocked', 'Blocked'), ('failed', 'Failed'),
    ], default='running', required=True, index=True)

    _unique_company = models.Constraint('unique(company_id)', 'Only one scan cursor may exist per company.')

    @api.model
    def _search(self, domain, offset=0, limit=None, order=None, *, active_test=True, bypass_access=False):
        if not self.env.su:
            if not self.env.user.has_group('gt_crm_duplicate_review.group_duplicate_review_user'):
                raise AccessError(self.env._('You are not allowed to view scan progress.'))
            domain = Domain(domain) & Domain('company_id', 'in', self.env.companies.ids)
        return super()._search(domain, offset, limit, order, active_test=active_test, bypass_access=bypass_access)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError(self.env._('Scan progress is created only by the scan service.'))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su:
            raise AccessError(self.env._('Scan progress is read only.'))
        return super().write(vals)

    def unlink(self):
        raise AccessError(self.env._('Scan progress cannot be deleted.'))


class ResCompanyDuplicateReview(models.Model):
    _inherit = 'res.company'

    duplicate_review_scan_enabled = fields.Boolean(
        string='Enable Scheduled Duplicate Review Scan', default=False,
        groups='base.group_system',
    )
