from odoo.tests import tagged, TransactionCase
from odoo.exceptions import AccessError
from unittest.mock import patch
import logging
import time

from ..models.normalization import normalize_email, normalize_phone
from ..models import duplicate_case

_logger = logging.getLogger(__name__)


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
        return self.Lead.create({'name': 'Duplicate test', 'company_id': self.company.id, **values})

    def test_normalization(self):
        self.assertEqual(self.env.ref('gt_crm_duplicate_review.menu_duplicate_review').name, 'Duplicate Review')
        self.assertEqual(self.env.ref('gt_crm_duplicate_review.action_duplicate_case').res_model, 'gt.crm.duplicate.case')
        self.assertEqual(normalize_email('  A.B+tag@Example.COM  '), 'a.b+tag@example.com')
        self.assertNotEqual(normalize_email('a@example.com'), normalize_email('b@example.com'))
        self.assertFalse(normalize_email('bad@'))
        self.assertFalse(normalize_email('a,b@example.com'))
        self.assertFalse(normalize_email('a..b@example.com'))
        self.assertFalse(normalize_email('a@-example.com'))
        self.assertFalse(normalize_email('a@ex\u00a0ample.com'))
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
        self.assertEqual(cases.match_type, 'email_phone')
        self.assertEqual(cases.match_score, 95)
        self.assertEqual(cases.confidence_band, 'strong')
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
        self.assertEqual(case.match_type, 'phone_exact')
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
        with self.assertRaises(AccessError):
            case.with_user(user).web_read({'id': {}})

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

    def test_evidence_becomes_stale_and_recovers(self):
        first = self._lead(email_from='original@example.com')
        second = self._lead(email_from='original@example.com')
        self.Case.with_user(self.manager).action_scan()
        case = self.Case.search([('lead_a_id', '=', first.id), ('lead_b_id', '=', second.id)])
        self.assertEqual(case.status, 'pending')
        second.email_from = 'changed@example.com'
        self.assertEqual(case.status, 'stale')
        first.email_from = 'changed@example.com'
        self.assertEqual(case.status, 'stale')
        self.Case.with_user(self.manager).action_scan()  # reach the end of the prior pass
        self.Case.with_user(self.manager).action_scan()  # refresh evidence on a new pass
        self.assertEqual(case.status, 'pending')
        second.active = False
        self.assertEqual(case.status, 'stale')

    def test_company_move_invalidates_evidence(self):
        first = self._lead(email_from='move@example.com')
        second = self._lead(email_from='move@example.com')
        self.Case.with_user(self.manager).action_scan()
        case = self.Case.search([('lead_a_id', '=', first.id), ('lead_b_id', '=', second.id)])
        other_company = self.env['res.company'].create({'name': 'Moved Lead Company'})
        second.company_id = other_company
        self.assertEqual(case.status, 'stale')

    def test_large_group_pauses_without_claiming_completion(self):
        leads = [self._lead(email_from='group@example.com') for _ in range(4)]
        with patch.object(duplicate_case, 'GROUP_LIMIT', 2):
            result = self.Case.with_user(self.manager).action_scan()
        self.assertEqual(result['params']['type'], 'warning')
        cursor = self.env['gt.crm.duplicate.scan'].sudo().search([('company_id', '=', self.company.id)])
        self.assertEqual(cursor.state, 'blocked')
        self.assertEqual(cursor.blocked_lead_id, leads[-1].id)
        self.assertEqual(cursor.last_lead_id, leads[-2].id)
        self.assertEqual(self.Case.search_count([]), 3)
        leads[-1].email_from = 'corrected@different-domain.com'
        with patch.object(duplicate_case, 'GROUP_LIMIT', 2):
            result = self.Case.with_user(self.manager).action_scan()
        self.assertEqual(result['params']['type'], 'info')
        self.assertEqual(cursor.state, 'running')
        self.assertEqual(cursor.blocked_lead_id, 0)
        self.assertEqual(cursor.last_lead_id, leads[-1].id)

    def test_configured_batch_size_and_completion(self):
        leads = [self._lead(name=f'Unique {i}', email_from=f'unique{i}@example.com') for i in range(5)]
        self.env['ir.config_parameter'].sudo().set_int('gt_crm_duplicate_review.batch_size', 2)
        cursor_model = self.env['gt.crm.duplicate.scan'].sudo()
        for expected in (leads[1].id, leads[3].id, leads[4].id):
            result = self.Case.with_user(self.manager).action_scan()
            self.assertEqual(result['params']['type'], 'info')
            self.assertEqual(cursor_model.search([('company_id', '=', self.company.id)]).last_lead_id, expected)
        self.Case.with_user(self.manager).action_scan()
        cursor = cursor_model.search([('company_id', '=', self.company.id)])
        self.assertEqual(cursor.state, 'complete')
        self.assertEqual(cursor.last_lead_id, 0)

    def test_concurrent_scan_lock(self):
        self._lead(email_from='lock@example.com')
        with self.env.registry.cursor() as other_cursor:
            other_cursor.execute(
                'SELECT pg_try_advisory_xact_lock(%s, %s)',
                (duplicate_case.SCAN_LOCK_NAMESPACE, self.company.id),
            )
            self.assertTrue(other_cursor.fetchone()[0])
            with self.assertRaisesRegex(Exception, 'already running'):
                self.Case.with_user(self.manager).action_scan()
        self.assertFalse(self.env['gt.crm.duplicate.scan'].sudo().search([('company_id', '=', self.company.id)]))

    def test_database_pair_constraints(self):
        self.env.cr.execute("""
            SELECT pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE conrelid = 'gt_crm_duplicate_case'::regclass
        """)
        definitions = [row[0] for row in self.env.cr.fetchall()]
        self.assertTrue(any('UNIQUE (company_id, lead_a_id, lead_b_id)' in definition for definition in definitions))
        self.assertTrue(any('lead_a_id < lead_b_id' in definition for definition in definitions))

    def test_deleted_source_removes_case(self):
        first = self._lead(email_from='deleted@example.com')
        second = self._lead(email_from='deleted@example.com')
        self.Case.with_user(self.manager).action_scan()
        case = self.Case.search([('lead_a_id', '=', first.id), ('lead_b_id', '=', second.id)])
        self.assertTrue(case)
        second.unlink()
        self.assertFalse(self.Case.search([('id', '=', case.id)]))

    def test_conservative_near_email_and_false_positive_control(self):
        a = self._lead(name='John Smith - Website Project', email_from='john.smith@example.com')
        b = self._lead(name='John Smith - Website Project', email_from='john.smit@example.com')
        c = self._lead(name='Sales Request', email_from='sales@example.com')
        d = self._lead(name='Support Request', email_from='support@example.com')
        self.Case.with_user(self.manager).action_scan()
        near = self.Case.search([('lead_a_id', '=', a.id), ('lead_b_id', '=', b.id)])
        self.assertTrue(near)
        self.assertEqual((near.match_type, near.match_score, near.confidence_band), ('email_near', 65, 'possible'))
        self.assertFalse(self.Case.search([('lead_a_id', '=', c.id), ('lead_b_id', '=', d.id)]))

    def test_shared_contact_and_conflicts_are_explanatory(self):
        a = self._lead(name='New ERP implementation', email_from='info@company.com', expected_revenue=1000)
        b = self._lead(name='Support renewal', email_from='info@company.com', expected_revenue=5000, priority='3')
        self.Case.with_user(self.manager).action_scan()
        case = self.Case.search([('lead_a_id', '=', a.id), ('lead_b_id', '=', b.id)])
        self.assertTrue(case.shared_contact)
        self.assertTrue(any('Shared contact warning' in line for line in case.matched_signals))
        self.assertTrue(any('different' in line for line in case.conflict_indicators))
        self.assertEqual(case.match_score, 85)

    def test_restricted_user_cannot_read_queue_search_read_or_group(self):
        a = self._lead(email_from='private@example.com', user_id=self.manager.id)
        b = self._lead(email_from='private@example.com', user_id=self.manager.id)
        self.Case.with_user(self.manager).action_scan()
        case = self.Case.search([('lead_a_id', '=', a.id), ('lead_b_id', '=', b.id)])
        user = self.env['res.users'].create({
            'name': 'Queue Restricted', 'login': 'queue_restricted_test',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id,
                                    self.env.ref('sales_team.group_sale_salesman').id,
                                    self.env.ref('gt_crm_duplicate_review.group_duplicate_review_user').id])],
        })
        model = self.Case.with_user(user)
        self.assertFalse(model.search_read([('id', '=', case.id)], ['match_score']))
        self.assertFalse(model.read_group([], ['company_id'], ['__count']))

    def test_near_phone_is_disabled(self):
        first = self._lead(name='Same Contact', phone='+1 650-253-0000')
        second = self._lead(name='Same Contact', phone='+1 650-253-0001')
        self.assertFalse(duplicate_case.evaluate_pair(first, second))

    def test_bounded_120_record_scan_benchmark(self):
        leads = self.Lead.create([{
            'name': f'Benchmark Opportunity {i:03}', 'company_id': self.company.id,
            'email_from': f'person{i:03}@unique{i:03}.example.com',
        } for i in range(120)])
        started = time.perf_counter()
        results = [self.Case.with_user(self.manager).action_scan() for _ in range(3)]
        results.append(self.Case.with_user(self.manager).action_scan())
        elapsed = time.perf_counter() - started
        self.assertEqual(len(leads), 120)
        self.assertTrue(all(result['type'] == 'ir.actions.client' for result in results))
        _logger.info('P2 bounded workload: 120 CRM records scanned in %.3f seconds across 4 bounded actions', elapsed)
