/** @odoo-module **/

import {
    Component, onMounted, onWillStart, onWillUnmount, onWillUpdateProps, useRef, useState,
} from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardWidgetProps } from "@web/views/widgets/standard_widget_props";
import { optimizeWhatsAppImage } from "./image_optimizer";

const MAX_SESSION_MEDIA = 16 * 1024 * 1024;
const MAX_DOCUMENT = 50 * 1024 * 1024;
const MAX_SOURCE_IMAGE = 50 * 1024 * 1024;
const VOICE_FORMATS = [
    { mime: "audio/mp4;codecs=mp4a.40.2", apiMime: "audio/mp4", extension: "m4a" },
    { mime: "audio/mp4", apiMime: "audio/mp4", extension: "m4a" },
    { mime: "audio/ogg;codecs=opus", apiMime: "audio/ogg", extension: "ogg" },
    { mime: "audio/ogg", apiMime: "audio/ogg", extension: "ogg" },
];
const EMOJIS = ["😀", "😂", "😊", "😍", "🙏", "👍", "👏", "🎉", "❤️", "✅", "👋", "🤝", "📦", "📞", "✨", "🔥"];

export class WhatsAppLeadDrawer extends Component {
    static template = "odx_whatsapp_integration.LeadDrawer";
    static props = { ...standardWidgetProps };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.timelineRef = useRef("timeline");
        this.fileRef = useRef("fileInput");
        this.state = useState({
            open: false, loading: false, refreshing: false, sending: false, data: null,
            mode: "text", text: "", emojiOpen: false,
            templateId: false, templateValues: [],
            mediaType: false, mediaName: "", mediaMime: "", mediaBase64: "", mediaPreview: "", caption: "",
            mediaItems: [],
            preparingMedia: false,
            question: "", buttons: [""],
            recording: false, recordingPaused: false, recordingSeconds: 0, voiceBase64: "", voicePreview: "",
            previewImageUrl: "", previewImageDownloadUrl: "", previewImageName: "",
        });
        this.emojis = EMOJIS;
        onWillStart(() => this.state.data = null);
        onMounted(() => window.addEventListener("keydown", this.onWindowKeydown));
        onWillUpdateProps((nextProps) => {
            if (nextProps.record.resId !== this.props.record.resId) {
                this.close();
                this.state.data = null;
                this.resetComposer();
            }
        });
        onWillUnmount(() => {
            window.removeEventListener("keydown", this.onWindowKeydown);
            this.stopPolling();
            this.discardRecording();
            this.clearMedia(false);
        });
    }

    get leadId() { return this.props.record.resId; }
    get canOpen() { return Boolean(this.leadId && this.props.record.data.whatsapp_can_access); }
    get chat() { return this.state.data?.chat; }
    get selectedAccountId() { return this.state.data?.selected_account_id || false; }
    get selectedConversationId() { return this.state.data?.selected_conversation_id || false; }
    get selectedTemplate() { return this.chat?.templates.find((item) => item.id === this.state.templateId); }

    async toggle() {
        if (!this.canOpen) return;
        if (this.state.open) return this.close();
        this.state.open = true;
        await this.load(false, true);
        this.pollTimer = setInterval(() => this.load(true), 8000);
    }

    close() {
        this.closeImagePreview();
        this.state.open = false;
        this.stopPolling();
    }

    stopPolling() {
        if (this.pollTimer) clearInterval(this.pollTimer);
        this.pollTimer = null;
    }

    onWindowKeydown = (event) => {
        if (event.key !== "Escape") return;
        if (this.state.previewImageUrl) this.closeImagePreview();
        else if (this.state.open) this.close();
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
        if (event.target === event.currentTarget) this.closeImagePreview();
    }

    async load(quiet = false, markRead = false, conversationId = false, accountId = false) {
        if (!this.state.open || this.state.loading || this.state.sending) return;
        this.state.loading = !quiet;
        this.state.refreshing = quiet;
        const timeline = this.timelineRef.el;
        const atBottom = !timeline || timeline.scrollHeight - timeline.scrollTop - timeline.clientHeight < 80;
        try {
            const data = await this.orm.call("crm.lead", "get_whatsapp_panel_data", [
                [this.leadId], conversationId || this.selectedConversationId,
                accountId || this.selectedAccountId, false, 100,
            ]);
            this.state.data = data;
            this.decorateMessages();
            if (markRead && data.selected_conversation_id && data.chat.unread_count) {
                await this.orm.call("crm.lead", "whatsapp_panel_mark_read", [[this.leadId], data.selected_conversation_id]);
                data.chat.unread_count = 0;
            }
            if (!data.chat.window_open && this.state.mode !== "template") this.state.mode = "template";
            this.syncTemplateValues();
            if (!quiet || atBottom) this.scrollBottom();
        } catch (error) {
            this.close();
            this.notifyError(error);
        } finally {
            this.state.loading = false;
            this.state.refreshing = false;
        }
    }

    async loadOlder() {
        if (!this.chat?.has_older || !this.selectedConversationId) return;
        const timeline = this.timelineRef.el;
        const previousHeight = timeline?.scrollHeight || 0;
        try {
            const older = await this.orm.call("odx.whatsapp.conversation", "get_chat_data", [
                [this.selectedConversationId], 100, this.chat.oldest_message_id,
            ]);
            this.chat.messages = [...older.messages, ...this.chat.messages];
            this.chat.has_older = older.has_older;
            this.chat.oldest_message_id = older.oldest_message_id;
            this.decorateMessages();
            setTimeout(() => { if (timeline) timeline.scrollTop = timeline.scrollHeight - previousHeight; });
        } catch (error) { this.notifyError(error); }
    }

    decorateMessages() {
        let previous = "";
        for (const message of this.chat?.messages || []) {
            const date = this.formatDate(message.message_at);
            message.showDate = date !== previous;
            message.dateLabel = date;
            previous = date;
        }
    }

    async changeConversation(event) {
        this.resetComposer();
        await this.load(false, true, Number(event.target.value), false);
    }

    async changeAccount(event) {
        const accountId = Number(event.target.value);
        const conversation = this.state.data.conversations.find((item) => item.account_id === accountId);
        this.resetComposer();
        await this.load(false, true, conversation?.id || false, accountId);
    }

    setMode(mode) {
        if (mode !== "template" && (!this.chat?.window_open || !this.selectedConversationId)) return;
        this.state.mode = mode;
        this.state.emojiOpen = false;
    }

    addEmoji(emoji) {
        this.state.text += emoji;
        this.state.emojiOpen = false;
    }

    onTemplateChange(event) {
        this.state.templateId = Number(event.target.value) || false;
        this.syncTemplateValues(true);
    }

    syncTemplateValues(force = false) {
        const count = this.selectedTemplate?.parameter_count || 0;
        if (force || this.state.templateValues.length !== count) this.state.templateValues = Array(count).fill("");
    }

    async sendText() {
        const body = this.state.text.trim();
        if (!body || !this.chat?.window_open) return;
        await this.sendRpc("whatsapp_panel_send_text", [this.selectedAccountId, this.selectedConversationId, body]);
        this.state.text = "";
    }

    async sendTemplate() {
        if (!this.selectedTemplate || this.state.templateValues.some((value) => !value.trim())) return;
        await this.sendRpc("whatsapp_panel_send_template", [
            this.selectedAccountId, this.selectedConversationId, this.state.templateId, [...this.state.templateValues],
        ]);
        this.state.templateId = false;
        this.state.templateValues = [];
    }

    async sendInteractive() {
        const buttons = this.state.buttons.map((value) => value.trim()).filter(Boolean);
        if (!this.state.question.trim() || !buttons.length) return;
        await this.sendRpc("whatsapp_panel_send_interactive", [
            this.selectedAccountId, this.selectedConversationId, this.state.question, buttons,
        ]);
        this.state.question = "";
        this.state.buttons = [""];
    }

    async retryMessage(message) {
        await this.sendRpc("whatsapp_panel_retry_message", [this.selectedConversationId, message.id]);
    }

    async sendRpc(method, args) {
        this.state.sending = true;
        try {
            this.state.data = await this.orm.call("crm.lead", method, [[this.leadId], ...args]);
            this.decorateMessages();
            this.scrollBottom();
        } catch (error) { this.notifyError(error); }
        finally { this.state.sending = false; }
    }

    triggerFile(type) {
        this.pendingMediaType = type;
        const input = this.fileRef.el;
        input.accept = type === "image" ? "image/*" : type === "audio" ? "audio/*" : type === "video" ? "video/*" : "*/*";
        input.multiple = type === "image";
        input.click();
    }

    async onFileChange(event) {
        const files = [...(event.target.files || [])];
        event.target.value = "";
        if (!files.length) return;
        const type = this.pendingMediaType || "document";
        if (type === "image") {
            if (this.state.preparingMedia) return;
            this.state.mode = "media";
            this.state.preparingMedia = true;
            try {
            if (this.state.mediaBase64) this.clearMedia(false);
            const available = 10 - this.state.mediaItems.length;
            if (available <= 0) {
                this.notification.add("You can send up to 10 images in one batch.", { type: "warning" });
                return;
            }
            if (files.length > available) {
                this.notification.add(`Only the first ${available} images were added. A batch can contain up to 10 images.`, { type: "warning" });
            }
            const selected = files.slice(0, available);
            const imageTypes = new Set(["image/jpeg", "image/png", "image/webp"]);
            if (selected.some((file) => !imageTypes.has(file.type))) {
                this.notification.add("WhatsApp images must be JPG, PNG, or WebP.", { type: "warning" });
                return;
            }
            if (selected.some((file) => file.size > MAX_SOURCE_IMAGE)) {
                this.notification.add("Original photos over 50 MB cannot be optimized safely.", { type: "danger" });
                return;
            }
            const optimizedImages = [];
            for (const original of selected) {
                optimizedImages.push(await optimizeWhatsAppImage(original));
            }
            if (optimizedImages.some((result) => result.file.size > MAX_SESSION_MEDIA)) {
                this.notification.add("One photo is still over 16 MB after optimization.", { type: "danger" });
                return;
            }
            let savedBytes = 0;
            for (const [index, result] of optimizedImages.entries()) {
                const file = result.file;
                savedBytes += result.originalSize - file.size;
                this.state.mediaItems.push({
                    id: `${Date.now()}-${index}-${file.name}`, name: file.name,
                    mime: file.type || "image/jpeg", base64: await this.blobToBase64(file),
                    preview: URL.createObjectURL(file), caption: "", optimized: result.optimized,
                    size: file.size,
                });
            }
            if (savedBytes > 0) {
                this.notification.add(`Photos optimized — ${this.formatBytes(savedBytes)} less to upload.`, {
                    title: "Ready to send", type: "success",
                });
            }
            this.state.mediaType = "image";
            this.state.mode = "media";
            return;
            } finally {
                this.state.preparingMedia = false;
            }
        }
        const file = files[0];
        const maximum = type === "document" ? MAX_DOCUMENT : MAX_SESSION_MEDIA;
        if (file.size > maximum) {
            this.notification.add(type === "document" ? "Documents are limited to 50 MB." : "Images, audio, and video are limited to 16 MB.", { type: "danger" });
            return;
        }
        this.revokePreview();
        this.state.mediaType = type;
        this.state.mediaName = file.name;
        this.state.mediaMime = file.type || "application/octet-stream";
        this.state.mediaPreview = URL.createObjectURL(file);
        this.state.mediaBase64 = await this.blobToBase64(file);
        this.state.mode = "media";
    }

    async sendMedia() {
        if (this.state.mediaItems.length) {
            this.state.sending = true;
            try {
                while (this.state.mediaItems.length) {
                    const item = this.state.mediaItems[0];
                    this.state.data = await this.orm.call("crm.lead", "whatsapp_panel_send_media", [[this.leadId],
                        this.selectedAccountId, this.selectedConversationId, "image",
                        item.base64, item.name, item.mime, item.caption,
                    ]);
                    this.decorateMessages();
                    URL.revokeObjectURL(item.preview);
                    this.state.mediaItems.shift();
                }
                this.clearMedia();
                this.scrollBottom();
            } catch (error) {
                this.notifyError(error);
            } finally {
                this.state.sending = false;
            }
            return;
        }
        if (!this.state.mediaBase64) return;
        await this.sendRpc("whatsapp_panel_send_media", [
            this.selectedAccountId, this.selectedConversationId, this.state.mediaType,
            this.state.mediaBase64, this.state.mediaName, this.state.mediaMime, this.state.caption,
        ]);
        this.clearMedia();
    }

    clearMedia(changeMode = true) {
        this.revokePreview();
        for (const item of this.state.mediaItems) URL.revokeObjectURL(item.preview);
        Object.assign(this.state, { mediaType: false, mediaName: "", mediaMime: "", mediaBase64: "", mediaPreview: "", caption: "", mediaItems: [] });
        if (changeMode) this.state.mode = "text";
    }

    removeMediaItem(index) {
        const [item] = this.state.mediaItems.splice(index, 1);
        if (item?.preview) URL.revokeObjectURL(item.preview);
        if (!this.state.mediaItems.length) this.clearMedia();
    }

    setMediaItemCaption(index, event) { this.state.mediaItems[index].caption = event.target.value; }

    revokePreview() {
        if (this.state.mediaPreview) URL.revokeObjectURL(this.state.mediaPreview);
    }

    async startRecording() {
        if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
            this.notification.add("Voice recording is not supported by this browser.", { type: "warning" });
            return;
        }
        const format = VOICE_FORMATS.find((item) => MediaRecorder.isTypeSupported(item.mime));
        if (!format) {
            this.notification.add("This browser cannot record MP4/AAC or OGG/Opus audio accepted by WhatsApp. Update the browser or attach a supported audio file.", { type: "warning" });
            return;
        }
        try {
            this.voiceStream = await navigator.mediaDevices.getUserMedia({ audio: true });
            this.voiceChunks = [];
            this.voiceFormat = format;
            this.mediaRecorder = new MediaRecorder(this.voiceStream, { mimeType: format.mime });
            this.mediaRecorder.ondataavailable = (event) => event.data.size && this.voiceChunks.push(event.data);
            this.mediaRecorder.onstop = () => this.finishRecording();
            this.mediaRecorder.start();
            this.state.recording = true;
            this.state.recordingPaused = false;
            this.state.recordingSeconds = 0;
            this.voiceTimer = setInterval(() => {
                if (!this.state.recordingPaused) this.state.recordingSeconds++;
                if (this.state.recordingSeconds >= 300) this.stopRecording();
            }, 1000);
        } catch (error) {
            this.notification.add("Microphone access was denied or is unavailable.", { type: "warning" });
        }
    }

    pauseRecording() {
        if (!this.mediaRecorder) return;
        if (this.mediaRecorder.state === "recording") { this.mediaRecorder.pause(); this.state.recordingPaused = true; }
        else if (this.mediaRecorder.state === "paused") { this.mediaRecorder.resume(); this.state.recordingPaused = false; }
    }

    stopRecording() {
        if (this.mediaRecorder && this.mediaRecorder.state !== "inactive") this.mediaRecorder.stop();
        this.state.recording = false;
        clearInterval(this.voiceTimer);
        this.voiceStream?.getTracks().forEach((track) => track.stop());
    }

    async finishRecording() {
        if (!this.voiceFormat || !this.voiceChunks?.length) return this.discardRecording();
        const blob = new Blob(this.voiceChunks, { type: this.voiceFormat.mime });
        if (blob.size > MAX_SESSION_MEDIA) {
            this.notification.add("Voice notes are limited to 16 MB.", { type: "danger" });
            return this.discardRecording();
        }
        if (this.state.voicePreview) URL.revokeObjectURL(this.state.voicePreview);
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
        if (this.state.voicePreview) URL.revokeObjectURL(this.state.voicePreview);
        Object.assign(this.state, { recording: false, recordingPaused: false, recordingSeconds: 0, voiceBase64: "", voicePreview: "" });
        this.voiceFormat = null;
    }

    async sendVoice() {
        if (!this.state.voiceBase64 || !this.voiceFormat) return;
        await this.sendRpc("whatsapp_panel_send_media", [
            this.selectedAccountId, this.selectedConversationId, "audio", this.state.voiceBase64,
            `voice-note-${Date.now()}.${this.voiceFormat.extension}`, this.voiceFormat.apiMime, false,
        ]);
        this.discardRecording();
    }

    blobToBase64(blob) {
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(String(reader.result).split(",")[1]);
            reader.onerror = reject;
            reader.readAsDataURL(blob);
        });
    }

    formatBytes(bytes) {
        if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
        return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    }

    addButton() { if (this.state.buttons.length < 3) this.state.buttons.push(""); }
    removeButton(index) { if (this.state.buttons.length > 1) this.state.buttons.splice(index, 1); }
    setButton(index, event) { this.state.buttons[index] = event.target.value; }
    setTemplateValue(index, event) { this.state.templateValues[index] = event.target.value; }
    onTextKeydown(event) { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); this.sendText(); } }

    openInbox() { this.action.doAction("odx_whatsapp_integration.action_whatsapp_inbox"); }
    mediaUrl(message, download = false) { return `/web/content/odx.whatsapp.message/${message.id}/attachment/${encodeURIComponent(message.attachment_name || "media")}?download=${download ? 1 : 0}`;
    }
    formatTime(value) { if (!value) return ""; return new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(new Date(value.replace(" ", "T") + "Z")); }
    formatDate(value) { if (!value) return ""; return new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(new Date(value.replace(" ", "T") + "Z")); }
    formatDuration(seconds) { return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`; }
    initials(name) { return (name || "WA").split(/\s+/).slice(0, 2).map((part) => part[0]).join("").toUpperCase(); }
    statusTicks(state) { return state === "read" || state === "delivered" ? "✓✓" : state === "failed" ? "!" : "✓"; }
    scrollBottom() { setTimeout(() => { const el = this.timelineRef.el; if (el) el.scrollTop = el.scrollHeight; }); }
    resetComposer() { this.state.mode = "text"; this.state.text = ""; this.state.templateId = false; this.state.templateValues = []; this.state.question = ""; this.state.buttons = [""]; this.clearMedia(); this.discardRecording(); }
    notifyError(error) { this.notification.add(error?.data?.message || error?.message || "WhatsApp operation failed.", { type: "danger", sticky: true }); }
}

registry.category("view_widgets").add("odx_whatsapp_lead_drawer", { component: WhatsAppLeadDrawer });
