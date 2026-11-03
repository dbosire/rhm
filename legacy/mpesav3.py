import re
import csv
import tkinter as tk
from tkinter import ttk, messagebox
import os

def parse_mpesa_message(message):
    # --- Patterns ---
    receipt_pattern_start = r"^([A-Z0-9]{8,12})\s+Confirmed"  # Receipt at start
    receipt_pattern_ref = r"Ref[.:]?\s*([A-Z0-9]+)"          # Receipt after 'Ref'
    amount_pattern1 = r"Ksh\s*([\d,]+\.\d{2})"               # Ksh500.00 or Ksh 500.00
    amount_pattern2 = r"([\d,]+\.\d{2})\s*KES"               # 500.00 KES
    paybill_pattern = r"(?:Pay[\s-]?Bill|Paybill\s*AC)\s*(\d+)?"  # Pay Bill 522522 OR Paybill AC
    account_pattern = r"account\s+(\S+)"
    date_time_pattern = r"on\s*([\d/]+\s*\w*\s*\d*)\s*at\s*([\d:]+\s*[APM]{0,2})"

    # --- Extract receipt ---
    receipt = None
    match_start = re.search(receipt_pattern_start, message, re.IGNORECASE)
    if match_start:
        receipt = match_start.group(1)
    else:
        match_ref = re.search(receipt_pattern_ref, message, re.IGNORECASE)
        if match_ref:
            receipt = match_ref.group(1)

    # --- Extract amount ---
    amount = 0.0
    match_amt1 = re.search(amount_pattern1, message, re.IGNORECASE)
    match_amt2 = re.search(amount_pattern2, message, re.IGNORECASE)
    if match_amt1:
        amount = float(match_amt1.group(1).replace(",", ""))
    elif match_amt2:
        amount = float(match_amt2.group(1).replace(",", ""))

    # --- Extract paybill & account ---
    paybill = ""
    account = ""
    match_paybill = re.search(paybill_pattern, message, re.IGNORECASE)
    if match_paybill:
        paybill = match_paybill.group(1) if match_paybill.group(1) else "KCB"  # fallback for "KCB Paybill AC"

    match_account = re.search(account_pattern, message, re.IGNORECASE)
    if match_account:
        account = match_account.group(1)

    # --- Extract date & time ---
    date = ""
    time = ""
    match_dt = re.search(date_time_pattern, message, re.IGNORECASE)
    if match_dt:
        date = match_dt.group(1).replace(" ", "")
        time = match_dt.group(2)

    return [receipt or "", amount, paybill, account, date, time]


def extract_message():
    message = text_box.get("1.0", tk.END).strip()
    if not message:
        messagebox.showwarning("Warning", "Please paste an M-Pesa message first.")
        return

    # Clean hidden Unicode marks
    message = message.replace('\u200e', '')

    global last_data
    last_data = parse_mpesa_message(message)

    for row in preview.get_children():
        preview.delete(row)

    preview.insert("", "end", values=last_data)


