LEASE DECLARATION + WORD PACKAGE PATCH

This patch assumes migrations 0071 and 0072 from the previous patch are already applied.

FEATURES
1. Adds proposer_relationship and seconder_relationship to Lease.
2. Adds relationship selectors to Lease form and Agreement Clauses party editor.
3. Replaces the signature-page wording with a compact Letter-size proposer/seconder declaration.
4. Lease information is written inside each declaration, not in a separate heading table.
5. Proposer/seconder detail rows contain Full Name, CNIC, Phone, Relationship, Signature and Date.
6. Thumb-impression lines are controlled by Settings and default to Off.
7. Witnesses remain as compact signature fields only; no witness declaration is printed.
8. Adds a Lease Parties badge/panel to Settings Home.
9. Download Word now creates one DOCX package containing:
   - Agreement
   - Inspection sheet
   - Police verification report
   - Proposer/seconder declaration and witness signature fields
10. Word package uses the same selected lease/renewal history as PDF.
11. If no inspection exists, PDF and Word both create the configured blank Move In inspection.

INSTALL
1. Commit/back up the project and database.
2. Extract this ZIP separately.
3. Compare and copy files using their relative paths.
4. Run:
   python manage.py makemigrations --check --dry-run
   python manage.py migrate
   python manage.py check
   python manage.py test tenants leases

MIGRATION
leases.0073_lease_party_relationships_and_declaration_defaults

MANUAL TEST
1. Open Lease #79 > Edit Clauses.
2. Select proposer/seconder and both relationship fields, then click Update Agreement Parties.
3. Open Settings > Lease Parties and confirm the editable wording.
4. Leave Show thumb-impression lines unchecked and download PDF.
5. Confirm the declaration page is Letter size and compact.
6. Enable thumb impression and download again; confirm the lines appear.
7. Click Download Word and confirm one DOCX contains Agreement, Inspection, Police Report, and Declaration sections in that order.
8. Test a renewal and confirm its selected witnesses are used.
