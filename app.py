import streamlit as st
import sqlite3
import pandas as pd
import pytesseract
import pypdf
import re
import io
import os 
import hashlib
import cv2
import logging
import contextlib
from datetime import datetime
from PIL import Image
import numpy as np
from deep_translator import GoogleTranslator
from fpdf import FPDF
import base64
from pdf2image import convert_from_bytes
from pyzbar.pyzbar import decode

from db import (
    init_db,
    connect,
    get_inventory,
    upsert_inventory,
    get_orders,
    create_order,
    update_order_status,
    get_templates,
    save_template,
    save_memory,
    get_memory,
    get_recent_preferences,
    add_action_log,
    record_preference,
    auth_login,
    add_user,
    load_sim_db,
    save_sim_db,
)
from memory import (
    get_setting,
    set_setting,
    suggest_alias,
    suggest_template,
    upsert_alias,
)
from sync import enqueue_action, process_queue, queue_status, can_sync_now
from ozone_wms_guardian import Guardian, GuardianConfig
from ozone_wms_guardian.admin.dashboard import render_guardian_dashboard

# GUARDIAN INITIALIZATION (lightweight, singleton)
_guardian = None

def get_guardian():
    global _guardian
    if _guardian is None:
        _guardian = Guardian(GuardianConfig())
        _guardian.start()
    return _guardian

# PAGE CONFIG
st.set_page_config(page_title="FULFILLMEENT - YESI", layout="wide", page_icon="📦")
init_db()
_guardian = get_guardian()  # Ensure guardian is warmed up after DB init

SCANNING_ID_REGEX = re.compile(r"[A-Z0-9][A-Z0-9\-]{2,}[A-Z0-9]")
PHONE_CODE_REGEX = re.compile(r"(\d{7})\s+(\d{4})")

# --- CORE LOGIC UTILITIES ---
def robust_parse_multiline(text):
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = re.split(r"\s+", line, maxsplit=1)
        key = parts[0].strip()
        value = parts[1].strip() if len(parts) > 1 else ""
        out.setdefault(key, set()).add(value)
    return out

def standardize_title(text):
    return re.sub(r"\s+", " ", text).strip().title()

