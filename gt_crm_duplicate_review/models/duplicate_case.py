import hashlib

from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError
from odoo.fields import Domain
from .matching import evaluate_pair


KEYS = ('gt_review_email_key', 'gt_review_phone_key', 'gt_review_mobile_key')
GROUP_LIMIT = 100
DEFAULT_BATCH_SIZE = 50
MAX_BATCH_SIZE = 200
SCAN_LOCK_NAMESPACE = 67209120


class DuplicateCase(models.Model):
    _name = 'gt.crm.duplicate.case'
    _description = 'CRM Duplicate Review Case'
    _order = 'id desc'
    _check_company_auto = True

    company_id = fields.Many2one('res.company', required=True, index=True)
    lead_a_id = fields.Many2one('crm.lead', required=True, ondelete='cascade', check_company=True)
    lead_b_id = fields.Many2one('crm.lead', required=True, ondelete='cascade', check_company=True)
    match_type = fields.Selection([
        ('email_phone', 'Exact Email + Phone'), ('email_exact', 'Exact Email'),
        ('phone_exact', 'Exact Phone'), ('email_near', 'Near Email'),
    ], required=True)
    match_score = fields.Integer(required=True)
    confidence_band = fields.Selection([('strong', 'Strong Match'), ('review', 'Review Required'), ('possible', 'Possible Match')], required=True)
    matched_signals = fields.Json(default=list)
    conflict_indicators = fields.Json(default=list)
    conflict_count = fields.Integer(compute='_compute_conflict_count')
    shared_contact = fields.Boolean()
    last_evaluated = fields.Datetime()
    match_explanation = fields.Char(required=True)
    status = fields.Selection(
        [('pending', 'Pending'), ('stale', 'Stale')],
        compute='_compute_status', store=True,
    )
    evidence_current = fields.Boolean(compute='_compute_evidence_current', store=True)
    reviewer_id = fields.Many2one('res.users')
    reviewed_at = fields.Datetime()
    decision_reason = fields.Text()
    source_fingerprint = fields.Char(required=True)
    lead_a_name = fields.Char(related='lead_a_id.name', readonly=True)
    lead_b_name = fields.Char(related='lead_b_id.name', readonly=True)
    lead_a_contact_name = fields.Char(related='lead_a_id.contact_name', readonly=True)
    lead_b_contact_name = fields.Char(related='lead_b_id.contact_name', readonly=True)
    lead_a_partner_id = fields.Many2one(related='lead_a_id.partner_id', readonly=True)
    lead_b_partner_id = fields.Many2one(related='lead_b_id.partner_id', readonly=True)
    lead_a_email = fields.Char(related='lead_a_id.email_from', readonly=True)
    lead_b_email = fields.Char(related='lead_b_id.email_from', readonly=True)
    lead_a_phone = fields.Char(related='lead_a_id.phone', readonly=True)
    lead_b_phone = fields.Char(related='lead_b_id.phone', readonly=True)
    lead_a_mobile = fields.Char(related='lead_a_id.mobile', readonly=True)
    lead_b_mobile = fields.Char(related='lead_b_id.mobile', readonly=True)
    lead_a_user_id = fields.Many2one(related='lead_a_id.user_id', readonly=True)
    lead_b_user_id = fields.Many2one(related='lead_b_id.user_id', readonly=True)
    lead_a_team_id = fields.Many2one(related='lead_a_id.team_id', readonly=True)
    lead_b_team_id = fields.Many2one(related='lead_b_id.team_id', readonly=True)
    lead_a_stage_id = fields.Many2one(related='lead_a_id.stage_id', readonly=True)
    lead_b_stage_id = fields.Many2one(related='lead_b_id.stage_id', readonly=True)
    currency_id = fields.Many2one(related='company_id.currency_id', readonly=True)
    lead_a_revenue = fields.Monetary(related='lead_a_id.expected_revenue', currency_field='currency_id', readonly=True)
    lead_b_revenue = fields.Monetary(related='lead_b_id.expected_revenue', currency_field='currency_id', readonly=True)
    lead_a_probability = fields.Float(related='lead_a_id.probability', readonly=True)
    lead_b_probability = fields.Float(related='lead_b_id.probability', readonly=True)
    lead_a_priority = fields.Selection(related='lead_a_id.priority', readonly=True)
    lead_b_priority = fields.Selection(related='lead_b_id.priority', readonly=True)
    lead_a_type = fields.Selection(related='lead_a_id.type', readonly=True)
    lead_b_type = fields.Selection(related='lead_b_id.type', readonly=True)
    lead_a_active = fields.Boolean(related='lead_a_id.active', readonly=True)
    lead_b_active = fields.Boolean(related='lead_b_id.active', readonly=True)
    lead_a_create_date = fields.Datetime(related='lead_a_id.create_date', readonly=True)
    lead_b_create_date = fields.Datetime(related='lead_b_id.create_date', readonly=True)
    lead_a_write_date = fields.Datetime(related='lead_a_id.write_date', readonly=True)
    lead_b_write_date = fields.Datetime(related='lead_b_id.write_date', readonly=True)

    _unique_pair = models.Constraint('unique(company_id, lead_a_id, lead_b_id)', 'This candidate pair already exists.')
    _ordered_pair = models.Constraint('check(lead_a_id < lead_b_id)', 'Candidate pairs must contain two distinct leads in canonical order.')

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

    @api.depends('evidence_current')
    def _compute_status(self):
        for case in self:
            case.status = 'pending' if case.evidence_current else 'stale'

    @api.depends('conflict_indicators')
    def _compute_conflict_count(self):
        for case in self:
            case.conflict_count = len(case.conflict_indicators or [])

    @api.model
    def _fingerprint(self, a_id, b_id, kind, key):
        return hashlib.sha256(f'{a_id}:{b_id}:{kind}:{key}'.encode()).hexdigest()

    @api.constrains('company_id', 'lead_a_id', 'lead_b_id')
    def _check_pair_company(self):
        for case in self:
            if not case.lead_a_id.company_id or case.lead_a_id.company_id != case.company_id or case.lead_b_id.company_id != case.company_id:
                raise ValidationError(self.env._('Both CRM records must belong to the case company.'))

    @api.model
    def _search(self, domain, offset=0, limit=None, order=None, *, active_test=True, bypass_access=False):
        if not self.env.su and not bypass_access:
            leads = self.env['crm.lead']
            readable = leads._search([])
            domain = Domain(domain) & Domain('lead_a_id', 'in', readable) & Domain('lead_b_id', 'in', readable)
        return super()._search(domain, offset, limit, order, active_test=active_test, bypass_access=bypass_access)

    def _check_source_access(self):
        if self and not self.env.su:
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
        self.ensure_one()
        if not self.env.user.has_group('gt_crm_duplicate_review.group_duplicate_review_manager'):
            raise AccessError(self.env._('Only Duplicate Review Managers may refresh matching evidence.'))
        self._check_source_access()
        a, b = self.lead_a_id, self.lead_b_id
        result = evaluate_pair(a, b) if a.exists() and b.exists() else False
        if not result:
            self.sudo().write({'source_fingerprint': 'stale', 'last_evaluated': fields.Datetime.now()})
            return self._scan_notice(self.env._('No actionable match remains. This case is now stale.'), 'warning')
        self.sudo().write({
            'match_type': result['match_type'], 'match_score': result['match_score'],
            'confidence_band': result['confidence_band'], 'matched_signals': result['matched_signals'],
            'conflict_indicators': result['conflict_indicators'], 'shared_contact': result['shared_contact'],
            'match_explanation': '; '.join(result['matched_signals']),
            'source_fingerprint': self._fingerprint(a.id, b.id, result['match_type'], result['fingerprint_key']),
            'last_evaluated': fields.Datetime.now(),
        })
        return self._scan_notice(self.env._('Matching evidence has been refreshed.'))

    @api.model
    def action_scan(self):
        if not self.env.user.has_group('gt_crm_duplicate_review.group_duplicate_review_manager'):
            raise AccessError(self.env._('Only Duplicate Review Managers may scan.'))
        company = self.env.company
        self.env.cr.execute(
            'SELECT pg_try_advisory_xact_lock(%s, %s)',
            (SCAN_LOCK_NAMESPACE, company.id),
        )
        if not self.env.cr.fetchone()[0]:
            raise ValidationError(self.env._('A duplicate scan is already running for this company.'))
        cursor = self.env['gt.crm.duplicate.scan'].sudo().search([('company_id', '=', company.id)], limit=1)
        if not cursor:
            cursor = self.env['gt.crm.duplicate.scan'].sudo().create({'company_id': company.id})
        if cursor.state == 'blocked':
            cursor.write({'state': 'running', 'blocked_lead_id': 0})
        size = self.env['ir.config_parameter'].sudo().get_int('gt_crm_duplicate_review.batch_size', DEFAULT_BATCH_SIZE)
        size = max(1, min(size, MAX_BATCH_SIZE))
        leads = self.env['crm.lead'].sudo().search([
            ('company_id', '=', company.id), ('active', '=', True), ('lost_reason_id', '=', False),
            ('id', '>', cursor.last_lead_id),
        ], order='id', limit=size)
        if not leads:
            cursor.write({'last_lead_id': 0, 'state': 'complete'})
            return self._scan_notice(self.env._('The company scan is complete.'))
        for lead in leads:
            if not self._scan_lead(lead, cursor):
                return self._scan_notice(
                    self.env._('Scan paused at CRM record %s because one exact-match group exceeds the safe limit.', lead.id),
                    'warning',
                )
            cursor.last_lead_id = lead.id
        cursor.state = 'running'
        return self._scan_notice(self.env._('Processed %s CRM records. Run the scan again to continue.', len(leads)))

    @api.model
    def _scan_lead(self, lead, cursor):
        if not self.env.su and not self.env.user.has_group('gt_crm_duplicate_review.group_duplicate_review_manager'):
            raise AccessError(self.env._('Only Duplicate Review Managers may scan.'))
        for field in KEYS:
            key = lead[field]
            if not key:
                continue
            if field == 'gt_review_email_key':
                candidate_domain = ['|', ('gt_review_email_key', '=', key), ('gt_review_email_domain', '=', lead.gt_review_email_domain)]
            else:
                candidate_domain = ['|', ('gt_review_phone_key', '=', key), ('gt_review_mobile_key', '=', key)]
            matches = self.env['crm.lead'].sudo().search([
                ('company_id', '=', lead.company_id.id), ('active', '=', True),
                ('lost_reason_id', '=', False),
                ('id', '<', lead.id), *candidate_domain,
            ], order='id', limit=GROUP_LIMIT + 1)
            if len(matches) > GROUP_LIMIT:
                cursor.write({'state': 'blocked', 'blocked_lead_id': lead.id})
                return False
            for other in matches:
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
                case = self.sudo().search([('company_id', '=', lead.company_id.id), ('lead_a_id', '=', a), ('lead_b_id', '=', b)], limit=1)
                if case:
                    if case.status == 'stale':
                        case.write(values)
                    else:
                        case.write(values)
                else:
                    values.update({'company_id': lead.company_id.id, 'lead_a_id': a, 'lead_b_id': b})
                    self.sudo().create(values)
        return True


class DuplicateScan(models.Model):
    _name = 'gt.crm.duplicate.scan'
    _description = 'CRM Duplicate Scan Progress'

    company_id = fields.Many2one('res.company', required=True, index=True)
    last_lead_id = fields.Integer(default=0)
    blocked_lead_id = fields.Integer()
    state = fields.Selection([('running', 'Running'), ('complete', 'Complete'), ('blocked', 'Blocked')], default='running', required=True)

    _unique_company = models.Constraint('unique(company_id)', 'Only one scan cursor may exist per company.')
