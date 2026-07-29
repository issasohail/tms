(function () {
  "use strict";

  if (window.TMSImageEditorInstalled) return;
  window.TMSImageEditorInstalled = true;

  const IMAGE_NAME_RE = /\.(avif|bmp|gif|heic|heif|jpe?g|png|webp)$/i;
  const IMAGE_FIELD_RE = /(avatar|cnic|id[_-]?(front|back)|image|photo|picture|scan)/i;
  let activeResolve = null;
  let state = null;

  function isImageFile(file) {
    return Boolean(
      file &&
      (
        String(file.type || "").toLowerCase().startsWith("image/") ||
        IMAGE_NAME_RE.test(String(file.name || ""))
      )
    );
  }

  function isEligibleInput(input) {
    if (
      !input ||
      input.type !== "file" ||
      input.disabled ||
      input.dataset.tmsImageEditor === "off"
    ) return false;
    const accept = String(input.accept || "").toLowerCase();
    return accept.includes("image/") || IMAGE_FIELD_RE.test(String(input.name || input.id || ""));
  }

  function createEditor() {
    const style = document.createElement("style");
    style.textContent = `
      .tms-img-editor[hidden]{display:none!important}
      .tms-img-editor{position:fixed;inset:0;z-index:30000;display:flex;align-items:center;justify-content:center;padding:14px;background:rgba(0,0,0,.82)}
      .tms-img-editor__panel{width:min(980px,100%);max-height:96vh;display:flex;flex-direction:column;overflow:hidden;border-radius:12px;background:#fff;box-shadow:0 24px 80px rgba(0,0,0,.45);font-family:Arial,sans-serif}
      .tms-img-editor__head{display:flex;align-items:center;justify-content:space-between;padding:10px 14px;border-bottom:1px solid #d8dee8}
      .tms-img-editor__title{font-size:16px;font-weight:700}
      .tms-img-editor__close{width:34px;height:34px;border:0;border-radius:50%;background:#eef1f5;font-size:24px;line-height:30px;cursor:pointer}
      .tms-img-editor__stage{min-height:260px;padding:10px;background:#15171a;text-align:center;overflow:auto}
      .tms-img-editor canvas{display:block;width:min(100%,900px);height:auto;margin:auto;touch-action:none;cursor:grab;background:#20242a}
      .tms-img-editor canvas:active{cursor:grabbing}
      .tms-img-editor__tools{display:flex;flex-wrap:wrap;align-items:center;gap:8px;padding:10px 14px;border-top:1px solid #d8dee8;background:#f8fafc}
      .tms-img-editor__tools button,.tms-img-editor__tools select{min-height:36px;padding:6px 11px;border:1px solid #b8c2cf;border-radius:7px;background:#fff;color:#172033;font-size:13px;cursor:pointer}
      .tms-img-editor__tools label{display:flex;align-items:center;gap:7px;font-size:13px;color:#39455a}
      .tms-img-editor__tools input[type=range]{width:150px}
      .tms-img-editor__hint{flex:1 1 220px;font-size:12px;color:#667085}
      .tms-img-editor__apply{border-color:#0d6efd!important;background:#0d6efd!important;color:#fff!important;font-weight:700}
      @media(max-width:640px){
        .tms-img-editor{padding:0}
        .tms-img-editor__panel{height:100vh;max-height:100vh;border-radius:0}
        .tms-img-editor__stage{flex:1;display:flex;align-items:center;padding:6px}
        .tms-img-editor__tools{padding:8px}
        .tms-img-editor__hint{display:none}
      }
    `;
    document.head.appendChild(style);

    const modal = document.createElement("div");
    modal.className = "tms-img-editor";
    modal.hidden = true;
    modal.innerHTML = `
      <div class="tms-img-editor__panel" role="dialog" aria-modal="true" aria-labelledby="tmsImageEditorTitle">
        <div class="tms-img-editor__head">
          <div class="tms-img-editor__title" id="tmsImageEditorTitle">Crop and rotate image</div>
          <button type="button" class="tms-img-editor__close" aria-label="Use original image">&times;</button>
        </div>
        <div class="tms-img-editor__stage">
          <canvas width="1200" height="800"></canvas>
        </div>
        <div class="tms-img-editor__tools">
          <button type="button" data-action="left">&#8634; Rotate left</button>
          <button type="button" data-action="right">&#8635; Rotate right</button>
          <label>Crop
            <select data-role="ratio">
              <option value="free">Free</option>
              <option value="1.586">ID card</option>
              <option value="1.333333">Photo 4:3</option>
              <option value="1">Square</option>
            </select>
          </label>
          <label>Zoom <input data-role="zoom" type="range" min="1" max="3" step=".01" value="1"></label>
          <span class="tms-img-editor__hint">Drag the image to position it inside the crop frame.</span>
          <button type="button" data-action="original">Use original</button>
          <button type="button" class="tms-img-editor__apply" data-action="apply">Apply crop</button>
        </div>
      </div>
    `;
    document.body.appendChild(modal);

    const canvas = modal.querySelector("canvas");
    const ratio = modal.querySelector('[data-role="ratio"]');
    const zoom = modal.querySelector('[data-role="zoom"]');
    const close = modal.querySelector(".tms-img-editor__close");

    modal.addEventListener("click", function (event) {
      if (event.target === modal) finishEditor(state?.file || null);
      const action = event.target.closest("[data-action]")?.dataset.action;
      if (!action || !state) return;
      if (action === "left" || action === "right") {
        state.rotation = (state.rotation + (action === "left" ? -90 : 90) + 360) % 360;
        state.offsetX = 0;
        state.offsetY = 0;
        render();
      } else if (action === "original") {
        finishEditor(state.file);
      } else if (action === "apply") {
        exportCrop().then(finishEditor).catch(function () {
          finishEditor(state.file);
        });
      }
    });
    close.addEventListener("click", function () { finishEditor(state?.file || null); });
    ratio.addEventListener("change", function () {
      state.ratio = ratio.value;
      state.offsetX = 0;
      state.offsetY = 0;
      render();
    });
    zoom.addEventListener("input", function () {
      state.zoom = Number(zoom.value) || 1;
      render();
    });

    let dragging = false;
    let lastX = 0;
    let lastY = 0;
    canvas.addEventListener("pointerdown", function (event) {
      if (!state) return;
      dragging = true;
      lastX = event.clientX;
      lastY = event.clientY;
      canvas.setPointerCapture(event.pointerId);
    });
    canvas.addEventListener("pointermove", function (event) {
      if (!dragging || !state) return;
      const rect = canvas.getBoundingClientRect();
      state.offsetX += (event.clientX - lastX) * canvas.width / rect.width;
      state.offsetY += (event.clientY - lastY) * canvas.height / rect.height;
      lastX = event.clientX;
      lastY = event.clientY;
      render();
    });
    canvas.addEventListener("pointerup", function () { dragging = false; });
    canvas.addEventListener("pointercancel", function () { dragging = false; });
    canvas.addEventListener("wheel", function (event) {
      if (!state) return;
      event.preventDefault();
      state.zoom = Math.max(1, Math.min(3, state.zoom + (event.deltaY < 0 ? .08 : -.08)));
      zoom.value = String(state.zoom);
      render();
    }, {passive: false});

    return {modal, canvas, ratio, zoom};
  }

  let ui = null;
  function getUi() {
    if (!ui) ui = createEditor();
    return ui;
  }

  function defaultRatio(input) {
    const value = String(input.name || input.id || "").toLowerCase();
    if (/(cnic|id[_-]?(front|back)|registration_book)/.test(value)) return "1.586";
    if (/(avatar|photo|picture|image)/.test(value)) return "1.333333";
    return "free";
  }

  function loadImage(file) {
    return new Promise(function (resolve, reject) {
      const image = new Image();
      const url = URL.createObjectURL(file);
      image.onload = function () {
        URL.revokeObjectURL(url);
        resolve(image);
      };
      image.onerror = function () {
        URL.revokeObjectURL(url);
        reject(new Error("Unable to read image"));
      };
      image.src = url;
    });
  }

  function cropRect() {
    const canvas = getUi().canvas;
    const padding = 54;
    const maxWidth = canvas.width - padding * 2;
    const maxHeight = canvas.height - padding * 2;
    const ratioValue = state.ratio === "free" ? maxWidth / maxHeight : Number(state.ratio);
    let width = maxWidth;
    let height = width / ratioValue;
    if (height > maxHeight) {
      height = maxHeight;
      width = height * ratioValue;
    }
    return {
      x: (canvas.width - width) / 2,
      y: (canvas.height - height) / 2,
      width,
      height,
    };
  }

  function drawingMetrics() {
    const crop = cropRect();
    const sideways = state.rotation % 180 !== 0;
    const rotatedWidth = sideways ? state.image.naturalHeight : state.image.naturalWidth;
    const rotatedHeight = sideways ? state.image.naturalWidth : state.image.naturalHeight;
    const scale = Math.max(crop.width / rotatedWidth, crop.height / rotatedHeight) * state.zoom;
    const drawnWidth = rotatedWidth * scale;
    const drawnHeight = rotatedHeight * scale;
    const maxOffsetX = Math.max(0, (drawnWidth - crop.width) / 2);
    const maxOffsetY = Math.max(0, (drawnHeight - crop.height) / 2);
    state.offsetX = Math.max(-maxOffsetX, Math.min(maxOffsetX, state.offsetX));
    state.offsetY = Math.max(-maxOffsetY, Math.min(maxOffsetY, state.offsetY));
    return {crop, scale};
  }

  function render() {
    if (!state) return;
    const canvas = getUi().canvas;
    const context = canvas.getContext("2d");
    const metrics = drawingMetrics();
    const crop = metrics.crop;

    context.clearRect(0, 0, canvas.width, canvas.height);
    context.fillStyle = "#20242a";
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.save();
    context.beginPath();
    context.rect(crop.x, crop.y, crop.width, crop.height);
    context.clip();
    context.translate(
      crop.x + crop.width / 2 + state.offsetX,
      crop.y + crop.height / 2 + state.offsetY
    );
    context.rotate(state.rotation * Math.PI / 180);
    context.scale(metrics.scale, metrics.scale);
    context.drawImage(
      state.image,
      -state.image.naturalWidth / 2,
      -state.image.naturalHeight / 2
    );
    context.restore();
    context.strokeStyle = "#fff";
    context.lineWidth = 4;
    context.strokeRect(crop.x, crop.y, crop.width, crop.height);

    context.strokeStyle = "rgba(255,255,255,.65)";
    context.lineWidth = 1;
    for (let index = 1; index < 3; index += 1) {
      const x = crop.x + crop.width * index / 3;
      const y = crop.y + crop.height * index / 3;
      context.beginPath();
      context.moveTo(x, crop.y);
      context.lineTo(x, crop.y + crop.height);
      context.stroke();
      context.beginPath();
      context.moveTo(crop.x, y);
      context.lineTo(crop.x + crop.width, y);
      context.stroke();
    }
  }

  function exportCrop() {
    return new Promise(function (resolve, reject) {
      const preview = getUi().canvas;
      const crop = cropRect();
      const ratio = crop.width / crop.height;
      let width = Math.min(1800, Math.max(900, Math.round(crop.width * 1.5)));
      let height = Math.round(width / ratio);
      if (height > 1800) {
        height = 1800;
        width = Math.round(height * ratio);
      }
      const output = document.createElement("canvas");
      output.width = width;
      output.height = height;
      output.getContext("2d").drawImage(
        preview,
        crop.x,
        crop.y,
        crop.width,
        crop.height,
        0,
        0,
        width,
        height
      );
      output.toBlob(function (blob) {
        if (!blob) {
          reject(new Error("Crop failed"));
          return;
        }
        const name = String(state.file.name || "image.jpg").replace(/\.[^.]+$/, "") + "-edited.jpg";
        resolve(new File([blob], name, {type: "image/jpeg", lastModified: Date.now()}));
      }, "image/jpeg", .92);
    });
  }

  function finishEditor(file) {
    if (!activeResolve) return;
    const resolve = activeResolve;
    activeResolve = null;
    getUi().modal.hidden = true;
    document.body.style.overflow = state?.previousOverflow || "";
    state = null;
    resolve(file);
  }

  async function editFile(file, input) {
    const image = await loadImage(file);
    const editor = getUi();
    const ratio = defaultRatio(input);
    state = {
      file,
      image,
      ratio,
      rotation: 0,
      zoom: 1,
      offsetX: 0,
      offsetY: 0,
      previousOverflow: document.body.style.overflow,
    };
    editor.ratio.value = ratio;
    editor.zoom.value = "1";
    editor.modal.hidden = false;
    document.body.style.overflow = "hidden";
    render();
    return new Promise(function (resolve) { activeResolve = resolve; });
  }

  async function processInput(input, originalFiles) {
    const processed = [];
    for (const file of originalFiles) {
      if (!isImageFile(file)) {
        processed.push(file);
        continue;
      }
      try {
        processed.push(await editFile(file, input));
      } catch (error) {
        console.warn("Image editor skipped this file.", error);
        processed.push(file);
      }
    }
    const transfer = new DataTransfer();
    processed.filter(Boolean).forEach(function (file) { transfer.items.add(file); });
    input.files = transfer.files;
    input.dataset.tmsImageEditorApplying = "1";
    input.dispatchEvent(new Event("change", {bubbles: true}));
    delete input.dataset.tmsImageEditorApplying;
    input.dispatchEvent(new CustomEvent("tms:image-edited", {
      bubbles: true,
      detail: {files: processed},
    }));
  }

  document.addEventListener("change", function (event) {
    const input = event.target;
    if (
      !isEligibleInput(input) ||
      input.dataset.tmsImageEditorApplying === "1"
    ) return;
    const files = Array.from(input.files || []);
    if (!files.some(isImageFile)) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    processInput(input, files);
  }, true);
})();
