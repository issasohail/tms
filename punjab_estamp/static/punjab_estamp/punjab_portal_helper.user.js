// ==UserScript==
// @name         TMS Punjab e-Stamp Helper
// @namespace    https://kirayas.com/tms/
// @version      1.1.8
// @description  Safely fills Punjab e-Stamp workflows launched from TMS.
// @match        https://es.punjab-zameen.gov.pk/eStampCitizenPortal/*
// @run-at       document-start
// @grant        none
// ==/UserScript==

(function () {
  "use strict";

  const PUNJAB_ORIGIN = "https://es.punjab-zameen.gov.pk";
  const ACTIVE_KEY = "TMS_ESTAMP_ACTIVE_FLOW";
  const FLOW_PREFIX = "TMS_ESTAMP_FLOW_";
  const OTP_PREFIX = "TMS_ESTAMP_OTP_";
  const pending = new Map();
  let tmsOrigin = "";
  let activeFlow = null;
  let pdfCaptured = false;

  function safeSessionGet(key) {
    try { return sessionStorage.getItem(key); } catch (_error) { return null; }
  }

  function safeSessionSet(key, value) {
    try { sessionStorage.setItem(key, value); } catch (_error) { /* session-only best effort */ }
  }

  function safeSessionRemove(key) {
    try { sessionStorage.removeItem(key); } catch (_error) { /* session-only best effort */ }
  }

  function flowKey(channel) { return FLOW_PREFIX + channel; }
  function otpKey(flow) { return OTP_PREFIX + String(flow.workflowId); }

  function loadFlow() {
    const channel = safeSessionGet(ACTIVE_KEY);
    if (!channel) return null;
    try { return JSON.parse(safeSessionGet(flowKey(channel)) || "null"); }
    catch (_error) { return null; }
  }

  function saveFlow(envelope) {
    safeSessionSet(ACTIVE_KEY, envelope.channel);
    safeSessionSet(flowKey(envelope.channel), JSON.stringify(envelope));
    activeFlow = envelope;
    tmsOrigin = envelope.tmsOrigin;
  }

  function clearSensitiveFlow() {
    if (!activeFlow) return;
    safeSessionRemove(otpKey(activeFlow.flow));
    safeSessionRemove(flowKey(activeFlow.channel));
    safeSessionRemove(ACTIVE_KEY);
  }

  function requestId() {
    if (window.crypto && window.crypto.randomUUID) return window.crypto.randomUUID();
    return String(Date.now()) + "-" + Math.random().toString(36).slice(2);
  }

  function sendRequest(kind, payload, transfer) {
    return new Promise((resolve, reject) => {
      if (!window.opener || !tmsOrigin || !activeFlow) {
        reject(new Error("Keep the TMS Agreement page open and launch Punjab again."));
        return;
      }
      const id = requestId();
      const timeout = window.setTimeout(() => {
        pending.delete(id);
        reject(new Error("TMS did not respond. Keep the Agreement page open and try again."));
      }, 30000);
      pending.set(id, { resolve, reject, timeout });
      window.opener.postMessage(
        {
          type: "TMS_ESTAMP_REQUEST",
          channel: activeFlow.channel,
          requestId: id,
          kind: kind,
          payload: payload || {}
        },
        tmsOrigin,
        transfer || []
      );
    });
  }

  window.addEventListener("message", (event) => {
    const data = event.data || {};
    if (event.source !== window.opener) return;
    if (data.type === "TMS_ESTAMP_INIT") {
      let parsed;
      try { parsed = new URL(event.origin); } catch (_error) { return; }
      if (!["http:", "https:"].includes(parsed.protocol)) return;
      if (!data.channel || !data.flow || !data.flow.workflowId) return;
      saveFlow({ channel: data.channel, tmsOrigin: event.origin, flow: data.flow });
      routed = false;
      runWhenReady();
      return;
    }
    if (data.type !== "TMS_ESTAMP_RESPONSE" || event.origin !== tmsOrigin) return;
    const waiter = pending.get(data.requestId);
    if (!waiter) return;
    window.clearTimeout(waiter.timeout);
    pending.delete(data.requestId);
    if (data.ok) waiter.resolve(data.payload || {});
    else waiter.reject(new Error(data.error || "TMS rejected the portal update."));
  });

  function announceReady() {
    if (window.opener) {
      window.opener.postMessage({ type: "TMS_ESTAMP_HELPER_READY" }, "*");
    }
  }

  function banner(message, level) {
    if (!document.body) return;
    let el = document.getElementById("tms-estamp-helper-banner");
    if (!el) {
      el = document.createElement("div");
      el.id = "tms-estamp-helper-banner";
      el.style.cssText = "position:fixed;top:8px;left:50%;transform:translateX(-50%);z-index:2147483647;max-width:760px;padding:10px 14px;border-radius:6px;color:#fff;font:600 14px/1.35 Arial,sans-serif;box-shadow:0 2px 12px rgba(0,0,0,.3);text-align:center";
      document.body.appendChild(el);
    }
    el.style.background = level === "error" ? "#b91c1c" : level === "success" ? "#15803d" : "#1d4ed8";
    el.textContent = message;
  }

  const sleep = (ms) => new Promise((resolve) => window.setTimeout(resolve, ms));

  function fireNative(el) {
    if (!el) return false;
    ["input", "keyup", "change", "blur"].forEach((type) => {
      el.dispatchEvent(new Event(type, { bubbles: true }));
    });
    return true;
  }

  function setNative(id, value) {
    const el = document.getElementById(id);
    if (!el) return false;
    el.value = String(value == null ? "" : value);
    return fireNative(el);
  }

  function kendoDropdown(id) {
    if (!window.jQuery) return null;
    return window.jQuery("#" + id).data("kendoDropDownList") || null;
  }

  function setDropdown(id, value) {
    const el = document.getElementById(id);
    const widget = kendoDropdown(id);
    if (!el || !widget) return false;
    const text = String(value == null ? "" : value);
    widget.value(text);
    el.value = text;
    widget.trigger("change");
    window.jQuery(el).trigger("change");
    fireNative(el);
    return true;
  }

  function setMasked(id, value) {
    const el = document.getElementById(id);
    if (!el) return false;

    const text = String(value || "");
    const widget = window.jQuery &&
      window.jQuery("#" + id).data("kendoMaskedTextBox");

    if (widget) {
      // Let Kendo apply its own mask. Do not overwrite the DOM value
      // afterward with the unmasked text, because Punjab validation can
      // reject and clear it.
      widget.value(text);
      widget.trigger("change");
      return true;
    }

    // Fallback only when the page is not using a Kendo masked control.
    el.value = text;
    return fireNative(el);
  }

  function formatCnic(value) {
    const digits = String(value || "").replace(/\D/g, "");
    if (digits.length !== 13) return String(value || "");
    return digits.slice(0, 5) + "-" + digits.slice(5, 12) + "-" + digits.slice(12);
  }

  function formatPakistanMobile(value) {
    let digits = String(value || "").replace(/\D/g, "");
    if (digits.length === 12 && digits.startsWith("92")) digits = "0" + digits.slice(2);
    else if (digits.length === 10 && digits.startsWith("3")) digits = "0" + digits;
    return digits;
  }

  async function setDistrictAndTehsil(flow) {
    const districtValue = String(flow.property.district_portal_value || "");
    const expectedValue = String(flow.property.tehsil_portal_value || "");
    const expectedLabel = String(flow.property.tehsil_name || "").trim().toLowerCase();

    if (!setDropdown("District", districtValue)) {
      throw new Error("Punjab District control was not available.");
    }

    // The Punjab page's District control uses its own populateTehsils()
    // function. Because this helper starts at document-start, that function may
    // appear slightly after the form controls themselves. Wait for it instead
    // of silently skipping the dependent Tehsil load.
    let populateTehsilsReady = false;
    for (let attempt = 0; attempt < 30; attempt += 1) {
      if (typeof window.populateTehsils === "function") {
        populateTehsilsReady = true;
        break;
      }
      await sleep(200);
    }

    if (!populateTehsilsReady) {
      throw new Error("Punjab Tehsil loader was not available.");
    }

    // Give the portal a short moment to finish binding the District control,
    // then invoke its own dependent-dropdown loader.
    await sleep(300);
    window.populateTehsils();

    // Punjab loads Tehsil asynchronously after District changes.  Do not try
    // to select the Tehsil until the Kendo data source has actually loaded.
    let requestedRead = false;
    let lastAvailable = [];
    for (let attempt = 0; attempt < 50; attempt += 1) {
      await sleep(400);

      const el = document.getElementById("Tehsil");
      const widget = kendoDropdown("Tehsil");
      if (!el || !widget) continue;

      if (!requestedRead && attempt >= 2 && widget.dataSource && typeof widget.dataSource.read === "function") {
        requestedRead = true;
        try { widget.dataSource.read(); } catch (_error) { /* portal change handler may already be loading it */ }
      }

      const data = widget.dataSource && typeof widget.dataSource.data === "function"
        ? Array.from(widget.dataSource.data() || [])
        : [];

      const records = data.map((item) => {
        const value = item && (item.Value ?? item.value ?? item.Id ?? item.id ?? item.TehsilId ?? item.TehsilID);
        const label = item && (item.Text ?? item.text ?? item.Name ?? item.name ?? item.TehsilName);
        return {
          value: value == null ? "" : String(value),
          label: label == null ? "" : String(label).trim(),
        };
      });

      const optionRecords = Array.from(el.options || []).map((item) => ({
        value: String(item.value || ""),
        label: String(item.textContent || item.text || "").trim(),
      }));

      const available = records.length ? records : optionRecords;
      lastAvailable = available.map((item) => item.label).filter(Boolean).slice(0, 8);

      let match = available.find((item) => item.value === expectedValue);
      if (!match && expectedLabel) {
        match = available.find((item) => item.label.toLowerCase() === expectedLabel);
      }
      if (!match) continue;

      widget.value(match.value || expectedValue);
      widget.trigger("change");
      window.jQuery("#Tehsil").trigger("change");
      fireNative(el);

      const selectedValue = String(widget.value() || el.value || "");
      const selectedText = String(widget.text ? widget.text() : "").trim().toLowerCase();
      if (selectedValue === String(match.value || expectedValue) ||
          (expectedLabel && selectedText === expectedLabel)) {
        return;
      }
    }

    const availableText = lastAvailable.length
      ? " Available Tehsils: " + lastAvailable.join(", ") + "."
      : "";
    throw new Error(
      "The expected Tehsil did not load for the selected District. " +
      "District=" + districtValue + ", expected Tehsil=" +
      (flow.property.tehsil_name || expectedValue || "unknown") + "." +
      availableText
    );
  }

  async function fillChallan(flow) {
    if (!document.getElementById("District") || !document.getElementById("PersonName")) return false;
    if (flow.dryRun !== true) {
      throw new Error("TMS refused to fill the Challan because the dry-run guard is missing.");
    }
    if (document.body.dataset.tmsEstampChallanFilled === String(flow.workflowId)) {
      watchForChallan(flow);
      return true;
    }
    const self = document.getElementById("Self");
    if (self) {
      self.checked = true;
      fireNative(self);
    }
    await setDistrictAndTehsil(flow);
    const applicantCnic = formatCnic(flow.applicant.cnic);
    const applicantPhone = formatPakistanMobile(flow.applicant.phone);
    const applicantEmail = String(flow.applicant.email || "").trim();

    if (!applicantEmail) {
      throw new Error(
        "Applicant Email is missing in TMS. Add an email address to the linked " +
        (flow.applicant.source_label || "caretaker/owner") +
        " record, then launch the Punjab e-Stamp again."
      );
    }

    setNative("PersonName", flow.applicant.name);
    setMasked("PersonCnic", applicantCnic);
    setDropdown("Relation", flow.applicant.relation_portal_value);
    setNative("RelationName", flow.applicant.relation_person_name);
    setMasked("PersonPhone", applicantPhone);
    setNative("PersonEmail", applicantEmail);
    setNative("PersonAddress", flow.applicant.address);
    setDropdown("Purpose", flow.portal.purpose_portal_value);
    setNative("Denomination", flow.portal.denomination);
    setNative("Reason", flow.portal.reason);
    document.body.dataset.tmsEstampChallanFilled = String(flow.workflowId);
    banner("TMS dry run complete. Review every field, solve CAPTCHA, then click Punjab NEXT yourself. TMS has not submitted the form.", "success");
    watchForChallan(flow);
    return true;
  }

  function watchForChallan(flow) {
    const parse = async () => {
      const text = (document.body && document.body.innerText) || "";
      const match = text.match(/Your\s+Challan\s+Number\s+is\s+([A-Za-z0-9-]+)\s+And\s+Your\s+PSID\s+is\s+([0-9]+)/i);
      if (!match || safeSessionGet("TMS_ESTAMP_CHALLAN_SENT_" + flow.workflowId)) return;
      safeSessionSet("TMS_ESTAMP_CHALLAN_SENT_" + flow.workflowId, "1");
      try {
        await sendRequest("event", {
          action: "challan_generated",
          challan_number: match[1].trim(),
          psid: match[2].trim(),
          launch_token: flow.launchToken
        });
        banner("Challan and PSID saved to TMS. Return to TMS for the payment action.", "success");
      } catch (error) {
        safeSessionRemove("TMS_ESTAMP_CHALLAN_SENT_" + flow.workflowId);
        banner(error.message, "error");
      }
    };
    parse();
    const observer = new MutationObserver(parse);
    if (document.body) observer.observe(document.body, { childList: true, subtree: true, characterData: true });
  }

  function collectOtp() {
    const combined = document.getElementById("otp-input");
    let otp = combined ? String(combined.value || "").replace(/\D/g, "") : "";
    if (!/^\d{6}$/.test(otp)) {
      otp = Array.from({ length: 6 }, (_item, index) => {
        const box = document.getElementById("box" + index);
        return box ? String(box.value || "").replace(/\D/g, "") : "";
      }).join("");
    }
    if (/^\d{6}$/.test(otp) && activeFlow) safeSessionSet(otpKey(activeFlow.flow), otp);
    return otp;
  }

  function fillOtp(otp) {
    if (!/^\d{6}$/.test(otp)) return;
    for (let index = 0; index < 6; index += 1) {
      const box = document.getElementById("box" + index);
      if (box) { box.value = otp[index]; fireNative(box); }
    }
    const combined = document.getElementById("otp-input");
    if (combined) { combined.value = otp; fireNative(combined); }
    collectOtp();
  }

  function installOtpCapture() {
    document.addEventListener("input", (event) => {
      if (event.target && (event.target.id === "otp-input" || /^box[0-5]$/.test(event.target.id))) collectOtp();
    }, true);
    if (window.OTPCredential && navigator.credentials) {
      const controller = new AbortController();
      window.setTimeout(() => controller.abort(), 60000);
      navigator.credentials.get({ otp: { transport: ["sms"] }, signal: controller.signal })
        .then((credential) => {
          const code = credential && String(credential.code || "").replace(/\D/g, "");
          if (/^\d{6}$/.test(code)) fillOtp(code);
        })
        .catch(() => { /* manual OTP entry always remains available */ });
    }
  }

  async function handleRetrieval(flow) {
    setNative("CNICSearchBox", flow.applicant.cnic);
    setNative("MobileSearchBox", formatPakistanMobile(flow.applicant.phone));
    const email = document.getElementById("useEmailCheckbox");
    if (email && email.checked) email.click();
    installOtpCapture();
    const marker = "TMS_ESTAMP_RETRIEVAL_REQUESTED_" + flow.workflowId;
    if (!safeSessionGet(marker)) {
      await sleep(500);
      const button = document.getElementById("btnSearchChallan");
      if (button) {
        safeSessionSet(marker, "1");
        button.click();
      }
    }
    banner("Retrieval details filled. Enter the OTP received from Punjab; manual entry always remains available.");
  }

  function normalized(value) {
    return String(value || "").replace(/\s+/g, "").toUpperCase();
  }

  function tableRecords() {
    return Array.from(document.querySelectorAll("table tr")).slice(1).map((row) => {
      const cells = Array.from(row.querySelectorAll("td")).map((cell) => String(cell.innerText || "").replace(/\s+/g, " ").trim());
      return {
        issuedBy: cells[0] || "",
        stampNumber: cells[1] || "",
        challanNumber: cells[2] || "",
        deed: cells[3] || "",
        amountPaid: cells[4] || "",
        paymentDate: cells[5] || "",
        status: cells[6] || ""
      };
    }).filter((record) => record.stampNumber && record.challanNumber);
  }

  async function handleRetrievalResults(flow) {
    for (let attempt = 0; attempt < 60; attempt += 1) {
      const records = tableRecords();
      if (!records.length) { await sleep(500); continue; }
      const match = records.find((record) => normalized(record.challanNumber) === normalized(flow.challan));
      if (!match) {
        banner("No exact Punjab row matched the TMS Challan. No other row was selected.", "error");
        await sendRequest("event", { action: "portal_error", error: "No exact Challan match was found in Punjab retrieval results." }).catch(() => {});
        return;
      }
      await sendRequest("event", {
        action: "stamp_found",
        challan_number: match.challanNumber,
        stamp_number: match.stampNumber,
        amount_paid: match.amountPaid,
        payment_date: match.paymentDate,
        portal_status: match.status
      });
      flow.stampNumber = match.stampNumber;
      activeFlow.flow = flow;
      saveFlow(activeFlow);
      window.location.assign(flow.downloadUrl);
      return;
    }
    banner("Punjab retrieval results did not load. Try the search again.", "error");
  }

  async function handleDownload(flow) {
    setNative("SearchBox", flow.challan || flow.psid);
    setNative("StampSearchBox", flow.stampNumber);
    setNative("CNICSearchBox", flow.applicant.cnic);
    setNative("MobileSearchBox", formatPakistanMobile(flow.applicant.phone));
    setNative("continuitySheetBox", flow.portal.continuation_sheets || "1");
    const email = document.getElementById("useEmailCheckbox");
    if (email && email.checked) email.click();
    const pageText = (document.body && document.body.innerText) || "";
    if (/already\s+(?:been\s+)?(?:issued|generated)/i.test(pageText)) {
      banner("Punjab reports an existing stamp. TMS will only download it and will never regenerate it.");
    }
    const marker = "TMS_ESTAMP_DOWNLOAD_SEARCHED_" + flow.workflowId;
    if (!safeSessionGet(marker)) {
      await sleep(500);
      const button = document.getElementById("btnSearchChallan");
      if (button) { safeSessionSet(marker, "1"); button.click(); }
    }
    scanPdfLinks();
    const observer = new MutationObserver(scanPdfLinks);
    if (document.body) observer.observe(document.body, { childList: true, subtree: true });
  }

  function looksLikePdf(response, url) {
    const type = String(response.headers.get("content-type") || "").toLowerCase();
    const disposition = String(response.headers.get("content-disposition") || "").toLowerCase();
    return type.includes("application/pdf") || disposition.includes(".pdf") || /\.pdf(?:$|[?#])/i.test(String(url || ""));
  }

  async function uploadPdfBlob(blob, filename) {
    if (pdfCaptured || !activeFlow || !blob || !blob.size) return;
    const otp = safeSessionGet(otpKey(activeFlow.flow)) || "";
    if (!/^\d{6}$/.test(otp)) {
      banner("PDF found, but the workflow OTP/PIN is unavailable. Enter the OTP and download again.", "error");
      return;
    }
    pdfCaptured = true;
    banner("Punjab PDF captured. Uploading through the existing TMS E-Stamp processor...");
    try {
      const buffer = await blob.arrayBuffer();
      await sendRequest("pdf", {
        filename: filename || "Punjab-eStamp.pdf",
        mimeType: blob.type || "application/pdf",
        otp: otp,
        buffer: buffer
      }, [buffer]);
      clearSensitiveFlow();
      banner("E-Stamp PDF was processed and uploaded to TMS. Temporary OTP/PIN cleared.", "success");
    } catch (error) {
      pdfCaptured = false;
      banner(error.message, "error");
    }
  }

  function installPdfCapture() {
    const originalFetch = window.fetch;
    if (originalFetch) {
      window.fetch = async function () {
        const response = await originalFetch.apply(this, arguments);
        try {
          const url = typeof arguments[0] === "string" ? arguments[0] : arguments[0] && arguments[0].url;
          if (looksLikePdf(response, url)) response.clone().blob().then((blob) => uploadPdfBlob(blob, "Punjab-eStamp.pdf"));
        } catch (_error) { /* normal download continues */ }
        return response;
      };
    }
    const originalOpen = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function (_method, url) {
      this.__tmsEstampUrl = url;
      this.addEventListener("load", function () {
        try {
          const type = String(this.getResponseHeader("content-type") || "").toLowerCase();
          if (!type.includes("pdf") && !/\.pdf(?:$|[?#])/i.test(String(this.__tmsEstampUrl || ""))) return;
          if (this.response instanceof Blob) uploadPdfBlob(this.response, "Punjab-eStamp.pdf");
          else if (this.response instanceof ArrayBuffer) uploadPdfBlob(new Blob([this.response], { type: "application/pdf" }), "Punjab-eStamp.pdf");
        } catch (_error) { /* normal download continues */ }
      });
      return originalOpen.apply(this, arguments);
    };
    document.addEventListener("click", (event) => {
      const link = event.target && event.target.closest ? event.target.closest("a[href]") : null;
      if (!link || !/^(blob:|.*\.pdf(?:$|[?#]))/i.test(link.href || "")) return;
      window.setTimeout(() => originalFetch(link.href, { credentials: "include" })
        .then((response) => response.blob())
        .then((blob) => uploadPdfBlob(blob, (link.download || "Punjab-eStamp.pdf")))
        .catch(() => {}), 0);
    }, true);
  }

  function scanPdfLinks() {
    const link = Array.from(document.querySelectorAll("a[href]")).find((item) => /^(blob:|.*\.pdf(?:$|[?#]))/i.test(item.href || ""));
    if (!link || !window.fetch) return;
    window.fetch(link.href, { credentials: "include" })
      .then((response) => response.blob())
      .then((blob) => uploadPdfBlob(blob, link.download || "Punjab-eStamp.pdf"))
      .catch(() => {});
  }

  async function route() {
    const envelope = activeFlow || loadFlow();
    if (!envelope) { announceReady(); return; }
    activeFlow = envelope;
    tmsOrigin = envelope.tmsOrigin;
    const flow = envelope.flow;
    const path = window.location.pathname.toLowerCase();
    try {
      if (path.includes("/stamp/stampretrievalbycnic")) {
        await handleRetrievalResults(flow);
      } else if (path.includes("/stamp/searchchallan")) {
        await handleDownload(flow);
      } else if (path.includes("/stamp/stampretrieval")) {
        await handleRetrieval(flow);
      } else if (!(await fillChallan(flow))) {
        banner("Open Generate Challan 32-A on Punjab. TMS will fill it in dry-run mode.");
      }
    } catch (error) {
      banner(error.message || "Punjab helper could not continue.", "error");
      sendRequest("event", { action: "portal_error", error: error.message || "Punjab helper error." }).catch(() => {});
    }
  }

  let routed = false;
  function runWhenReady() {
    if (routed || document.readyState === "loading") return;
    routed = true;
    route();
  }

  installPdfCapture();
  activeFlow = loadFlow();
  if (activeFlow) tmsOrigin = activeFlow.tmsOrigin;
  window.addEventListener("DOMContentLoaded", runWhenReady, { once: true });
  runWhenReady();
  announceReady();
})();


