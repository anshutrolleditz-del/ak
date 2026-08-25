"""
attendance_core.py
===================

Shared logic for the facial recognition attendance system. Both the
command-line tool (attendance_system.py) and the desktop app (gui_app.py)
import this module -- it's the single source of truth for recognition,
rosters, and file layout, so the two front ends never drift out of sync.

Nothing in this module opens a window or calls cv2.imshow/waitKey; it only
processes frames handed to it and reports results. Display and the capture
loop are the caller's responsibility.
"""

import csv
import os
from datetime import datetime

import cv2
import face_recognition
import numpy as np

VALID_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.bmp')


class AttendanceSystem:
    """Loads known faces for one or more classes and matches them against frames.

    Each class keeps its own roster and its own attendance CSV, even when
    several classes are loaded together in the same run.
    """

    VALID_EXTENSIONS = VALID_EXTENSIONS

    def __init__(self, class_names, students_base='students', attendance_base='attendance',
                 on_present=None):
        self.class_names = list(class_names)
        self.students_base = students_base
        self.attendance_base = attendance_base
        self.on_present = on_present  # optional callback(name, class_name, time_str)

        self.known_encodings = []   # flat pool across every loaded class
        self.known_names = []       # parallel to known_encodings
        self.known_classes = []     # parallel to known_encodings -- which class each face belongs to

        self.rosters = {}              # class_name -> {student_name: {"status":.., "time":..}}
        self.class_student_order = {}  # class_name -> [student_name, ...] in scan order
        self.attendance_paths = {}     # class_name -> csv path

        self.load_log = []  # human-readable lines describing what happened while loading

        for class_name in self.class_names:
            self._load_class(class_name)

        self._prepare_attendance_files()

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------
    def _log(self, message):
        self.load_log.append(message)

    def _load_class(self, class_name):
        """Scan one class's folder and encode every photo found in it."""
        students_folder = os.path.join(self.students_base, class_name)
        os.makedirs(students_folder, exist_ok=True)

        self.rosters[class_name] = {}
        self.class_student_order[class_name] = []

        self._log(f"Scanning '{students_folder}/' for student photos ({class_name})...")

        filenames = sorted(os.listdir(students_folder))
        image_files = [f for f in filenames if f.lower().endswith(self.VALID_EXTENSIONS)]

        if not image_files:
            self._log(f"  No photos found in '{students_folder}/'.")
            return

        for filename in image_files:
            path = os.path.join(students_folder, filename)
            name = os.path.splitext(filename)[0].replace('_', ' ').replace('-', ' ').title()

            try:
                image = face_recognition.load_image_file(path)
                encodings = face_recognition.face_encodings(image)
            except Exception as exc:
                self._log(f"  Skipped {filename}: could not read image ({exc})")
                continue

            if not encodings:
                self._log(f"  Skipped {filename}: no face detected in photo")
                continue
            if len(encodings) > 1:
                self._log(f"  Warning: {filename} has multiple faces, using the first one")

            self.known_encodings.append(encodings[0])
            self.known_names.append(name)
            self.known_classes.append(class_name)
            self.rosters[class_name][name] = {"status": "Absent", "time": ""}
            self.class_student_order[class_name].append(name)
            self._log(f"  Registered: {name}")

        self._log(f"Loaded {len(self.class_student_order[class_name])} student(s) for {class_name}")

    def _prepare_attendance_files(self):
        """Create (or resume) today's CSV roster for every loaded class."""
        today = datetime.now().strftime('%Y-%m-%d')

        for class_name in self.class_names:
            folder = os.path.join(self.attendance_base, class_name)
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, f'attendance_{today}.csv')
            self.attendance_paths[class_name] = path

            if os.path.exists(path):
                with open(path, newline='') as f:
                    for row in csv.DictReader(f):
                        name = row.get('Name')
                        roster = self.rosters[class_name]
                        if name in roster and row.get('Status') == 'Present':
                            roster[name] = {"status": "Present", "time": row.get('Time', '')}

            self._write_roster_csv(class_name)

    # ------------------------------------------------------------------
    # Attendance
    # ------------------------------------------------------------------
    def _write_roster_csv(self, class_name):
        with open(self.attendance_paths[class_name], 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['Name', 'Status', 'Time'])
            for name in self.class_student_order[class_name]:
                info = self.rosters[class_name][name]
                writer.writerow([name, info['status'], info['time']])

    def _mark_attendance(self, name, class_name):
        roster = self.rosters[class_name]
        if roster[name]["status"] == "Present":
            return
        now = datetime.now()
        time_str = now.strftime('%H:%M:%S')
        roster[name] = {"status": "Present", "time": time_str}
        self._write_roster_csv(class_name)
        if self.on_present:
            self.on_present(name, class_name, time_str)

    # ------------------------------------------------------------------
    # Recognition
    # ------------------------------------------------------------------
    def process_frame(self, frame, tolerance=0.6, downscale=0.25, draw=True):
        """Run recognition on one BGR frame. Marks attendance as a side effect.

        Returns (frame, labels). If draw=True, boxes/labels/status are drawn
        onto frame in place before it's returned.
        """
        locations, labels = self._recognize_faces(frame, tolerance, downscale)
        if draw:
            self._draw_overlays(frame, locations, labels, downscale)
        return frame, labels

    def draw_status_only(self, frame, downscale=0.25):
        """Draw just the running status line, with no new face boxes.

        Useful for frames where recognition was skipped for performance
        (see process_every_n_frames in the CLI/GUI capture loops) but the
        display still needs to be refreshed every frame.
        """
        self._draw_overlays(frame, [], [], downscale)
        return frame

    def _recognize_faces(self, frame, tolerance, downscale):
        small = cv2.resize(frame, (0, 0), fx=downscale, fy=downscale)
        rgb_small = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)

        locations = face_recognition.face_locations(rgb_small)
        encodings = face_recognition.face_encodings(rgb_small, locations)

        labels = []
        for encoding in encodings:
            distances = face_recognition.face_distance(self.known_encodings, encoding)
            best_index = int(np.argmin(distances))
            label = "Unknown"

            if distances[best_index] <= tolerance:
                name = self.known_names[best_index]
                class_name = self.known_classes[best_index]
                self._mark_attendance(name, class_name)
                label = f"{name} ({class_name})" if len(self.class_names) > 1 else name

            labels.append(label)

        return locations, labels

    def _draw_overlays(self, frame, face_locations, face_labels, downscale):
        scale = int(1 / downscale)
        for (top, right, bottom, left), label in zip(face_locations, face_labels):
            top, right, bottom, left = top * scale, right * scale, bottom * scale, left * scale
            color = (0, 200, 0) if label != "Unknown" else (0, 0, 220)

            cv2.rectangle(frame, (left, top), (right, bottom), color, 2)
            cv2.rectangle(frame, (left, bottom - 28), (right, bottom), color, cv2.FILLED)
            cv2.putText(frame, label, (left + 6, bottom - 8),
                        cv2.FONT_HERSHEY_DUPLEX, 0.55, (255, 255, 255), 1)

        header = ", ".join(self.class_names) if len(self.class_names) <= 3 else f"{len(self.class_names)} classes"
        status = f"{header} - Present: {self.present_count()}/{self.total_count()}"
        cv2.putText(frame, status, (10, 25), cv2.FONT_HERSHEY_DUPLEX, 0.6, (255, 255, 0), 1)

    # ------------------------------------------------------------------
    # Status / reporting
    # ------------------------------------------------------------------
    def present_count(self):
        return sum(1 for r in self.rosters.values() for v in r.values() if v["status"] == "Present")

    def total_count(self):
        return sum(len(r) for r in self.rosters.values())

    def summary_lines(self):
        """Plain-text Present/Absent breakdown per class, as a list of lines."""
        lines = []
        for class_name in self.class_names:
            roster = self.rosters[class_name]
            order = self.class_student_order[class_name]
            present = [n for n in order if roster[n]["status"] == "Present"]
            absent = [n for n in order if roster[n]["status"] == "Absent"]
            lines.append(f"{class_name}:")
            lines.append(f"  Present ({len(present)}): {', '.join(present) if present else '-'}")
            lines.append(f"  Absent  ({len(absent)}): {', '.join(absent) if absent else '-'}")
            lines.append(f"  Saved to {self.attendance_paths[class_name]}")
        return lines