# SIM.PY INTEGRATION UTILITY
def calculate_luhn(base14):
    """Integrated from sim.py for admin IMEI tools."""
    digits = [int(d) for d in str(base14)]
    for i in range(len(digits) - 1, -1, -2):
        doubled = digits[i] * 2
        digits[i] = doubled if doubled <= 9 else (doubled // 10) + (doubled % 10)
    return str((10 - (sum(digits) % 10)) % 10)


# --- CUSTOM CSS (ENHANCED LOGIN + UI) ---
def apply_custom_theme():
    st.markdown(
        """
        <style>
        /* General App Background */
        .stApp {
            background: radial-gradient(circle at center, #0a192f 0%, #050a19 100%);
            background-size: cover;
            background-attachment: fixed;
        }

        /* Glassmorphism Container */
        .login-glass {
            background: rgba(15, 35, 60, 0.45);
            backdrop-filter: blur(25px) saturate(190%);
            -webkit-backdrop-filter: blur(25px) saturate(190%);
            border: 1px solid rgba(100, 255, 218, 0.2);
            border-radius: 35px;
            padding: 3rem;
            max-width: 450px;
            margin: 5rem auto;
            box-shadow: 0 0 40px rgba(100, 255, 218, 0.15);
            text-align: center;
            position: relative;
        }

        /* Cyan Edge Glow effect */
        .login-glass::before {
            content: "";
            position: absolute;
            top: 10px;
            left: 10px;
            width: 100px;
            height: 100px;
            background: rgba(100, 255, 218, 0.4);
            filter: blur(50px);
            border-radius: 50%;
            pointer-events: none;
        }

        /* General UI Glass Panel */
        .glass {
            background: rgba(10, 20, 40, 0.55);
            backdrop-filter: blur(18px) saturate(170%);
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 24px;
            padding: 1.5rem;
            margin-bottom: 1rem;
        }

        /* Login Form Styling */
        .login-glass .stTextInput > div > div > input {
            background-color: rgba(5, 10, 25, 0.8) !important;
            color: #ccd6f6 !important;
            border-radius: 12px !important;
            border: 1px solid rgba(100, 255, 218, 0.1) !important;
            padding: 12px 15px !important;
        }

        .login-glass .stTextInput > div > div > input:focus {
            border-color: rgba(100, 255, 218, 0.8) !important;
            box-shadow: 0 0 10px rgba(100, 255, 218, 0.3) !important;
        }

        /* Login Button Styling */
        .login-glass .stButton > button {
            background: linear-gradient(135deg, #64ffda 0%, #00b4db 100%) !important;
            color: #0a192f !important;
            font-weight: bold !important;
            border-radius: 12px !important;
            border: none !important;
            width: 100% !important;
            padding: 12px !important;
            text-transform: uppercase;
            letter-spacing: 1px;
            box-shadow: 0 4px 15px rgba(100, 255, 218, 0.3) !important;
            transition: all 0.3s ease;
        }

        .login-glass .stButton > button:hover {
            box-shadow: 0 6px 20px rgba(100, 255, 218, 0.5) !important;
            transform: translateY(-2px);
        }

        /* Forgot Password / Links color */
        .login-glass a {
            color: rgba(200, 200, 200, 0.8) !important;
            font-size: 0.85rem;
            text-decoration: none;
        }

        /* WMS Logo area placeholder styling */
        .wms-logo-placeholder {
            border: 4px solid #64ffda;
            color: #64ffda;
            font-family: 'Courier New', monospace;
            font-size: 2rem;
            font-weight: bold;
            border-radius: 50%;
            width: 80px;
            height: 80px;
            margin: 0 auto 2rem auto;
            display: flex;
            align-items: center;
            justify-content: center;
            box-shadow: 0 0 20px rgba(100, 255, 218, 0.4);
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

def log_action(user, action, ref=None):
    add_action_log(action, ref, None, user)

# INITIALIZATION
apply_custom_theme()

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
if "user" not in st.session_state:
    st.session_state.user = None
if 'df_sim_db' not in st.session_state:
    st.session_state.df_sim_db = None

# --- AUTHENTICATION INTERFACE ---
if not st.session_state.authenticated:
    st.markdown('<div class="login-glass">', unsafe_allow_html=True)

    st.markdown('<div class="wms-logo-placeholder">(WMS)</div>', unsafe_allow_html=True)

    with st.form("login_form", clear_on_submit=False):
        uname = st.text_input("Username", placeholder="Username", label_visibility="collapsed")
        pwd = st.text_input("Password", type="password", placeholder="Password", label_visibility="collapsed")

        col_rem, col_forgot = st.columns([1, 1])
        with col_rem:
            remember = st.checkbox("Remember me", value=True)
        with col_forgot:
            st.markdown('<div style="text-align:right;"><a href="#">Forgot Password?</a></div>', unsafe_allow_html=True)

        submitted = st.form_submit_button("LOGIN")

    if submitted:
        user_data = auth_login(uname, pwd)
        if user_data:
            st.session_state.authenticated = True
            st.session_state.user = user_data
            log_action(user_data["username"], "Login Successful")
            st.rerun()
        else:
            st.error("Invalid username or password.")
            log_action(uname if uname else "Unknown", "Login Failed", "Invalid Credentials")

    st.markdown("</div>", unsafe_allow_html=True)
    st.stop()

# --- MAIN APP INTERFACE ---
user = st.session_state.user["username"]
role = st.session_state.user["role"]

st.title("Warehouse Operations Pro")
st.caption(f"Welcome, {user} ({role}). Local-first warehouse app with memory, OCR, and advanced adaptations.")

# --- SIDEBAR: SYSTEM STATUS & SETTINGS ---
with st.sidebar:
    st.header("🌐 System Status")
    online_access_status = can_sync_now()

    is_online = st.toggle("Online Access (Sync)", value=online_access_status, help="Disable to stop offline queue synchronization.")

    if is_online != online_access_status:
        set_setting("online_access", str(is_online))
        status_text = "Enabled" if is_online else "Disabled"
        st.success(f"Online Sync {status_text}.")
        log_action(user, "Set Sync Status", status_text)

    status_color = "green" if is_online else "red"
    status_msg = "ONLINE" if is_online else "OFFLINE (Queue Paused)"
    st.markdown(f"Status: ::{status_color}[{status_msg}]")

    st.divider()

    st.header("📊 Queue & Settings")

    col_u, col_r = st.columns(2)
    col_u.write(f"User: **{user}**")
    col_r.write(f"Role: **{role}**")

    if st.button("Logout", use_container_width=True):
        log_action(user, "Logout")
        st.session_state.authenticated = False
        st.session_state.user = None
        st.rerun()

    with st.expander("Site Settings"):
        operator = st.text_input("Operator name", value=get_setting("operator_name", ""))
        site = st.text_input("Site name", value=get_setting("site_name", "Main"))
        if st.button("Save settings"):
            set_setting("operator_name", operator)
            set_setting("site_name", site)
            st.success("Saved")
            log_action(user, "Settings Updated", f"Site: {site}")

    qs = queue_status()
    st.metric("Queued actions", qs["queued"], help="Sync paused if OFFLINE.")
    st.metric("Last sync", qs["last_sync"] or "Never")

    if st.button("Process offline queue", disabled=not is_online, use_container_width=True, help="Only active when ONLINE."):
        if can_sync_now():
            synced, failed = process_queue()
            st.success(f"Synced {synced}, failed {failed}")
            log_action(user, "Manual Sync", f"Synced: {synced}, Failed: {failed}")
        else:
            st.warning("Enable Online Access first.")

    st.divider()

    st.header("📋 Reports")
    st.markdown("Download snapshot of current operations.")

    if st.button("📊 Generate Operations Summary", use_container_width=True):
        with st.spinner("Generating summary..."):
            log_action(user, "Report Generated", "Operations Summary")
            inv_df = get_inventory()
            orders_df = get_orders()
            queue_stats = queue_status()

            summary_data = {
                "Metric": [
                    "Report Generated At",
                    "Generating User",
                    "Site Name",
                    "Total SKUs",
                    "Total Stock Units",
                    "Open Orders (Pending)",
                    "Items Enqueued for Sync"
                ],
                "Value": [
                    datetime.utcnow().isoformat(timespec="seconds"),
                    user,
                    get_setting("site_name", "Main"),
                    len(inv_df),
                    int(inv_df["stock"].sum()) if not inv_df.empty else 0,
                    int((orders_df["status"] == "Pending").sum()) if not orders_df.empty else 0,
                    queue_stats["queued"]
                ]
            }
            summary_df = pd.DataFrame(summary_data)

            csv_buffer = io.BytesIO()
            summary_df.to_csv(csv_buffer, index=False)
            csv_data = csv_buffer.getvalue()

            st.download_button(
                label="📥 Download Summary CSV",
                data=csv_data,
                file_name=f"warehouse_summary_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
                mime="text/csv",
                use_container_width=True,
                key="download_summary"
            )


# --- DASHBOARD DATA PREP ---
inv = get_inventory()
orders = get_orders()
q = queue_status()

c1, c2, c3, c4 = st.columns(4)
c1.metric("Items", int(inv["stock"].sum()) if not inv.empty else 0)
c2.metric("SKUs", len(inv))
c3.metric("Open orders", int((orders["status"] == "Pending").sum()) if not orders.empty else 0)
c4.metric("Queue", q["queued"])

# --- TAB DEFINITIONS ---
tab_names = ["Dashboard", "Inventory", "Orders", "Auditor", "Bulk Convert", "PDF Sequencer", "Templates", "Memory"]
if role == "Admin":
    tab_names.append("Admin 🔐")

tabs = st.tabs(tab_names)

tab_dash = tabs[0]
tab_inv = tabs[1]
tab_ord = tabs[2]
tab_aud = tabs[3]
tab_bulk = tabs[4]
tab_pdf = tabs[5]
tab_temp = tabs[6]
tab_mem = tabs[7]
tab_admin = tabs[8] if role == "Admin" else None

# --- TABS CONTENT ---

with tab_dash:
    st.markdown('<div class="glass">', unsafe_allow_html=True)
    st.subheader("Dashboard")
    st.write("Local-first operations with queued writes and offline resilience.")
    st.dataframe(inv, use_container_width=True, hide_index=True)
    st.markdown("</div>", unsafe_allow_html=True)

with tab_inv:
    st.markdown('<div class="glass">', unsafe_allow_html=True)
    st.subheader("Inventory Management")
    with st.form("inventory_form", clear_on_submit=True):
        sku = st.text_input("SKU")
        product = st.text_input("Product", help="Alias and Template suggestions populate below.")
        stock = st.number_input("Stock", min_value=0, value=0, step=1)
        location = st.text_input("Location", value="UNASSIGNED")
        note = st.text_input("Note", value="")
        submitted = st.form_submit_button("Save inventory item")

    if submitted and sku:
        upsert_inventory(sku, product, int(stock), location)
        add_action_log("inventory_upsert", sku, f"{product} | {stock} | {location}", user)
        enqueue_action("inventory_upsert", {
            "sku": sku, "product": product, "stock": int(stock),
            "location": location, "note": note
        })
        st.success("Saved locally and queued for sync.")
        st.rerun()

    st.dataframe(get_inventory(), use_container_width=True, hide_index=True)

    col_sug1, col_sug2 = st.columns(2)

    col_sug1.write(f"Alias suggestion: {suggest_alias(product) or 'None'}")
    col_sug2.write(f"Template suggestion: {suggest_template(product) or 'None'}")
    st.markdown("</div>", unsafe_allow_html=True)

with tab_ord:
    st.markdown('<div class="glass">', unsafe_allow_html=True)
    st.subheader("Orders")
    with st.form("order_form", clear_on_submit=True):
        order_id = st.text_input("Order ID")
        status = st.selectbox("Status", ["Pending", "Shipped", "Returned", "Cancelled"])
        items = st.text_area("Required SKUs, one per line")
        created = st.form_submit_button("Create order")

    if created and order_id:
        skus = [x.strip() for x in items.splitlines() if x.strip()]
        create_order(order_id, status, skus)
        add_action_log("order_create", order_id, ",".join(skus), user)
        enqueue_action("order_create", {"order_id": order_id, "status": status, "required_skus": skus})
        st.success("Order created locally and queued.")
        st.rerun()

    orders_df = get_orders()
    st.dataframe(orders_df, use_container_width=True, hide_index=True)

    if not orders_df.empty:
        st.divider()
        st.subheader("Update Order")
        selected = st.selectbox("Select Order ID to Update", orders_df["order_id"].tolist())
        new_status = st.selectbox("New status", ["Pending", "Shipped", "Returned", "Cancelled"], key="new_status")
        if st.button("Update selected order"):
            update_order_status(selected, new_status)
            add_action_log("order_update", selected, new_status, user)
            enqueue_action("order_update", {"order_id": selected, "status": new_status})
            st.success("Order updated and queued.")
            st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)

with tab_aud:
    st.markdown('<div class="glass">', unsafe_allow_html=True)
    st.subheader("Discrepancy Auditor")
    col_a, col_b = st.columns(2)
    with col_a:
        master_in = st.text_area("MASTER (Expected)", height=200, placeholder="ID Value")
    with col_b:
        scan_in = st.text_area("SCAN (Actual)", height=200, placeholder="ID Value")

    if st.button("Run Discrepancy Analysis", type="primary", use_container_width=True):
        if master_in and scan_in:
            m_map, s_map = robust_parse_multiline(master_in), robust_parse_multiline(scan_in)
            results = []
            for tid in sorted(set(m_map.keys()) | set(s_map.keys())):
                exp, got = m_map.get(tid, set()), s_map.get(tid, set())
                status = "✅ MATCH" if exp == got else "❌ ERROR"
                results.append({"ID": tid, "Status": status, "Expected": " | ".join(exp), "Actual": " | ".join(got)})

            res_df = pd.DataFrame(results)
            st.dataframe(res_df.style.apply(lambda x: ['background-color: #ffcccc' if '❌' in str(v) else '' for v in x], axis=1), use_container_width=True, hide_index=True)
            log_action(user, "Auditor Run")
    st.markdown("</div>", unsafe_allow_html=True)

with tab_bulk:
    st.markdown('<div class="glass">', unsafe_allow_html=True)
    st.subheader("Bulk Title Converter (Translation + Templates)")
    st.markdown("Paste original titles (non-English). App will translate, standardize, and apply matched **Templates**.")

    output_format = st.radio(
        "Select Output Format for Matched Items:", 
        ["Template Only", "Translation Only", "Combined (Template + Translation)"], 
        horizontal=True
    )

    col_w, col_g = st.columns(2)
    with col_w:
        white_col = st.text_area("📄 Input Original Titles (one per line)", height=300)

    if st.button("✨ Convert & Translate & Apply Templates", type="primary", use_container_width=True):
        if white_col:
            lines = white_col.strip().split("\n")
            results = []
            matched_templates_count = 0

            with st.spinner("Translating and checking templates..."):
                translator = GoogleTranslator(source='auto', target='en')

                for l in lines:
                    line = l.strip()
                    if line:
                        try:
                            translated = translator.translate(line)
                            std_title = standardize_title(translated)

                            template_match = suggest_template(std_title)

                            if template_match:
                                matched_templates_count += 1
                                if output_format == "Template Only":
                                    results.append(template_match)
                                elif output_format == "Translation Only":
                                    results.append(std_title)
                                else:
                                    results.append(f"{template_match} (Match: {std_title})")
                            else:
                                results.append(std_title)

                        except Exception as e:
                            results.append(line.upper())
                    else:
                        results.append("")

            with col_g:
                output_text = "\n".join(results)
                st.text_area("Output (Standardized via Templates/Translation)", value=output_text, height=300)

            st.success(f"Processed {len(lines)} titles. Applied {matched_templates_count} templates.")
            log_action(user, "Bulk Conversion", f"Processed: {len(lines)}, Templates: {matched_templates_count}")

    st.markdown("</div>", unsafe_allow_html=True)

with tab_pdf:
    st.markdown('<div class="glass">', unsafe_allow_html=True)
    st.subheader("Pro PDF Label Sequencer")

    # --- MODE SELECTOR ---
    sequencer_mode = st.radio(
        "🎯 Select Sequencing Mode:",
        ["📋 Smart Sort (Flexible)", "🔒 Strict Rearrange (Exact Order)", "📱 WB Phone+Code Matcher"],
        horizontal=True,
        help="Smart Sort: Includes unmatched pages. Strict: ONLY pages matching your sequence. WB Phone+Code: Matches by phone number + 4-digit code from Wildberries labels."
    )

    st.divider()

    col1, col2 = st.columns([1, 2])

    with col1:
        if sequencer_mode == "📱 WB Phone+Code Matcher":
            sort_list = st.text_area(
                "Target Phone+Code List", 
                height=300, 
                placeholder="Paste phone numbers and 4-digit codes here...\nExample:\n5261288 1844\n5385799 3666\n5393912 9223"
            )
            st.caption("Format: `phone_number 4-digit_code` (one per line). OCR will scan the bottom-right QR area of each label.")
        else:
            sort_list = st.text_area("Target Sequence Order", height=300, placeholder="Paste Tracking IDs here...")

        remove_duplicates = st.checkbox("Auto-Remove Duplicate IDs", value=True, help="Removes duplicate entries from your pasted sequence while preserving the order.")

    with col2:
        label_file = st.file_uploader("Upload Labels PDF (Bulk)", type="pdf")
        use_ocr = st.checkbox("Enable OCR Fallback", value=True)

        if sequencer_mode == "📱 WB Phone+Code Matcher":
            st.info("""
            **📱 WB Phone+Code Mode**

            This mode is optimized for **Wildberries (WB) shipping labels** that contain:
            - A 7-digit phone number
            - A 4-digit delivery code

            The system will:
            1. Scan each PDF page for phone+code pairs
            2. Match against your provided list
            3. Reorder pages to match your sequence exactly
            4. Report any missing or extra pages
            """)

    if st.button("⚙️ Process PDF", type="primary", use_container_width=True):

        # --- WB PHONE+CODE MODE ---
        if sequencer_mode == "📱 WB Phone+Code Matcher":
            # Parse phone+code list
            target_entries = []
            for line in sort_list.strip().split('\n'):
                line = line.strip()
                if not line:
                    continue
                match = PHONE_CODE_REGEX.search(line)
                if match:
                    target_entries.append({
                        'phone': match.group(1),
                        'code': match.group(2),
                        'raw': line
                    })
                else:
                    # Try loose matching: first 7 digits and last 4 digits
                    digits = re.findall(r'\d+', line)
                    if len(digits) >= 2:
                        # Find 7-digit and 4-digit sequences
                        phone = next((d for d in digits if len(d) == 7), None)
                        code = next((d for d in digits if len(d) == 4), None)
                        if phone and code:
                            target_entries.append({
                                'phone': phone,
                                'code': code,
                                'raw': line
                            })

            if remove_duplicates and target_entries:
                seen = set()
                cleaned_entries = []
                dupes_found = 0
                for entry in target_entries:
                    key = (entry['phone'], entry['code'])
                    if key not in seen:
                        seen.add(key)
                        cleaned_entries.append(entry)
                    else:
                        dupes_found += 1
                target_entries = cleaned_entries
                if dupes_found > 0:
                    st.toast(f"Cleaned {dupes_found} duplicate entries from sequence!", icon="🧹")

            if not target_entries or not label_file:
                st.warning("Provide phone+code list and upload a PDF.")
            else:
                with st.spinner("Scanning WB labels for phone+code pairs..."):
                    try:
                        pdf_reader = pypdf.PdfReader(io.BytesIO(label_file.getvalue()))
                        pdf_writer = pypdf.PdfWriter()

                        images = convert_from_bytes(label_file.getvalue(), dpi=200)
                        page_matches = []  # List of dicts with page info

                        for i, img in enumerate(images):
                            page_num = i + 1
                            w, h = img.size

                            # Strategy 1: Try barcode/QR decode first (fast)
                            barcodes = decode(img)
                            all_text = ""
                            for b in barcodes:
                                all_text += b.data.decode("utf-8") + " "

                            # Strategy 2: OCR on bottom-right crop (WB labels have phone+code there)
                            # Crop the bottom-right region where WB phone+code appears
                            crop = img.crop((w * 0.6, h * 0.65, w, h))
                            ocr_text = pytesseract.image_to_string(crop)
                            all_text += " " + ocr_text

                            # Strategy 3: If still no match, OCR full page
                            if not re.search(r'\d{7}', all_text):
                                full_text = pytesseract.image_to_string(img)
                                all_text += " " + full_text

                            # Extract phone+code pairs from all collected text
                            phones_found = re.findall(r'\b\d{7}\b', all_text)
                            codes_found = re.findall(r'\b\d{4}\b', all_text)

                            # Also try to find tracking ID
                            tracking_ids = SCANNING_ID_REGEX.findall(all_text)
                            tracking_id = tracking_ids[0] if tracking_ids else "N/A"

                            # Build candidate matches: pair each phone with each code
                            candidates = []
                            for phone in set(phones_found):
                                for code in set(codes_found):
                                    candidates.append({'phone': phone, 'code': code})

                            page_matches.append({
                                'page_idx': i,
                                'page_obj': pdf_reader.pages[i],
                                'tracking_id': tracking_id,
                                'candidates': candidates,
                                'phones': list(set(phones_found)),
                                'codes': list(set(codes_found)),
                                'raw_text': all_text[:200]
                            })

                        # --- MATCHING PHASE ---
                        # For each target entry, find the best matching page
                        matched_pages = []  # Pages already assigned
                        results_dataset = []
                        new_page_counter = 1

                        st.info(f"🔍 Found {len(page_matches)} pages in PDF. Matching against {len(target_entries)} target entries...")

                        for target in target_entries:
                            target_phone = target['phone']
                            target_code = target['code']
                            matched_page = None

                            # Find page with exact phone+code match
                            for pm in page_matches:
                                if pm['page_idx'] in matched_pages:
                                    continue
                                for cand in pm['candidates']:
                                    if cand['phone'] == target_phone and cand['code'] == target_code:
                                        matched_page = pm
                                        break
                                if matched_page:
                                    break

                            # Fallback: phone-only match if exact not found
                            if not matched_page:
                                for pm in page_matches:
                                    if pm['page_idx'] in matched_pages:
                                        continue
                                    if target_phone in pm['phones']:
                                        matched_page = pm
                                        break

                            if matched_page:
                                matched_pages.append(matched_page['page_idx'])
                                pdf_writer.add_page(matched_page['page_obj'])

                                results_dataset.append({
                                    "Status": "✅ MATCHED",
                                    "Sequence #": new_page_counter,
                                    "Phone": target_phone,
                                    "Code": target_code,
                                    "Original Page": matched_page['page_idx'] + 1,
                                    "Output Page": new_page_counter,
                                    "Tracking ID": matched_page['tracking_id'],
                                    "Match Type": "Exact" if any(c['phone'] == target_phone and c['code'] == target_code for c in matched_page['candidates']) else "Phone-Only",
                                    "Notes": "Found and sequenced"
                                })
                                new_page_counter += 1
                            else:
                                results_dataset.append({
                                    "Status": "❌ MISSING",
                                    "Sequence #": "—",
                                    "Phone": target_phone,
                                    "Code": target_code,
                                    "Original Page": "N/A",
                                    "Output Page": "N/A",
                                    "Tracking ID": "—",
                                    "Match Type": "—",
                                    "Notes": "No matching page found in PDF"
                                })

                        # Add extra pages not in target list
                        extra_count = 0
                        for pm in page_matches:
                            if pm['page_idx'] not in matched_pages:
                                pdf_writer.add_page(pm['page_obj'])
                                extra_count += 1
                                results_dataset.append({
                                    "Status": "ℹ️ EXTRA",
                                    "Sequence #": "—",
                                    "Phone": ", ".join(pm['phones']) if pm['phones'] else "N/A",
                                    "Code": ", ".join(pm['codes']) if pm['codes'] else "N/A",
                                    "Original Page": pm['page_idx'] + 1,
                                    "Output Page": new_page_counter,
                                    "Tracking ID": pm['tracking_id'],
                                    "Match Type": "—",
                                    "Notes": "Page in PDF but not in target list"
                                })
                                new_page_counter += 1

                        # --- RESULTS DISPLAY ---
                        if results_dataset:
                            st.divider()
                            st.markdown("### 📊 Processing Results")
                            res_df = pd.DataFrame(results_dataset)

                            # Color coding
                            def color_status(val):
                                if '✅' in str(val):
                                    return 'background-color: #d4edda; color: #155724'
                                elif '❌' in str(val):
                                    return 'background-color: #f8d7da; color: #721c24'
                                elif 'ℹ️' in str(val):
                                    return 'background-color: #fff3cd; color: #856404'
                                return ''

                            styled_df = res_df.style.applymap(color_status, subset=['Status'])
                            st.dataframe(styled_df, use_container_width=True, hide_index=True)

                            matched_count = sum(1 for r in results_dataset if '✅' in r['Status'])
                            missing_count = sum(1 for r in results_dataset if '❌' in r['Status'])
                            extra_count = sum(1 for r in results_dataset if 'ℹ️' in r['Status'])

                            col_stats1, col_stats2, col_stats3 = st.columns(3)
                            col_stats1.metric("✅ Matched", matched_count)
                            col_stats2.metric("❌ Missing", missing_count)
                            col_stats3.metric("ℹ️ Extra Pages", extra_count)

                        # --- PDF GENERATION & DOWNLOAD ---
                        if matched_count > 0 or extra_count > 0:
                            out_io = io.BytesIO()
                            pdf_writer.write(out_io)
                            log_action(user, "PDF_SEQUENCED_WB", f"Mode: WB Phone+Code, Matched: {matched_count}, Missing: {missing_count}, Extra: {extra_count}")
                            st.success(f"✅ PDF Ready: {matched_count} matched, {missing_count} missing, {extra_count} extra pages!")

                            filename = f"wb_sorted_labels_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
                            st.download_button(
                                label="📥 Download Sequenced PDF", 
                                data=out_io.getvalue(), 
                                file_name=filename, 
                                mime="application/pdf",
                                use_container_width=True
                            )
                        else:
                            st.error("❌ No matches found in document.")

                    except Exception as e:
                        st.error(f"❌ Processing Error: {str(e)}")
                        import traceback
                        st.error(traceback.format_exc())

        # --- ORIGINAL MODES (Smart Sort / Strict Rearrange) ---
        else:
            target_ids_raw = [tid.strip() for tid in sort_list.split('\n') if tid.strip()]
            target_ids = []
            for tid in target_ids_raw:
                match = SCANNING_ID_REGEX.search(tid)
                target_ids.append(match.group() if match else tid)

            if remove_duplicates and target_ids:
                seen = set()
                cleaned_ids = []
                list_duplicates_found = 0
                for tid in target_ids:
                    if tid not in seen:
                        seen.add(tid)
                        cleaned_ids.append(tid)
                    else:
                        list_duplicates_found += 1
                target_ids = cleaned_ids

                if list_duplicates_found > 0:
                    st.toast(f"Cleaned {list_duplicates_found} duplicate IDs from sequence!", icon="🧹")

            if not target_ids or not label_file:
                st.warning("Provide sequence IDs and upload a PDF.")
            else:
                with st.spinner("Analyzing PDF pages..."):
                    try:
                        pdf_reader = pypdf.PdfReader(io.BytesIO(label_file.getvalue()))
                        pdf_writer = pypdf.PdfWriter()

                        images = convert_from_bytes(label_file.getvalue(), dpi=200)
                        id_to_page_map = {}
                        pdf_duplicates_skipped = 0

                        for i, img in enumerate(images):
                            page_codes = []
                            barcodes = decode(img)
                            for b in barcodes: 
                                page_codes.extend(SCANNING_ID_REGEX.findall(b.data.decode("utf-8")))

                            if not barcodes and use_ocr: 
                                page_codes.extend(SCANNING_ID_REGEX.findall(pytesseract.image_to_string(img)))

                            for code in set(page_codes): 
                                if code not in id_to_page_map:
                                    id_to_page_map[code] = {"page": pdf_reader.pages[i], "original_idx": i + 1}
                                else:
                                    pdf_duplicates_skipped += 1

                        if pdf_duplicates_skipped > 0:
                            st.toast(f"Skipped {pdf_duplicates_skipped} duplicate page(s) in PDF!", icon="ℹ️")

                        results_dataset = []
                        matched_count = 0
                        new_page_counter = 1
                        expected_set = set(target_ids)

                        # --- STRICT REARRANGE MODE ---
                        if sequencer_mode == "🔒 Strict Rearrange (Exact Order)":
                            st.info("🔒 **STRICT MODE**: Only pages matching your sequence will be included. Pages NOT in your list will be EXCLUDED.")

                            for tid in target_ids:
                                if tid in id_to_page_map:
                                    orig_page = id_to_page_map[tid]["original_idx"]
                                    conv_page = new_page_counter
                                    pdf_writer.add_page(id_to_page_map[tid]["page"])
                                    matched_count += 1
                                    new_page_counter += 1
                                    mis_pdf = ""
                                    mis_table = ""
                                else:
                                    orig_page = "N/A"
                                    conv_page = "N/A"
                                    mis_pdf = ""
                                    mis_table = tid

                                results_dataset.append({
                                    "Status": "✅ INCLUDED" if tid in id_to_page_map else "❌ MISSING",
                                    "Sequence Order": target_ids.index(tid) + 1,
                                    "ID": tid,
                                    "Original Page": orig_page,
                                    "Output Page": conv_page,
                                    "Notes": "Found and sequenced" if tid in id_to_page_map else "ID not detected in PDF"
                                })

                        # --- SMART SORT MODE ---
                        else:
                            st.info("📋 **SMART MODE**: Pages matching your sequence come first (in order), followed by any extra pages found.")

                            for tid in target_ids:
                                if tid in id_to_page_map:
                                    orig_page = id_to_page_map[tid]["original_idx"]
                                    conv_page = new_page_counter
                                    pdf_writer.add_page(id_to_page_map[tid]["page"])
                                    matched_count += 1
                                    new_page_counter += 1
                                    mis_pdf = ""
                                    mis_table = ""
                                else:
                                    orig_page = "N/A"
                                    conv_page = "N/A"
                                    mis_pdf = ""
                                    mis_table = tid

                                results_dataset.append({
                                    "Original pdf page": orig_page,
                                    "CONVERTED pdf page": conv_page,
                                    "MISMATCH from pdf": mis_pdf,
                                    "MISMATCH from TABLE": mis_table
                                })

                            # Phase 2: Identify extra items found in the PDF that were NOT in the target sequence
                            for tid, data in id_to_page_map.items():
                                if tid not in expected_set:
                                    pdf_writer.add_page(data["page"])
                                    new_page_counter += 1
                                    results_dataset.append({
                                        "Original pdf page": data["original_idx"],
                                        "CONVERTED pdf page": new_page_counter - 1,
                                        "MISMATCH from pdf": tid,
                                        "MISMATCH from TABLE": ""
                                    })

                        # --- RESULTS DISPLAY ---
                        if results_dataset:
                            st.divider()
                            st.markdown("### 📊 Processing Results")
                            res_df = pd.DataFrame(results_dataset)
                            st.dataframe(res_df, use_container_width=True, hide_index=True)

                        # --- PDF GENERATION & DOWNLOAD ---
                        if matched_count > 0:
                            out_io = io.BytesIO()
                            pdf_writer.write(out_io)
                            log_action(user, "PDF_SEQUENCED", f"Mode: {sequencer_mode}, Matched: {matched_count} pages.")
                            st.success(f"✅ PDF Ready: {matched_count} pages sequenced!")

                            filename = f"sorted_labels_{sequencer_mode.split('(')[1].split(')')[0].lower().replace(' ', '_')}.pdf"
                            st.download_button(
                                label="📥 Download Sequenced PDF", 
                                data=out_io.getvalue(), 
                                file_name=filename, 
                                mime="application/pdf",
                                use_container_width=True
                            )
                        else:
                            st.error("❌ No matches found in document.")

                    except Exception as e:
                        st.error(f"❌ Processing Error: {str(e)}")

    st.markdown("</div>", unsafe_allow_html=True)

with tab_temp:
    st.markdown('<div class="glass">', unsafe_allow_html=True)
    st.subheader("Templates Database")
    with st.form("template_form", clear_on_submit=True):
        raw = st.text_input("Raw/Translated Title")
        standard = st.text_input("Standard/Clean Title")
        saved = st.form_submit_button("Save template")

    if saved and raw and standard:
        save_template(raw, standard)
        enqueue_action("template_save", {"raw": raw, "standard": standard})
        st.success("Template saved locally and queued for sync.")
        log_action(user, "Template Saved", f"{raw} -> {standard}")
        st.rerun()

    st.dataframe(get_templates(), use_container_width=True, hide_index=True)
    st.markdown("</div>", unsafe_allow_html=True)

with tab_mem:
    st.markdown('<div class="glass">', unsafe_allow_html=True)
    st.subheader("Memory & Preferences")

    col_m1, col_m2 = st.columns(2)

    with col_m1:
        st.write("### General Preferences")
        with st.form("memory_form", clear_on_submit=True):
            pref_key = st.text_input("Preference key")
            pref_value = st.text_input("Preference value")
            save_pref = st.form_submit_button("Save memory")

        if save_pref and pref_key:
            save_memory(pref_key, pref_value)
            record_preference(pref_key, pref_value)
            st.success("Memory stored locally.")
            log_action(user, "Memory Saved", pref_key)
            st.rerun()

    with col_m2:
        st.write("### Product Aliases")
        alias_src = st.text_input("Alias source text")
        alias_dst = st.text_input("Alias target text")
        if st.button("Save alias") and alias_src and alias_dst:
            upsert_alias(alias_src, alias_dst)
            enqueue_action("memory_save", {"key": f"alias:{alias_src.lower().strip()}", "value": alias_dst})
            st.success("Alias saved and queued.")
            log_action(user, "Alias Saved", f"{alias_src} -> {alias_dst}")
            st.rerun()

    st.write("### Recent System Preferences")
    st.dataframe(get_recent_preferences(), use_container_width=True, hide_index=True)
    st.markdown("</div>", unsafe_allow_html=True)

# --- ADMIN PANEL (Role Restricted) ---
if role == "Admin" and tab_admin:
    with tab_admin:
        st.markdown('<div class="glass">', unsafe_allow_html=True)
        st.subheader("Admin Control Panel")

        adm_opt = st.radio("Admin Tool", ["👤 User Management & Logs", "📱 SIM Database Manager", "🛡️ Ozone Guardian Ops Center"], horizontal=True)
        st.divider()

        if adm_opt == "👤 User Management & Logs":
            st.subheader("User Management")
            with st.expander("Add New System User"):
                new_u = st.text_input("New Username")
                new_p = st.text_input("New Password", type="password")
                new_r = st.selectbox("Role", ["Operator", "Admin"])
                if st.button("Create User") and new_u and new_p:
                    add_user(new_u, new_p, new_r)
                    st.success(f"User {new_u} added.")
                    add_action_log("User Created", new_u, new_r, user)

            st.subheader("System Audit Logs")
            with connect() as conn:
                logs_df = pd.read_sql_query("SELECT created_at, user, action_type, ref_id, payload FROM action_logs ORDER BY created_at DESC LIMIT 100", conn)
            st.dataframe(logs_df, use_container_width=True, hide_index=True)

        elif adm_opt == "📱 SIM Database Manager":
            st.subheader("📱 Samsung IMEI Database Manager")

            if st.session_state.df_sim_db is None:
                st.session_state.df_sim_db = load_sim_db()

            sim_tools_col, sim_conv_col = st.columns([1, 2])

            with sim_tools_col:
                st.markdown("### 🛠️ SIM Database Tools")

                search_query = st.text_input("🔍 Search Model or TAC (8 digits)", help="Enter Model Name or TAC Prefix.")
                display_sim_df = st.session_state.df_sim_db

                if search_query:
                    display_sim_df = display_sim_df[
                        display_sim_df['Model_Series'].str.contains(search_query, case=False, na=False) | 
                        display_sim_df['TAC_Prefix'].str.contains(search_query, na=False)
                    ]

                st.write(f"Showing {len(display_sim_df)} entries")

                edited_sim_df = st.data_editor(
                    display_sim_df, 
                    num_rows="dynamic", 
                    use_container_width=True,
                    column_config={
                        "TAC_Prefix": st.column_config.TextColumn("TAC Prefix (8 digits)"),
                        "Expected_Offset": st.column_config.NumberColumn("Offset", format="%d"),
                        "Model_Series": "Model Name",
                        "Type": "Type"
                    },
                    key="sim_data_editor"
                )

                if st.button("💾 Save SIM Changes to CSV"):
                    if search_query:
                        st.session_state.df_sim_db.update(edited_sim_df)
                    else:
                        st.session_state.df_sim_db = edited_sim_df

                    save_sim_db(st.session_state.df_sim_db)
                    st.success("SIM Database file updated!")
                    log_action(user, "SIM DB Saved", f"{len(st.session_state.df_sim_db)} entries")

            with sim_conv_col:
                st.markdown("### 📱 IMEI Converter Tools")
                sim_db_map = dict(zip(st.session_state.df_sim_db['TAC_Prefix'], st.session_state.df_sim_db['Expected_Offset']))

                col_c1, col_c2 = st.columns(2)
                with col_c1:
                    st.write("#### 1. Calibration")
                    cal_input = st.text_area("Paste samples (IMEI 1 | IMEI 2):", height=150, placeholder="15 digits each")
                with col_c2:
                    st.write("#### 2. Targets")
                    batch_input = st.text_area("Paste IMEI 1 list (15 digits):", height=150)

                if batch_input:
                    active_sim_map = sim_db_map.copy()
                    if cal_input:
                        for line in cal_input.strip().split('\n'):
                            imeis = re.findall(r'\b\d{15}\b', line)
                            if len(imeis) >= 2:
                                active_sim_map[imeis[0][:8]] = int(imeis[1][:14]) - int(imeis[0][:14])

                    target_imeis = re.findall(r'\b\d{15}\b', batch_input)
                    sim_results = []

                    for i1 in target_imeis:
                        tac = i1[:8]
                        default_sim_val = sim_db_map.get('0', 8) 
                        sim_offset = active_sim_map.get(tac, default_sim_val)

                        model_info = st.session_state.df_sim_db[st.session_state.df_sim_db['TAC_Prefix'] == tac]
                        model_sim_name = model_info['Model_Series'].values[0] if not model_info.empty else "Unknown TAC"

                        base14 = i1[:14]
                        new_base = str(int(base14) + int(sim_offset)).zfill(14)
                        i2 = new_base + calculate_luhn(new_base)

                        sim_results.append({
                            "Model": model_sim_name,
                            "IMEI 1": i1,
                            "IMEI 2": i2,
                            "TAC": tac,
                            "Applied Offset": f"{int(sim_offset):+}"
                        })

                    if sim_results:
                        st.divider()
                        st.write("#### Integrated Results")
                        st.dataframe(pd.DataFrame(sim_results), use_container_width=True, hide_index=True)
                        log_action(user, "SIM IMEI Converted", f"Processed: {len(sim_results)}")

        elif adm_opt == "🛡️ Ozone Guardian Ops Center":
            # Build lightweight context from current warehouse state
            inv_df = get_inventory()
            orders_df = get_orders()
            q = queue_status()
            ctx = {
                "inventory": {
                    "total_skus": len(inv_df),
                    "total_stock": int(inv_df["stock"].sum()) if not inv_df.empty else 0,
                },
                "orders": orders_df.to_dict("records") if not orders_df.empty else [],
                "pending_orders": int((orders_df["status"] == "Pending").sum()) if not orders_df.empty else 0,
                "stale_pending_minutes": 0,  # Could be computed from timestamps
                "sync_queue": q,
                "low_stock_skus": inv_df[inv_df["stock"] < 5]["sku"].tolist() if not inv_df.empty else [],
                "failed_logins_last_5m": 0,
            }
            _guardian.analyze(ctx)
            render_guardian_dashboard(
                health=_guardian.health,
                alerts=_guardian.alerts,
                suggestions=_guardian.suggestions,
                recovery=_guardian.recovery,
                tuner=_guardian.tuner,
                ozone=_guardian.ozone,
            )
            log_action(user, "Guardian Dashboard Opened", "Ops Center")

        st.markdown("</div>", unsafe_allow_html=True)
