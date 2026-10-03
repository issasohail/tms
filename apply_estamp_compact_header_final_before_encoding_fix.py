from pathlib import Path
from datetime import datetime
import shutil
import re

ROOT = Path.cwd()
TEMPLATE = ROOT / "leases" / "templates" / "leases" / "edit_clause.html"
TESTS = ROOT / "punjab_estamp" / "tests" / "test_workflow_service.py"

if not TEMPLATE.exists():
    raise SystemExit(f"ERROR: not found: {TEMPLATE}")

stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
backup_root = ROOT / "backups" / f"estamp_compact_collapsed_header_{stamp}"

for path in (TEMPLATE, TESTS):
    if path.exists():
        dest = backup_root / path.relative_to(ROOT)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)

text = TEMPLATE.read_text(encoding="utf-8-sig")

# ------------------------------------------------------------
# 1. Remove Owner/Caretaker icons
# ------------------------------------------------------------
text = re.sub(
    r'<i class="fas fa-user(?:-shield)? me-1"></i>\s*',
    '',
    text,
)

# ------------------------------------------------------------
# 2. Find current applicant toggle and move it to header
# ------------------------------------------------------------
toggle_match = re.search(
    r'(<div class="btn-group btn-group-sm mt-1"[\s\S]*?'
    r'name="punjab_estamp_applicant_source"[\s\S]*?</div>)',
    text,
)

if not toggle_match:
    raise RuntimeError("Could not find Owner/Caretaker toggle.")

toggle_html = toggle_match.group(1)

# Remove the toggle from body
text = text[:toggle_match.start()] + text[toggle_match.end():]

# Remove locked note beside old toggle if present
text = re.sub(
    r'\s*\{% if punjab_estamp\.applicant_locked %\}\s*'
    r'<span class="small text-muted mt-1">[\s\S]*?</span>\s*'
    r'\{% endif %\}',
    '',
    text,
    count=1,
)

# ------------------------------------------------------------
# 3. Find Punjab e-Stamp header action area
# ------------------------------------------------------------
card_pos = text.find('id="punjabEstampCard"')
if card_pos < 0:
    raise RuntimeError("Punjab e-Stamp card not found.")

action_anchor = '<div class="d-flex flex-wrap gap-1">'
action_pos = text.find(action_anchor, card_pos)

if action_pos < 0:
    raise RuntimeError("Punjab e-Stamp header action area not found.")

insert_at = action_pos + len(action_anchor)

header_html = f'''
          <div class="punjab-estamp-header-applicant d-flex align-items-center gap-1">
            <span class="punjab-estamp-header-label">Applicant</span>
            {toggle_html}
          </div>

          <div class="punjab-estamp-header-meta d-flex flex-wrap align-items-center gap-1">
            <span class="punjab-estamp-header-pill">
              <span class="meta-label">PSID</span>
              <span class="meta-value">{{{{ punjab_estamp.workflow.psid|default:"-" }}}}</span>
            </span>

            <span class="punjab-estamp-header-pill">
              <span class="meta-label">Stamp #</span>
              <span class="meta-value">{{{{ punjab_estamp.workflow.stamp_number|default:"-" }}}}</span>
            </span>

            <span class="punjab-estamp-header-pill">
              <span class="meta-label">Status</span>
              <span class="meta-value">{{{{ punjab_estamp.status_label }}}}</span>
            </span>
          </div>

          <button type="button"
                  class="btn btn-outline-secondary btn-sm"
                  data-bs-toggle="modal"
                  data-bs-target="#punjabEstampHelperInstallModal"
                  title="Install or update Punjab e-Stamp browser helper">
            <i class="fas fa-puzzle-piece me-1"></i>Helper
          </button>

          <button type="button"
                  class="btn btn-outline-secondary btn-sm"
                  id="punjabEstampToggleDetails"
                  aria-controls="punjabEstampBody"
                  aria-expanded="false">
            <i class="fas fa-chevron-down me-1"></i>
            <span>Show Details</span>
          </button>
'''

if 'punjab-estamp-header-meta' not in text:
    text = text[:insert_at] + header_html + text[insert_at:]