def save_message():
    if not last_data:
        messagebox.showwarning("Warning", "Extract data first before saving.")
        return

    # Clean hidden Unicode marks and strip whitespace
    clean_data = [str(item).replace('\u200e', '').strip() for item in last_data]

    # Write using UTF-8 encoding
    with open("mpesa.csv", mode="a", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        if file.tell() == 0:
            writer.writerow(columns)
        writer.writerow(clean_data)

    messagebox.showinfo("Success", "M-Pesa data saved to mpesa.csv")

    text_box.delete("1.0", tk.END)
    for row in preview.get_children():
        preview.delete(row)


def load_csv():
    if not os.path.exists("mpesa.csv"):
        messagebox.showwarning("Warning", "No mpesa.csv file found yet.")
        return

    global all_csv_rows
    all_csv_rows = []

    for row in csv_view.get_children():
        csv_view.delete(row)

    # Read with UTF-8 encoding
    with open("mpesa.csv", newline="", encoding="utf-8") as file:
        reader = csv.reader(file)
        headers = next(reader, None)
        for row in reader:
            csv_view.insert("", "end", values=row)
            all_csv_rows.append(row)


def delete_row():
    selected = csv_view.selection()
    if not selected:
        messagebox.showwarning("Warning", "Select a row to delete.")
        return

    confirm = messagebox.askyesno("Confirm Delete", "Are you sure you want to delete the selected row(s)?")
    if not confirm:
        return

    for row in selected:
        csv_view.delete(row)

    all_rows = [csv_view.item(row, "values") for row in csv_view.get_children()]

    with open("mpesa.csv", mode="w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(columns)
        writer.writerows(all_rows)

    messagebox.showinfo("Deleted", "Selected row(s) removed from mpesa.csv")


def live_search(event=None):
    keyword = search_entry.get().strip().lower()
    selected_col = column_filter.get()

    for row in csv_view.get_children():
        csv_view.delete(row)

    if not keyword:
        rows = all_csv_rows
    else:
        col_index = None if selected_col == "All" else columns.index(selected_col)
        rows = []
        for row in all_csv_rows:
            if col_index is None:
                if any(keyword in str(cell).lower() for cell in row):
                    rows.append(row)
            else:
                if keyword in str(row[col_index]).lower():
                    rows.append(row)

    for row in rows:
        csv_view.insert("", "end", values=row)


def reset_csv():
    if not all_csv_rows:
        return

    for row in csv_view.get_children():
        csv_view.delete(row)

    for row in all_csv_rows:
        csv_view.insert("", "end", values=row)


# --- GUI setup ---
root = tk.Tk()
root.title("M-Pesa Extractor")
root.geometry("1100x700")

label = tk.Label(root, text="Paste your M-Pesa message below:", font=("Arial", 12))
label.pack(pady=5)

text_box = tk.Text(root, height=5, width=100, wrap="word")
text_box.pack(pady=10)

button_frame = tk.Frame(root)
button_frame.pack(pady=5)

extract_button = tk.Button(button_frame, text="Extract", command=extract_message, bg="blue", fg="white", font=("Arial", 11))
extract_button.grid(row=0, column=0, padx=10)

save_button = tk.Button(button_frame, text="Save to CSV", command=save_message, bg="green", fg="white", font=("Arial", 11))
save_button.grid(row=0, column=1, padx=10)

view_button = tk.Button(button_frame, text="View CSV", command=load_csv, bg="orange", fg="white", font=("Arial", 11))
view_button.grid(row=0, column=2, padx=10)

delete_button = tk.Button(button_frame, text="Delete Row", command=delete_row, bg="red", fg="white", font=("Arial", 11))
delete_button.grid(row=0, column=3, padx=10)

# Preview table
columns = ["m_pesa_receipt", "amount", "paybill", "account", "date", "time"]
preview_label = tk.Label(root, text="Preview Extracted Data:", font=("Arial", 11, "bold"))
preview_label.pack(pady=5)

preview = ttk.Treeview(root, columns=columns, show="headings", height=3)
for col in columns:
    preview.heading(col, text=col)
    preview.column(col, width=150, anchor="center")
preview.pack(pady=5, fill="x")

# CSV viewer
csv_label = tk.Label(root, text="Saved CSV Data:", font=("Arial", 11, "bold"))
csv_label.pack(pady=5)

search_frame = tk.Frame(root)
search_frame.pack(pady=5)

column_filter = ttk.Combobox(search_frame, values=["All"] + columns, state="readonly", width=18, font=("Arial", 11))
column_filter.set("All")
column_filter.grid(row=0, column=0, padx=5)

search_entry = tk.Entry(search_frame, width=40, font=("Arial", 11))
search_entry.grid(row=0, column=1, padx=5)
search_entry.bind("<KeyRelease>", live_search)

reset_button = tk.Button(search_frame, text="Reset", command=reset_csv, bg="gray", fg="white", font=("Arial", 11))
reset_button.grid(row=0, column=2, padx=5)

csv_view = ttk.Treeview(root, columns=columns, show="headings", height=15)
for col in columns:
    csv_view.heading(col, text=col)
    csv_view.column(col, width=160, anchor="center")
csv_view.pack(pady=5, fill="both", expand=True)

last_data = None
all_csv_rows = []

root.mainloop()
