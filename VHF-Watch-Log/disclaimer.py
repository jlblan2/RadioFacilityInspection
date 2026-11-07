"""Disclaimer the user must accept on first use.

require(app_name) runs before the main window is built and returns False if the
user declines. Acceptance is remembered per app and per Windows user under
%APPDATA%\\N. Tesla\\<app_name>. show(parent, app_name) shows it again.
"""
import os
import tkinter as tk
from tkinter import messagebox

VERSION = "1"


def text(app_name):
    return (
        f'{app_name} is provided "as is", without warranty of any kind, express or implied, '
        "including any warranty of accuracy, reliability or fitness for a particular purpose.\n\n"
        "N. Tesla, Inc. and the developer assume no responsibility or liability to the user of "
        "this application, or to anyone else, for its use, or for any loss, damage, injury or "
        "death arising from its use, from the information it provides, or from being unable to "
        "use it.\n\n"
        "The user is responsible for using due diligence: checking all information against "
        "official sources, charts, forecasts and equipment, and using sound judgment. The user "
        "assumes all risk of using this application."
    )


def _flag_path(app_name):
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "N. Tesla", app_name, "disclaimer_accepted.txt")


def accepted(app_name):
    try:
        with open(_flag_path(app_name), encoding="utf-8") as f:
            return f.read().strip() == VERSION
    except OSError:
        return False


def _remember(app_name):
    path = _flag_path(app_name)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(VERSION)
    except OSError:
        pass  # asked again next time


def require(app_name):
    """Show the disclaimer until accepted. Returns True to carry on, False to exit."""
    if accepted(app_name):
        return True
    result = {"ok": False}
    root = tk.Tk()
    root.title(f"{app_name} — Disclaimer")
    root.resizable(False, False)
    frame = tk.Frame(root, padx=20, pady=16)
    frame.pack(fill="both", expand=True)
    tk.Label(frame, text="Disclaimer", font=("Segoe UI", 14, "bold")).pack(anchor="w")
    tk.Label(frame, text=text(app_name), font=("Segoe UI", 10), wraplength=480,
             justify="left").pack(anchor="w", pady=(8, 16))
    buttons = tk.Frame(frame)
    buttons.pack(fill="x")

    def accept():
        _remember(app_name)
        result["ok"] = True
        root.destroy()

    tk.Button(buttons, text="Decline and exit", width=16, command=root.destroy).pack(side="right")
    tk.Button(buttons, text="I understand and accept", width=22, default="active",
              command=accept).pack(side="right", padx=(0, 8))
    root.protocol("WM_DELETE_WINDOW", root.destroy)
    root.update_idletasks()
    x = (root.winfo_screenwidth() - root.winfo_reqwidth()) // 2
    y = (root.winfo_screenheight() - root.winfo_reqheight()) // 3
    root.geometry(f"+{x}+{y}")
    root.lift()
    root.attributes("-topmost", True)
    root.after(300, lambda: root.attributes("-topmost", False))
    root.mainloop()
    return result["ok"]


def show(parent, app_name):
    messagebox.showinfo("Disclaimer", text(app_name), parent=parent)