# Remove old duplicate Hide Details button if one exists
matches = list(re.finditer(r'id="punjabEstampToggleDetails"', text))
if len(matches) > 1:
    # Remove later duplicate button block
    text = re.sub(
        r'\s*<button type="button"'
        r'[\s\S]*?id="punjabEstampToggleDetails"'
        r'[\s\S]*?</button>',
        '',
        text,
        count=1,
    )

# ------------------------------------------------------------
# 4. Make body hidden by default
# ------------------------------------------------------------
if 'id="punjabEstampBody"' in text:
    text = re.sub(
        r'<div class="card-body py-2" id="punjabEstampBody"(?: style="[^"]*")?>',
        '<div class="card-body py-2" id="punjabEstampBody" style="display:none;">',
        text,
        count=1,
    )
else:
    text = text.replace(
        '<div class="card-body py-2">',
        '<div class="card-body py-2" id="punjabEstampBody" style="display:none;">',
        1,
    )

# ------------------------------------------------------------
# 5. Purpose gets 2 columns
# ------------------------------------------------------------
text = re.sub(
    r'<div class="punjab-estamp-item">'
    r'(<span class="punjab-estamp-label">Purpose</span>)',
    r'<div class="punjab-estamp-item punjab-estamp-purpose">\1',
    text,
    count=1,
)

# ------------------------------------------------------------
# 6. Mark body PSID / Stamp / Status duplicates for hiding
# ------------------------------------------------------------
for label, cls in (
    ("PSID", "punjab-estamp-body-psid"),
    ("Stamp #", "punjab-estamp-body-stamp"),
    ("Status", "punjab-estamp-body-status"),
):
    pattern = re.compile(
        r'<div class="punjab-estamp-item">'
        r'(<span class="punjab-estamp-label">' + re.escape(label) + r'</span>)'
        r'([\s\S]*?)</div>'
    )
    m = pattern.search(text)
    if m:
        replacement = (
            f'<div class="punjab-estamp-item {cls}">'
            + m.group(1)
            + m.group(2)
            + '</div>'
        )
        text = text[:m.start()] + replacement + text[m.end():]

# ------------------------------------------------------------
# 7. Remove bottom Helper text link
# ------------------------------------------------------------
text = re.sub(
    r'\s*<button type="button" '
    r'class="btn btn-link btn-sm p-0 align-baseline" '
    r'data-bs-toggle="modal" '
    r'data-bs-target="#punjabEstampHelperInstallModal">'
    r'Install/update browser helper</button>\.?',
    '',
    text,
)

# ------------------------------------------------------------
# 8. Add compact responsive CSS
# ------------------------------------------------------------
style_pos = text.find('id="estamp_responsive_compact_layout_v1"')

if style_pos < 0:
    raise RuntimeError(
        "Could not find current e-Stamp responsive CSS block."
    )

style_end = text.find('</style>', style_pos)

if style_end < 0:
    raise RuntimeError("Could not find closing </style>.")

