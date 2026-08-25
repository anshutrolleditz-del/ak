#!/usr/bin/env python3
"""
Facial Recognition Attendance System -- desktop app
======================================================

A Tkinter GUI over attendance_core.py: take attendance from one or many
live cameras at once, manage classes and students, and browse past
attendance -- all from windows and buttons instead of terminal commands.

Camera capture and face recognition run on background threads (one per
active camera), not the GUI's main thread. This is what keeps the preview
smooth: recognition is CPU-heavy and would otherwise briefly freeze the
whole window, not just the video, every time it runs. The main thread's
job is now just picking up whatever frame each worker thread most
recently produced and painting it -- cheap, so it stays responsive.

Run directly with Python:
    python gui_app.py

Or build it into a standalone Windows .exe -- see build_exe.bat and
README.md for that step.
"""

import os
import queue
import shutil
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog, filedialog

import cv2
import face_recognition
from PIL import Image, ImageTk

import attendance_core as core

DEFAULT_CAMERA_SCAN_RANGE = 6  # how many indices "Detect Cameras" probes by default


class CameraWorker:
    """Owns one camera + an AttendanceSystem (one or more classes), running
    entirely on its own background thread.

    The GUI never touches cv2/face_recognition directly for this camera --
    it only reads get_latest_frame() (thread-safe) and drains the shared
    event_queue for log messages. This separation is what makes multiple
    simultaneous cameras -- and a smooth preview generally -- possible.
    """

    def __init__(self, cam_index, class_names, students_dir, attendance_dir,
                 tolerance, process_every_n_frames, event_queue):
        self.cam_index = cam_index
        self.class_names = list(class_names)
        self.tolerance = tolerance
        self.process_every_n_frames = max(1, process_every_n_frames)
        self.event_queue = event_queue

        self.system = core.AttendanceSystem(
            self.class_names, students_dir, attendance_dir,
            on_present=self._on_present,
        )
        for line in self.system.load_log:
            self.event_queue.put(f"[Cam {cam_index}] {line}")

        self.cap = None
        self._running = False
        self._thread = None
        self._frame_lock = threading.Lock()
        self._latest_frame = None  # RGB numpy array, ready for display
        self._frame_counter = 0

    def _on_present(self, name, class_name, time_str):
        self.event_queue.put(f"[Cam {self.cam_index}] [{time_str}] {name} marked Present ({class_name})")

    def start(self):
        self.cap = cv2.VideoCapture(self.cam_index)
        if not self.cap.isOpened():
            self.event_queue.put(f"Camera {self.cam_index}: could not open.")
            self.cap = None
            return False
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        return True

    def _run_loop(self):
        while self._running:
            ok, frame = self.cap.read()
            if not ok:
                self.event_queue.put(f"[Cam {self.cam_index}] Camera feed lost.")
                break

            self._frame_counter += 1
            if self._frame_counter % self.process_every_n_frames == 0:
                self.system.process_frame(frame, tolerance=self.tolerance)
            else:
                self.system.draw_status_only(frame)

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            with self._frame_lock:
                self._latest_frame = rgb

            time.sleep(0.005)  # yield -- avoid pegging a CPU core at 100%

        self._running = False

    def get_latest_frame(self):
        with self._frame_lock:
            return self._latest_frame

    def present_count(self):
        return self.system.present_count()

    def total_count(self):
        return self.system.total_count()

    def stop(self):
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        if self.cap is not None:
            self.cap.release()
            self.cap = None
        self.event_queue.put(f"[Cam {self.cam_index}] summary:")
        for line in self.system.summary_lines():
            self.event_queue.put(line)


class AttendanceApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Facial Recognition Attendance System")
        self.geometry("1100x720")
        self.minsize(950, 620)

        self.students_dir = "students"
        self.attendance_dir = "attendance"
        os.makedirs(self.students_dir, exist_ok=True)
        os.makedirs(self.attendance_dir, exist_ok=True)

        # Camera-tab runtime state
        self.detecting_cameras = False
        self.cameras_running = False
        self.camera_row_vars = {}     # detected cam index -> {"enabled": BooleanVar, "class": StringVar}
        self.camera_workers = {}      # active cam index -> CameraWorker
        self.preview_labels = {}      # active cam index -> ttk.Label
        self._camera_imgtks = {}      # active cam index -> PhotoImage (keep references!)
        self._thumb_size = (640, 480)
        self._preview_after_id = None
        self.event_queue = queue.Queue()
        self._detection_result = None
        self._detection_done = False

        # Student-tab state
        self.student_files = []  # [(filename, display_name), ...] for the currently shown class
        self._preview_imgtk = None

        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self._build_ui()
        self.refresh_all_class_lists()

    # ==================================================================
    # UI construction
    # ==================================================================
    def _build_ui(self):
        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True)

        self.attendance_tab = ttk.Frame(notebook)
        self.classes_tab = ttk.Frame(notebook)
        self.students_tab = ttk.Frame(notebook)
        self.records_tab = ttk.Frame(notebook)

        notebook.add(self.attendance_tab, text="Take Attendance")
        notebook.add(self.classes_tab, text="Manage Classes")
        notebook.add(self.students_tab, text="Manage Students")
        notebook.add(self.records_tab, text="Attendance Records")

        self._build_attendance_tab()
        self._build_classes_tab()
        self._build_students_tab()
        self._build_records_tab()

    # ------------------------------------------------------------------
    # Tab 1: Take Attendance (one or many cameras, one click to start all)
    # ------------------------------------------------------------------
    def _build_attendance_tab(self):
        root = self.attendance_tab

        left = ttk.Frame(root, padding=10)
        left.pack(side="left", fill="y")

        scan_frame = ttk.Frame(left)
        scan_frame.pack(fill="x", pady=(0, 4))
        ttk.Label(scan_frame, text="Scan camera indices 0 to:").pack(side="left")
        self.scan_range_var = tk.IntVar(value=DEFAULT_CAMERA_SCAN_RANGE)
        ttk.Spinbox(scan_frame, from_=1, to=15, textvariable=self.scan_range_var, width=4).pack(side="left", padx=4)

        self.detect_btn = ttk.Button(left, text="Detect Cameras", command=self.detect_cameras)
        self.detect_btn.pack(fill="x", pady=(2, 10))

        ttk.Label(left, text="Detected cameras -- enable and assign a class:",
                  font=("", 9, "bold")).pack(anchor="w")
        self.camera_rows_frame = ttk.Frame(left)
        self.camera_rows_frame.pack(fill="x", pady=(2, 10))
        ttk.Label(self.camera_rows_frame, text="(press Detect Cameras to scan)",
                  foreground="gray").pack(anchor="w")

        ttk.Label(left, text="Match tolerance (lower = stricter):").pack(anchor="w")
        self.tolerance_var = tk.DoubleVar(value=0.6)
        ttk.Scale(left, from_=0.3, to=0.8, variable=self.tolerance_var, orient="horizontal").pack(fill="x", pady=(0, 6))

        ttk.Label(left, text="Process every N frames (higher = lighter load):").pack(anchor="w")
        self.skip_var = tk.IntVar(value=3)
        ttk.Spinbox(left, from_=1, to=15, textvariable=self.skip_var, width=5).pack(anchor="w", pady=(0, 10))

        self.start_btn = ttk.Button(left, text="Start All Cameras", command=self.start_all_cameras)
        self.start_btn.pack(fill="x", pady=(6, 2))
        self.stop_btn = ttk.Button(left, text="Stop All Cameras", command=self.stop_all_cameras, state="disabled")
        self.stop_btn.pack(fill="x", pady=2)

        self.status_var = tk.StringVar(value="")
        ttk.Label(left, textvariable=self.status_var, justify="left", font=("", 10, "bold")).pack(pady=(12, 4), anchor="w")

        ttk.Label(left, text="Activity log:").pack(anchor="w")
        log_frame = ttk.Frame(left)
        log_frame.pack(fill="both", expand=True)
        self.log_text = tk.Text(log_frame, width=34, height=14, state="disabled", wrap="word")
        log_scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

        right = ttk.Frame(root, padding=10)
        right.pack(side="left", fill="both", expand=True)
        self.preview_container = ttk.Frame(right)
        self.preview_container.pack(fill="both", expand=True)
        ttk.Label(self.preview_container, text="Camera previews appear here",
                  foreground="gray").pack(expand=True)

    def _log(self, message):
        self.log_text.configure(state="normal")
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state="disabled")

    # -- Detection (runs the actual probing on a background thread too,
    #    since opening several nonexistent camera indices can be slow) --
    def detect_cameras(self):
        if self.detecting_cameras or self.cameras_running:
            return
        self.detecting_cameras = True
        self._detection_done = False
        self.detect_btn.configure(state="disabled", text="Detecting...")
        max_index = self.scan_range_var.get()
        threading.Thread(target=self._detect_cameras_worker, args=(max_index,), daemon=True).start()
        self.after(200, self._poll_detection)

    def _detect_cameras_worker(self, max_index):
        found = []
        for i in range(max_index):
            cap = cv2.VideoCapture(i)
            if cap.isOpened():
                ok, frame = cap.read()
                if ok and frame is not None:
                    found.append(i)
            cap.release()
        self._detection_result = found
        self._detection_done = True

    def _poll_detection(self):
        if not self._detection_done:
            self.after(200, self._poll_detection)
            return
        self.detecting_cameras = False
        self.detect_btn.configure(state="normal", text="Detect Cameras")
        self._populate_camera_rows(self._detection_result or [])

    def _populate_camera_rows(self, indices):
        for widget in self.camera_rows_frame.winfo_children():
            widget.destroy()
        self.camera_row_vars = {}

        if not indices:
            ttk.Label(self.camera_rows_frame, text="No cameras found in that range.",
                      foreground="gray").pack(anchor="w")
            self._log("Detect Cameras: none found.")
            return

        for idx in indices:
            row = ttk.Frame(self.camera_rows_frame)
            row.pack(fill="x", pady=2)

            enabled_var = tk.BooleanVar(value=True)
            ttk.Checkbutton(row, variable=enabled_var).pack(side="left")
            ttk.Label(row, text=f"Camera {idx}:", width=9).pack(side="left")

            summary_var = tk.StringVar(value="(no classes selected)")
            ttk.Label(row, textvariable=summary_var, width=24, foreground="gray").pack(side="left")

            self.camera_row_vars[idx] = {
                "enabled": enabled_var,
                "classes": [],       # plain list of class names this camera watches
                "summary_var": summary_var,
            }

            ttk.Button(row, text="Classes...", width=10,
                       command=lambda i=idx: self._open_class_selector(i)).pack(side="left", padx=(4, 0))

        self._log(f"Detect Cameras: found {len(indices)} -> {indices}")

    def _update_camera_row_summary(self, idx):
        row = self.camera_row_vars[idx]
        selected = row["classes"]
        all_classes = core.discover_classes(self.students_dir)
        if not selected:
            row["summary_var"].set("(no classes selected)")
        elif all_classes and set(selected) == set(all_classes):
            row["summary_var"].set(f"All classes ({len(selected)})")
        else:
            row["summary_var"].set(f"{len(selected)} selected: {', '.join(selected)}")

    def _open_class_selector(self, idx):
        """Modal dialog letting one camera watch several classes at once, or all of them."""
        classes = core.discover_classes(self.students_dir)
        current = set(self.camera_row_vars[idx]["classes"])

        if not classes:
            messagebox.showinfo("No classes yet", "Add a class first in the Manage Classes tab.")
            return

        dialog = tk.Toplevel(self)
        dialog.title(f"Camera {idx} -- classes to watch")
        dialog.geometry("320x420")
        dialog.transient(self)
        dialog.grab_set()

        all_var = tk.BooleanVar(value=bool(classes) and current == set(classes))
        class_vars = {c: tk.BooleanVar(value=(c in current)) for c in classes}
        checkbuttons = []

        ttk.Checkbutton(dialog, text="All classes", variable=all_var,
                         command=lambda: _on_all_toggle()).pack(anchor="w", padx=12, pady=(12, 6))
        ttk.Separator(dialog).pack(fill="x", padx=12, pady=4)

        list_outer = ttk.Frame(dialog)
        list_outer.pack(fill="both", expand=True, padx=12, pady=(4, 0))
        canvas = tk.Canvas(list_outer, highlightthickness=0)
        scrollbar = ttk.Scrollbar(list_outer, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        for c in classes:
            cb = ttk.Checkbutton(inner, text=c, variable=class_vars[c])
            cb.pack(anchor="w", pady=1, fill="x")
            checkbuttons.append(cb)

        def _on_all_toggle():
            state = "disabled" if all_var.get() else "normal"
            for cb in checkbuttons:
                cb.configure(state=state)
            if all_var.get():
                for v in class_vars.values():
                    v.set(True)

        _on_all_toggle()  # apply initial disabled state if "All classes" started checked

        def on_ok():
            selected = list(classes) if all_var.get() else [c for c, v in class_vars.items() if v.get()]
            self.camera_row_vars[idx]["classes"] = selected
            self._update_camera_row_summary(idx)
            dialog.destroy()

        btns = ttk.Frame(dialog)
        btns.pack(fill="x", padx=12, pady=12)
        ttk.Button(btns, text="OK", command=on_ok).pack(side="right", padx=(4, 0))
        ttk.Button(btns, text="Cancel", command=dialog.destroy).pack(side="right")

    def _refresh_camera_row_class_options(self):
        """Drop any selected classes that got renamed/deleted elsewhere, and refresh summaries."""
        if not self.camera_row_vars:
            return
        valid = set(core.discover_classes(self.students_dir))
        for idx, row in self.camera_row_vars.items():
            row["classes"] = [c for c in row["classes"] if c in valid]
            self._update_camera_row_summary(idx)

    # -- Start / stop all selected cameras --
    def start_all_cameras(self):
        if self.cameras_running:
            return
        if not self.camera_row_vars:
            messagebox.showinfo("No cameras detected", "Press 'Detect Cameras' first.")
            return

        selections = {}  # cam index -> list of class_names
        for idx, row in self.camera_row_vars.items():
            if row["enabled"].get() and row["classes"]:
                selections[idx] = list(row["classes"])

        if not selections:
            messagebox.showwarning("Nothing to start",
                                    "Enable at least one camera and assign it at least one class "
                                    "(use the 'Classes...' button).")
            return

        # A class can only be watched by one active camera at a time -- check across
        # every camera's full class list, not just single values.
        claimed_by = {}  # class_name -> cam_index that claimed it first
        for idx, classes in selections.items():
            for cls in classes:
                if cls in claimed_by:
                    messagebox.showerror(
                        "Overlapping class assignment",
                        f"'{cls}' is assigned to both Camera {claimed_by[cls]} and Camera {idx}.\n"
                        f"Each class can only be watched by one camera at a time -- "
                        f"two cameras writing to the same roster at once isn't supported."
                    )
                    return
                claimed_by[cls] = idx

        self.event_queue = queue.Queue()
        self.camera_workers = {}
        self._camera_imgtks = {}
        self._build_preview_grid(selections)

        tolerance = self.tolerance_var.get()
        skip = self.skip_var.get()

        for idx, classes in selections.items():
            worker = CameraWorker(idx, classes, self.students_dir, self.attendance_dir,
                                   tolerance, skip, self.event_queue)
            if worker.start():
                self.camera_workers[idx] = worker

        self._drain_event_queue()

        if not self.camera_workers:
            messagebox.showerror("No cameras started", "None of the selected cameras could be opened.")
            return

        self.cameras_running = True
        self.detect_btn.configure(state="disabled")
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self._log(f"--- Started {len(self.camera_workers)} camera(s) ---")
        self._update_preview_loop()

    def _build_preview_grid(self, selections):
        for widget in self.preview_container.winfo_children():
            widget.destroy()
        self.preview_labels = {}

        indices = list(selections.keys())
        n = len(indices)
        cols = 1 if n <= 1 else 2
        if n <= 1:
            self._thumb_size = (640, 480)
        elif n <= 4:
            self._thumb_size = (400, 300)
        else:
            self._thumb_size = (280, 210)

        for pos, idx in enumerate(indices):
            r, c = divmod(pos, cols)
            cell = ttk.Frame(self.preview_container, padding=4)
            cell.grid(row=r, column=c, sticky="nsew")
            ttk.Label(cell, text=f"Camera {idx} -- {self._class_label(selections[idx])}",
                      font=("", 9, "bold")).pack(anchor="w")
            video_label = ttk.Label(cell, background="black")
            video_label.pack()
            self.preview_labels[idx] = video_label

    @staticmethod
    def _class_label(class_names):
        if len(class_names) == 1:
            return class_names[0]
        return f"{len(class_names)} classes"

    def _drain_event_queue(self):
        while True:
            try:
                msg = self.event_queue.get_nowait()
            except queue.Empty:
                break
            self._log(msg)

    def _update_preview_loop(self):
        if not self.cameras_running:
            return

        self._drain_event_queue()

        status_lines = []
        for idx, worker in self.camera_workers.items():
            frame = worker.get_latest_frame()
            if frame is not None:
                image = Image.fromarray(frame)
                image = image.resize(self._thumb_size)
                imgtk = ImageTk.PhotoImage(image=image)
                self._camera_imgtks[idx] = imgtk  # keep a reference!
                self.preview_labels[idx].configure(image=imgtk)
            status_lines.append(
                f"Cam {idx} ({self._class_label(worker.class_names)}): "
                f"{worker.present_count()}/{worker.total_count()}"
            )

        self.status_var.set("   |   ".join(status_lines))
        self._preview_after_id = self.after(30, self._update_preview_loop)

    def stop_all_cameras(self):
        self.cameras_running = False
        if self._preview_after_id is not None:
            try:
                self.after_cancel(self._preview_after_id)
            except Exception:
                pass
            self._preview_after_id = None

        for worker in self.camera_workers.values():
            worker.stop()

        self._drain_event_queue()
        self.camera_workers = {}

        self.detect_btn.configure(state="normal")
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self._log("--- All cameras stopped ---")
        self.refresh_records_dates()

    # ------------------------------------------------------------------
    # Tab 2: Manage Classes
    # ------------------------------------------------------------------
    def _build_classes_tab(self):
        root = self.classes_tab
        ttk.Label(root, text="Classes / Sections", font=("", 11, "bold")).pack(anchor="w", padx=12, pady=(12, 4))

        body = ttk.Frame(root)
        body.pack(fill="both", expand=True, padx=12, pady=(0, 6))
        self.classes_listbox = tk.Listbox(body, height=16)
        classes_scroll = ttk.Scrollbar(body, command=self.classes_listbox.yview)
        self.classes_listbox.configure(yscrollcommand=classes_scroll.set)
        self.classes_listbox.pack(side="left", fill="both", expand=True)
        classes_scroll.pack(side="left", fill="y")

        btns = ttk.Frame(root)
        btns.pack(fill="x", padx=12, pady=(0, 12))
        ttk.Button(btns, text="Add Class", command=self.add_class).pack(side="left", padx=(0, 4))
        ttk.Button(btns, text="Rename", command=self.rename_class).pack(side="left", padx=4)
        ttk.Button(btns, text="Delete", command=self.delete_class).pack(side="left", padx=4)
        ttk.Button(btns, text="Refresh", command=self.refresh_all_class_lists).pack(side="left", padx=4)

    def add_class(self):
        name = simpledialog.askstring("Add Class", "Class/section name (e.g. Grade10-C):", parent=self)
        if not name or not name.strip():
            return
        name = name.strip()
        path = os.path.join(self.students_dir, name)
        if os.path.exists(path):
            messagebox.showwarning("Already exists", f"'{name}' already exists.")
            return
        os.makedirs(path, exist_ok=True)
        messagebox.showinfo("Class added", f"Created '{name}'. Add student photos to it in Manage Students.")
        self.refresh_all_class_lists()

    def rename_class(self):
        sel = self.classes_listbox.curselection()
        if not sel:
            messagebox.showinfo("Select a class", "Pick a class to rename first.")
            return
        old_name = self.classes_listbox.get(sel[0])
        new_name = simpledialog.askstring("Rename Class", f"New name for '{old_name}':",
                                           initialvalue=old_name, parent=self)
        if not new_name or not new_name.strip() or new_name.strip() == old_name:
            return
        new_name = new_name.strip()
        old_path = os.path.join(self.students_dir, old_name)
        new_path = os.path.join(self.students_dir, new_name)
        if os.path.exists(new_path):
            messagebox.showwarning("Already exists", f"'{new_name}' already exists.")
            return
        os.rename(old_path, new_path)
        messagebox.showinfo("Renamed", f"'{old_name}' is now '{new_name}'.\n"
                                        f"Past attendance files stay filed under the old name.")
        self.refresh_all_class_lists()

    def delete_class(self):
        sel = self.classes_listbox.curselection()
        if not sel:
            messagebox.showinfo("Select a class", "Pick a class to delete first.")
            return
        name = self.classes_listbox.get(sel[0])
        if not messagebox.askyesno(
                "Delete class",
                f"Delete '{name}' and all its student photos?\n\n"
                f"Past attendance records under attendance/{name}/ are kept."):
            return
        shutil.rmtree(os.path.join(self.students_dir, name), ignore_errors=True)
        messagebox.showinfo("Deleted", f"'{name}' removed.")
        self.refresh_all_class_lists()

    # ------------------------------------------------------------------
    # Tab 3: Manage Students
    # ------------------------------------------------------------------
    def _build_students_tab(self):
        root = self.students_tab

        top = ttk.Frame(root, padding=(12, 12, 12, 4))
        top.pack(fill="x")
        ttk.Label(top, text="Class:").pack(side="left")
        self.student_class_var = tk.StringVar()
        self.student_class_combo = ttk.Combobox(top, textvariable=self.student_class_var,
                                                  state="readonly", width=22)
        self.student_class_combo.pack(side="left", padx=6)
        self.student_class_combo.bind("<<ComboboxSelected>>", lambda e: self.refresh_student_list())
        ttk.Button(top, text="Refresh", command=self.refresh_student_list).pack(side="left", padx=6)

        body = ttk.Frame(root, padding=12)
        body.pack(fill="both", expand=True)

        list_frame = ttk.Frame(body)
        list_frame.pack(side="left", fill="y")
        self.students_listbox = tk.Listbox(list_frame, width=28, height=18, exportselection=False)
        students_scroll = ttk.Scrollbar(list_frame, command=self.students_listbox.yview)
        self.students_listbox.configure(yscrollcommand=students_scroll.set)
        self.students_listbox.pack(side="left", fill="y")
        students_scroll.pack(side="left", fill="y")
        self.students_listbox.bind("<<ListboxSelect>>", lambda e: self._preview_student_photo())

        preview_frame = ttk.Frame(body, padding=(20, 0))
        preview_frame.pack(side="left", fill="both", expand=True)
        self.preview_label = ttk.Label(preview_frame, text="(select a student to preview their photo)",
                                        anchor="center")
        self.preview_label.pack(fill="both", expand=True)

        btns = ttk.Frame(root, padding=(12, 0, 12, 12))
        btns.pack(fill="x")
        ttk.Button(btns, text="Add Student (choose photo)...", command=self.add_student).pack(side="left", padx=(0, 4))
        ttk.Button(btns, text="Remove Selected", command=self.remove_student).pack(side="left", padx=4)

    def add_student(self):
        class_name = self.student_class_var.get()
        if not class_name:
            messagebox.showinfo("Select a class", "Pick a class first.")
            return

        name = simpledialog.askstring("Student name", "Student's full name:", parent=self)
        if not name or not name.strip():
            return
        name = name.strip()

        filepath = filedialog.askopenfilename(
            title="Choose a photo",
            filetypes=[("Images", "*.jpg *.jpeg *.png *.bmp"), ("All files", "*.*")],
        )
        if not filepath:
            return

        try:
            image = face_recognition.load_image_file(filepath)
            encodings = face_recognition.face_encodings(image)
        except Exception as exc:
            messagebox.showerror("Couldn't read photo", str(exc))
            return
        if not encodings:
            messagebox.showwarning("No face detected",
                                    "This photo doesn't have a clearly detectable face.\n"
                                    "Try a clearer, front-facing, well-lit photo.")
            return

        ext = os.path.splitext(filepath)[1].lower()
        if ext not in core.VALID_EXTENSIONS:
            ext = ".jpg"
        dest_filename = name.replace(' ', '_') + ext
        dest_path = os.path.join(self.students_dir, class_name, dest_filename)
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)

        if os.path.exists(dest_path) and not messagebox.askyesno(
                "Overwrite?", f"A student saved as '{dest_filename}' already exists in {class_name}. Replace it?"):
            return

        shutil.copy(filepath, dest_path)
        messagebox.showinfo("Student added", f"Added '{name}' to {class_name}.")
        self.refresh_student_list()

    def remove_student(self):
        class_name = self.student_class_var.get()
        sel = self.students_listbox.curselection()
        if not class_name or not sel:
            messagebox.showinfo("Select a student", "Pick a student to remove first.")
            return
        filename, display_name = self.student_files[sel[0]]
        if not messagebox.askyesno("Remove student", f"Remove '{display_name}' from {class_name}?"):
            return
        os.remove(os.path.join(self.students_dir, class_name, filename))
        self.refresh_student_list()

    def refresh_student_list(self):
        class_name = self.student_class_var.get()
        self.students_listbox.delete(0, tk.END)
        self.student_files = core.list_students(self.students_dir, class_name) if class_name else []
        for _, display_name in self.student_files:
            self.students_listbox.insert(tk.END, display_name)
        self.preview_label.configure(image="", text="(select a student to preview their photo)")

    def _preview_student_photo(self):
        class_name = self.student_class_var.get()
        sel = self.students_listbox.curselection()
        if not class_name or not sel:
            return
        filename, _ = self.student_files[sel[0]]
        path = os.path.join(self.students_dir, class_name, filename)
        try:
            image = Image.open(path)
            image.thumbnail((300, 300))
            self._preview_imgtk = ImageTk.PhotoImage(image)
            self.preview_label.configure(image=self._preview_imgtk, text="")
        except Exception:
            self.preview_label.configure(image="", text="(preview unavailable)")

    # ------------------------------------------------------------------
    # Tab 4: Attendance Records
    # ------------------------------------------------------------------
    def _build_records_tab(self):
        root = self.records_tab

        top = ttk.Frame(root, padding=(12, 12, 12, 4))
        top.pack(fill="x")

        ttk.Label(top, text="Class:").pack(side="left")
        self.records_class_var = tk.StringVar()
        self.records_class_combo = ttk.Combobox(top, textvariable=self.records_class_var,
                                                  state="readonly", width=20)
        self.records_class_combo.pack(side="left", padx=6)
        self.records_class_combo.bind("<<ComboboxSelected>>", lambda e: self.refresh_records_dates())

        ttk.Label(top, text="Date:").pack(side="left", padx=(12, 0))
        self.records_date_var = tk.StringVar()
        self.records_date_combo = ttk.Combobox(top, textvariable=self.records_date_var,
                                                 state="readonly", width=15)
        self.records_date_combo.pack(side="left", padx=6)
        self.records_date_combo.bind("<<ComboboxSelected>>", lambda e: self.load_records())

        ttk.Button(top, text="Refresh", command=self.refresh_records_dates).pack(side="left", padx=6)

        table_frame = ttk.Frame(root, padding=12)
        table_frame.pack(fill="both", expand=True)
        columns = ("Name", "Status", "Time")
        self.records_tree = ttk.Treeview(table_frame, columns=columns, show="headings")
        for col in columns:
            self.records_tree.heading(col, text=col)
            self.records_tree.column(col, width=180)
        tree_scroll = ttk.Scrollbar(table_frame, command=self.records_tree.yview)
        self.records_tree.configure(yscrollcommand=tree_scroll.set)
        self.records_tree.pack(side="left", fill="both", expand=True)
        tree_scroll.pack(side="left", fill="y")

        self.records_summary_var = tk.StringVar(value="")
        ttk.Label(root, textvariable=self.records_summary_var, padding=(12, 0, 12, 12)).pack(anchor="w")

    def refresh_records_dates(self):
        class_name = self.records_class_var.get()
        self.records_date_combo['values'] = []
        if not class_name:
            return
        dates = core.list_attendance_dates(self.attendance_dir, class_name)
        self.records_date_combo['values'] = dates
        if dates:
            self.records_date_var.set(dates[0])
            self.load_records()
        else:
            self.records_date_var.set("")
            self._clear_records_table()

    def _clear_records_table(self):
        for row in self.records_tree.get_children():
            self.records_tree.delete(row)
        self.records_summary_var.set("")

    def load_records(self):
        class_name = self.records_class_var.get()
        date_str = self.records_date_var.get()
        self._clear_records_table()
        if not class_name or not date_str:
            return
        rows = core.read_attendance_csv(self.attendance_dir, class_name, date_str)
        present = absent = 0
        for row in rows:
            self.records_tree.insert('', tk.END, values=(row.get('Name', ''), row.get('Status', ''), row.get('Time', '')))
            if row.get('Status') == 'Present':
                present += 1
            else:
                absent += 1
        self.records_summary_var.set(f"Present: {present}    Absent: {absent}    Total: {present + absent}")

    # ------------------------------------------------------------------
    # Shared refresh / lifecycle
    # ------------------------------------------------------------------
    def refresh_all_class_lists(self):
        classes = core.discover_classes(self.students_dir)

        self.classes_listbox.delete(0, tk.END)
        for c in classes:
            self.classes_listbox.insert(tk.END, c)

        self.student_class_combo['values'] = classes
        if classes and self.student_class_var.get() not in classes:
            self.student_class_var.set(classes[0])
        elif not classes:
            self.student_class_var.set("")
        self.refresh_student_list()

        self.records_class_combo['values'] = classes
        if classes and self.records_class_var.get() not in classes:
            self.records_class_var.set(classes[0])
        elif not classes:
            self.records_class_var.set("")
        self.refresh_records_dates()

        self._refresh_camera_row_class_options()

    def on_close(self):
        if self.cameras_running:
            self.stop_all_cameras()
        self.destroy()


def main():
    app = AttendanceApp()
    app.mainloop()


if __name__ == "__main__":
    main()
