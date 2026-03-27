import gradio as gr
import requests
import json
import os
from contacts_manager import (
    get_all_contacts, update_contact_meta,
    VALID_RELATIONSHIPS, VALID_GENDERS
)
from clone_manager import get_ai_settings, save_ai_settings, parse_whatsapp_chats, ingest_into_vectordb, clear_vectordb
from message_scheduler import get_all_schedules, add_schedule, toggle_schedule, delete_schedule
from data_analyst import (
    ingest_file as analyst_ingest_file, query_data as analyst_query_data,
    list_documents as analyst_list_documents, delete_document as analyst_delete_doc,
    clear_session as analyst_clear_session, get_session_info as analyst_session_info
)

API_BASE = "http://localhost:5001"

# --- HELPER FUNCTIONS ---
def get_messages(platform="all"):
    try:
        params = {} if platform == "all" else {"platform": platform}
        return requests.get(f"{API_BASE}/api/messages", params=params).json()
    except: return []

def send_message_api(platform, user_id, message):
    try:
        # SMART JID FORMATTING
        user_id = str(user_id).strip()
        if platform == "whatsapp":
            # If plain number (10 digits), add 91 prefix
            if user_id.isdigit() and len(user_id) == 10:
                user_id = "91" + user_id
            
            # If seemingly phone number (digits only), add suffix
            if user_id.isdigit() and "@" not in user_id:
                user_id += "@s.whatsapp.net"

        payload = {"platform": platform, "user_id": user_id, "message": message, "type": "text"}
        return requests.post(f"{API_BASE}/api/send", json=payload).json()
    except Exception as e: return {"error": str(e)}

def update_contact_choices():
    contacts = get_all_contacts()
    return gr.update(choices=[f"{c['name']} ({c['platform']})" for c in contacts.values()])

def fill_contact_info(selection):
    contacts = get_all_contacts()
    for c in contacts.values():
        if f"{c['name']} ({c['platform']})" == selection:
            user_id = c['id']
            # Re-add suffix for display if it's WhatsApp and missing
            if c['platform'] == "whatsapp" and "@" not in user_id:
                user_id += "@s.whatsapp.net"
            return c['platform'], user_id
    return "whatsapp", ""

def format_messages(messages):
    if not messages: return "No messages found"
    formatted = []
    for msg in messages:
        direction = "→" if msg.get('direction') == 'outgoing' else "←"
        formatted.append(f"[{msg.get('timestamp')}] {msg.get('platform')} {direction} {msg.get('user_name')}: {msg.get('message_text')}")
    return "\n".join(formatted)


# --- Contacts Tab helpers ---
def get_contacts_table():
    """Returns a formatted table of all contacts with their relationship and gender."""
    contacts = get_all_contacts()
    if not contacts:
        return "No contacts saved yet."
    
    rows = []
    rows.append("| # | Name | Platform | Relationship | Gender | Last Seen |")
    rows.append("|---|------|----------|-------------|--------|-----------|")
    for i, (key, c) in enumerate(sorted(contacts.items(), key=lambda x: x[1].get("last_seen", ""), reverse=True), 1):
        name = c.get("name", "Unknown")
        platform = c.get("platform", "?")
        rel = c.get("relationship", "friend")
        gender = c.get("gender", "unknown")
        last_seen = c.get("last_seen", "?")
        rows.append(f"| {i} | {name} | {platform} | **{rel}** | {gender} | {last_seen} |")
    return "\n".join(rows)


def get_contact_choices_for_edit():
    """Returns contact choices for the edit dropdown."""
    contacts = get_all_contacts()
    choices = []
    for key, c in sorted(contacts.items(), key=lambda x: x[1].get("last_seen", ""), reverse=True):
        choices.append(f"{c['name']} ({c['platform']}) [{key}]")
    return choices


def fill_contact_meta(selection):
    """When a contact is selected for editing, fill in the current relationship and gender."""
    if not selection:
        return "friend", "unknown"
    # Extract key from "[key]" at the end
    try:
        key = selection.split("[")[-1].rstrip("]")
        contacts = get_all_contacts()
        c = contacts.get(key, {})
        return c.get("relationship", "friend"), c.get("gender", "unknown")
    except Exception:
        return "friend", "unknown"


def save_contact_meta(selection, relationship, gender):
    """Save updated relationship and gender for the selected contact."""
    if not selection:
        return "⚠️ Select a contact first.", get_contacts_table()
    try:
        key = selection.split("[")[-1].rstrip("]")
        success = update_contact_meta(key, relationship=relationship, gender=gender)
        if success:
            contacts = get_all_contacts()
            name = contacts[key].get("name", "?")
            return f"✅ Updated **{name}**: relationship=**{relationship}**, gender=**{gender}**", get_contacts_table()
        return "❌ Contact not found.", get_contacts_table()
    except Exception as e:
        return f"❌ Error: {e}", get_contacts_table()

