# CRM Duplicate Review — Customer Guide

**Publisher:** Golok Technology  
**Supported platform:** Odoo 20 Community  
**Module:** `gt_crm_duplicate_review`  
**Support and demo:** [patelyash.gokul@gmail.com](mailto:patelyash.gokul@gmail.com)

## 1. What the app does

CRM Duplicate Review identifies potentially similar `crm.lead` leads and opportunities. It provides match evidence, context warnings, a review queue, manager approval and a separate confirmation that invokes Odoo's native CRM merge. It never merges automatically.

A high similarity score does not guarantee that two opportunities represent the same business requirement. Shared customers, switchboards and generic mailboxes can occur on legitimate separate deals.

## 2. Install

1. Install Odoo 20 Community with the CRM application.
2. Add the `gt_crm_duplicate_review` module folder to an Odoo addons path.
3. Restart Odoo, update the Apps list, then install **CRM Duplicate Review**.
4. Confirm the CRM menu includes **Duplicate Review**.

The module depends on Community `crm`; it does not require Enterprise apps or an external service. The installed Odoo CRM merge implementation is used for approved merges.

## 3. Configure users and permissions

Open **Settings → Users & Companies → Users** and assign the least privilege role needed:

- **Duplicate Review User** can read review cases only when they can read both CRM source records and the company. They cannot decide, approve or merge.
- **Duplicate Review Manager** can scan and submit review/approval/confirmation actions, subject to the same underlying CRM record rules and required CRM merge permissions.

The duplicate-review groups do not grant additional CRM lead access. A manager who cannot read or write both leads cannot force a merge through this addon.

## 4. Manual scanning

Go to **CRM → Duplicate Review → Review Queue** or **Dashboard**. A manager runs a scan for the active company using the scan action. Scans run in bounded batches; run the action again to continue. Oversized candidate groups are reported as incomplete and require the supported skip/resume action. An incomplete scan is not reported as a complete scan.

Company-less leads are excluded from automatic pair discovery. Candidates are never generated across companies.

## 5. Scheduled scanning

Scheduled scanning is disabled by default. A system administrator can enable **Enable Scheduled Duplicate Review Scan** on the relevant company in company settings. The scheduled action processes enabled companies in bounded, company-scoped batches. Review visibility is checked separately for each interactive user.

Use **CRM → Duplicate Review → Scan History** to see company, progress, status, counts and safe error details. `partial`, `failed` or `blocked` status means work remains or needs review.

## 6. Match scores and evidence

Scores rank matching evidence; they are not probabilities:

| Evidence | Score | Classification |
|---|---:|---|
| Exact email plus exact phone/mobile | 95 | Strong Match |
| Exact email | 85 | Review Required |
| Exact phone/mobile | 80 | Review Required |
| Conservative near-email | 65 | Possible Match |
| Near-phone | 60 only if safely supported; otherwise disabled | Possible Match |

Email normalization trims surrounding whitespace and ignores case; it does not remove plus tags or dots. Phone normalization uses valid parsed international numbers and does not equate ambiguous local values. Generic mailboxes and shared contacts produce cautionary context.

## 7. Review a case

Open a case from **Review Queue**. Read the classification, score, matched signals, freshness indicator, conflict summary and source comparison. Use **Open Record A/B** to open source records you are authorized to access.

Conflict warnings are informational. Different opportunity names, owners, teams, stages, customers, priorities or revenue can indicate separate work. The addon does not overwrite CRM fields during review.

### Mark Not Duplicate

Managers choose a controlled reason and provide the required explanation. This leaves both CRM records unchanged. An unchanged repeat scan preserves the decision; material matching-data changes can reopen review while retaining the old event history.

### Approve a merge

Approval records the manager, time, reason and approved source snapshot. It does not merge the records. If relevant CRM data changes, the approval becomes stale and must be reviewed again.

### Confirm and merge

Open the separate **Execute Approved Merge** confirmation. Re-read the warnings and acknowledge the irreversible native CRM merge. The server rechecks current records, company, CRM permissions, approval fingerprint and pair. It then calls the verified Odoo CRM merge method and checks the actual survivor. A failed or stale operation is not shown as merged.

Odoo's native merge chooses the survivor and fields according to its own rules. Expected revenue is not summed by this addon. Review actual business differences before confirming.

## 8. Review history

The **Review History** section records review, decision, approval, confirmation, invalidation and successful merge events. Events are created by validated server workflows and cannot be edited or deleted through ordinary addon access. This is application-level protection; a database administrator can still alter database contents.

## 9. Dashboard definitions

- **Pending Reviews:** visible pending cases.
- **Strong Matches / Possible Matches:** visible current-evidence cases in active review/approval states.
- **Merge Approved:** approved but not completed cases.
- **Successfully Merged:** cases with verified native merge outcome.
- **Not Duplicate, Stale, Invalid:** visible cases in those states.
- **Cases With Conflicts:** visible cases with at least one contextual warning.
- **Recent Scan Status:** latest scan visible in the active allowed companies.

These categories overlap and should not be summed into a total.

## 10. Multi-company behavior

Scans use explicit company scope. Matching and merging require two eligible source leads in the same company. The company switcher controls which company cases are visible; changing company does not broaden CRM lead permissions. Company-less records are not paired automatically.

## 11. Known limitations

- This app reviews CRM leads/opportunities only. It does not deduplicate contacts or merge accounting, sales or stock documents.
- Near-email is conservative. Near-phone suggestions are disabled where safe equivalence cannot be established.
- Native CRM merge field precedence is determined by Odoo. The addon does not offer a custom field-reconciliation wizard.
- Matching score is a heuristic, not a probability or duplicate guarantee.
- Source visibility remains subject to Odoo CRM record rules.
- The application audit trail is not protected from database administrators.

## 12. Troubleshooting

**I cannot see a case:** confirm you can read both CRM records and the company is in your active companies.

**I cannot scan:** ask an administrator to assign Duplicate Review Manager and confirm CRM access to the active company.

**A merge is rejected as stale:** recheck both CRM records, then review and approve again. Do not reuse an old confirmation.

**A scan is partial/blocked:** read Scan History. Broad candidate groups are bounded intentionally; follow the resume/skip instruction shown by Odoo.

**I cannot see an import button for cases:** case evidence is protected read-only data. Use review methods rather than importing status or matching evidence.

## 13. Upgrade and support

Back up the database and test the new addon version in a staging copy before upgrading. Keep CRM source records and audit history under normal Odoo backup policy. Contact [Golok Technology](mailto:patelyash.gokul@gmail.com?subject=CRM%20Duplicate%20Review%20Support) with Odoo version, module version, safe reproduction steps and relevant non-sensitive logs.

To schedule a demo, email [patelyash.gokul@gmail.com](mailto:patelyash.gokul@gmail.com?subject=CRM%20Duplicate%20Review%20-%20Schedule%20Demo&body=Hello%20Golok%20Technology%2C%0A%0AI%20would%20like%20to%20schedule%20a%20demo%20of%20CRM%20Duplicate%20Review.%0A%0ACompany%3A%0AContact%20Name%3A%0APreferred%20Date%2FTime%3A%0A%0AThank%20you.).
