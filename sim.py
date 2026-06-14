import streamlit as st
import pandas as pd
import re
import os

# --- CORE LOGIC ---
def calculate_luhn(base14):
    digits = [int(d) for d in str(base14)]
    for i in range(len(digits) - 1, -1, -2):
        doubled = digits[i] * 2
        digits[i] = doubled if doubled <= 9 else (doubled // 10) + (doubled % 10)
    return str((10 - (sum(digits) % 10)) % 10)

def load_db():
    csv_path = "samsung_offsets.csv"
    if os.path.exists(csv_path):
        # Force TAC_Prefix as string to keep leading zeros
        return pd.read_csv(csv_path, dtype={'TAC_Prefix': str})
    # Default structure matching your image
    return pd.DataFrame(columns=['TAC_Prefix', 'Model_Series', 'Expected_Offset', 'Type'])

# --- APP CONFIG ---
st.set_page_config(page_title="Samsung IMEI Pro", layout="wide")
st.title("📱 Samsung IMEI Database Manager")

if 'df_db' not in st.session_state:
    st.session_state.df_db = load_db()

# --- SIDEBAR: DATABASE EDITOR ---
with st.sidebar:
    st.header("🛠️ Database Tools")
    
    # Search Functionality
    search_query = st.text_input("🔍 Search Model or TAC")
    display_df = st.session_state.df_db
    if search_query:
        display_df = display_df[
            display_df['Model_Series'].str.contains(search_query, case=False, na=False) | 
            display_df['TAC_Prefix'].str.contains(search_query, na=False)
        ]

    st.write(f"Showing {len(display_df)} entries")
    
    # The Editor - Matches your Excel structure
    edited_df = st.data_editor(
        display_df, 
        num_rows="dynamic", 
        use_container_width=True,
        column_config={
            "TAC_Prefix": st.column_config.TextColumn("TAC Prefix (8 digits)"),
            "Expected_Offset": st.column_config.NumberColumn("Offset", format="%d")
        }
    )
    
    if st.button("💾 Save All Changes to CSV"):
        # If searching, we merge edits back to the main session state before saving
        if search_query:
            st.session_state.df_db.update(edited_df)
        else:
            st.session_state.df_db = edited_df
            
        st.session_state.df_db.to_csv("samsung_offsets.csv", index=False)
        st.success("Database file updated!")

# --- MAIN INTERFACE ---
# Create mapping for the converter logic
db_map = dict(zip(st.session_state.df_db['TAC_Prefix'], st.session_state.df_db['Expected_Offset']))

col1, col2 = st.columns(2)
with col1:
    st.subheader("1. Calibration")
    cal_input = st.text_area("Paste samples (IMEI 1 | IMEI 2):", height=150)
with col2:
    st.subheader("2. Targets")
    batch_input = st.text_area("Paste IMEI 1 list:", height=150)

if batch_input:
    active_map = db_map.copy()
    # Process manual calibration if provided
    if cal_input:
        for line in cal_input.strip().split('\n'):
            imeis = re.findall(r'\b\d{15}\b', line)
            if len(imeis) >= 2:
                active_map[imeis[0][:8]] = int(imeis[1][:14]) - int(imeis[0][:14])

    target_imeis = re.findall(r'\b\d{15}\b', batch_input)
    results = []
    
    for i1 in target_imeis:
        tac = i1[:8]
        # Get offset from DB or use Default row if available, else 8
        default_val = db_map.get('0', 8) 
        offset = active_map.get(tac, default_val)
        
        # Identify Model Name from DB
        model_info = st.session_state.df_db[st.session_state.df_db['TAC_Prefix'] == tac]
        model_name = model_info['Model_Series'].values[0] if not model_info.empty else "Unknown"
        
        base14 = i1[:14]
        new_base = str(int(base14) + int(offset)).zfill(14)
        i2 = new_base + calculate_luhn(new_base)
        
        results.append({
            "Model": model_name,
            "IMEI 1": i1,
            "IMEI 2": i2,
            "TAC": tac,
            "Applied Offset": f"{int(offset):+}"
        })

    if results:
        st.divider()
        st.dataframe(pd.DataFrame(results), use_container_width=True)
