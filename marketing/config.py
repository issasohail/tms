PRICING_PLANS = (
    {
        "slug": "starter",
        "name": "Starter",
        "units": "1–5 units",
        "price": "Rs 500",
        "summary": "For individual landlords.",
        "features": (
            "Core property and tenant management",
            "Agreements and documents",
            "Billing, receipts, and reports",
        ),
    },
    {
        "slug": "growth",
        "name": "Growth",
        "units": "6–10 units",
        "price": "Rs 900",
        "summary": "For growing portfolios.",
        "featured": True,
        "features": (
            "Everything in Starter",
            "Utility billing workflows",
            "WhatsApp-ready communication",
        ),
    },
    {
        "slug": "professional",
        "name": "Professional",
        "units": "11–25 units",
        "price": "Rs 1,800",
        "summary": "For teams and larger portfolios.",
        "features": (
            "Everything in Growth",
            "Staff roles and permissions",
            "Portfolio reporting and exports",
        ),
    },
    {
        "slug": "business",
        "name": "Business",
        "units": "26–50 units",
        "price": "Rs 3,000",
        "summary": "For business portfolios.",
    },
    {
        "slug": "large",
        "name": "Large",
        "units": "51–100 units",
        "price": "Rs 5,000",
        "summary": "For large portfolios.",
    },
    {
        "slug": "custom",
        "name": "Custom",
        "units": "100+ units",
        "price": None,
        "summary": "Pricing for custom scale.",
    },
)


FEATURES = (
    ("agreement-builder", "Agreement Builder", "Build professional rental agreements with reusable clauses, parties, witnesses, guarantors, e-stamp records, signatures, renewals, and PDF generation."),
    ("whatsapp-ai", "WhatsApp AI Integration", "Handle routine tenant and staff requests, receipt images, reminders, and safe escalation to a human through one WhatsApp number."),
    ("electric-billing", "Electric Billing", "Record meter readings, calculate electricity usage, apply rates, and preserve transparent billing history."),
    ("internet-billing", "Internet Billing", "Manage recurring internet packages and charges by property, unit, or tenant."),
    ("water-billing", "Water Usage Billing", "Track metered usage or fixed water charges and include them in tenant invoices and ledgers."),
    ("late-fees", "Late Fee Integration", "Apply configurable grace periods, fixed or percentage fees, caps, and permission-controlled overrides."),
    ("user-management", "User Management", "Create staff accounts and manage roles, groups, permissions, and accountability."),
    ("property-units", "Property and Unit Management", "Manage owners, properties, buildings, floors, units, occupancy, rents, utilities, photos, and availability."),
    ("tenant-management", "Tenant Registration and Profiles", "Store tenant profiles, CNIC images, photographs, family members, references, employment, and approval status."),
    ("lease-management", "Lease Management", "Manage lease terms, deposits, renewals, move-in and move-out workflows, and complete agreement history."),
    ("rent-payments", "Rent, Invoices, Payments, Receipts, and Ledgers", "Generate charges, allocate payments, print receipts, track arrears, and maintain tenant and lease ledgers."),
    ("documents", "Documents and Media", "Store categorized documents, identity records, lease files, receipts, photographs, and inspection media."),
    ("maintenance", "Maintenance and Inspections", "Track complaints, repairs, inspections, costs, responsible staff, progress, media, and completion history."),
    ("reports", "Reports and Exports", "Review operational and financial performance with exportable portfolio, vacancy, billing, and collection reports."),
    ("roles-permissions", "Roles and Permissions", "Limit access by responsibility so staff only view or change the records their work requires."),
)