css = r'''

  /* ===== Final collapsed e-Stamp header layout ===== */

  #punjabEstampCard .card-header {
    gap: .35rem !important;
    padding-top: .32rem !important;
    padding-bottom: .32rem !important;
  }

  #punjabEstampCard .card-header > div:last-child {
    flex: 1 1 auto;
    justify-content: flex-end;
    align-items: center;
  }

  #punjabEstampCard .punjab-estamp-header-applicant {
    white-space: nowrap;
  }

  #punjabEstampCard .punjab-estamp-header-label {
    font-size: .62rem;
    font-weight: 700;
    text-transform: uppercase;
    opacity: .7;
  }

  #punjabEstampCard .punjab-estamp-header-applicant .btn-group {
    margin-top: 0 !important;
  }

  #punjabEstampCard .punjab-estamp-applicant-toggle + label {
    padding: .15rem .32rem !important;
    font-size: .66rem !important;
    line-height: 1.1 !important;
  }

  #punjabEstampCard .punjab-estamp-applicant-toggle + label i,
  #punjabEstampCard .punjab-estamp-applicant-toggle + label svg {
    display: none !important;
  }

  #punjabEstampCard .punjab-estamp-header-pill {
    display: inline-flex;
    align-items: center;
    gap: .2rem;
    padding: .13rem .3rem;
    border: 1px solid rgba(0,0,0,.13);
    border-radius: .25rem;
    background: rgba(255,255,255,.7);
    white-space: nowrap;
    line-height: 1.08;
  }

  #punjabEstampCard .punjab-estamp-header-pill .meta-label {
    font-size: .58rem;
    font-weight: 700;
    text-transform: uppercase;
    opacity: .65;
  }

  #punjabEstampCard .punjab-estamp-header-pill .meta-value {
    font-size: .67rem;
    font-weight: 600;
  }

  #punjabEstampCard .punjab-estamp-purpose {
    grid-column: span 2;
  }

  #punjabEstampCard .punjab-estamp-body-psid,
  #punjabEstampCard .punjab-estamp-body-stamp,
  #punjabEstampCard .punjab-estamp-body-status {
    display: none !important;
  }

  @media (min-width: 1200px) {
    #punjabEstampCard .punjab-estamp-grid {
      grid-template-columns: repeat(6, minmax(0, 1fr));
      column-gap: .55rem;
      row-gap: .25rem;
    }

    #punjabEstampCard .punjab-estamp-purpose {
      grid-column: span 2;
    }
  }

  @media (min-width: 768px) and (max-width: 1199.98px) {
    #punjabEstampCard .card-header > div:last-child {
      justify-content: flex-start;
    }

    #punjabEstampCard .punjab-estamp-grid {
      grid-template-columns:
        repeat(auto-fit, minmax(155px, 1fr));
    }

    #punjabEstampCard .punjab-estamp-purpose {
      grid-column: span 2;
    }
  }

  @media (max-width: 767.98px) {
    #punjabEstampCard .card-header {
      align-items: flex-start !important;
    }

    #punjabEstampCard .card-header > div:last-child {
      width: 100%;
      justify-content: flex-start;
    }

    #punjabEstampCard .punjab-estamp-header-meta {
      width: 100%;
    }

    #punjabEstampCard .punjab-estamp-header-pill {
      flex: 1 1 auto;
      min-width: 0;
    }

    #punjabEstampCard .punjab-estamp-header-pill .meta-value {
      overflow-wrap: anywhere;
    }

    #punjabEstampCard .punjab-estamp-purpose {
      grid-column: 1 / -1;
    }

    #punjabEstampCard .punjab-estamp-applicant-detail-original {
      display: none !important;
    }

    #punjabEstampCard .punjab-estamp-applicant-mobile-summary {
      display: block !important;
      font-size: .74rem;
      line-height: 1.15;
    }

    #punjabEstampCard .punjab-estamp-applicant-mobile-summary .line {
      margin-bottom: .12rem;
      overflow-wrap: anywhere;
    }
  }
'''

if 'Final collapsed e-Stamp header layout' not in text:
    text = text[:style_end] + css + text[style_end:]

# ------------------------------------------------------------
# 9. Add placeholder for compact mobile summary
# ------------------------------------------------------------
if 'punjab-estamp-applicant-mobile-summary' not in text:
    text = re.sub(
        r'(<div class="punjab-estamp-applicant-detail[^"]*"'
        r'[\s\S]*?data-address="[^"]*">)',
        r'\1\n'
        r'              <div '
        r'class="punjab-estamp-applicant-mobile-summary d-none">'
        r'</div>',
        text,
    )

# ------------------------------------------------------------
# 10. Add delegated Show/Hide JS + compact mobile applicant
# ------------------------------------------------------------
js_anchor = "  function selectedPunjabApplicantSource() {"

if js_anchor not in text:
    raise RuntimeError(
        "Could not find Punjab applicant JavaScript."
    )

