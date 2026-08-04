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

    return {
      modal,
      canvas,
      ratio,
      zoom,
      title: modal.querySelector(".tms-img-editor__title"),
      hint: modal.querySelector(".tms-img-editor__hint"),
    };
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

  function identitySide(input) {
    const value = String(input.name || input.id || "").toLowerCase();
    if (/(cnic|id)[_-]?back|back[_-]?(cnic|id)/.test(value)) return "back";
    if (/(cnic|id)[_-]?front|front[_-]?(cnic|id)/.test(value)) return "front";
    return "";
  }

  function orientedPreviewCanvas(image, rotation) {
    const sideways = rotation % 180 !== 0;
    const rotatedWidth = sideways ? image.naturalHeight : image.naturalWidth;
    const rotatedHeight = sideways ? image.naturalWidth : image.naturalHeight;
    const scale = Math.min(1, 520 / Math.max(rotatedWidth, rotatedHeight));
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(rotatedWidth * scale));
    canvas.height = Math.max(1, Math.round(rotatedHeight * scale));
    const context = canvas.getContext("2d", {willReadFrequently: true});
    context.translate(canvas.width / 2, canvas.height / 2);
    context.rotate(rotation * Math.PI / 180);
    context.drawImage(
      image,
      -image.naturalWidth * scale / 2,
      -image.naturalHeight * scale / 2,
      image.naturalWidth * scale,
      image.naturalHeight * scale
    );
    return canvas;
  }

  function regionTransitionScore(canvas, box) {
    const left = Math.max(0, Math.floor(canvas.width * box[0]));
    const top = Math.max(0, Math.floor(canvas.height * box[1]));
    const width = Math.max(1, Math.min(canvas.width - left, Math.ceil(canvas.width * (box[2] - box[0]))));
    const height = Math.max(1, Math.min(canvas.height - top, Math.ceil(canvas.height * (box[3] - box[1]))));
    const data = canvas.getContext("2d", {willReadFrequently: true}).getImageData(left, top, width, height).data;
    const rawGray = function (x, y) {
      const index = (y * width + x) * 4;
      return (data[index] * 3 + data[index + 1] * 6 + data[index + 2]) / 10;
    };
    const histogram = new Array(256).fill(0);
    for (let y = 0; y < height; y += 1) {
      for (let x = 0; x < width; x += 1) histogram[Math.round(rawGray(x, y))] += 1;
    }
    const cutoff = width * height * .01;
    let low = 0, high = 255, seen = 0;
    while (low < 255 && seen + histogram[low] <= cutoff) seen += histogram[low++];
    seen = 0;
    while (high > 0 && seen + histogram[high] <= cutoff) seen += histogram[high--];
    const scale = high > low ? 255 / (high - low) : 1;
    const gray = function (x, y) {
      return Math.max(0, Math.min(255, (rawGray(x, y) - low) * scale));
    };
    let transitions = 0;
    let comparisons = 0;
    for (let y = 0; y < height; y += 2) {
      for (let x = 0; x < width; x += 2) {
        const current = gray(x, y);
        if (x + 2 < width) {
          if (Math.abs(current - gray(x + 2, y)) >= 48) transitions += 1;
          comparisons += 1;
        }
        if (y + 2 < height) {
          if (Math.abs(current - gray(x, y + 2)) >= 48) transitions += 1;
          comparisons += 1;
        }
      }
    }
    return comparisons ? transitions / comparisons : 0;
  }

  function regionPortraitColorScore(canvas, box) {
    const left = Math.max(0, Math.floor(canvas.width * box[0]));
    const top = Math.max(0, Math.floor(canvas.height * box[1]));
    const width = Math.max(1, Math.min(canvas.width - left, Math.ceil(canvas.width * (box[2] - box[0]))));
    const height = Math.max(1, Math.min(canvas.height - top, Math.ceil(canvas.height * (box[3] - box[1]))));
    const data = canvas.getContext("2d", {willReadFrequently: true}).getImageData(left, top, width, height).data;
    let portraitPixels = 0;
    let pixels = 0;
    for (let index = 0; index < data.length; index += 16) {
      const red = data[index];
      const green = data[index + 1];
      const blue = data[index + 2];
      if (
        red > 45 && red < 245 && green > 30 && green < 225 && blue > 20 && blue < 215 &&
        red > green * 1.06 && red > blue * 1.08 && Math.abs(green - blue) < 75
      ) portraitPixels += 1;
      pixels += 1;
    }
    return pixels ? portraitPixels / pixels : 0;
  }

  async function initialIdentityRotation(image, input) {
    const side = identitySide(input);
    if (!side) return 0;
    let rotation = image.naturalHeight > image.naturalWidth ? 270 : 0;
    const preview = orientedPreviewCanvas(image, rotation);
    if (side === "back") {
      const expected = regionTransitionScore(preview, [.64, .02, .98, .54]);
      const inverted = regionTransitionScore(preview, [.02, .46, .36, .98]);
      if (inverted >= .025 && inverted > expected * 1.18) {
        rotation = (rotation + 180) % 360;
      }
    } else if (side === "front" && typeof window.FaceDetector === "function") {
      try {
        const faces = await new window.FaceDetector({fastMode: true, maxDetectedFaces: 1}).detect(preview);
        const face = faces[0]?.boundingBox;
        if (face && face.x + face.width / 2 < preview.width / 2) {
          rotation = (rotation + 180) % 360;
        }
      } catch (_) {
        // Keep aspect-based orientation when browser face detection is unavailable.
      }
    }
    return rotation;
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
    const sideways = state.rotation % 180 !== 0;
    const sourceWidth = sideways ? state.image.naturalHeight : state.image.naturalWidth;
    const sourceHeight = sideways ? state.image.naturalWidth : state.image.naturalHeight;
    const ratioValue = state.ratio === "free" ? sourceWidth / sourceHeight : Number(state.ratio);
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
    const initialRotation = await initialIdentityRotation(image, input);
    state = {
      file,
      image,
      ratio,
      rotation: initialRotation,
      zoom: 1,
      offsetX: 0,
      offsetY: 0,
      previousOverflow: document.body.style.overflow,
    };
    editor.ratio.value = ratio;
    editor.zoom.value = "1";
    editor.title.textContent = initialRotation
      ? "Crop and rotate image - orientation corrected"
      : "Crop and rotate image";
    editor.hint.textContent = initialRotation
      ? "Auto-rotation is already shown in this preview and is the orientation that will be saved. Rotate again only if it still looks wrong."
      : "This preview is the orientation that will be saved. Drag the image to position it inside the crop frame.";
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
