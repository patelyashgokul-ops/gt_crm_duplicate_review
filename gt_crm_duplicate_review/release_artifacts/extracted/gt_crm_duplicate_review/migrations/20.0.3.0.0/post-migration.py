"""Preserve P1/P2 case history across the controlled merge schema update."""


def migrate(cr, version):
    cr.execute("""
        UPDATE gt_crm_duplicate_case
           SET lead_a_original_id = LEAST(lead_a_id, lead_b_id),
               lead_b_original_id = GREATEST(lead_a_id, lead_b_id)
         WHERE lead_a_id IS NOT NULL AND lead_b_id IS NOT NULL
           AND (lead_a_original_id IS NULL OR lead_b_original_id IS NULL)
    """)
    cr.execute("""
        UPDATE gt_crm_duplicate_case AS c
           SET lead_a_original_type = a.type,
               lead_b_original_type = b.type
          FROM crm_lead AS a, crm_lead AS b
         WHERE a.id = c.lead_a_original_id AND b.id = c.lead_b_original_id
           AND (c.lead_a_original_type IS NULL OR c.lead_b_original_type IS NULL)
    """)
    cr.execute("""
        UPDATE gt_crm_duplicate_case
           SET status = CASE WHEN evidence_current THEN 'pending' ELSE 'stale' END
         WHERE status NOT IN ('pending', 'stale', 'not_duplicate', 'merge_approved', 'merged', 'invalid')
            OR status IS NULL
    """)
