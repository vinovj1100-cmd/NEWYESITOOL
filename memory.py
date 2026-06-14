from db import get_memory, save_memory, record_preference, get_templates

def get_setting(key, default=""):
    row = get_memory(key)
    return row["value"] if row else default

def set_setting(key, value):
    save_memory(key, value)

def upsert_alias(source, target):
    save_memory(f"alias:{source.lower().strip()}", target)

def suggest_alias(text):
    if not text:
        return None
    row = get_memory(f"alias:{text.lower().strip()}")
    return row["value"] if row else None

def suggest_template(text):
    if not text:
        return None
    templates = get_templates()
    if templates.empty:
        return None

    best = None
    score = -1
    text_words = set(text.lower().split())

    for _, r in templates.iterrows():
        raw = str(r["raw_title"]).lower()
        raw_words = set(raw.split())
        s = len(raw_words & text_words)
        if s > score:
            score = s
            best = r["standard_title"]

    return best if score > 0 else None

def get_all_aliases():
    df = get_memory()
    if hasattr(df, "empty") and not df.empty:
        return df[df["key"].str.startswith("alias:")]
    return df

def record_preference_value(key, value):
    record_preference(key, value)