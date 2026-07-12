TMS LEGAL AGREEMENT / RELATIONSHIP SELECT2 PATCH

This patch contains only affected files and preserves project-relative paths.

Implemented:
- Legal portrait (8.5 x 14 in) for agreement, inspection, police report, declaration PDF templates.
- Word package uses Legal portrait and PDF-matching margins: top/bottom 0.70 in, left/right 0.55 in.
- Word agreement uses justified Times New Roman formatting and current-history witnesses.
- Witness phone prints below CNIC in the main PDF/Word agreement.
- Witnesses removed from the separate Proposer/Seconder Declaration page.
- Proposer and Seconder signature/date are on one line; optional thumb impression remains settings-controlled.
- Agreement buttons renamed and ordered: Back, PDF, Inspection, Word, Load Default Clause, Email.
- Missing relationship names can be created directly from Proposer/Seconder relationship Select2 fields.
- Relationship creation reuses or reactivates case-insensitive matches and creates a unique code.
- Settings label renamed to Tenant Relationship Types.

No model or migration changes are required.

After copying:
python manage.py check
python manage.py test tenants leases --keepdb

Manual checks:
1. Open lease Edit Clauses.
2. Type a new relationship and choose Add “...”.
3. Update Agreement Parties and reload.
4. Confirm the new item appears under Settings > Tenant Relationship Types.
5. Download PDF and Word.
6. Confirm Legal page size, combined package sections, witness phone under CNIC, and no witnesses on declaration page.
