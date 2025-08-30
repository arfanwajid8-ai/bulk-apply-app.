"""
bulk_apply_desktop_app.py
A simple desktop GUI wrapper around the bulk-apply Selenium script.

Features:
- Tkinter GUI for entering applicant info (name, email, phone), selecting resume and cover letter template
- Add / remove job entries (name, url, selector type, selector value for common fields)
- Start/Stop apply process (runs Selenium in a background thread)
- Live logging panel
- Save / Load JSON config

IMPORTANT: Use responsibly and only on sites you have permission to automate. This app does NOT bypass CAPTCHAs and does not automate logins requiring 2FA. Read the in-app warning before running.

Dependencies:
- Python 3.9+
- pip install selenium webdriver-manager

To build a single-file executable (Windows) with PyInstaller:
  pip install pyinstaller
  pyinstaller --onefile --add-data "path/to/chromedriver;." bulk_apply_desktop_app.py

"""

import json
import threading
import time
import queue
import logging
from pathlib import Path
from tkinter import (
    Tk, Frame, Label, Entry, Button, Text, Scrollbar, END, LEFT, RIGHT, BOTH, X, Y,
    StringVar, BooleanVar, Checkbutton, filedialog, Listbox, SINGLE, Toplevel, simpledialog
)

# Selenium imports
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.common.exceptions import TimeoutException, ElementNotInteractableException

# Basic logging to console as well
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

BY_MAP = {
    "css": By.CSS_SELECTOR,
    "xpath": By.XPATH,
    "id": By.ID,
    "name": By.NAME,
    "class": By.CLASS_NAME,
    "tag": By.TAG_NAME,
    "link": By.LINK_TEXT,
}

