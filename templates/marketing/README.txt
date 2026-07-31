KIRAYAS STATIC WEBSITE

Open index.html to preview.

IMPORTANT CONFIGURATION
1. Open assets/site.js.
2. Replace:
   const KIRAYAS_WHATSAPP_NUMBER = "923XXXXXXXXX";
   with the actual WhatsApp Business number in international format without +, spaces, or dashes.
   Example: 923001234567

DJANGO INTEGRATION
- Convert the HTML pages into Django templates.
- Move assets/site.css and assets/site.js into your static directory.
- Replace .html links with {% url %} tags.
- Connect login.html and register.html to the real authentication views.
- Add {% csrf_token %} to POST forms.
- Store subscription tiers in database models or settings.
- The legal pages are placeholders and require legal review.
