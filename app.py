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
    get_memory, # Added to access setting details directly
    get_recent_preferences,
    add_action_log,
    record_preference,
    auth_login,
    add_user,
    # ADMIN SIM INTEGRATION
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

# PAGE CONFIG
st.set_page_config(page_title="FULFILLMEENT - YESI", layout="wide", page_icon="📦")
init_db()

SCANNING_ID_REGEX = re.compile(r"[A-Z0-9]{4,}")

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
    # Enhanced CSS to match the glassmorphism login interface exactly (image_0.png)
    # Plus general app glass styling.
    st.markdown(
        f"""
        <style>
        /* General App Background (replicated from app.py but refined) */
        .stApp {{
            background: radial-gradient(circle at center, #0a192f 0%, #050a19 100%);
            background-size: cover;
            background-attachment: fixed;
        }}
        
        /* Glassmorphism Container (Image_0.png style) */
        .login-glass {{
            background: rgba(15, 35, 60, 0.45);
            backdrop-filter: blur(25px) saturate(190%);
            -webkit-backdrop-filter: blur(25px) saturate(190%);
            border: 1px solid rgba(100, 255, 218, 0.2); /* Faint cyan border */
            border_radius: 35px;
            padding: 3rem;
            max_width: 450px;
            margin: 5rem auto;
            box_shadow: 0 0 40px rgba(100, 255, 218, 0.15); /* Internal glow */
            text-align: center;
            position: relative;
        }}
        
        /* Cyan Edge Glow effect (top-left) derived from Image_0.png */
        .login-glass::before {{
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
        }}

        /* General UI Glass Panel (retained from app.py) */
        .glass {{
            background: rgba(10, 20, 40, 0.55);
            backdrop-filter: blur(18px) saturate(170%);
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 24px;
            padding: 1.5rem;
            margin_bottom: 1rem;
        }}
        
        /* Login Form Styling (matching Image_0.png inputs) */
        .login-glass .stTextInput > div > div > input {{
            background-color: rgba(5, 10, 25, 0.8) !important;
            color: #ccd6f6 !important;
            border-radius: 12px !important;
            border: 1px solid rgba(100, 255, 218, 0.1) !important;
            padding: 12px 15px !important;
        }}
        
        .login-glass .stTextInput > div > div > input:focus {{
            border-color: rgba(100, 255, 218, 0.8) !important;
            box_shadow: 0 0 10px rgba(100, 255, 218, 0.3) !important;
        }}

        /* Login Button Styling (Image_0.png) */
        .login-glass .stButton > button {{
            background: linear-gradient(135deg, #64ffda 0%, #00b4db 100%) !important;
            color: #0a192f !important;
            font-weight: bold !important;
            border-radius: 12px !important;
            border: none !important;
            width: 100% !important;
            padding: 12px !important;
            text_transform: uppercase;
            letter_spacing: 1px;
            box_shadow: 0 4px 15px rgba(100, 255, 218, 0.3) !important;
            transition: all 0.3s ease;
        }}
        
        .login-glass .stButton > button:hover {{
            box_shadow: 0 6px 20px rgba(100, 255, 218, 0.5) !important;
            transform: translateY(-2px);
        }}
        
        /* Forgot Password / Links color (Image_0.png) */
        .login-glass a {{
            color: rgba(200, 200, 200, 0.8) !important;
            font_size: 0.85rem;
            text_decoration: none;
        }}
        
        /* WMS Logo area placeholder styling */
        .wms-logo-placeholder {{
            border: 4px solid #64ffda;
            color: #64ffda;
            font_family: 'Courier New', monospace;
            font_size: 2rem;
            font_weight: bold;
            border_radius: 50%;
            width: 80px;
            height: 80px;
            margin: 0 auto 2rem auto;
            display: flex;
            align_items: center;
            justify-content: center;
            box_shadow: 0 0 20px rgba(100, 255, 218, 0.4);
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )

def log_action(user, action, ref=None):
    add_action_log(action, ref, None, user) # Fixed log call signature

# INITIALIZATION
apply_custom_theme()

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
if "user" not in st.session_state:
    st.session_state.user = None
if 'df_sim_db' not in st.session_state: # SIM.py Integrated state
    st.session_state.df_sim_db = None

# --- AUTHENTICATION INTERFACE (ENHANCED LIKE IMAGE_0.PNG) ---
if not st.session_state.authenticated:
    # Top-level glass container (Image_0.png style)
    st.markdown('<div class="login-glass">', unsafe_allow_html=True)
    
    # Integrated WMS Logo placeholder (replaces standard st.title inside login)
    st.markdown('<div class="wms-logo-placeholder">WMS</div>', unsafe_allow_html=True)
    
    with st.form("login_form", clear_on_submit=False):
        # Username Input (styled via CSS to look like Image_0.png)
        # Note: Icons require custom html injection not standard in st.text_input
        # but the style is replicated.
        uname = st.text_input("Username", placeholder="Username", label_visibility="collapsed")
        
        # Password Input (styled via CSS)
        pwd = st.text_input("Password", type="password", placeholder="Password", label_visibility="collapsed")
        
        # Flex container for Remember Me and Forgot Password (Image_0.png style)
        col_rem, col_forgot = st.columns([1, 1])
        with col_rem:
            remember = st.checkbox("Remember me", value=True)
        with col_forgot:
            st.markdown('<div style="text-align:right;"><a href="#">Forgot Password?</a></div>', unsafe_allow_html=True)
            
        # LOGIN Button (styled via CSS)
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
    st.stop() # Halt main app execution until logged in

# --- MAIN APP INTERFACE ---
user = st.session_state.user["username"]
role = st.session_state.user["role"]

st.title("Warehouse Operations Pro")
st.caption(f"Welcome, {user} ({role}). Local-first warehouse app with memory, OCR, and advanced adaptations.")

# --- SIDEBAR: REWORKED ---
with st.sidebar:
    # 1. ONLINE ACCESS SWITCH (Integrated Requirement)
    st.header("🌐 System Status")
    online_access_status = can_sync_now()
    
    # Toggle switch in sidebar
    is_online = st.toggle("Online Access (Sync)", value=online_access_status, help="Disable to stop offline queue synchronization.")
    
    # Save the setting if it changes
    if is_online != online_access_status:
        set_setting("online_access", str(is_online))
        status_text = "Enabled" if is_online else "Disabled"
        st.success(f"Online Sync {status_text}.")
        log_action(user, "Set Sync Status", status_text)
        st.rerun()

    status_color = "green" if is_online else "red"
    status_msg = "ONLINE" if is_online else "OFFLINE (Queue Paused)"
    st.markdown(f"Status: ::{status_color}[{status_msg}]")
    
    st.divider()

    # 2. SESSION & OFFLINE QUEUE (Existing)
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

    # Offline Queue Status (Integrated check for online status)
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

    # 3. REPORTS (Integrated Requirement)
    st.header("📋 Reports")
    # New sidebar option for summary report
    st.markdown("Download snapshot of current operations.")
    
    if st.button("📊 Generate Operations Summary", use_container_width=True):
        with st.spinner("Generating summary..."):
            log_action(user, "Report Generated", "Operations Summary")
            # Compile current stats
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
            
            # Prepare CSV for download
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

# Key Metrics Row
c1, c2, c3, c4 = st.columns(4)
c1.metric("Items", int(inv["stock"].sum()) if not inv.empty else 0)
c2.metric("SKUs", len(inv))
c3.metric("Open orders", int((orders["status"] == "Pending").sum()) if not orders.empty else 0)
c4.metric("Queue", q["queued"])

# --- TAB DEFINITIONS ---
tab_names = ["Dashboard", "Inventory", "Orders", "Auditor", "Bulk Convert", "PDF Sequencer", "Templates", "Memory"]
# Only add Admin tab if role is Admin
if role == "Admin":
    tab_names.append("Admin 🔐")

# Correct logic to handle tabs based on role
tabs = st.tabs(tab_names)

# Mapping tabs to variables manually as st.tabs(names) returns a list
tab_dash = tabs[0]
tab_inv = tabs[1]
tab_ord = tabs[2]
tab_aud = tabs[3]
tab_bulk = tabs[4]
tab_pdf = tabs[5]
tab_temp = tabs[6]
tab_mem = tabs[7]
# Handle Admin tab visibility correctly
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
    
    # Wrap the output in an f-string so it evaluates as one single element
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
                # Use standard error icon for mismatched status (Image_3.png style)
                status = "✅ MATCH" if exp == got else "❌ ERROR"
                results.append({"ID": tid, "Status": status, "Expected": " | ".join(exp), "Actual": " | ".join(got)})
            
            res_df = pd.DataFrame(results)
            # Apply styling matching image_4.png
            st.dataframe(res_df.style.apply(lambda x: ['background-color: #ffcccc' if '❌' in str(v) else '' for v in x], axis=1), use_container_width=True, hide_index=True)
            log_action(user, "Auditor Run")
    st.markdown("</div>", unsafe_allow_html=True)

with tab_bulk:
    st.markdown('<div class="glass">', unsafe_allow_html=True)
    st.subheader("Bulk Title Converter (Translation + Templates)")
    st.markdown("Paste original titles (non-English). App will translate, standardize, and apply matched **Templates**.")
    
    # --- NEW: Output Format Selector ---
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
                            # 1. Translate
                            translated = translator.translate(line)
                            # 2. Standardize Title
                            std_title = standardize_title(translated)
                            
                            # 3. Check for Template
                            template_match = suggest_template(std_title)
                            
                            if template_match:
                                matched_templates_count += 1
                                # --- NEW: Apply Formatting based on Selection ---
                                if output_format == "Template Only":
                                    results.append(template_match)
                                elif output_format == "Translation Only":
                                    results.append(std_title)
                                else:
                                    # Combined format
                                    results.append(f"{template_match} (Match: {std_title})")
                            else:
                                # No template found, just return the translated standardized title
                                results.append(std_title)
                                
                        except Exception as e:
                            results.append(line.upper()) # Fallback
                    else:
                        results.append("")
                        
            with col_g:
                output_text = "\n".join(results)
                st.text_area(" Output (Standardized via Templates/Translation)", value=output_text, height=300)
                
            st.success(f"Processed {len(lines)} titles. Applied {matched_templates_count} templates.")
            log_action(user, "Bulk Conversion", f"Processed: {len(lines)}, Templates: {matched_templates_count}")
                
    st.markdown("</div>", unsafe_allow_html=True)

with tab_pdf:
    st.markdown('<div class="glass">', unsafe_allow_html=True)
    st.subheader("Pro PDF Label Sequencer")
    
    col1, col2 = st.columns([1, 2])
    with col1:
        sort_list = st.text_area("Target Sequence Order", height=300, placeholder="Paste Tracking IDs here...")
        remove_duplicates = st.checkbox("Auto-Remove Duplicate IDs", value=True, help="Removes duplicate tracking IDs from your pasted sequence while preserving the order.")
    with col2:
        label_file = st.file_uploader("Upload Labels PDF (Bulk)", type="pdf")
        use_ocr = st.checkbox("Enable OCR Fallback", value=True)

    if st.button("Scan & Sort PDF", type="primary", use_container_width=True):
        
        # Clean target IDs based on regex to form the expected TABLE sequence
        target_ids_raw = [tid.strip() for tid in sort_list.split('\n') if tid.strip()]
        target_ids = []
        for tid in target_ids_raw:
            match = SCANNING_ID_REGEX.search(tid)
            target_ids.append(match.group() if match else tid)

        # Remove Duplicates Logic from Target List
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
            with st.spinner("Mapping PDF pages via Barcodes & OCR..."):
                try:
                    pdf_reader = pypdf.PdfReader(io.BytesIO(label_file.getvalue()))
                    pdf_writer = pypdf.PdfWriter()
                    
                    # Convert to images using pdf2image
                    images = convert_from_bytes(label_file.getvalue(), dpi=200)
                    id_to_page_map = {}
                    pdf_duplicates_skipped = 0 # Track duplicate pages in the physical PDF
                    
                    for i, img in enumerate(images):
                        page_codes = []
                        barcodes = decode(img)
                        for b in barcodes: 
                            page_codes.extend(SCANNING_ID_REGEX.findall(b.data.decode("utf-8")))
                        
                        if not barcodes and use_ocr: 
                            page_codes.extend(SCANNING_ID_REGEX.findall(pytesseract.image_to_string(img)))
                        
                        for code in set(page_codes): 
                            # ONLY map the page if we haven't seen this ID yet
                            if code not in id_to_page_map:
                                id_to_page_map[code] = {"page": pdf_reader.pages[i], "original_idx": i + 1}
                            else:
                                pdf_duplicates_skipped += 1

                    if pdf_duplicates_skipped > 0:
                        st.toast(f"Skipped {pdf_duplicates_skipped} duplicate page(s) in the uploaded PDF!", icon="ℹ️")

                    results_dataset = []
                    matched_count = 0
                    new_page_counter = 1
                    expected_set = set(target_ids)

                    # Phase 1: Process items in the exact order of the Target Sequence
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
                            mis_table = tid # ID exists in TABLE but is missing from the uploaded PDF
                            
                        results_dataset.append({
                            "Original pdf page": orig_page,
                            "CONVERTED pdf page": conv_page,
                            "MISMATCH from pdf": mis_pdf,
                            "MISMATCH from TABLE": mis_table
                        })

                    # Phase 2: Identify extra items found in the PDF that were NOT in the target sequence
                    for tid, data in id_to_page_map.items():
                        if tid not in expected_set:
                            results_dataset.append({
                                "Original pdf page": data["original_idx"],
                                "CONVERTED pdf page": "N/A",
                                "MISMATCH from pdf": tid,
                                "MISMATCH from TABLE": ""
                            })

                    # Render Output DataFrame
                    if results_dataset:
                        st.dataframe(pd.DataFrame(results_dataset), use_container_width=True, hide_index=True)

                    # Provide PDF Generation & Download
                    if matched_count > 0:
                        out_io = io.BytesIO()
                        pdf_writer.write(out_io)
                        log_action(user, "PDF_SEQUENCED", f"Matched {matched_count} pages.")
                        st.success(f"✅ Created PDF with {matched_count} sorted pages!")
                        
                        st.download_button(
                            label="📥 Download CONVERTED PDF", 
                            data=out_io.getvalue(), 
                            file_name="sorted_labels.pdf", 
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
            # Preference recording is local only in db.py
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
            # Aliases are part of memory table via "alias:" prefix
            enqueue_action("memory_save", {"key": f"alias:{alias_src.lower().strip()}", "value": alias_dst})
            st.success("Alias saved and queued.")
            log_action(user, "Alias Saved", f"{alias_src} -> {alias_dst}")
            st.rerun()
            
    st.write("### Recent System Preferences")
    st.dataframe(get_recent_preferences(), use_container_width=True, hide_index=True)
    st.markdown("</div>", unsafe_allow_html=True)

# --- TABS CONTENT: ADMIN PANEL (🔐 Role Restricted) ---
if role == "Admin" and tab_admin:
    with tab_admin:
        st.markdown('<div class="glass">', unsafe_allow_html=True)
        st.subheader("Admin Control Panel")
        
        adm_opt = st.radio("Admin Tool", ["👤 User Management & Logs", "📱 SIM Database Manager"], horizontal=True)
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
                # Ordering by created_at desc, matching Audit Log requirement
                logs_df = pd.read_sql_query("SELECT created_at, user, action_type, ref_id, payload FROM action_logs ORDER BY created_at DESC LIMIT 100", conn)
            st.dataframe(logs_df, use_container_width=True, hide_index=True)
            
        elif adm_opt == "📱 SIM Database Manager":
            # ------------------------------------------------------------------
            # INTEGRATION POINT: ALL SIM.PY FUNCTIONS (ADMIN ONLY)
            # ------------------------------------------------------------------
            st.subheader("📱 Samsung IMEI Database Manager (Integrated from sim.py)")
            
            # Initialize integrated SIM DB state
            if st.session_state.df_sim_db is None:
                st.session_state.df_sim_db = load_sim_db()
            
            # Replicating sim.py sidebar structure within the admin main panel
            sim_tools_col, sim_conv_col = st.columns([1, 2])
            
            with sim_tools_col:
                st.markdown("### 🛠️ SIM Database Tools")
                
                # Search Functionality (from sim.py)
                search_query = st.text_input("🔍 Search Model or TAC (8 digits)", help="Enter Model Name or TAC Prefix.")
                display_sim_df = st.session_state.df_sim_db
                
                if search_query:
                    display_sim_df = display_sim_df[
                        display_sim_df['Model_Series'].str.contains(search_query, case=False, na=False) | 
                        display_sim_df['TAC_Prefix'].str.contains(search_query, na=False)
                    ]

                st.write(f"Showing {len(display_sim_df)} entries")
                
                # The Data Editor - Replicated from sim.py Image structure
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
                    # Merge logic from sim.py
                    if search_query:
                        # Update specific rows in session state
                        # Note: This simple update assumes indices match, may need robust implementation
                        # for highly filtered/sorted data. Adhering to sim.py logic strictly.
                        st.session_state.df_sim_db.update(edited_sim_df)
                    else:
                        st.session_state.df_sim_db = edited_sim_df
                        
                    # Save to integrated CSV (defined in db.py)
                    save_sim_db(st.session_state.df_sim_db)
                    st.success("SIM Database file updated!")
                    log_action(user, "SIM DB Saved", f"{len(st.session_state.df_sim_db)} entries")

            with sim_conv_col:
                st.markdown("### 📱 IMEI Converter Tools (sim.py)")
                # Main Interface from sim.py
                # Create mapping for the converter logic based on current DB
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
                    # Process manual calibration if provided (sim.py logic)
                    if cal_input:
                        for line in cal_input.strip().split('\n'):
                            imeis = re.findall(r'\b\d{15}\b', line)
                            if len(imeis) >= 2:
                                active_sim_map[imeis[0][:8]] = int(imeis[1][:14]) - int(imeis[0][:14])

                    target_imeis = re.findall(r'\b\d{15}\b', batch_input)
                    sim_results = []
                    
                    # Ensure regex import for findall used in sim.py logic (already imported in app.py)
                    
                    for i1 in target_imeis:
                        tac = i1[:8]
                        # Get offset from DB or use Default row if available, else 8 (sim.py logic)
                        default_sim_val = sim_db_map.get('0', 8) 
                        sim_offset = active_sim_map.get(tac, default_sim_val)
                        
                        # Identify Model Name from DB
                        model_info = st.session_state.df_sim_db[st.session_state.df_sim_db['TAC_Prefix'] == tac]
                        model_sim_name = model_info['Model_Series'].values[0] if not model_info.empty else "Unknown TAC"
                        
                        base14 = i1[:14]
                        # Integrated Luhn calculation utility
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
                        st.write("#### Integrated Results (sim.py)")
                        st.dataframe(pd.DataFrame(sim_results), use_container_width=True, hide_index=True)
                        log_action(user, "SIM IMEI Converted", f"Processed: {len(sim_results)}")
            # End of sim.py integration

        st.markdown("</div>", unsafe_allow_html=True)
