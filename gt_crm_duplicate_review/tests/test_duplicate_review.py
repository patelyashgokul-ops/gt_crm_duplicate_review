from odoo.tests import tagged, TransactionCase
from odoo.exceptions import AccessError

from ..models.normalization import normalize_email, normalize_phone


@tagged('post_install', '-at_install')
class TestDuplicateReview(TransactionCase):

    def setUp(self):
        super().setUp()
        self.company = self.env.company
        self.Case = self.env['gt.crm.duplicate.case']
        self.Lead = self.env['crm.lead']
        self.manager = self.env['res.users'].create({
            'name': 'Review Manager', 'login': 'review_manager_test',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id,
                                    self.env.ref('sales_team.group_sale_manager').id,
                                    self.env.ref('gt_crm_duplicate_review.group_duplicate_review_manager').id])],
        })

    def _lead(self, **values):
        return self.Lead.create(dict(name='Duplicate test', company_id=self.company.id, **values))

    def test_normalization(self):
        self.assertEqual(self.env.ref('gt_crm_duplicate_review.menu_duplicate_review').name, 'Duplicate Review')
        self.assertEqual(self.env.ref('gt_crm_duplicate_review.action_duplicate_case').res_model, 'gt.crm.duplicate.case')
        self.assertEqual(normalize_email('  A.B+tag@Example.COM  '), 'a.b+tag@example.com')
        self.assertNotEqual(normalize_email('a@example.com'), normalize_email('b@example.com'))
        self.assertFalse(normalize_email('bad@'))
        self.assertFalse(normalize_email(''))
        self.assertFalse(normalize_phone('5551234567'))
        self.assertFalse(normalize_phone('garbage'))
        self.assertEqual(normalize_phone('+1 650-253-0000'), '+16502530000')

    def test_exact_match_and_repeat(self):
        a = self._lead(email_from='First@Example.com', phone='+1 650-253-0000')
        b = self._lead(email_from=' first@example.com ', mobile='+1 (650) 253-0000')
        before = {x.id: (x.active, x.company_id.id) for x in (a, b)}
        self.Case.with_user(self.manager).action_scan()
        cases = self.Case.search([('lead_a_id', '=', a.id), ('lead_b_id', '=', b.id)])
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases.match_type, 'email')
        self.Case.with_user(self.manager).action_scan()
        self.assertEqual(len(self.Case.search([('lead_a_id', '=', a.id), ('lead_b_id', '=', b.id)])), 1)
        self.assertEqual({x.id: (x.active, x.company_id.id) for x in (a, b)}, before)

    def test_company_and_blank_exclusion(self):
        a = self._lead(email_from='same@example.com')
        other_company = self.env['res.company'].create({'name': 'Other Scan Company'})
        self.Lead.create({'name': 'Other', 'company_id': other_company.id, 'email_from': 'same@example.com'})
        self._lead(email_from=False, phone=False, mobile=False)
        self.Case.with_user(self.manager).action_scan()
        self.assertFalse(self.Case.search([('lead_a_id', '=', a.id)]))

    def test_company_less_lead_is_not_paired(self):
        self._lead(email_from='nocompany@example.com')
        without_company = self.Lead.create({'name': 'Company-less', 'company_id': False,
                                            'email_from': 'nocompany@example.com'})
        self.assertFalse(without_company.company_id)
        self.Case.with_user(self.manager).action_scan()
        self.assertFalse(self.Case.search([('lead_a_id', '=', without_company.id)]))
        self.assertFalse(self.Case.search([('lead_b_id', '=', without_company.id)]))

    def test_scan_permission_and_candidate_create(self):
        user = self.env['res.users'].create({
            'name': 'Review User', 'login': 'review_user_test',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id,
                                    self.env.ref('gt_crm_duplicate_review.group_duplicate_review_user').id])],
        })
        with self.assertRaises(AccessError):
            self.Case.with_user(user).action_scan()
        with self.assertRaises(AccessError):
            self.Case.with_user(user).create({'company_id': self.company.id})

    def test_phone_mobile_pair_and_canonical_order(self):
        first = self._lead(phone='+1 650-253-0000')
        second = self._lead(mobile='+1 650 253 0000')
        self.Case.with_user(self.manager).action_scan()
        case = self.Case.search([('lead_a_id', '=', first.id), ('lead_b_id', '=', second.id)])
        self.assertEqual(len(case), 1)
        self.assertEqual(case.match_type, 'phone')
        self.assertLess(case.lead_a_id.id, case.lead_b_id.id)

    def test_restricted_crm_user_cannot_see_case(self):
        first = self._lead(email_from='restricted@example.com', user_id=self.manager.id)
        second = self._lead(email_from='restricted@example.com', user_id=self.manager.id)
        self.Case.with_user(self.manager).action_scan()
        case = self.Case.search([('lead_a_id', '=', first.id), ('lead_b_id', '=', second.id)])
        self.assertTrue(case)
        user = self.env['res.users'].create({
            'name': 'Personal CRM Reviewer', 'login': 'personal_reviewer_test',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id,
                                    self.env.ref('sales_team.group_sale_salesman').id,
                                    self.env.ref('gt_crm_duplicate_review.group_duplicate_review_user').id])],
        })
        self.assertEqual(self.Case.with_user(user).search_count([('id', '=', case.id)]), 0)
        with self.assertRaises(AccessError):
            case.with_user(user).read(['match_explanation'])

    def test_scan_rollback(self):
        first = self._lead(email_from='rollback@example.com')
        second = self._lead(email_from='rollback@example.com')
        try:
            with self.env.cr.savepoint():
                self.Case.with_user(self.manager).action_scan()
                raise RuntimeError('test rollback')
        except RuntimeError:
            pass
        self.assertFalse(self.Case.search([('lead_a_id', '=', first.id), ('lead_b_id', '=', second.id)]))