class BulkApplyApp:
    def __init__(self, root):
        self.root = root
        root.title("Bulk Apply - Desktop App")
        root.geometry("900x600")

        self.log_queue = queue.Queue()
        self.running = False
        self.thread = None

        # Applicant info
        self.full_name_var = StringVar()
        self.email_var = StringVar()
        self.phone_var = StringVar()
        self.resume_path = None
        self.cover_template = None
        self.headless_var = BooleanVar(value=False)

        # Jobs list (in-memory)
        self.jobs = []  # each job: dict with name,url,fields,submit

        self._build_ui()
        self._periodic_log_flush()

    def _build_ui(self):
        top_frame = Frame(self.root)
        top_frame.pack(fill=X, padx=8, pady=6)

        Label(top_frame, text="Full name").grid(row=0, column=0)
        Entry(top_frame, textvariable=self.full_name_var, width=25).grid(row=0, column=1, padx=4)
        Label(top_frame, text="Email").grid(row=0, column=2)
        Entry(top_frame, textvariable=self.email_var, width=25).grid(row=0, column=3, padx=4)
        Label(top_frame, text="Phone").grid(row=0, column=4)
        Entry(top_frame, textvariable=self.phone_var, width=15).grid(row=0, column=5, padx=4)

        btn_frame = Frame(self.root)
        btn_frame.pack(fill=X, padx=8, pady=6)

        Button(btn_frame, text="Choose Resume", command=self.choose_resume).pack(side=LEFT, padx=4)
        Button(btn_frame, text="Choose Cover Template", command=self.choose_cover).pack(side=LEFT, padx=4)
        Checkbutton(btn_frame, text="Headless (no browser window)", variable=self.headless_var).pack(side=LEFT, padx=12)

        config_btn_frame = Frame(self.root)
        config_btn_frame.pack(fill=X, padx=8, pady=6)
        Button(config_btn_frame, text="Add Job", command=self.add_job_dialog).pack(side=LEFT)
        Button(config_btn_frame, text="Edit Job", command=self.edit_job_dialog).pack(side=LEFT, padx=6)
        Button(config_btn_frame, text="Remove Job", command=self.remove_job).pack(side=LEFT)
        Button(config_btn_frame, text="Load Config", command=self.load_config).pack(side=LEFT, padx=6)
        Button(config_btn_frame, text="Save Config", command=self.save_config).pack(side=LEFT)

        # Jobs listbox
        jobs_frame = Frame(self.root)
        jobs_frame.pack(fill=X, padx=8, pady=6)
        Label(jobs_frame, text="Jobs to apply").pack(anchor='w')
        self.jobs_listbox = Listbox(jobs_frame, selectmode=SINGLE, height=6)
        self.jobs_listbox.pack(fill=X)

        # Controls
        control_frame = Frame(self.root)
        control_frame.pack(fill=X, padx=8, pady=6)
        Button(control_frame, text="Start Applying", command=self.start_apply).pack(side=LEFT)
        Button(control_frame, text="Stop", command=self.stop_apply).pack(side=LEFT, padx=6)
        Button(control_frame, text="Clear Log", command=self.clear_log).pack(side=LEFT, padx=6)

        # Log panel
        log_frame = Frame(self.root)
        log_frame.pack(fill=BOTH, expand=True, padx=8, pady=6)
        self.log_text = Text(log_frame)
        self.log_text.pack(side=LEFT, fill=BOTH, expand=True)
        scrollbar = Scrollbar(log_frame, command=self.log_text.yview)
        scrollbar.pack(side=RIGHT, fill=Y)
        self.log_text['yscrollcommand'] = scrollbar.set

        # Footer warning
        footer = Frame(self.root)
        footer.pack(fill=X, padx=8, pady=4)
        Label(footer, text="Warning: Automating site interactions may violate TOS. Use only on sites you own or have permission to automate.", fg="red").pack()

    # ---------------- UI helper methods ----------------
    def choose_resume(self):
        p = filedialog.askopenfilename(title="Select Resume (PDF/DOCX)")
        if p:
            self.resume_path = p
            self.log(f"Selected resume: {p}")

    def choose_cover(self):
        p = filedialog.askopenfilename(title="Select Cover Template (txt)", filetypes=[('Text','*.txt'),('All','*.*')])
        if p:
            self.cover_template = p
            self.log(f"Selected cover template: {p}")

    def add_job_dialog(self):
        dialog = JobDialog(self.root)
        self.root.wait_window(dialog.top)
        if dialog.result:
            self.jobs.append(dialog.result)
            self.jobs_listbox.insert(END, dialog.result.get('name','(unnamed)'))
            self.log(f"Added job: {dialog.result.get('name')}")

    def edit_job_dialog(self):
        sel = self.jobs_listbox.curselection()
        if not sel:
            self.log("No job selected to edit")
            return
        idx = sel[0]
        job = self.jobs[idx]
        dialog = JobDialog(self.root, initial=job)
        self.root.wait_window(dialog.top)
        if dialog.result:
            self.jobs[idx] = dialog.result
            self.jobs_listbox.delete(idx)
            self.jobs_listbox.insert(idx, dialog.result.get('name','(unnamed)'))
            self.log(f"Edited job: {dialog.result.get('name')}")

    def remove_job(self):
        sel = self.jobs_listbox.curselection()
        if not sel:
            self.log("No job selected to remove")
            return
        idx = sel[0]
        name = self.jobs[idx].get('name')
        del self.jobs[idx]
        self.jobs_listbox.delete(idx)
        self.log(f"Removed job: {name}")

    def load_config(self):
        p = filedialog.askopenfilename(title="Load config JSON", filetypes=[('JSON','*.json')])
        if not p:
            return
        try:
            cfg = json.loads(Path(p).read_text(encoding='utf-8'))
            applicant = cfg.get('applicant', {})
            self.full_name_var.set(applicant.get('full_name',''))
            self.email_var.set(applicant.get('email',''))
            self.phone_var.set(applicant.get('phone',''))
            self.resume_path = applicant.get('resume_path')
            self.cover_template = applicant.get('cover_letter_template')
            self.jobs = cfg.get('jobs', [])
            self.jobs_listbox.delete(0, END)
            for j in self.jobs:
                self.jobs_listbox.insert(END, j.get('name','(unnamed)'))
            self.log(f"Loaded config: {p}")
        except Exception as e:
            self.log(f"Failed to load config: {e}")

    def save_config(self):
        p = filedialog.asksaveasfilename(title="Save config JSON", defaultextension='.json', filetypes=[('JSON','*.json')])
        if not p:
            return
        cfg = {
            'applicant': {
                'full_name': self.full_name_var.get(),
                'email': self.email_var.get(),
                'phone': self.phone_var.get(),
                'resume_path': self.resume_path,
                'cover_letter_template': self.cover_template,
            },
            'jobs': self.jobs
        }
        Path(p).write_text(json.dumps(cfg, indent=2), encoding='utf-8')
        self.log(f"Saved config to: {p}")

    # ---------------- Logging ----------------
    def log(self, msg):
        logging.info(msg)
        self.log_queue.put(msg)

    def _periodic_log_flush(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self.log_text.insert(END, msg + '\n')
                self.log_text.see(END)
        except queue.Empty:
            pass
        self.root.after(200, self._periodic_log_flush)

    def clear_log(self):
        self.log_text.delete('1.0', END)

    # ---------------- Apply logic (runs in thread) ----------------
    def start_apply(self):
        if self.running:
            self.log('Already running')
            return
        if not self.jobs:
            self.log('No jobs configured')
            return
        self.running = True
        self.thread = threading.Thread(target=self._apply_worker, daemon=True)
        self.thread.start()
        self.log('Started applying thread')

    def stop_apply(self):
        if not self.running:
            self.log('Not running')
            return
        self.running = False
        self.log('Stopping... (will stop after current job)')

    def _start_driver(self, headless=False):
        options = webdriver.ChromeOptions()
        if headless:
            options.add_argument('--headless=new')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--window-size=1200,900')
        driver = webdriver.Chrome(service=ChromeService(ChromeDriverManager().install()), options=options)
        return driver

    def _wait_for(self, driver, by, value, timeout=12):
        wait = WebDriverWait(driver, timeout)
        return wait.until(EC.presence_of_element_located((by, value)))

    def _safe_fill(self, driver, by_name, selector, text):
        by = BY_MAP.get(by_name)
        if not by:
            self.log(f'Unknown selector type: {by_name}')
            return False
        try:
            el = self._wait_for(driver, by, selector, timeout=10)
            driver.execute_script("arguments[0].scrollIntoView({block:'center'})", el)
            try:
                el.clear()
            except Exception:
                pass
            el.send_keys(text)
            time.sleep(0.4)
            return True
        except TimeoutException:
            self.log(f'Element not found (timeout) {selector}')
            return False
        except ElementNotInteractableException:
            self.log(f'Element not interactable: {selector}')
            return False

    def _safe_upload(self, driver, by_name, selector, file_path):
        by = BY_MAP.get(by_name)
        try:
            el = self._wait_for(driver, by, selector, timeout=10)
            el.send_keys(str(file_path))
            time.sleep(0.6)
            return True
        except Exception as e:
            self.log(f'File upload failed for {selector}: {e}')
            return False

    def _safe_click(self, driver, by_name, selector):
        by = BY_MAP.get(by_name)
        try:
            el = self._wait_for(driver, by, selector, timeout=10)
            driver.execute_script("arguments[0].scrollIntoView({block:'center'})", el)
            el.click()
            time.sleep(1)
            return True
        except Exception as e:
            self.log(f'Click failed for {selector}: {e}')
            return False

    def _prepare_cover(self, template_path, job):
        if not template_path:
            return ''
        try:
            t = Path(template_path).read_text(encoding='utf-8')
            context = {
                'full_name': self.full_name_var.get(),
                'email': self.email_var.get(),
                'phone': self.phone_var.get(),
                'job_name': job.get('name',''),
                'company': job.get('company','')
            }
            return t.format(**context)
        except Exception as e:
            self.log(f'Cover prepare failed: {e}')
            return ''

    def _apply_worker(self):
        driver = None
        try:
            driver = self._start_driver(headless=self.headless_var.get())
            for job in list(self.jobs):
                if not self.running:
                    break
                self.log(f"Applying to: {job.get('name','(no name)')} -> {job.get('url')}")
                try:
                    driver.get(job.get('url'))
                    time.sleep(1.2)
                    page = driver.page_source.lower()
                    if 'recaptcha' in page or 'captcha' in page:
                        self.log('CAPTCHA detected on page; skipping job')
                        continue

                    fields = job.get('fields', {})
                    for logical_name, sel in fields.items():
                        by = sel.get('by','css')
                        selector = sel.get('value')
                        if logical_name == 'full_name':
                            self._safe_fill(driver, by, selector, self.full_name_var.get())
                        elif logical_name == 'email':
                            self._safe_fill(driver, by, selector, self.email_var.get())
                        elif logical_name == 'phone':
                            self._safe_fill(driver, by, selector, self.phone_var.get())
                        elif logical_name == 'cover_letter':
                            cl = self._prepare_cover(self.cover_template, job)
                            self._safe_fill(driver, by, selector, cl)
                        elif logical_name == 'resume':
                            if self.resume_path:
                                self._safe_upload(driver, by, selector, self.resume_path)
                            else:
                                self.log('No resume selected; skipping upload')
                        else:
                            # custom override
                            override = sel.get('value_override')
                            if override is not None:
                                self._safe_fill(driver, by, selector, override)

                    submit = job.get('submit')
                    if submit:
                        ok = self._safe_click(driver, submit.get('by','css'), submit.get('value'))
                        if ok:
                            time.sleep(2)
                            s = driver.page_source.lower()
                            if any(w in s for w in ["thank you", "application received", "we have received", "thanks for applying"]):
                                self.log(f"Likely success for {job.get('name')}")
                            else:
                                self.log(f"Submitted (unknown result) for {job.get('name')}")
                        else:
                            self.log(f"Submit failed for {job.get('name')}")
                    else:
                        self.log(f"No submit selector configured for {job.get('name')}")

                except Exception as e:
                    self.log(f"Error applying to job {job.get('name')}: {e}")
                time.sleep(3)
        finally:
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass
            self.running = False
            self.log('Apply worker finished')


class JobDialog:
    def __init__(self, parent, initial=None):
        top = self.top = Toplevel(parent)
        top.title('Add / Edit Job')
        self.result = None

        Label(top, text='Job name').grid(row=0, column=0)
        self.name_e = Entry(top, width=60)
        self.name_e.grid(row=0, column=1)

        Label(top, text='Company (optional)').grid(row=1, column=0)
        self.company_e = Entry(top, width=60)
        self.company_e.grid(row=1, column=1)

        Label(top, text='URL').grid(row=2, column=0)
        self.url_e = Entry(top, width=60)
        self.url_e.grid(row=2, column=1)

        Label(top, text='Field selector type (css/xpath/id/name)').grid(row=3, column=0)
        self.sel_type_e = Entry(top, width=20)
        self.sel_type_e.grid(row=3, column=1, sticky='w')

        Label(top, text='Full name selector').grid(row=4, column=0)
        self.name_sel = Entry(top, width=60)
        self.name_sel.grid(row=4, column=1)

        Label(top, text='Email selector').grid(row=5, column=0)
        self.email_sel = Entry(top, width=60)
        self.email_sel.grid(row=5, column=1)

        Label(top, text='Phone selector').grid(row=6, column=0)
        self.phone_sel = Entry(top, width=60)
        self.phone_sel.grid(row=6, column=1)

        Label(top, text='Cover letter selector').grid(row=7, column=0)
        self.cover_sel = Entry(top, width=60)
        self.cover_sel.grid(row=7, column=1)

        Label(top, text='Resume (file input) selector').grid(row=8, column=0)
        self.resume_sel = Entry(top, width=60)
        self.resume_sel.grid(row=8, column=1)

        Label(top, text='Submit button selector').grid(row=9, column=0)
        self.submit_sel = Entry(top, width=60)
        self.submit_sel.grid(row=9, column=1)

        Button(top, text='OK', command=self.on_ok).grid(row=10, column=0)
        Button(top, text='Cancel', command=self.on_cancel).grid(row=10, column=1)

        if initial:
            self.name_e.insert(0, initial.get('name',''))
            self.company_e.insert(0, initial.get('company',''))
            self.url_e.insert(0, initial.get('url',''))
            self.sel_type_e.insert(0, 'css')
            fields = initial.get('fields', {})
            self.name_sel.insert(0, fields.get('full_name',{}).get('value',''))
            self.email_sel.insert(0, fields.get('email',{}).get('value',''))
            self.phone_sel.insert(0, fields.get('phone',{}).get('value',''))
            self.cover_sel.insert(0, fields.get('cover_letter',{}).get('value',''))
            self.resume_sel.insert(0, fields.get('resume',{}).get('value',''))
            self.submit_sel.insert(0, initial.get('submit',{}).get('value',''))

    def on_ok(self):
        name = self.name_e.get().strip()
        url = self.url_e.get().strip()
        sel_type = self.sel_type_e.get().strip() or 'css'
        if not url:
            simpledialog.messagebox.showerror('Error','URL required')
            return
        job = {
            'name': name or url,
            'company': self.company_e.get().strip(),
            'url': url,
            'fields': {
                'full_name': {'by': sel_type, 'value': self.name_sel.get().strip()},
                'email': {'by': sel_type, 'value': self.email_sel.get().strip()},
                'phone': {'by': sel_type, 'value': self.phone_sel.get().strip()},
                'cover_letter': {'by': sel_type, 'value': self.cover_sel.get().strip()},
                'resume': {'by': sel_type, 'value': self.resume_sel.get().strip()},
            },
            'submit': {'by': sel_type, 'value': self.submit_sel.get().strip()}
        }
        self.result = job
        self.top.destroy()

    def on_cancel(self):
        self.top.destroy()


if __name__ == '__main__':
    root = Tk()
    app = BulkApplyApp(root)
    root.mainloop()
