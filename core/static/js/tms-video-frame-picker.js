(function () {
  "use strict";

  const MAX_SIDE = 1800;
  const JPEG_QUALITY = 0.82;
  const DEFAULT_SAMPLE_COUNT = 12;

  function formatBytes(bytes) {
    if (!Number.isFinite(bytes)) return "";
    const units = ["B", "KiB", "MiB", "GiB"];
    let value = bytes;
    let unit = 0;
    while (value >= 1024 && unit < units.length - 1) {
      value /= 1024;
      unit += 1;
    }
    return `${value.toFixed(unit ? 2 : 0)} ${units[unit]}`;
  }

  function safeBaseName(name) {
    return (name || "video")
      .replace(/\.[^.]+$/, "")
      .replace(/[^a-z0-9_-]+/gi, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 80) || "video";
  }

  function ensureModal() {
    let element = document.getElementById("tmsVideoFramePicker");
    if (element) return element;
    element = document.createElement("div");
    element.id = "tmsVideoFramePicker";
    element.className = "modal fade";
    element.tabIndex = -1;
    element.setAttribute("aria-hidden", "true");
    element.innerHTML = `
      <div class="modal-dialog modal-xl modal-dialog-scrollable">
        <div class="modal-content">
          <div class="modal-header py-2">
            <div>
              <h5 class="modal-title mb-0">Choose Photos From Video</h5>
              <div class="small text-muted js-vfp-meta"></div>
            </div>
            <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
          </div>
          <div class="modal-body">
            <div class="row g-3">
              <div class="col-12 col-lg-7">
                <video class="w-100 rounded border bg-dark js-vfp-video" controls playsinline style="max-height:55vh;"></video>
                <div class="d-flex flex-wrap align-items-center gap-2 mt-2">
                  <button type="button" class="btn btn-sm btn-outline-primary js-vfp-capture">Capture Current Frame</button>
                  <button type="button" class="btn btn-sm btn-outline-secondary js-vfp-samples">Regenerate 12 Suggestions</button>
                  <span class="small text-muted js-vfp-status"></span>
                </div>
              </div>
              <div class="col-12 col-lg-5">
                <div class="d-flex justify-content-between align-items-center mb-2">
                  <strong class="small">Suggested photos</strong>
                  <span class="badge bg-secondary js-vfp-count">0 selected</span>
                </div>
                <div class="row g-2 js-vfp-grid"></div>
              </div>
            </div>
          </div>
          <div class="modal-footer py-2">
            <button type="button" class="btn btn-sm btn-outline-secondary" data-bs-dismiss="modal">Cancel</button>
            <button type="button" class="btn btn-sm btn-primary js-vfp-confirm">Use Selected Photos</button>
          </div>
        </div>
      </div>`;
    document.body.appendChild(element);
    return element;
  }

  function seek(video, time) {
    return new Promise((resolve, reject) => {
      const target = Math.max(0, Math.min(time, Math.max(0, video.duration - 0.05)));
      if (Math.abs(video.currentTime - target) < 0.01) {
        resolve();
        return;
      }
      const timeout = window.setTimeout(() => {
        cleanup();
        reject(new Error("The browser could not seek through this video."));
      }, 10000);
      const cleanup = () => {
        window.clearTimeout(timeout);
        video.removeEventListener("seeked", onSeeked);
        video.removeEventListener("error", onError);
      };
      const onSeeked = () => {
        cleanup();
        resolve();
      };
      const onError = () => {
        cleanup();
        reject(new Error("The browser could not read this video."));
      };
      video.addEventListener("seeked", onSeeked, { once: true });
      video.addEventListener("error", onError, { once: true });
      video.currentTime = target;
    });
  }

  function frameFromVideo(video, filename, sequence) {
    return new Promise((resolve, reject) => {
      const sourceWidth = video.videoWidth;
      const sourceHeight = video.videoHeight;
      if (!sourceWidth || !sourceHeight) {
        reject(new Error("The selected video frame is not ready."));
        return;
      }
      const scale = Math.min(1, MAX_SIDE / Math.max(sourceWidth, sourceHeight));
      const canvas = document.createElement("canvas");
      canvas.width = Math.max(1, Math.round(sourceWidth * scale));
      canvas.height = Math.max(1, Math.round(sourceHeight * scale));
      canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
      canvas.toBlob((blob) => {
        if (!blob) {
          reject(new Error("The browser could not create a photo from this frame."));
          return;
        }
        const seconds = Math.max(0, video.currentTime).toFixed(1).replace(".", "-");
        resolve(new File(
          [blob],
          `${safeBaseName(filename)}-frame-${String(sequence).padStart(2, "0")}-${seconds}s.jpg`,
          { type: "image/jpeg", lastModified: Date.now() }
        ));
      }, "image/jpeg", JPEG_QUALITY);
    });
  }

  async function open(options) {
    if (!window.bootstrap || !window.bootstrap.Modal) {
      throw new Error("The video photo picker requires Bootstrap.");
    }
    const element = ensureModal();
    const modal = window.bootstrap.Modal.getOrCreateInstance(element);
    const video = element.querySelector(".js-vfp-video");
    const grid = element.querySelector(".js-vfp-grid");
    const status = element.querySelector(".js-vfp-status");
    const count = element.querySelector(".js-vfp-count");
    const meta = element.querySelector(".js-vfp-meta");
    const confirm = element.querySelector(".js-vfp-confirm");
    const capture = element.querySelector(".js-vfp-capture");
    const samples = element.querySelector(".js-vfp-samples");
    const objectUrl = options.file ? URL.createObjectURL(options.file) : "";
    const sourceUrl = objectUrl || options.url;
    const filename = options.filename || options.file?.name || "video.mp4";
    const size = Number.isFinite(options.size) ? options.size : options.file?.size;
    const frames = [];
    let busy = false;

    function updateCount() {
      const selected = frames.filter((frame) => frame.selected).length;
      count.textContent = `${selected} selected`;
      confirm.disabled = busy || !selected;
    }

    function renderFrame(frame) {
      const column = document.createElement("div");
      column.className = "col-6";
      column.innerHTML = `
        <label class="border rounded p-1 d-block h-100">
          <img class="img-fluid rounded w-100" style="height:120px;object-fit:cover;" alt="Video frame">
          <span class="d-flex align-items-center gap-1 mt-1 small">
            <input class="form-check-input mt-0" type="checkbox" checked>
            <span>${frame.time.toFixed(1)}s</span>
          </span>
        </label>`;
      column.querySelector("img").src = frame.previewUrl;
      column.querySelector("input").addEventListener("change", (event) => {
        frame.selected = event.target.checked;
        updateCount();
      });
      grid.appendChild(column);
    }

    async function addCurrentFrame() {
      const file = await frameFromVideo(video, filename, frames.length + 1);
      const frame = {
        file,
        time: video.currentTime,
        selected: true,
        previewUrl: URL.createObjectURL(file),
      };
      frames.push(frame);
      renderFrame(frame);
      updateCount();
    }

    async function generateSuggestions() {
      if (busy) return;
      busy = true;
      capture.disabled = true;
      samples.disabled = true;
      confirm.disabled = true;
      status.textContent = "Generating suggested photos…";
      frames.forEach((frame) => URL.revokeObjectURL(frame.previewUrl));
      frames.length = 0;
      grid.innerHTML = "";
      try {
        const sampleCount = options.sampleCount || DEFAULT_SAMPLE_COUNT;
        for (let index = 1; index <= sampleCount; index += 1) {
          await seek(video, (video.duration * index) / (sampleCount + 1));
          await addCurrentFrame();
          status.textContent = `Generating suggested photos… ${index}/${sampleCount}`;
        }
        status.textContent = "Select the clearest photos, or seek and capture another frame.";
      } catch (error) {
        status.textContent = error.message || "Could not generate suggested photos.";
      } finally {
        busy = false;
        capture.disabled = false;
        samples.disabled = false;
        updateCount();
      }
    }

    meta.textContent = `${filename}${Number.isFinite(size) ? ` • ${formatBytes(size)}` : ""}`;
    status.textContent = "Loading video…";
    grid.innerHTML = "";
    confirm.disabled = true;
    video.src = sourceUrl;
    video.load();
    capture.onclick = async () => {
      if (busy) return;
      try {
        await addCurrentFrame();
      } catch (error) {
        status.textContent = error.message || "Could not capture this frame.";
      }
    };
    samples.onclick = generateSuggestions;
    confirm.onclick = async () => {
      const selectedFiles = frames.filter((frame) => frame.selected).map((frame) => frame.file);
      if (!selectedFiles.length || busy) return;
      busy = true;
      confirm.disabled = true;
      status.textContent = "Saving selected photos…";
      try {
        await options.onConfirm(selectedFiles);
        modal.hide();
      } catch (error) {
        status.textContent = error.message || "Could not save the selected photos.";
        busy = false;
        updateCount();
      }
    };
    video.onloadedmetadata = () => {
      meta.textContent = `${filename}${Number.isFinite(size) ? ` • ${formatBytes(size)}` : ""} • ${video.duration.toFixed(1)} seconds`;
      generateSuggestions();
    };
    video.onerror = () => {
      status.textContent = "This browser cannot decode the selected video format.";
    };
    element.addEventListener("hidden.bs.modal", () => {
      video.pause();
      video.removeAttribute("src");
      video.load();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      frames.forEach((frame) => URL.revokeObjectURL(frame.previewUrl));
    }, { once: true });
    modal.show();
  }

  window.TMSVideoFramePicker = { open, formatBytes };
})();
