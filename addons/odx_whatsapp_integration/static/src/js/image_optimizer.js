/** @odoo-module **/

const OPTIMIZE_ABOVE = 750 * 1024;
const MAX_EDGE = 1600;
const JPEG_QUALITY = 0.82;
const WEBP_QUALITY = 0.84;

function optimizedFilename(filename, mimetype) {
    const stem = (filename || "whatsapp-image").replace(/\.[^.]+$/, "");
    const extension = { "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp" }[mimetype] || "jpg";
    return `${stem}.${extension}`;
}

function canvasBlob(canvas, mimetype, quality) {
    return new Promise((resolve) => canvas.toBlob(resolve, mimetype, quality));
}

async function loadImage(file) {
    if (typeof createImageBitmap === "function") {
        return createImageBitmap(file, { imageOrientation: "from-image" });
    }
    const url = URL.createObjectURL(file);
    try {
        const image = new Image();
        image.src = url;
        await image.decode();
        return image;
    } catch (error) {
        URL.revokeObjectURL(url);
        throw error;
    }
}

/**
 * Reduce the expensive browser -> Odoo -> Meta transfer for camera photos.
 * Small/already-efficient files are returned unchanged. If the browser cannot
 * decode or encode a particular image, sending the original remains possible.
 */
export async function optimizeWhatsAppImage(file) {
    if (!["image/jpeg", "image/png", "image/webp"].includes(file.type)) {
        return { file, optimized: false, originalSize: file.size };
    }
    let image;
    try {
        image = await loadImage(file);
        const width = image.width || image.naturalWidth;
        const height = image.height || image.naturalHeight;
        const scale = Math.min(1, MAX_EDGE / Math.max(width, height));
        if (file.size <= OPTIMIZE_ABOVE && scale === 1) {
            return { file, optimized: false, originalSize: file.size };
        }
        const canvas = document.createElement("canvas");
        canvas.width = Math.max(1, Math.round(width * scale));
        canvas.height = Math.max(1, Math.round(height * scale));
        const context = canvas.getContext("2d", { alpha: file.type !== "image/jpeg" });
        context.drawImage(image, 0, 0, canvas.width, canvas.height);
        const outputType = file.type === "image/jpeg" ? "image/jpeg" : "image/webp";
        const quality = outputType === "image/jpeg" ? JPEG_QUALITY : WEBP_QUALITY;
        const blob = await canvasBlob(canvas, outputType, quality);
        if (!blob || blob.size >= file.size) {
            return { file, optimized: false, originalSize: file.size };
        }
        return {
            file: new File([blob], optimizedFilename(file.name, blob.type), {
                type: blob.type,
                lastModified: file.lastModified,
            }),
            optimized: true,
            originalSize: file.size,
        };
    } catch {
        return { file, optimized: false, originalSize: file.size };
    } finally {
        image?.close?.();
        if (image instanceof HTMLImageElement && image.src?.startsWith("blob:")) {
            URL.revokeObjectURL(image.src);
        }
    }
}