js = r'''
  function renderPunjabApplicantMobileSummary() {
    card.querySelectorAll(
      '.punjab-estamp-applicant-detail'
    ).forEach((detail) => {
      const box = detail.querySelector(
        '.punjab-estamp-applicant-mobile-summary'
      );

      if (!box) return;

      const name = detail.dataset.name || '-';
      const relation = detail.dataset.relationName || '';
      const father =
        detail.dataset.relationPersonName || '';
      const cnic = detail.dataset.cnic || '-';
      const phone = detail.dataset.phone || '-';
      const email = detail.dataset.email || '';
      const address = detail.dataset.address || '-';

      const identity =
        [name, relation, father]
        .filter(Boolean)
        .join(' · ');

      const contact =
        [phone, email]
        .filter(Boolean)
        .join(' · ');

      box.innerHTML =
        '<div class="line"><strong>' +
        identity +
        '</strong></div>' +
        '<div class="line"><strong>CNIC:</strong> ' +
        cnic +
        '</div>' +
        '<div class="line">' +
        contact +
        '</div>' +
        '<div class="line"><strong>Address:</strong> ' +
        address +
        '</div>';
    });
  }

  function setPunjabEstampDetailsHidden(hidden) {
    const body =
      card.querySelector('#punjabEstampBody');

    const button =
      card.querySelector('#punjabEstampToggleDetails');

    if (body) {
      body.style.display = hidden ? 'none' : '';
    }

    card.classList.toggle(
      'estamp-details-hidden',
      hidden
    );

    if (!button) return;

    button.setAttribute(
      'aria-expanded',
      hidden ? 'false' : 'true'
    );

    const icon = button.querySelector('i');
    const label = button.querySelector('span');

    if (icon) {
      icon.classList.toggle(
        'fa-chevron-down',
        hidden
      );
      icon.classList.toggle(
        'fa-chevron-up',
        !hidden
      );
    }

    if (label) {
      label.textContent =
        hidden ? 'Show Details' : 'Hide Details';
    }
  }

  card.addEventListener('click', (event) => {
    const button = event.target.closest(
      '#punjabEstampToggleDetails'
    );

    if (!button || !card.contains(button)) {
      return;
    }

    const hidden =
      !card.classList.contains(
        'estamp-details-hidden'
      );

    setPunjabEstampDetailsHidden(hidden);
  });

  renderPunjabApplicantMobileSummary();
  setPunjabEstampDetailsHidden(true);

'''

if 'renderPunjabApplicantMobileSummary' not in text:
    text = text.replace(
        js_anchor,
        js + js_anchor,
        1,
    )

# ------------------------------------------------------------
# 11. Collapse again after AJAX card refresh
# ------------------------------------------------------------
refresh_start = text.find(
    'async function refreshPunjabEstampCard'
)

if refresh_start >= 0:
    refresh_end = text.find(
        'async function',
        refresh_start + 20
    )

    if refresh_end < 0:
        refresh_end = len(text)

    refresh_block = text[
        refresh_start:refresh_end
    ]

    marker = '''    syncPunjabApplicant(
      selectedPunjabApplicantSource()
    );
'''

    if (
        marker in refresh_block
        and
        'renderPunjabApplicantMobileSummary();'
        not in refresh_block
    ):
        replacement = marker + '''    renderPunjabApplicantMobileSummary();
    setPunjabEstampDetailsHidden(true);
'''
        text = text.replace(
            marker,
            replacement,
            1,
        )

# ------------------------------------------------------------
# 12. Whitespace cleanup
# ------------------------------------------------------------
text = "\n".join(
    line.rstrip()
    for line in text.splitlines()
) + "\n"

TEMPLATE.write_text(
    text,
    encoding="utf-8",
    newline="\n",
)

if TESTS.exists():
    test_text = TESTS.read_text(
        encoding="utf-8-sig"
    )

    test_text = "\n".join(
        line.rstrip()
        for line in test_text.splitlines()
    ) + "\n"

    TESTS.write_text(
        test_text,
        encoding="utf-8",
        newline="\n",
    )

print("")
print("SUCCESS")
print("Updated:", TEMPLATE)
print("Backup :", backup_root)
print("")
print("Installed:")
print(" - details hidden by default")
print(" - applicant Owner/Caretaker in header")
print(" - PSID / Stamp # / Status in header")
print(" - Helper button in header")
print(" - Show Details before main action")
print(" - Purpose spans two columns")
print(" - compact desktop/tablet layout")
print(" - compact mobile applicant summary")
print(" - Owner/Caretaker icons removed")
print(" - AJAX refresh collapses card again")
print(" - whitespace cleaned")