# ------------------------------------------------------------------
# Class/section discovery and management
# ------------------------------------------------------------------
def discover_classes(students_base):
    """Every subfolder of students_base is treated as one class/section."""
    if not os.path.isdir(students_base):
        return []
    return sorted(
        d for d in os.listdir(students_base)
        if os.path.isdir(os.path.join(students_base, d)) and not d.startswith('.')
    )


def no_classes_message(students_base):
    loose_images = []
    if os.path.isdir(students_base):
        loose_images = [f for f in os.listdir(students_base)
                         if f.lower().endswith(VALID_EXTENSIONS)]
    if loose_images:
        return (f"Found photos directly inside '{students_base}/', but students are now "
                f"organized by class/section folder. Move them into a subfolder, e.g. "
                f"'{students_base}/Grade10-A/', and re-run.")
    return (f"No class folders found in '{students_base}/'. Create one, e.g. "
            f"'{students_base}/Grade10-A/', and add student photos to it.")


def list_students(students_base, class_name):
    """Returns [(filename, display_name), ...] for one class's photos."""
    folder = os.path.join(students_base, class_name)
    if not os.path.isdir(folder):
        return []
    results = []
    for f in sorted(os.listdir(folder)):
        if f.lower().endswith(VALID_EXTENSIONS):
            display_name = os.path.splitext(f)[0].replace('_', ' ').replace('-', ' ').title()
            results.append((f, display_name))
    return results


def list_attendance_dates(attendance_base, class_name):
    """Returns YYYY-MM-DD strings, newest first, for a class's saved attendance files."""
    folder = os.path.join(attendance_base, class_name)
    if not os.path.isdir(folder):
        return []
    dates = []
    for f in os.listdir(folder):
        if f.startswith('attendance_') and f.endswith('.csv'):
            dates.append(f[len('attendance_'):-len('.csv')])
    return sorted(dates, reverse=True)


def read_attendance_csv(attendance_base, class_name, date_str):
    """Returns a list of {'Name':.., 'Status':.., 'Time':..} dict rows."""
    path = os.path.join(attendance_base, class_name, f'attendance_{date_str}.csv')
    if not os.path.exists(path):
        return []
    with open(path, newline='') as f:
        return list(csv.DictReader(f))
