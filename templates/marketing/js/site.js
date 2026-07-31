
// Replace this with your actual WhatsApp Business number in international format.
// Example Pakistan number: 923001234567 (no + sign, spaces, or dashes).
const KIRAYAS_WHATSAPP_NUMBER = "923XXXXXXXXX";

document.querySelectorAll("[data-year]").forEach(el => el.textContent = new Date().getFullYear());

document.querySelectorAll("[data-feature-link]").forEach(card => {
  card.addEventListener("click", e => {
    if (e.target.closest("a")) return;
    window.location.href = card.dataset.featureLink;
  });
  card.addEventListener("keydown", e => {
    if (e.key === "Enter" || e.key === " ") window.location.href = card.dataset.featureLink;
  });
});

const contactForm = document.getElementById("whatsapp-contact-form");
if (contactForm) {
  contactForm.addEventListener("submit", event => {
    event.preventDefault();

    if (KIRAYAS_WHATSAPP_NUMBER.includes("X")) {
      alert("Please configure KIRAYAS_WHATSAPP_NUMBER in assets/site.js before using the WhatsApp contact form.");
      return;
    }

    const data = new FormData(contactForm);
    const message = [
      "New Kirayas Website Inquiry",
      "",
      `Name: ${data.get("name") || ""}`,
      `Business/Company: ${data.get("company") || ""}`,
      `Phone: ${data.get("phone") || ""}`,
      `Email: ${data.get("email") || ""}`,
      `Units Managed: ${data.get("units") || ""}`,
      `Interested Plan: ${data.get("plan") || ""}`,
      "",
      "Message:",
      `${data.get("message") || ""}`
    ].join("\n");

    const url = `https://wa.me/${KIRAYAS_WHATSAPP_NUMBER}?text=${encodeURIComponent(message)}`;
    window.open(url, "_blank", "noopener,noreferrer");
  });
}
