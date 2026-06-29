# WhatsApp Role-Based Assistant Manual Test Checklist

Run after applying `whatsapp` migration `0004_role_based_assistant_foundation`.

1. Unknown WhatsApp number -> Guest Mode and Guest Menu.
2. Tenant phone with active lease where today is between `start_date` and `end_date` -> Tenant Mode.
3. Tenant phone with expired lease -> Guest Mode unless also active staff.
4. Active staff phone only -> Staff Mode and Staff Menu.
5. Active staff phone that is also active tenant -> asks Staff/Tenant with numbered choices.
6. Active staff phone that is also expired/no-lease tenant -> Staff Mode only.
7. Staff replies `MENU` -> Staff Menu.
8. Staff replies `SWITCH` -> mode selection appears if they also have active Tenant Mode.
9. Staff replies `1` in Staff Menu -> Tenant Management Menu.
10. Staff chooses Add New Tenant -> Add Tenant menu.
11. Staff chooses public tenant registration link -> inactive tenant shell is created and public link is returned.
12. Tenant registration public form opens without `base.html` and submission creates Pending Approval.
13. Tenant replies `1` in Tenant Menu -> outstanding balance flow remains tenant-only.
14. Tenant sends payment receipt -> pending payment verification, not approved payment.
15. Tenant sends maintenance photo -> pending/open maintenance workflow, not closed ticket.
16. Staff sends image/PDF -> upload purpose menu appears.
17. Staff WhatsApp action creates `WhatsAppStaffActionLog`.
18. Staff property access can be configured in `WhatsAppStaffPropertyAccess`.
19. Generated external registration link creates `WhatsAppExternalLinkToken`.
20. Trusted devices are visible in admin for external-link/device capture integration.
