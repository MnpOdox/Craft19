/** @odoo-module **/

import { Component, onMounted, onWillStart, onWillUnmount, useRef, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { optimizeWhatsAppImage } from "./image_optimizer";

const MAX_VOICE_SIZE = 16 * 1024 * 1024;
const MAX_SOURCE_IMAGE = 50 * 1024 * 1024;
const VOICE_FORMATS = [
    { mime: "audio/mp4;codecs=mp4a.40.2", apiMime: "audio/mp4", extension: "m4a" },
    { mime: "audio/mp4", apiMime: "audio/mp4", extension: "m4a" },
    { mime: "audio/ogg;codecs=opus", apiMime: "audio/ogg", extension: "ogg" },
    { mime: "audio/ogg", apiMime: "audio/ogg", extension: "ogg" },
];

export class WhatsAppInbox extends Component {
    static template = "odx_whatsapp_integration.Inbox";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.timelineRef = useRef("timeline");
        this.mediaInputRef = useRef("mediaInput");
        this.state = useState({
            loading: true,
            chatLoading: false,
            refreshing: false,
            sending: false,
            conversations: [],
            selectedId: Number(this.props.action?.params?.conversation_id) || false,
            chat: null,
            search: "",
            filter: "open",
            mode: "text",
            text: "",
            selectedTemplateId: false,
            templateValues: [],
            sidebarVisible: true,
            mediaType: false,
            mediaName: "",
            mediaMime: "",
            mediaBase64: "",
            mediaPreview: "",
            mediaCaption: "",
            mediaDragging: false,
            mediaItems: [],
            preparingMedia: false,
            recording: false,
            recordingPaused: false,
            recordingSeconds: 0,
            voiceBase64: "",
            voicePreview: "",
            voiceMime: "",
            voiceExtension: "",
            previewImageUrl: "",
            previewImageDownloadUrl: "",
            previewImageName: "",
        });
        onWillStart(() => this.loadInbox());
        onMounted(() => {
            this.pollTimer = setInterval(() => this.refreshQuietly(), 8000);
            window.addEventListener("keydown", this.onWindowKeydown);
        });
        onWillUnmount(() => {
            window.removeEventListener("keydown", this.onWindowKeydown);
            clearInterval(this.pollTimer);
            this.clearMedia(false);
            this.discardRecording();
        });
    }

    onWindowKeydown = (event) => {
        if (event.key === "Escape" && this.state.previewImageUrl) {
            this.closeImagePreview();
        }
    };

    openImagePreview(message) {
        this.state.previewImageUrl = this.mediaUrl(message);
        this.state.previewImageDownloadUrl = this.mediaUrl(message, true);
        this.state.previewImageName = message.attachment_name || "WhatsApp image";
    }

    closeImagePreview() {
        this.state.previewImageUrl = "";
        this.state.previewImageDownloadUrl = "";
        this.state.previewImageName = "";
    }

    onImagePreviewBackdrop(event) {
        if (event.target === event.currentTarget) {
            this.closeImagePreview();
        }
    }

    async loadInbox(keepSelection = true) {
        if (this.state.refreshing) {
            return;
        }
        this.state.refreshing = true;
        try {
            const previousSelectedId = this.state.selectedId;
            const rows = await this.orm.call("odx.whatsapp.conversation", "get_inbox_data", [
                this.state.search,
                this.state.filter,
                100,
            ]);
            this.state.conversations = rows;
            const selectedStillExists = rows.some((row) => row.id === this.state.selectedId);
            if (!keepSelection || !selectedStillExists) {
                this.state.selectedId = rows.length ? rows[0].id : false;
            }
            if (this.state.selectedId) {
                const shouldMarkRead = !this.state.chat || previousSelectedId !== this.state.selectedId;
                await this.loadChat(this.state.selectedId, shouldMarkRead);
            } else {
                this.state.chat = null;
            }
        } catch (error) {
            this.notifyError(error);
        } finally {
            this.state.loading = false;
            this.state.refreshing = false;
        }
    }

    async refreshQuietly() {
        if (!this.state.sending && !this.state.chatLoading) {
            await this.loadInbox(true);
        }
    }

    async loadChat(id, markRead = true) {
        this.state.chatLoading = true;
        try {
            let chat = await this.orm.call("odx.whatsapp.conversation", "get_chat_data", [[id]]);
            if (markRead && chat.unread_count) {
                chat = await this.orm.call("odx.whatsapp.conversation", "ui_mark_read", [[id]]);
                const row = this.state.conversations.find((conversation) => conversation.id === id);
                if (row) {
                    row.unread_count = 0;
                }
            }
            this.state.selectedId = id;
            this.state.chat = chat;
            if (!chat.window_open) {
                this.state.mode = "template";
            }
            this.syncTemplateValues();
            this.scrollToBottom();
        } catch (error) {
            this.notifyError(error);
            await this.loadInbox(false);
        } finally {
            this.state.chatLoading = false;
        }
    }

    async selectConversation(id) {
        this.state.sidebarVisible = false;
        if (id === this.state.selectedId) {
            if (this.state.chat?.unread_count) {
                await this.loadChat(id);
            }
            return;
        }
        this.resetComposer();
        await this.loadChat(id);
        await this.loadConversationRows();
    }

    async loadConversationRows() {
        const rows = await this.orm.call("odx.whatsapp.conversation", "get_inbox_data", [
            this.state.search, this.state.filter, 100,
        ]);
        this.state.conversations = rows;
    }

    async applySearch() {
        await this.loadInbox(false);
    }

    async setFilter(filter) {
        this.state.filter = filter;
        await this.loadInbox(false);
    }

    setMode(mode) {
        if ((mode === "text" || mode === "media") && !this.state.chat?.window_open) {
            return;
        }
        this.state.mode = mode;
    }

    onTemplateChange(event) {
        this.state.selectedTemplateId = Number(event.target.value) || false;
        this.syncTemplateValues();
    }

    syncTemplateValues() {
        const template = this.selectedTemplate;
        const count = template?.parameter_count || 0;
        if (this.state.templateValues.length !== count) {
            this.state.templateValues = Array(count).fill("");
        }
    }

    setTemplateParameter(index, event) {
        this.state.templateValues[index] = event.target.value;
    }

    get selectedTemplate() {
        return this.state.chat?.templates.find((item) => item.id === this.state.selectedTemplateId);
    }

    get canSend() {
        if (!this.state.chat || !this.state.chat.account_active || this.state.chat.state === "closed" || this.state.sending) {
            return false;
        }
        if (this.state.mode === "text") {
            return this.state.chat.window_open && Boolean(this.state.text.trim());
        }
        return Boolean(this.selectedTemplate) && this.state.templateValues.every((value) => value.trim());
    }

    async send() {
        if (!this.canSend) {
            return;
        }
        this.state.sending = true;
        try {
            let chat;
            if (this.state.mode === "text") {
                chat = await this.orm.call("odx.whatsapp.conversation", "ui_send_text", [
                    [this.state.selectedId], this.state.text,
                ]);
            } else {
                chat = await this.orm.call("odx.whatsapp.conversation", "ui_send_template", [
                    [this.state.selectedId], this.state.selectedTemplateId, [...this.state.templateValues],
                ]);
            }
            this.state.chat = chat;
            this.resetComposer();
            if (!chat.window_open) {
                this.state.mode = "template";
            }
            await this.loadConversationRows();
            this.scrollToBottom();
        } catch (error) {
            this.notifyError(error);
        } finally {
            this.state.sending = false;
        }
    }

    onComposerKeydown(event) {
        if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            this.send();
        }
    }

    async toggleState() {
        const state = this.state.chat.state === "closed" ? "open" : "closed";
        try {
            this.state.chat = await this.orm.call("odx.whatsapp.conversation", "ui_set_state", [
                [this.state.selectedId], state,
            ]);
            await this.loadConversationRows();
        } catch (error) {
            this.notifyError(error);
        }
    }

    openLead() {
        return this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "crm.lead",
            res_id: this.state.chat.lead_id,
            views: [[false, "form"]],
            target: "current",
        });
    }

    openMediaPicker() {
        if (!this.state.chat?.window_open) {
            return;
        }
        this.state.mode = "media";
        if (!this.hasMedia) {
            setTimeout(() => {
                const input = this.mediaInputRef.el;
                if (input) {
                    input.accept = ".jpg,.jpeg,.png,.webp,.aac,.m4a,.mp3,.amr,.ogg,.opus,.mp4,.3gp,.pdf,.txt,.doc,.docx,.xls,.xlsx,.ppt,.pptx";
                    input.multiple = true;
                    input.click();
                }
            });
        }
    }

    openImagePicker() {
        const input = this.mediaInputRef.el;
        input.accept = ".jpg,.jpeg,.png,.webp";
        input.multiple = true;
        input.click();
    }

    get hasMedia() {
        return Boolean(this.state.mediaBase64 || this.state.mediaItems.length);
    }

    async onMediaFileChange(event) {
        const files = [...(event.target.files || [])];
        event.target.value = "";
        if (files.length) {
            await this.prepareMediaFiles(files);
        }
    }

    async onMediaDrop(event) {
        event.preventDefault();
        this.state.mediaDragging = false;
        const files = [...(event.dataTransfer?.files || [])];
        if (files.length) {
            await this.prepareMediaFiles(files);
        }
    }

    async prepareMediaFiles(files) {
        if (this.state.preparingMedia) {
            return;
        }
        this.state.preparingMedia = true;
        try {
        const prepared = files.map((file) => ({ file, info: this.mediaInfo(file) }));
        if (prepared.some((item) => !item.info)) {
            this.notification.add("This file type is not supported by WhatsApp. Choose a JPG, PNG, WebP, PDF, Office document, text, supported audio, MP4, or 3GP file.", { title: "Unsupported file", type: "warning" });
            return;
        }
        if (prepared.length > 1 && prepared.some((item) => item.info.type !== "image")) {
            this.notification.add("Multiple selection is available for images. Send documents, audio, and video one at a time.", { title: "Select images only", type: "warning" });
            return;
        }
        const oversized = prepared.find(({ file, info }) => file.size > (
            info.type === "document" || info.type === "image" ? MAX_SOURCE_IMAGE : 16 * 1024 * 1024
        ));
        if (oversized) {
            this.notification.add(
                oversized.info.type === "image" ? "The original photo is over 50 MB and cannot be optimized safely." :
                oversized.info.type === "document" ? "Documents are limited to 50 MB." :
                "Audio and video are limited to 16 MB.",
                { title: "File too large", type: "danger" },
            );
            return;
        }
        if (prepared.every((item) => item.info.type === "image")) {
            if (this.state.mediaBase64) {
                this.clearMedia(false);
            }
            const available = 10 - this.state.mediaItems.length;
            if (available <= 0) {
                this.notification.add("You can send up to 10 images in one batch.", { title: "Image limit", type: "warning" });
                return;
            }
            if (prepared.length > available) {
                this.notification.add(`Only the first ${available} images were added. A batch can contain up to 10 images.`, { title: "Image limit", type: "warning" });
            }
            const optimizedImages = [];
            for (const selected of prepared.slice(0, available)) {
                optimizedImages.push(await optimizeWhatsAppImage(selected.file));
            }
            if (optimizedImages.some((result) => result.file.size > 16 * 1024 * 1024)) {
                this.notification.add("One photo is still over 16 MB after optimization.", { title: "Photo too large", type: "danger" });
                return;
            }
            let savedBytes = 0;
            for (const [index, result] of optimizedImages.entries()) {
                const file = result.file;
                savedBytes += result.originalSize - file.size;
                this.state.mediaItems.push({
                    id: `${Date.now()}-${index}-${file.name}`, name: file.name, mime: file.type,
                    base64: await this.blobToBase64(file), preview: URL.createObjectURL(file), caption: "",
                    optimized: result.optimized, size: file.size,
                });
            }
            if (savedBytes > 0) {
                this.notification.add(`Photos optimized — ${this.formatBytes(savedBytes)} less to upload.`, {
                    title: "Ready to send", type: "success",
                });
            }
            this.state.mode = "media";
            this.state.mediaType = "image";
            return;
        }
        const { file, info } = prepared[0];
        this.clearMedia(false);
        this.state.mode = "media";
        this.state.mediaType = info.type;
        this.state.mediaMime = info.mime;
        this.state.mediaName = file.name;
        this.state.mediaPreview = URL.createObjectURL(file);
        this.state.mediaBase64 = await this.blobToBase64(file);
        } finally {
            this.state.preparingMedia = false;
        }
    }

    mediaInfo(file) {
        const extension = (file.name.split(".").pop() || "").toLowerCase();
        const byExtension = {
            jpg: ["image", "image/jpeg"], jpeg: ["image", "image/jpeg"], png: ["image", "image/png"], webp: ["image", "image/webp"],
            aac: ["audio", "audio/aac"], m4a: ["audio", "audio/mp4"], mp3: ["audio", "audio/mpeg"], amr: ["audio", "audio/amr"], ogg: ["audio", "audio/ogg"], opus: ["audio", "audio/opus"],
            mp4: ["video", "video/mp4"], "3gp": ["video", "video/3gpp"],
            pdf: ["document", "application/pdf"], txt: ["document", "text/plain"], doc: ["document", "application/msword"],
            docx: ["document", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"],
            xls: ["document", "application/vnd.ms-excel"], xlsx: ["document", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"],
            ppt: ["document", "application/vnd.ms-powerpoint"], pptx: ["document", "application/vnd.openxmlformats-officedocument.presentationml.presentation"],
        };
        const inferred = byExtension[extension];
        if (!inferred) return false;
        return { type: inferred[0], mime: inferred[1] };
    }

    blobToBase64(blob) {
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(String(reader.result).split(",")[1]);
            reader.onerror = reject;
            reader.readAsDataURL(blob);
        });
    }

    async sendMedia() {
        if (!this.hasMedia || this.state.sending) {
            return;
        }
        this.state.sending = true;
        try {
            while (this.state.mediaItems.length) {
                const item = this.state.mediaItems[0];
                this.state.chat = await this.orm.call("odx.whatsapp.conversation", "ui_send_media", [[this.state.selectedId],
                    "image", item.base64, item.name, item.mime, item.caption,
                ]);
                URL.revokeObjectURL(item.preview);
                this.state.mediaItems.shift();
            }
            if (this.state.mediaBase64) {
                this.state.chat = await this.orm.call("odx.whatsapp.conversation", "ui_send_media", [[this.state.selectedId],
                    this.state.mediaType, this.state.mediaBase64, this.state.mediaName,
                    this.state.mediaMime, this.state.mediaCaption,
                ]);
            }
            this.clearMedia();
            await this.loadConversationRows();
            this.scrollToBottom();
        } catch (error) {
            this.notifyError(error);
        } finally {
            this.state.sending = false;
        }
    }

    clearMedia(changeMode = true) {
        if (this.state.mediaPreview) {
            URL.revokeObjectURL(this.state.mediaPreview);
        }
        for (const item of this.state.mediaItems) {
            URL.revokeObjectURL(item.preview);
        }
        Object.assign(this.state, {
            mediaType: false, mediaName: "", mediaMime: "", mediaBase64: "",
            mediaPreview: "", mediaCaption: "", mediaDragging: false, mediaItems: [],
        });
        if (changeMode) {
            this.state.mode = "text";
        }
    }

    removeMediaItem(index) {
        const [item] = this.state.mediaItems.splice(index, 1);
        if (item?.preview) {
            URL.revokeObjectURL(item.preview);
        }
        if (!this.state.mediaItems.length) {
            this.clearMedia();
        }
    }

    setMediaItemCaption(index, event) {
        this.state.mediaItems[index].caption = event.target.value;
    }

    get recorderFormat() {
        if (typeof MediaRecorder === "undefined") {
            return false;
        }
        return VOICE_FORMATS.find((format) => MediaRecorder.isTypeSupported(format.mime)) || false;
    }

    async startRecording() {
        if (!this.state.chat?.window_open || this.state.recording || this.state.sending) {
            return;
        }
        if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
            this.notification.add("Voice recording is not supported by this browser.", { title: "Voice note", type: "warning" });
            return;
        }
        const format = this.recorderFormat;
        if (!format) {
            this.notification.add("This browser cannot record MP4/AAC or OGG/Opus audio accepted by WhatsApp. Update the browser or attach a supported audio file from Media.", { title: "Voice note format unavailable", type: "warning" });
            return;
        }
        try {
            this.discardRecording();
            this.voiceStream = await navigator.mediaDevices.getUserMedia({ audio: true });
            this.voiceChunks = [];
            this.voiceFormat = format;
            this.mediaRecorder = new MediaRecorder(this.voiceStream, { mimeType: format.mime });
            this.mediaRecorder.ondataavailable = (event) => event.data.size && this.voiceChunks.push(event.data);
            this.mediaRecorder.onstop = () => this.finishRecording();
            this.mediaRecorder.start(1000);
            Object.assign(this.state, {
                recording: true, recordingPaused: false, recordingSeconds: 0,
                voiceBase64: "", voiceMime: format.apiMime, voiceExtension: format.extension,
            });
            this.voiceTimer = setInterval(() => {
                if (!this.state.recordingPaused) {
                    this.state.recordingSeconds++;
                }
                if (this.state.recordingSeconds >= 300) {
                    this.stopRecording();
                }
            }, 1000);
        } catch (error) {
            this.voiceStream?.getTracks().forEach((track) => track.stop());
            this.notification.add("Microphone access was denied or is unavailable.", { title: "Voice note", type: "warning" });
        }
    }

    pauseRecording() {
        if (!this.mediaRecorder) {
            return;
        }
        if (this.mediaRecorder.state === "recording") {
            this.mediaRecorder.pause();
            this.state.recordingPaused = true;
        } else if (this.mediaRecorder.state === "paused") {
            this.mediaRecorder.resume();
            this.state.recordingPaused = false;
        }
    }

    stopRecording() {
        if (this.mediaRecorder && this.mediaRecorder.state !== "inactive") {
            this.mediaRecorder.stop();
        }
        this.state.recording = false;
        clearInterval(this.voiceTimer);
        this.voiceStream?.getTracks().forEach((track) => track.stop());
    }

    async finishRecording() {
        const format = this.voiceFormat;
        if (!format || !this.voiceChunks?.length) {
            return this.discardRecording();
        }
        const blob = new Blob(this.voiceChunks, { type: format.mime });
        if (blob.size > MAX_VOICE_SIZE) {
            this.notification.add("Voice notes are limited to 16 MB.", { title: "Voice note", type: "danger" });
            return this.discardRecording();
        }
        if (this.state.voicePreview) {
            URL.revokeObjectURL(this.state.voicePreview);
        }
        this.state.voicePreview = URL.createObjectURL(blob);
        this.state.voiceBase64 = await this.blobToBase64(blob);
    }

    discardRecording() {
        clearInterval(this.voiceTimer);
        if (this.mediaRecorder?.state && this.mediaRecorder.state !== "inactive") {
            this.mediaRecorder.onstop = null;
            this.mediaRecorder.stop();
        }
        this.voiceStream?.getTracks().forEach((track) => track.stop());
        if (this.state.voicePreview) {
            URL.revokeObjectURL(this.state.voicePreview);
        }
        this.voiceStream = null;
        this.mediaRecorder = null;
        this.voiceChunks = [];
        this.voiceFormat = null;
        Object.assign(this.state, {
            recording: false, recordingPaused: false, recordingSeconds: 0,
            voiceBase64: "", voicePreview: "", voiceMime: "", voiceExtension: "",
        });
    }

    async sendVoice() {
        if (!this.state.voiceBase64 || this.state.sending) {
            return;
        }
        this.state.sending = true;
        try {
            this.state.chat = await this.orm.call("odx.whatsapp.conversation", "ui_send_media", [[this.state.selectedId],
                "audio", this.state.voiceBase64, `voice-note-${Date.now()}.${this.state.voiceExtension}`,
                this.state.voiceMime, "",
            ]);
            this.discardRecording();
            await this.loadConversationRows();
            this.scrollToBottom();
        } catch (error) {
            this.notifyError(error);
        } finally {
            this.state.sending = false;
        }
    }

    formatDuration(seconds) {
        const minutes = Math.floor(seconds / 60);
        return `${String(minutes).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
    }

    resetComposer() {
        this.state.text = "";
        this.state.selectedTemplateId = false;
        this.state.templateValues = [];
        this.clearMedia(false);
        this.discardRecording();
    }

    scrollToBottom() {
        setTimeout(() => {
            const element = this.timelineRef.el;
            if (element) {
                element.scrollTop = element.scrollHeight;
            }
        });
    }

    formatTime(value) {
        if (!value) {
            return "";
        }
        const date = new Date(value.replace(" ", "T") + "Z");
        return new Intl.DateTimeFormat(undefined, {
            day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit",
        }).format(date);
    }

    formatFileSize(base64) {
        const bytes = Math.floor((base64?.length || 0) * 0.75);
        return bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
    }

    formatBytes(bytes) {
        if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
        return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    }

    initials(name) {
        return (name || "?").split(/\s+/).slice(0, 2).map((part) => part[0]).join("").toUpperCase();
    }

    statusLabel(state) {
        return { pending: "Queued", sent: "Sent", delivered: "Delivered", read: "Read", failed: "Failed" }[state] || state;
    }

    mediaUrl(message, download = false) {
        const filename = encodeURIComponent(message.attachment_name || "whatsapp-media");
        return `/web/content/odx.whatsapp.message/${message.id}/attachment/${filename}?download=${download ? 1 : 0}`;
    }

    notifyError(error) {
        const message = error?.data?.message || error?.message || "WhatsApp operation failed.";
        this.notification.add(message, { title: "WhatsApp", type: "danger" });
    }
}

registry.category("actions").add("odx_whatsapp_integration.Inbox", WhatsAppInbox);
