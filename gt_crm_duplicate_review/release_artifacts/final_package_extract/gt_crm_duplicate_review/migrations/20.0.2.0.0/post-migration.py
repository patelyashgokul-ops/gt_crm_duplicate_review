"""Keep exact-match cases created by P1 readable after the P2 schema change."""


def migrate(cr, version):
    cr.execute("""
        UPDATE gt_crm_duplicate_case
           SET match_type = CASE match_type
                   WHEN 'email' THEN 'email_exact'
                   WHEN 'phone' THEN 'phone_exact'
                   ELSE match_type
               END,
               match_score = CASE match_type
                   WHEN 'email' THEN 85
                   WHEN 'phone' THEN 80
                   ELSE LEAST(100, GREATEST(0, match_score))
               END,
               confidence_band = CASE match_type
                   WHEN 'email' THEN 'review'
                   WHEN 'phone' THEN 'review'
                   WHEN 'email_exact' THEN 'review'
                   WHEN 'phone_exact' THEN 'review'
                   WHEN 'email_phone' THEN 'strong'
                   WHEN 'email_near' THEN 'possible'
                   ELSE 'review'
               END,
               matched_signals = CASE
                   WHEN match_type = 'email' THEN '["Legacy exact email match; rescan to refresh evidence"]'::jsonb
                   WHEN match_type = 'phone' THEN '["Legacy exact phone match; rescan to refresh evidence"]'::jsonb
                   ELSE COALESCE(matched_signals, '[]'::jsonb)
               END,
               source_fingerprint = CASE
                   WHEN match_type IN ('email', 'phone') THEN 'legacy-rescan-required'
                   ELSE source_fingerprint
               END
         WHERE match_type IN ('email', 'phone')
    """)
