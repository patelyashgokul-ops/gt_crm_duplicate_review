import hashlib

from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError
from odoo.fields import Domain


KEYS = ('gt_review_email_key', 'gt_review_phone_key', 'gt_review_mobile_key')
GROUP_LIMIT = 100
DEFAULT_BATCH_SIZE = 50
MAX_BATCH_SIZE = 200


class DuplicateCase(models.Model):
    _name = 'gt.crm.duplicate.case'
    _description = 'CRM Duplicate Review Case'
    _order = 'id desc'
    _check_company_auto = True

    company_id = fields.Many2one('res.company', required=True, index=True)
    lead_a_id = fields.Many2one('crm.lead', required=True, ondelete='cascade', check_company=True)
    lead_b_id = fields.Many2one('crm.lead', required=True, ondelete='cascade', check_company=True)
    match_type = fields.Selection([('email', 'Email'), ('phone', 'Phone')], required=True)
    match_score = fields.Integer(default=100, required=True)
    match_explanation = fields.Char(required=True)
    status = fields.Selection([('pending', 'Pending')], required=True, default='pending')
    reviewer_id = fields.Many2one('res.users')
    reviewed_at = fields.Datetime()
    decision_reason = fields.Text()
    source_fingerprint = fields.Char(required=True)

    _unique_pair = models.Constraint('unique(company_id, lead_a_id, lead_b_id)', 'This candidate pair already exists.')
    _ordered_pair = models.Constraint('check(lead_a_id < lead_b_id)', 'Candidate pairs must contain two distinct leads in canonical order.')

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
    def action_scan(self):
        if not self.env.user.has_group('gt_crm_duplicate_review.group_duplicate_review_manager'):
            raise AccessError(self.env._('Only Duplicate Review Managers may scan.'))
        company = self.env.company
        cursor = self.env['gt.crm.duplicate.scan'].sudo().search([('company_id', '=', company.id)], limit=1)
        if not cursor:
            cursor = self.env['gt.crm.duplicate.scan'].sudo().create({'company_id': company.id})
        if cursor.state == 'blocked':
            raise ValidationError(self.env._('Scan is paused: a matching group exceeded the safe group limit.'))
        size = self.env['ir.config_parameter'].sudo().get_int('gt_crm_duplicate_review.batch_size', DEFAULT_BATCH_SIZE)
        size = max(1, min(size, MAX_BATCH_SIZE))
        leads = self.env['crm.lead'].sudo().search([
            ('company_id', '=', company.id), ('active', '=', True), ('id', '>', cursor.last_lead_id),
        ], order='id', limit=size)
        if not leads:
            cursor.write({'last_lead_id': 0, 'state': 'complete'})
            return True
        for lead in leads:
            if not self._scan_lead(lead, cursor):
                return False
            cursor.last_lead_id = lead.id
        cursor.state = 'running'
        return True

    @api.model
    def _scan_lead(self, lead, cursor):
        if not self.env.su and not self.env.user.has_group('gt_crm_duplicate_review.group_duplicate_review_manager'):
            raise AccessError(self.env._('Only Duplicate Review Managers may scan.'))
        for field in KEYS:
            key = lead[field]
            if not key:
                continue
            other_fields = ('gt_review_email_key',) if field == 'gt_review_email_key' else ('gt_review_phone_key', 'gt_review_mobile_key')
            for other_field in other_fields:
                matches = self.env['crm.lead'].sudo().search([
                    ('company_id', '=', lead.company_id.id), ('active', '=', True),
                    ('id', '<', lead.id), (other_field, '=', key),
                ], order='id', limit=GROUP_LIMIT + 1)
                if len(matches) > GROUP_LIMIT:
                    cursor.write({'state': 'blocked', 'blocked_lead_id': lead.id})
                    return False
                for other in matches:
                    a, b = sorted((lead.id, other.id))
                    case = self.sudo().search([('company_id', '=', lead.company_id.id), ('lead_a_id', '=', a), ('lead_b_id', '=', b)], limit=1)
                    if case:
                        continue
                    kind = 'email' if field == 'gt_review_email_key' else 'phone'
                    digest = hashlib.sha256(('%s:%s:%s:%s' % (a, b, kind, key)).encode()).hexdigest()
                    self.sudo().create({
                        'company_id': lead.company_id.id, 'lead_a_id': a, 'lead_b_id': b,
                        'match_type': kind, 'match_explanation': self.env._('Exact normalized %s match', kind),
                        'source_fingerprint': digest,
                    })
        return True


class DuplicateScan(models.Model):
    _name = 'gt.crm.duplicate.scan'
    _description = 'CRM Duplicate Scan Progress'

    company_id = fields.Many2one('res.company', required=True, index=True)
    last_lead_id = fields.Integer(default=0)
    blocked_lead_id = fields.Integer()
    state = fields.Selection([('running', 'Running'), ('complete', 'Complete'), ('blocked', 'Blocked')], default='running', required=True)

    _unique_company = models.Constraint('unique(company_id)', 'Only one scan cursor may exist per company.')
