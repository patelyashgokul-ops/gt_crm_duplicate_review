from odoo import api, fields, models

from .normalization import normalize_email, normalize_phone


class CrmLead(models.Model):
    _inherit = 'crm.lead'

    mobile = fields.Char(string='Mobile', tracking=True)
    gt_review_email_key = fields.Char(compute='_compute_gt_review_keys', store=True, index='btree_not_null', copy=False)
    gt_review_phone_key = fields.Char(compute='_compute_gt_review_keys', store=True, index='btree_not_null', copy=False)
    gt_review_mobile_key = fields.Char(compute='_compute_gt_review_keys', store=True, index='btree_not_null', copy=False)

    @api.depends('email_from', 'phone', 'mobile')
    def _compute_gt_review_keys(self):
        for lead in self:
            lead.gt_review_email_key = normalize_email(lead.email_from)
            lead.gt_review_phone_key = normalize_phone(lead.phone)
            lead.gt_review_mobile_key = normalize_phone(lead.mobile)
