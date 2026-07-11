TMS LEASE PARTY / PENDING REGISTRATION / AGREEMENT PACKAGE PATCH
================================================================

This archive contains ONLY affected files. Paths are relative to the project root.
Do not extract over production before testing locally.

BACKUP FIRST
------------
git add .
git commit -m "Backup before lease party workflow patch"

Or copy the project folder to a dated backup.

REPLACEMENT
-----------
1. Extract this archive into a separate folder.
2. Compare each existing file with your project copy using VS Code Compare or git diff.
3. Copy approved files while preserving their relative paths.
4. New files/directories can be copied directly.

DATABASE
--------
python manage.py makemigrations --check --dry-run
python manage.py migrate tenants
python manage.py migrate leases
python manage.py check
python manage.py test tenants leases

MIGRATIONS
----------
tenants/migrations/0019_pending_registration_people.py
leases/migrations/0070_lease_parties_authorized_occupants.py

IMPORTANT
---------
The uploaded source archive did not contain manage.py or a usable installed Django environment,
so python module syntax was validated, but Django system checks and database tests could not be
executed in this workspace. Run the commands above locally before production deployment.

The PDF merger first uses pypdf and then PyPDF2. Verify that one of these already exists in the
project environment before testing the package download:
python -c "import pypdf; print(pypdf.__version__)"
or
python -c "import PyPDF2; print(PyPDF2.__version__)"

MANUAL VERIFICATION
-------------------
1. Open public registration and submit with all agreement-party sections blank.
2. Submit proposer/seconder/witness CNIC in dashed and undashed form; confirm matched Tenant.
3. Review the pending registration detail page.
4. Create a lease and select the pending registration.
5. Confirm family links, vehicles, proposer, seconder and witnesses are linked once.
6. Edit the lease and verify primary tenant cannot be proposer/seconder and proposer != seconder.
7. Open lease detail and verify linked party names.
8. Open Tenant Detail and Role History; test all role filters.
9. Verify Authorized Occupants clause and placeholders in Agreement Settings.
10. Download agreement package with no prior inspection or police PDF.
11. Confirm PDF order: agreement, inspection, police report, signature page.
12. Test an existing legacy lease whose witnesses are only stored in text fields.