# --- CUSTOM THEME ---
custom_theme = gr.themes.Base(
    primary_hue=gr.themes.colors.violet,
    secondary_hue=gr.themes.colors.blue,
    neutral_hue=gr.themes.colors.slate,
    font=[gr.themes.GoogleFont("Inter"), "system-ui", "sans-serif"],
    font_mono=[gr.themes.GoogleFont("JetBrains Mono"), "monospace"],
).set(
    body_background_fill="linear-gradient(135deg, #0f0c29 0%, #1a1245 40%, #24243e 100%)",
    body_text_color="#e2e8f0",
    block_background_fill="rgba(30, 27, 75, 0.45)",
    block_border_width="1px",
    block_border_color="rgba(139, 92, 246, 0.15)",
    block_shadow="0 4px 24px rgba(0, 0, 0, 0.3)",
    block_label_text_color="#a78bfa",
    block_title_text_color="#c4b5fd",
    input_background_fill="rgba(15, 12, 41, 0.6)",
    input_border_color="rgba(139, 92, 246, 0.25)",
    button_primary_background_fill="linear-gradient(135deg, #7c3aed 0%, #6d28d9 50%, #5b21b6 100%)",
    button_primary_text_color="#ffffff",
    button_primary_border_color="rgba(139, 92, 246, 0.4)",
    button_secondary_background_fill="rgba(30, 27, 75, 0.6)",
    button_secondary_text_color="#c4b5fd",
    button_secondary_border_color="rgba(139, 92, 246, 0.25)",
    border_color_primary="rgba(139, 92, 246, 0.2)",
    color_accent_soft="rgba(139, 92, 246, 0.15)",
    checkbox_background_color="rgba(15, 12, 41, 0.6)",
    checkbox_border_color="rgba(139, 92, 246, 0.3)",
    table_even_background_fill="rgba(30, 27, 75, 0.3)",
    table_odd_background_fill="rgba(15, 12, 41, 0.3)",
    table_border_color="rgba(139, 92, 246, 0.15)",
    table_row_focus="rgba(139, 92, 246, 0.15)",
)

