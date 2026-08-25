#!/usr/bin/env python3
"""
Facial Recognition Attendance System -- command-line front end
================================================================

See attendance_core.py for how recognition and rosters work, and
README.md for full setup instructions. This file just wires that core
logic up to a terminal + OpenCV window.

Usage:
    python attendance_system.py                    # prompts you to pick class(es)
    python attendance_system.py --class Grade10-A   # runs just that class
    python attendance_system.py --all               # runs every class at once
    python attendance_system.py --list-classes      # shows available classes

Prefer a graphical app? See gui_app.py / README.md for the desktop version.
"""

import argparse
import os

import cv2

import attendance_core as core


def choose_classes_interactively(classes):
    """Returns a list of class names -- one, or all of them if the user picks 'all'."""
    print("Available classes/sections:")
    for i, name in enumerate(classes, start=1):
        print(f"  {i}. {name}")
    print(f"  {len(classes) + 1}. All classes (combined session)")

    while True:
        choice = input("Select a class, or 'all': ").strip()
        if choice.lower() == 'all' or choice == str(len(classes) + 1):
            return list(classes)
        if choice.isdigit() and 1 <= int(choice) <= len(classes):
            return [classes[int(choice) - 1]]
        if choice in classes:
            return [choice]
        print("Not a valid choice -- try again.")


def run_camera_loop(system, camera_index=0, tolerance=0.6, process_every_n_frames=3, downscale=0.25):
    """Blocking OpenCV display loop -- press 'q' to stop."""
    if not system.known_encodings:
        print("No known faces loaded -- add photos to the relevant class folder(s) first.")
        return

    video = cv2.VideoCapture(camera_index)
    if not video.isOpened():
        print(f"Could not open camera index {camera_index}.")
        return

    label = ", ".join(system.class_names)
    print(f"Camera started for: {label}. Press 'q' in the video window to stop.\n")

    frame_count = 0
    last_present = 0

    try:
        while True:
            ok, frame = video.read()
            if not ok:
                print("Camera feed lost.")
                break

            frame_count += 1
            if frame_count % process_every_n_frames == 0:
                frame, _ = system.process_frame(frame, tolerance=tolerance, downscale=downscale)
                if system.present_count() != last_present:
                    last_present = system.present_count()
                    print(f"[Attendance] Present: {last_present}/{system.total_count()}")
            else:
                system.draw_status_only(frame, downscale)

            window_title = (f'Attendance - {label if len(system.class_names) <= 2 else f"{len(system.class_names)} classes"} '
                             '(press q to quit)')
            cv2.imshow(window_title, frame)

            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
    finally:
        video.release()
        cv2.destroyAllWindows()
        print("\nSession ended.\n")
        for line in system.summary_lines():
            print(line)


def parse_args():
    parser = argparse.ArgumentParser(description="Facial recognition attendance system")
    parser.add_argument('--students-dir', default='students', help="Folder containing class subfolders")
    parser.add_argument('--attendance-dir', default='attendance', help="Folder for attendance CSV logs")
    parser.add_argument('--camera', type=int, default=0, help="Webcam index (default 0)")
    parser.add_argument('--tolerance', type=float, default=0.6,
                         help="Lower = stricter face match (default 0.6)")
    parser.add_argument('--class', dest='class_name', default=None,
                         help="Run a single class/section (a folder name under students-dir).")
    parser.add_argument('--all', action='store_true',
                         help="Run every class/section at once, in a single combined camera session")
    parser.add_argument('--list-classes', action='store_true',
                         help="List available class/section folders and exit")
    return parser.parse_args()


def main():
    args = parse_args()
    os.makedirs(args.students_dir, exist_ok=True)
    classes = core.discover_classes(args.students_dir)

    if args.list_classes:
        print(f"Available classes/sections in '{args.students_dir}/':" if classes
              else core.no_classes_message(args.students_dir))
        for c in classes:
            print(f"  - {c}")
        return

    if args.class_name and args.all:
        print("Use either --class or --all, not both.")
        return

    if args.all:
        if not classes:
            print(core.no_classes_message(args.students_dir))
            return
        class_names = classes

    elif args.class_name:
        if args.class_name not in classes:
            print(f"Class '{args.class_name}' not found in '{args.students_dir}/'.")
            print("Available: " + ", ".join(classes) if classes else core.no_classes_message(args.students_dir))
            return
        class_names = [args.class_name]

    else:
        if not classes:
            print(core.no_classes_message(args.students_dir))
            return
        class_names = choose_classes_interactively(classes)

    system = core.AttendanceSystem(
        class_names,
        students_base=args.students_dir,
        attendance_base=args.attendance_dir,
        on_present=lambda name, cls, t: print(f"[Attendance] {name} ({cls}) marked Present at {t}"),
    )
    for line in system.load_log:
        print(line)
    print()

    run_camera_loop(system, camera_index=args.camera, tolerance=args.tolerance)


if __name__ == "__main__":
    main()
