"""Company- and source-authorized Duplicate Review dashboard."""

from odoo import _, api, fields, models
from odoo.exceptions import AccessError


REVIEW_GROUP = 'gt_crm_duplicate_review.group_duplicate_review_user'


class DuplicateReviewDashboard(models.TransientModel):
    _name = 'gt.crm.duplicate.dashboard'
    _description = 'CRM Duplicate Review Dashboard'

    pending_count = fields.Integer(readonly=True)
    strong_count = fields.Integer(readonly=True)
    possible_count = fields.Integer(readonly=True)
    merge_approved_count = fields.Integer(readonly=True)
    merged_count = fields.Integer(readonly=True)
    not_duplicate_count = fields.Integer(readonly=True)
    stale_count = fields.Integer(readonly=True)
    invalid_count = fields.Integer(readonly=True)
    conflict_count = fields.Integer(readonly=True)
    recent_scan_status = fields.Char(readonly=True)

    @api.model
    def _dashboard_values(self):
        if not self.env.user.has_group(REVIEW_GROUP):
            raise AccessError(_('You are not allowed to view Duplicate Review metrics.'))
        company_ids = self.env.companies.ids
        Case = self.env['gt.crm.duplicate.case']

        def count(domain):
            # Case._search additionally intersects the current user's readable
            # crm.lead IDs for BOTH sources, so counts share queue authorization.
            return Case.search_count([('company_id', 'in', company_ids)] + domain)

        active_review = [('status', 'in', ['pending', 'stale', 'merge_approved'])]
        Scan = self.env['gt.crm.duplicate.scan']
        latest_scan = Scan.search([('company_id', 'in', company_ids)], order='write_date desc, id desc', limit=1)
        return {
            'pending_count': count([('status', '=', 'pending')]),
            'strong_count': count(active_review + [('confidence_band', '=', 'strong')]),
            'possible_count': count(active_review + [('confidence_band', '=', 'possible')]),
            'merge_approved_count': count([('status', '=', 'merge_approved')]),
            'merged_count': count([('status', '=', 'merged')]),
            'not_duplicate_count': count([('status', '=', 'not_duplicate')]),
            'stale_count': count([('status', '=', 'stale')]),
            'invalid_count': count([('status', '=', 'invalid')]),
            'conflict_count': count([('conflict_count', '>', 0)]),
            'recent_scan_status': latest_scan.state if latest_scan else _('No scan yet'),
        }

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.user.has_group(REVIEW_GROUP):
            raise AccessError(_('You are not allowed to create a Duplicate Review dashboard.'))
        values = self._dashboard_values()
        return super().create([dict(values) for _vals in vals_list])

    @api.model
    def default_get(self, fields_list):
        defaults = super().default_get(fields_list)
        if self.env.user.has_group(REVIEW_GROUP):
            defaults.update(self._dashboard_values())
        return defaults

    def write(self, vals):
        raise AccessError(_('Dashboard metrics are read only.'))

    @api.model
    def action_open_dashboard(self):
        if not self.env.user.has_group(REVIEW_GROUP):
            raise AccessError(_('You are not allowed to view Duplicate Review metrics.'))
        dashboard = self.create({})
        return {
            'type': 'ir.actions.act_window', 'name': _('Duplicate Review Dashboard'),
            'res_model': self._name, 'res_id': dashboard.id, 'view_mode': 'form',
            'target': 'current',
        }

    def action_refresh(self):
        self.ensure_one()
        if not self.env.user.has_group(REVIEW_GROUP):
            raise AccessError(_('You are not allowed to view Duplicate Review metrics.'))
        return super(DuplicateReviewDashboard, self).write(self._dashboard_values())

    def _open_cases(self, key):
        self.ensure_one()
        if not self.env.user.has_group(REVIEW_GROUP):
            raise AccessError(_('You are not allowed to open Duplicate Review cases.'))
        domains = {
            'pending': [('status', '=', 'pending')],
            'strong': [('confidence_band', '=', 'strong'), ('status', 'in', ['pending', 'stale', 'merge_approved'])],
            'possible': [('confidence_band', '=', 'possible'), ('status', 'in', ['pending', 'stale', 'merge_approved'])],
            'approved': [('status', '=', 'merge_approved')],
            'merged': [('status', '=', 'merged')],
            'not_duplicate': [('status', '=', 'not_duplicate')],
            'stale': [('status', '=', 'stale')],
            'invalid': [('status', '=', 'invalid')],
            'conflicts': [('conflict_count', '>', 0)],
        }
        if key not in domains:
            raise AccessError(_('This dashboard metric is not available.'))
        # Keep the action population identical to the company-scoped KPI. The
        # case model independently enforces both source CRM record rules.
        return {
            'type': 'ir.actions.act_window', 'name': _('Duplicate Cases'),
            'res_model': 'gt.crm.duplicate.case', 'view_mode': 'list,form',
            'domain': [('company_id', 'in', self.env.companies.ids)] + domains[key],
            'context': {'allowed_company_ids': self.env.companies.ids},
        }

    def action_pending(self): return self._open_cases('pending')
    def action_strong(self): return self._open_cases('strong')
    def action_possible(self): return self._open_cases('possible')
    def action_approved(self): return self._open_cases('approved')
    def action_merged(self): return self._open_cases('merged')
    def action_not_duplicate(self): return self._open_cases('not_duplicate')
    def action_stale(self): return self._open_cases('stale')
    def action_invalid(self): return self._open_cases('invalid')
    def action_conflicts(self): return self._open_cases('conflicts')