CUSTOM_CSS = """
/* ── Global ── */
.gradio-container {
    max-width: 1200px !important;
    margin: 0 auto !important;
}
footer { display: none !important; }

/* ── Header ── */
.dashboard-header {
    text-align: center;
    padding: 1.5rem 0 0.5rem;
}
.dashboard-header h1 {
    font-size: 2.2rem !important;
    font-weight: 800 !important;
    background: linear-gradient(135deg, #a78bfa, #818cf8, #6366f1);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    margin-bottom: 0.2rem !important;
    letter-spacing: -0.5px;
}
.dashboard-header p {
    color: #94a3b8 !important;
    font-size: 0.85rem !important;
    margin: 0 !important;
}

/* ── Tab styling ── */
.tabs > .tab-nav {
    background: rgba(15, 12, 41, 0.5) !important;
    border-radius: 12px !important;
    padding: 4px !important;
    border: 1px solid rgba(139, 92, 246, 0.12) !important;
    gap: 2px !important;
    margin-bottom: 1rem !important;
}
.tabs > .tab-nav > button {
    border-radius: 10px !important;
    padding: 10px 18px !important;
    font-weight: 600 !important;
    font-size: 0.85rem !important;
    color: #94a3b8 !important;
    background: transparent !important;
    border: none !important;
    transition: all 0.25s ease !important;
}
.tabs > .tab-nav > button:hover {
    color: #c4b5fd !important;
    background: rgba(139, 92, 246, 0.08) !important;
}
.tabs > .tab-nav > button.selected {
    color: #fff !important;
    background: linear-gradient(135deg, rgba(124, 58, 237, 0.4), rgba(99, 102, 241, 0.3)) !important;
    box-shadow: 0 2px 12px rgba(124, 58, 237, 0.25) !important;
}

/* ── Cards / Blocks ── */
.gr-group, .gr-box, .gr-panel,
div[class*="block"] > .wrap {
    border-radius: 14px !important;
    backdrop-filter: blur(12px) !important;
    -webkit-backdrop-filter: blur(12px) !important;
}

/* ── Buttons ── */
.gr-button-primary {
    border-radius: 10px !important;
    font-weight: 700 !important;
    letter-spacing: 0.3px;
    box-shadow: 0 4px 16px rgba(124, 58, 237, 0.3) !important;
    transition: all 0.3s ease !important;
}
.gr-button-primary:hover {
    box-shadow: 0 6px 24px rgba(124, 58, 237, 0.45) !important;
    transform: translateY(-1px);
}
.gr-button-secondary, button.secondary {
    border-radius: 10px !important;
    transition: all 0.2s ease !important;
}
.gr-button-secondary:hover, button.secondary:hover {
    background: rgba(139, 92, 246, 0.15) !important;
    border-color: rgba(139, 92, 246, 0.4) !important;
}
button[variant="stop"] {
    background: rgba(239, 68, 68, 0.15) !important;
    border-color: rgba(239, 68, 68, 0.3) !important;
    color: #fca5a5 !important;
}
button[variant="stop"]:hover {
    background: rgba(239, 68, 68, 0.25) !important;
}

/* ── Inputs ── */
input, textarea, select, .gr-input {
    border-radius: 10px !important;
    transition: border-color 0.2s ease, box-shadow 0.2s ease !important;
}
input:focus, textarea:focus, select:focus {
    border-color: rgba(139, 92, 246, 0.5) !important;
    box-shadow: 0 0 0 3px rgba(139, 92, 246, 0.1) !important;
}

/* ── Dropdown ── */
.gr-dropdown {
    border-radius: 10px !important;
}

/* ── Tables ── */
table {
    border-collapse: separate !important;
    border-spacing: 0 !important;
    border-radius: 10px !important;
    overflow: hidden !important;
}
th {
    background: rgba(139, 92, 246, 0.12) !important;
    color: #a78bfa !important;
    font-weight: 700 !important;
    font-size: 0.78rem !important;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    padding: 10px 14px !important;
    border-bottom: 1px solid rgba(139, 92, 246, 0.15) !important;
}
td {
    padding: 10px 14px !important;
    font-size: 0.85rem !important;
    border-bottom: 1px solid rgba(139, 92, 246, 0.08) !important;
}
tr:hover td {
    background: rgba(139, 92, 246, 0.06) !important;
}

/* ── Markdown sections ── */
.prose h3 {
    color: #c4b5fd !important;
    font-weight: 700 !important;
    border-bottom: 1px solid rgba(139, 92, 246, 0.15);
    padding-bottom: 0.4rem;
}
.prose h4 {
    color: #a5b4fc !important;
    font-weight: 600 !important;
}
.prose hr {
    border-color: rgba(139, 92, 246, 0.12) !important;
    margin: 1.2rem 0 !important;
}
.prose code {
    background: rgba(139, 92, 246, 0.15) !important;
    color: #c4b5fd !important;
    padding: 2px 6px !important;
    border-radius: 5px !important;
    font-size: 0.82rem !important;
}

/* ── Messages display ── */
.message-display textarea {
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 0.82rem !important;
    line-height: 1.6 !important;
}

/* ── Radio buttons ── */
.gr-radio label {
    border-radius: 8px !important;
    transition: all 0.2s ease !important;
}

/* ── Checkboxes ── */
.gr-checkbox input[type="checkbox"] {
    border-radius: 5px !important;
}

/* ── Status badges ── */
.status-active { color: #4ade80; }
.status-disabled { color: #f87171; }

/* ── Scrollbar ── */
::-webkit-scrollbar { width: 6px; }
::-webkit-scrollbar-track { background: rgba(15, 12, 41, 0.3); }
::-webkit-scrollbar-thumb {
    background: rgba(139, 92, 246, 0.3);
    border-radius: 3px;
}
::-webkit-scrollbar-thumb:hover {
    background: rgba(139, 92, 246, 0.5);
}
"""

