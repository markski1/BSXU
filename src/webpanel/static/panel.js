"use strict";

const toast = (message) => {
    const target = document.querySelector(".toast");
    target.textContent = message;
    target.hidden = false;
    window.clearTimeout(target.hideTimer);
    target.hideTimer = window.setTimeout(() => { target.hidden = true; }, 3500);
};

document.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-copy]");
    if (!button) return;
    try {
        await navigator.clipboard.writeText(button.dataset.copy);
        toast("Copied to clipboard.");
    } catch {
        toast("Clipboard unavailable. Select and copy the displayed link or key.");
    }
});

document.querySelectorAll("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (event) => {
        if (!window.confirm(form.dataset.confirm)) event.preventDefault();
    });
});

const form = document.querySelector("#upload-form");
if (form) {
    const input = form.querySelector("#fileupload");
    const zone = form.querySelector("#drop-zone");
    const selection = form.querySelector("#file-selection");
    const custom = form.querySelector("#file-name");
    const submit = form.querySelector("#upload-submit");
    const status = form.querySelector(".upload-status");
    const progress = form.querySelector("#upload-progress");
    const message = form.querySelector("#upload-message");
    const results = document.querySelector("#upload-results");
    let objectUrls = [];
    let uploading = false;

    const updateSelection = () => {
        objectUrls.forEach((url) => URL.revokeObjectURL(url));
        objectUrls = [];
        selection.replaceChildren();
        let total = 0;
        Array.from(input.files).forEach((file) => {
            total += file.size;
            const item = document.createElement("li");
            if (/^image\/(png|jpeg|gif|webp|avif|bmp)$/.test(file.type) || /^video\/(mp4|webm|ogg)$/.test(file.type)) {
                const preview = document.createElement(file.type.startsWith("image/") ? "img" : "video");
                const url = URL.createObjectURL(file);
                objectUrls.push(url);
                preview.src = url;
                if (preview.tagName === "VIDEO") preview.preload = "metadata";
                else preview.alt = "";
                item.append(preview);
            }
            const label = document.createElement("span");
            label.textContent = file.name + " · " + (file.size / 1024).toLocaleString(undefined, { maximumFractionDigits: 1 }) + " KiB";
            item.append(label);
            selection.append(item);
        });
        input.setCustomValidity(input.files.length > 5 ? "Select no more than 5 files." : total > Number(form.dataset.maxBytes) ? "The selected files exceed the combined upload limit." : "");
        custom.disabled = input.files.length > 1;
        if (custom.disabled) custom.value = "";
    };
    const useFiles = (files) => {
        if (uploading) return;
        const transfer = new DataTransfer();
        Array.from(files).slice(0, 6).forEach((file) => transfer.items.add(file));
        input.files = transfer.files;
        updateSelection();
    };
    input.addEventListener("change", updateSelection);
    zone.addEventListener("dragover", (event) => { event.preventDefault(); zone.classList.add("dragging"); });
    zone.addEventListener("dragleave", () => zone.classList.remove("dragging"));
    zone.addEventListener("drop", (event) => {
        event.preventDefault();
        zone.classList.remove("dragging");
        useFiles(event.dataTransfer.files);
    });
    document.addEventListener("paste", (event) => {
        const files = event.clipboardData.files;
        if (files.length && !uploading) {
            event.preventDefault();
            useFiles(files);
        }
    });
    form.addEventListener("submit", (event) => {
        event.preventDefault();
        if (uploading || !form.reportValidity()) return;
        const data = new FormData(form);
        uploading = true;
        submit.disabled = true;
        input.disabled = true;
        status.hidden = false;
        progress.value = 0;
        message.textContent = "Uploading…";
        const xhr = new XMLHttpRequest();
        xhr.open("POST", form.action);
        xhr.setRequestHeader("X-Requested-With", "XMLHttpRequest");
        xhr.setRequestHeader("X-CSRF-Token", data.get("csrf_token"));
        xhr.timeout = 120000;
        xhr.upload.addEventListener("progress", (update) => {
            if (update.lengthComputable) progress.value = Math.round(update.loaded / update.total * 100);
            if (progress.value === 100) message.textContent = "Upload received. Processing and storing files…";
        });
        const finish = () => {
            uploading = false;
            submit.disabled = false;
            input.disabled = false;
        };
        xhr.addEventListener("load", () => {
            finish();
            let payload;
            try { payload = JSON.parse(xhr.responseText); }
            catch {
                message.textContent = xhr.status === 401 ? "Your sign-in expired. Sign in again before retrying." : xhr.responseText || "Upload failed. Please retry.";
                return;
            }
            results.hidden = false;
            const content = results.querySelector("div");
            content.replaceChildren();
            (payload.files || []).forEach((file) => {
                const article = document.createElement("article");
                const name = document.createElement("strong");
                name.textContent = file.filename;
                const link = document.createElement("a");
                const url = new URL(file.url, location.href);
                if (!["http:", "https:"].includes(url.protocol)) return;
                link.href = url.href;
                link.textContent = url.href;
                const copy = document.createElement("button");
                copy.type = "button";
                copy.className = "secondary";
                copy.dataset.copy = url.href;
                copy.textContent = "Copy link";
                article.append(name, link, copy);
                content.append(article);
            });
            (payload.errors || []).forEach((error) => {
                const paragraph = document.createElement("p");
                paragraph.className = "notice error";
                paragraph.textContent = error.filename + ": " + error.message;
                content.append(paragraph);
            });
            const errors = payload.errors || [];
            message.textContent = errors.length ? "Some files could not be uploaded. Their details are below." : "Upload complete. Your links are ready.";
            if (!errors.length) {
                form.reset();
                custom.disabled = false;
                updateSelection();
            } else if ((payload.files || []).length) {
                const failed = new Set(errors.map((error) => error.index));
                useFiles(Array.from(input.files).filter((file, index) => failed.has(index)));
            }
        });
        xhr.addEventListener("error", () => { finish(); message.textContent = "Network error. Check your connection and retry."; });
        xhr.addEventListener("timeout", () => { finish(); message.textContent = "The request timed out. Check the library before retrying; the server may have stored the file."; });
        xhr.send(data);
    });
    window.addEventListener("beforeunload", () => objectUrls.forEach((url) => URL.revokeObjectURL(url)));
}