# --- DASHBOARD UI ---
def create_dashboard():
    with gr.Blocks(title="Unified Bot Dashboard", theme=custom_theme, css=CUSTOM_CSS) as demo:
        gr.HTML("""
            <div class="dashboard-header">
                <h1>🤖 Unified Bot Dashboard</h1>
                <p>WhatsApp & Telegram AI Bot • Clone Mode • Scheduled Messages</p>
            </div>
        """)
        
        with gr.Tabs():
            # Tab 1: Messages
            with gr.Tab("📨 Messages"):
                with gr.Row():
                    filter_platform = gr.Dropdown(["all", "telegram", "whatsapp"], label="Platform Filter", value="all", scale=3)
                    refresh_msg = gr.Button("🔄 Refresh", scale=1)
                msg_display = gr.Textbox(lines=22, label="Recent Messages", elem_classes="message-display")
                
                refresh_msg.click(lambda p: format_messages(get_messages(p)), inputs=[filter_platform], outputs=[msg_display])
                demo.load(lambda p: format_messages(get_messages(p)), inputs=[filter_platform], outputs=[msg_display])

            # Tab 2: Send
            with gr.Tab("📤 Send Message"):
                with gr.Row():
                    contact_dropdown = gr.Dropdown(label="📱 Select Contact", choices=[], interactive=True, scale=4)
                    refresh_contacts = gr.Button("🔄", scale=0)
                
                with gr.Row():
                    platform_radio = gr.Radio(["telegram", "whatsapp"], label="Platform", scale=1)
                    user_id_box = gr.Textbox(label="User ID / Phone", scale=2)
                
                msg_input = gr.Textbox(label="✉️ Message", placeholder="Type your message here...", lines=3)
                send_btn = gr.Button("📤 Send Message", variant="primary")
                result_box = gr.Textbox(label="Result", interactive=False)

                refresh_contacts.click(update_contact_choices, outputs=[contact_dropdown])
                contact_dropdown.change(fill_contact_info, inputs=[contact_dropdown], outputs=[platform_radio, user_id_box])
                
                send_btn.click(lambda p, u, m: json.dumps(send_message_api(p, u, m)), 
                             inputs=[platform_radio, user_id_box, msg_input], outputs=[result_box])

            # Tab 3: Contacts Management
            with gr.Tab("👥 Contacts"):
                gr.Markdown("### 👥 Contact Relationships & Gender")
                gr.Markdown("Set relationship and gender for each contact. The bot uses this to adjust tone (e.g., won't say 'bhai' to your girlfriend).")
                
                contacts_table = gr.Markdown(value=get_contacts_table())
                refresh_table_btn = gr.Button("🔄 Refresh Table")
                refresh_table_btn.click(get_contacts_table, outputs=[contacts_table])
                
                gr.Markdown("---")
                gr.Markdown("#### ✏️ Edit Contact")
                
                with gr.Row():
                    edit_contact_dd = gr.Dropdown(
                        label="Select Contact",
                        choices=get_contact_choices_for_edit(),
                        interactive=True
                    )
                    refresh_edit_btn = gr.Button("🔄", scale=0)
                
                with gr.Row():
                    rel_dd = gr.Dropdown(
                        label="Relationship",
                        choices=VALID_RELATIONSHIPS,
                        value="friend",
                        interactive=True
                    )
                    gender_dd = gr.Dropdown(
                        label="Gender",
                        choices=VALID_GENDERS,
                        value="unknown",
                        interactive=True
                    )
                
                save_meta_btn = gr.Button("💾 Save", variant="primary")
                edit_status = gr.Markdown("")
                
                # Wire up interactions
                refresh_edit_btn.click(
                    lambda: gr.update(choices=get_contact_choices_for_edit()),
                    outputs=[edit_contact_dd]
                )
                edit_contact_dd.change(
                    fill_contact_meta,
                    inputs=[edit_contact_dd],
                    outputs=[rel_dd, gender_dd]
                )
                save_meta_btn.click(
                    save_contact_meta,
                    inputs=[edit_contact_dd, rel_dd, gender_dd],
                    outputs=[edit_status, contacts_table]
                )

            # Tab 4: Scheduler
            with gr.Tab("⏰ Scheduler"):
                gr.Markdown("### ⏰ Scheduled Messages")
                gr.Markdown("Set up automatic messages to be sent at specific times. Supports daily, weekly, and one-time schedules.")
                
                # ── Create Schedule Form ──
                gr.Markdown("---")
                gr.Markdown("#### ➕ Create New Schedule")
                
                with gr.Row():
                    sched_contact = gr.Dropdown(
                        label="📱 Contacts (select multiple)",
                        choices=get_contact_choices_for_edit(),
                        multiselect=True,
                        interactive=True
                    )
                    sched_refresh_contacts = gr.Button("🔄", scale=0)
                
                with gr.Row():
                    sched_message = gr.Textbox(
                        label="Message",
                        placeholder="Type your message here...",
                        lines=2
                    )
                    sched_ai_gen = gr.Checkbox(
                        label="🤖 AI Generate",
                        value=False,
                        info="Bot will generate a natural Hinglish message"
                    )
                
                with gr.Row():
                    sched_hour = gr.Dropdown(
                        label="Hour (IST)",
                        choices=[f"{h:02d}" for h in range(24)],
                        value="09",
                        interactive=True
                    )
                    sched_min = gr.Dropdown(
                        label="Minute",
                        choices=[f"{m:02d}" for m in range(0, 60, 5)],
                        value="00",
                        interactive=True
                    )
                    sched_type = gr.Radio(
                        ["daily", "weekly", "once"],
                        label="Schedule Type",
                        value="daily"
                    )
                
                with gr.Row(visible=False) as weekly_row:
                    sched_mon = gr.Checkbox(label="Mon", value=False)
                    sched_tue = gr.Checkbox(label="Tue", value=False)
                    sched_wed = gr.Checkbox(label="Wed", value=False)
                    sched_thu = gr.Checkbox(label="Thu", value=False)
                    sched_fri = gr.Checkbox(label="Fri", value=False)
                    sched_sat = gr.Checkbox(label="Sat", value=False)
                    sched_sun = gr.Checkbox(label="Sun", value=False)
                
                sched_date = gr.Textbox(
                    label="Date (YYYY-MM-DD)",
                    placeholder="2026-02-25",
                    visible=False
                )
                
                # Show/hide weekly days and date based on schedule type
                def update_schedule_visibility(stype):
                    return (
                        gr.update(visible=(stype == "weekly")),
                        gr.update(visible=(stype == "once"))
                    )
                sched_type.change(
                    update_schedule_visibility,
                    inputs=[sched_type],
                    outputs=[weekly_row, sched_date]
                )
                
                create_sched_btn = gr.Button("📅 Create Schedule", variant="primary")
                create_sched_status = gr.Markdown("")
                
                def create_schedule_ui(contact_selections, message, ai_gen, hour, minute, stype,
                                       mon, tue, wed, thu, fri, sat, sun, date_str):
                    if not contact_selections:
                        return "⚠️ Please select at least one contact.", get_schedules_table()
                    
                    # Ensure it's a list (single select returns string, multi returns list)
                    if isinstance(contact_selections, str):
                        contact_selections = [contact_selections]
                    
                    # Message
                    msg = "__AI_GENERATE__" if ai_gen else message
                    if not msg:
                        return "⚠️ Please enter a message or enable AI Generate.", get_schedules_table()
                    
                    # Time
                    time_str = f"{hour}:{minute}"
                    
                    # Days of week
                    days = []
                    if stype == "weekly":
                        day_checks = {"mon": mon, "tue": tue, "wed": wed, "thu": thu, "fri": fri, "sat": sat, "sun": sun}
                        days = [d for d, checked in day_checks.items() if checked]
                        if not days:
                            return "⚠️ Please select at least one day for weekly schedule.", get_schedules_table()
                    
                    created_names = []
                    try:
                        contacts = get_all_contacts()
                        for contact_sel in contact_selections:
                            key = contact_sel.split("[")[-1].rstrip("]")
                            c = contacts.get(key, {})
                            if not c:
                                continue
                            
                            contact_name = c.get("name", "Unknown")
                            platform = c.get("platform", "whatsapp")
                            phone_or_id = c.get("id", "")
                            
                            if platform == "whatsapp" and "@" not in phone_or_id:
                                phone_or_id += "@s.whatsapp.net"
                            
                            add_schedule(
                                contact_key=key,
                                contact_name=contact_name,
                                platform=platform,
                                phone_or_id=phone_or_id,
                                message=msg,
                                time_str=time_str,
                                schedule_type=stype,
                                days_of_week=days,
                                one_time_date=date_str if stype == "once" else "",
                            )
                            created_names.append(contact_name)
                        
                        if not created_names:
                            return "❌ No valid contacts found.", get_schedules_table()
                        
                        msg_preview = "🤖 AI Generated" if ai_gen else msg[:30]
                        names_str = ", ".join(created_names)
                        return f"✅ {len(created_names)} schedule(s) created for **{names_str}** at **{time_str}** ({stype}) — {msg_preview}", get_schedules_table()
                    except Exception as e:
                        return f"❌ Error: {e}", get_schedules_table()
                
                # ── Active Schedules Table ──
                gr.Markdown("---")
                gr.Markdown("#### 📋 Active Schedules")
                gr.Markdown("✅ = Active &nbsp;&nbsp; ❌ = Disabled &nbsp;&nbsp; 🤖 = AI-generated message")
                
                def get_schedules_table():
                    schedules = get_all_schedules()
                    if not schedules:
                        return "No scheduled messages yet. Create one above!"
                    
                    rows = []
                    rows.append("| ID | Contact | Time | Type | Days | Message | Status | Last Sent |")
                    rows.append("|-----|---------|------|------|------|---------|:------:|-----------|")
                    for s in schedules:
                        msg = "🤖 AI" if s.get("message") == "__AI_GENERATE__" else (s.get("message", "")[:25] + "..." if len(s.get("message", "")) > 25 else s.get("message", ""))
                        days = ", ".join(s.get("days_of_week", [])) if s.get("schedule_type") == "weekly" else (s.get("one_time_date", "") if s.get("schedule_type") == "once" else "everyday")
                        enabled = "✅" if s.get("enabled") else "❌"
                        last = s.get("last_sent", "—") or "—"
                        rows.append(f"| `{s.get('id', '?')}` | {s.get('contact_name', '?')} | {s.get('time', '?')} | {s.get('schedule_type', '?')} | {days} | {msg} | {enabled} | {last} |")
                    return "\n".join(rows)
                
                def get_schedule_choices():
                    """Get schedule IDs as dropdown choices."""
                    schedules = get_all_schedules()
                    choices = []
                    for s in schedules:
                        status = "✅" if s.get("enabled") else "❌"
                        label = f"{status} {s.get('contact_name','?')} — {s.get('time','?')} ({s.get('schedule_type','?')}) [{s.get('id','?')}]"
                        choices.append(label)
                    return choices
                
                schedules_display = gr.Markdown(value=get_schedules_table())
                
                # ── Actions Row ──
                with gr.Row():
                    sched_select = gr.Dropdown(
                        label="Select Schedule",
                        choices=get_schedule_choices(),
                        interactive=True,
                        scale=3
                    )
                    toggle_sched_btn = gr.Button("⏯️ Toggle", scale=1)
                    delete_sched_btn = gr.Button("🗑️ Delete", variant="stop", scale=1)
                    refresh_sched_btn = gr.Button("🔄", scale=0)
                
                sched_action_status = gr.Markdown("")
                
                def _extract_id(selection):
                    """Extract schedule ID from dropdown like '✅ Name — 09:00 (daily) [abc123]'"""
                    if not selection:
                        return None
                    try:
                        return selection.split("[")[-1].rstrip("]")
                    except Exception:
                        return None
                
                def toggle_schedule_ui(selection):
                    sid = _extract_id(selection)
                    if not sid:
                        return "⚠️ Select a schedule first.", get_schedules_table(), gr.update(choices=get_schedule_choices())
                    result = toggle_schedule(sid)
                    if result is not None:
                        state = "enabled ✅" if result else "disabled ❌"
                        return f"Schedule now **{state}**", get_schedules_table(), gr.update(choices=get_schedule_choices())
                    return "❌ Schedule not found.", get_schedules_table(), gr.update(choices=get_schedule_choices())
                
                def delete_schedule_ui(selection):
                    sid = _extract_id(selection)
                    if not sid:
                        return "⚠️ Select a schedule first.", get_schedules_table(), gr.update(choices=get_schedule_choices())
                    success = delete_schedule(sid)
                    if success:
                        return f"🗑️ Deleted.", get_schedules_table(), gr.update(choices=get_schedule_choices(), value=None)
                    return "❌ Schedule not found.", get_schedules_table(), gr.update(choices=get_schedule_choices())
                
                def refresh_schedules_ui():
                    return get_schedules_table(), gr.update(choices=get_schedule_choices()), ""
                
                toggle_sched_btn.click(toggle_schedule_ui, inputs=[sched_select], outputs=[sched_action_status, schedules_display, sched_select])
                delete_sched_btn.click(delete_schedule_ui, inputs=[sched_select], outputs=[sched_action_status, schedules_display, sched_select])
                refresh_sched_btn.click(refresh_schedules_ui, outputs=[schedules_display, sched_select, sched_action_status])
                
                # Wire up create button
                create_sched_btn.click(
                    create_schedule_ui,
                    inputs=[sched_contact, sched_message, sched_ai_gen, sched_hour, sched_min, sched_type,
                            sched_mon, sched_tue, sched_wed, sched_thu, sched_fri, sched_sat, sched_sun, sched_date],
                    outputs=[create_sched_status, schedules_display]
                )
                
                # Refresh contacts button
                sched_refresh_contacts.click(
                    lambda: gr.update(choices=get_contact_choices_for_edit()),
                    outputs=[sched_contact]
                )

            # Tab 5: Stats
            with gr.Tab("📊 Statistics"):
                stats_btn = gr.Button("Refresh Stats")
                stats_json = gr.JSON()
                stats_btn.click(lambda: requests.get(f"{API_BASE}/api/stats").json(), outputs=[stats_json])

            # Tab 6: Business Analyst
            with gr.Tab("📈 Analyst"):
                gr.Markdown("### 📈 Business Data Analyst")
                gr.Markdown("Upload CSV, Excel, PDF, or text files and ask questions about your data.")

                with gr.Row():
                    with gr.Column(scale=1):
                        gr.Markdown("#### 📁 Upload Data")
                        analyst_file = gr.File(
                            file_count="single",
                            label="Upload File (.csv, .xlsx, .pdf, .txt)",
                            file_types=[".csv", ".xlsx", ".xls", ".pdf", ".txt", ".md"]
                        )
                        analyst_upload_btn = gr.Button("📤 Ingest File", variant="primary")
                        analyst_upload_status = gr.Markdown("")

                    with gr.Column(scale=2):
                        gr.Markdown("#### 💬 Ask a Question")
                        analyst_doc_select = gr.Dropdown(
                            label="Select Document",
                            choices=[],
                            allow_custom_value=False,
                        )
                        analyst_question = gr.Textbox(
                            label="Your Question",
                            placeholder="e.g. Top 5 companies by revenue? / Show pie chart of categories",
                            lines=2,
                        )
                        with gr.Row():
                            analyst_query_btn = gr.Button("🔍 Analyze", variant="primary", scale=3)
                            analyst_clear_ctx_btn = gr.Button("🧹 Clear Context", variant="secondary", scale=1)
                        analyst_session_indicator = gr.Markdown("")

                # Results area: text + chart side by side
                with gr.Row():
                    with gr.Column(scale=3):
                        analyst_result = gr.Markdown(label="Results", value="Upload a file and ask a question to get started.")
                    with gr.Column(scale=2):
                        analyst_chart = gr.Image(label="📊 Chart", visible=True, show_label=True, height=400)

                gr.Markdown("---")
                gr.Markdown("#### 📋 Ingested Documents")
                analyst_docs_display = gr.Markdown("")

                with gr.Row():
                    analyst_refresh_btn = gr.Button("🔄 Refresh")
                    analyst_delete_select = gr.Dropdown(label="Select to Delete", choices=[])
                    analyst_delete_btn = gr.Button("🗑️ Delete", variant="stop")
                analyst_delete_status = gr.Markdown("")

                # ── Analyst Helpers ──
                def _get_doc_choices():
                    docs = analyst_list_documents()
                    return [f"{d['filename']} [{d['id']}]" for d in docs]

                def _get_docs_table():
                    docs = analyst_list_documents()
                    if not docs:
                        return "*No documents uploaded yet.*"
                    lines = ["| # | File | Type | Size | Uploaded |"]
                    lines.append("|---|------|------|------|----------|")
                    for i, d in enumerate(docs, 1):
                        size = f"{d.get('rows', d.get('pages', d.get('lines', '?')))} {'rows' if d['type'] in ('csv','excel') else 'pages' if d['type']=='pdf' else 'lines'}"
                        time_str = d.get('upload_time', '')[:16]
                        lines.append(f"| {i} | {d['filename']} | {d['type'].upper()} | {size} | {time_str} |")
                    return "\n".join(lines)

                def _get_session_md(doc_selection):
                    if not doc_selection:
                        return ""
                    import re
                    m = re.search(r'\[([^\]]+)\]$', doc_selection)
                    if not m:
                        return ""
                    doc_id = m.group(1)
                    info = analyst_session_info(doc_id)
                    count = info.get("count", 0)
                    if count > 0:
                        return f"📝 **{count}** previous queries in context — follow-up questions will use this context"
                    return ""

                def upload_analyst_file(file):
                    if not file:
                        return "⚠️ Please select a file.", _get_docs_table(), gr.update(choices=_get_doc_choices()), gr.update(choices=_get_doc_choices())
                    filepath = file.name if hasattr(file, 'name') else str(file)
                    result = analyst_ingest_file(filepath, os.path.basename(filepath))
                    if 'error' in result:
                        return f"❌ {result['error']}", _get_docs_table(), gr.update(choices=_get_doc_choices()), gr.update(choices=_get_doc_choices())
                    return result.get('summary', '✅ File ingested!'), _get_docs_table(), gr.update(choices=_get_doc_choices()), gr.update(choices=_get_doc_choices())

                def query_analyst(question, doc_selection):
                    if not question.strip():
                        return "⚠️ Please enter a question.", None, ""
                    doc_id = None
                    if doc_selection:
                        import re
                        m = re.search(r'\[([^\]]+)\]$', doc_selection)
                        if m:
                            doc_id = m.group(1)
                    result = analyst_query_data(question, doc_id)
                    answer = result.get('answer', 'No answer.')
                    source = result.get('source', '')
                    confidence = result.get('confidence', 0)
                    code = result.get('code_used', '')
                    chart_path = result.get('chart_path', None)

                    output = f"{answer}\n\n"
                    if source:
                        output += f"📎 **Source:** {source}\n"
                    if confidence:
                        conf_pct = int(confidence * 100)
                        bar = '█' * (conf_pct // 10) + '░' * (10 - conf_pct // 10)
                        output += f"📊 **Confidence:** {bar} {conf_pct}%\n"
                    if code:
                        output += f"\n<details><summary>🐍 Code Used</summary>\n\n```python\n{code}\n```\n</details>\n"
                    if result.get('from_cache'):
                        output += "\n⚡ *Served from cache*"

                    session_md = _get_session_md(doc_selection)
                    return output, chart_path, session_md

                def clear_analyst_context(doc_selection):
                    if not doc_selection:
                        return "⚠️ Select a document first."
                    import re
                    m = re.search(r'\[([^\]]+)\]$', doc_selection)
                    if not m:
                        return ""
                    doc_id = m.group(1)
                    analyst_clear_session(doc_id)
                    return "🧹 Context cleared — next query starts fresh"

                def delete_analyst_doc(selection):
                    if not selection:
                        return "⚠️ Select a document.", _get_docs_table(), gr.update(choices=_get_doc_choices()), gr.update(choices=_get_doc_choices())
                    import re
                    m = re.search(r'\[([^\]]+)\]$', selection)
                    if not m:
                        return "❌ Invalid selection.", _get_docs_table(), gr.update(choices=_get_doc_choices()), gr.update(choices=_get_doc_choices())
                    doc_id = m.group(1)
                    success = analyst_delete_doc(doc_id)
                    if success:
                        return "🗑️ Deleted.", _get_docs_table(), gr.update(choices=_get_doc_choices(), value=None), gr.update(choices=_get_doc_choices(), value=None)
                    return "❌ Not found.", _get_docs_table(), gr.update(choices=_get_doc_choices()), gr.update(choices=_get_doc_choices())

                def refresh_analyst_ui():
                    return _get_docs_table(), gr.update(choices=_get_doc_choices()), gr.update(choices=_get_doc_choices()), ""

                # Wire up events
                analyst_upload_btn.click(
                    upload_analyst_file,
                    inputs=[analyst_file],
                    outputs=[analyst_upload_status, analyst_docs_display, analyst_doc_select, analyst_delete_select]
                )
                analyst_query_btn.click(
                    query_analyst,
                    inputs=[analyst_question, analyst_doc_select],
                    outputs=[analyst_result, analyst_chart, analyst_session_indicator]
                )
                analyst_clear_ctx_btn.click(
                    clear_analyst_context,
                    inputs=[analyst_doc_select],
                    outputs=[analyst_session_indicator]
                )
                analyst_delete_btn.click(
                    delete_analyst_doc,
                    inputs=[analyst_delete_select],
                    outputs=[analyst_delete_status, analyst_docs_display, analyst_doc_select, analyst_delete_select]
                )
                analyst_refresh_btn.click(
                    refresh_analyst_ui,
                    outputs=[analyst_docs_display, analyst_doc_select, analyst_delete_select, analyst_delete_status]
                )
                
            # Tab 7: AI Clone Settings
            with gr.Tab("🧠 AI Clone Settings"):
                gr.Markdown("### 🎭 Bot Personality Mode")
                
                settings = get_ai_settings()
                
                with gr.Row():
                    mode_radio = gr.Radio(
                        ["Assistant Mode", "Clone Mode"], 
                        label="Active Mode", 
                        value=settings.get("mode", "Assistant Mode")
                    )
                    
                    owner_name_input = gr.Textbox(
                        label="Your Name in Chat Export (Exact Match)",
                        value=settings.get("owner_name", "User"),
                        placeholder="e.g. User"
                    )
                
                def save_settings_ui(mode, name):
                    save_ai_settings(mode, name)
                    return f"✅ Settings Saved: Mode set to {mode} for User: {name}"
                    
                with gr.Row():
                    save_settings_btn = gr.Button("Save Global Settings")
                    settings_status = gr.Markdown("")
                
                save_settings_btn.click(
                    save_settings_ui, 
                    inputs=[mode_radio, owner_name_input], 
                    outputs=[settings_status]
                )
                
                gr.Markdown("---")
                gr.Markdown("### 🗃️ Vector Database Status")
                
                def check_vectordb_status():
                    try:
                        from clone_manager import _get_collection
                        collection = _get_collection()
                        if collection is None:
                            return "❌ **VectorDB unavailable** — ChromaDB not connected"
                        count = collection.count()
                        if count == 0:
                            return "⚠️ **VectorDB is EMPTY** — Upload chat exports below to enable Clone Mode"
                        return f"✅ **VectorDB has {count} entries** — Clone Mode is ready"
                    except Exception as e:
                        return f"❌ Error checking VectorDB: {e}"
                
                vectordb_status = gr.Markdown(value=check_vectordb_status())
                check_db_btn = gr.Button("🔍 Check VectorDB Status")
                check_db_btn.click(check_vectordb_status, outputs=[vectordb_status])
                
                gr.Markdown("---")
                gr.Markdown("### 📚 Upload Chat History to Vector Database")
                
                chat_files = gr.File(
                    file_count="multiple", 
                    label="Upload WhatsApp .txt Exports (Max 10)"
                )
                
                with gr.Row():
                    process_btn = gr.Button("Parse and Ingest to VectorDB", variant="primary")
                    clear_db_btn = gr.Button("🗑️ Clear Vector Database", variant="stop")
                    
                ingest_status = gr.Markdown("Ready to process.")
                
                def process_chats_ui(files, owner_name):
                    if not files:
                        return "⚠️ Please upload at least one .txt file."
                    
                    if len(files) > 10:
                        return "⚠️ Maximum 10 files allowed at once to prevent overload."
                        
                    try:
                        filepaths = []
                        for f in files:
                            if isinstance(f, str):
                                filepaths.append(f)
                            elif hasattr(f, 'name'):
                                filepaths.append(f.name)
                            elif isinstance(f, dict):
                                filepaths.append(f.get('path', f.get('name', str(f))))
                                
                        pairs = parse_whatsapp_chats(filepaths, owner_name)
                        
                        if not pairs:
                            return f"❌ Could not find any replies from {owner_name}. Make sure the name exactly matches the export."
                            
                        added = ingest_into_vectordb(pairs)
                        return f"✅ Success! Ingested {added} message pairs into ChromaDB for Clone Mode."
                    except Exception as e:
                        import traceback
                        traceback.print_exc()
                        return f"❌ Error processing files: {str(e)}"
                    
                process_btn.click(
                    process_chats_ui, 
                    inputs=[chat_files, owner_name_input], 
                    outputs=[ingest_status]
                )
                
                def clear_db_ui():
                    success = clear_vectordb()
                    return "✅ Vector DB Cleared!" if success else "❌ Error clearing Vector DB"
                
                clear_db_btn.click(clear_db_ui, outputs=[ingest_status])
    
    return demo

if __name__ == "__main__":
    dashboard = create_dashboard()
    dashboard.launch(server_name="0.0.0.0", server_port=7860)