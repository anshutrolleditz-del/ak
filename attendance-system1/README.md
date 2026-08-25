# Facial Recognition Attendance System

Two ways to use this:

- **`gui_app.py`** -- a desktop app: buttons and tabs instead of terminal
  commands. Can be packaged into a standalone `AttendanceSystem.exe`.
- **`attendance_system.py`** -- the original command-line version, still
  here as a lighter-weight fallback.

Both share the same underlying logic (`attendance_core.py`), the same
`students/` and `attendance/` folders, and the same file formats -- so you
can freely mix using one or the other.

## 1. Install dependencies

```
pip install -r requirements.txt
```

`face_recognition` depends on `dlib`, which compiles from source and needs
a C++ toolchain + CMake:

- **Windows:** install "Desktop development with C++" (Visual Studio Build
  Tools) and [CMake](https://cmake.org/download/) before running pip install.
- **macOS:** `brew install cmake`
- **Linux (Debian/Ubuntu):** `sudo apt install cmake build-essential`

If `pip install dlib` fails, install it standalone first and confirm it
succeeds before retrying `pip install -r requirements.txt`.

If face_recognition still complains `Please install face_recognition_models`
after that finishes, see **Troubleshooting** at the bottom -- it's a known
issue with a documented fix.

## 2. Add student photos, organized by class/section

Inside `students/`, each subfolder is one class or section. This project
ships with two examples -- `Grade10-A` and `Grade10-B` -- rename, delete,
or duplicate them to match your actual classes:

```
students/
    Grade10-A/
        Jane_Doe.jpg      ->  "Jane Doe"
        John-Smith.png    ->  "John Smith"
    Grade10-B/
        Alice_Wong.jpg    ->  "Alice Wong"
        Bob_Lee.jpg       ->  "Bob Lee"
```

The filename becomes the student's name automatically. You can do this by
dropping files into the folder directly, or through the GUI app's **Manage
Classes** / **Manage Students** tabs.

## 3. Run the desktop app

```
python gui_app.py
```

**In VS Code:** press **F5** and pick "Run GUI App" (this project includes
`.vscode/launch.json` with both that and the CLI as options).

Four tabs:

- **Take Attendance** -- press **Detect Cameras** to scan for available
  camera indices (works for any number of cameras: one webcam, or several
  USB cameras, or several channels off a capture card that exposes each
  as its own device). Each detected camera gets a row: enable it, then
  press **Classes...** to open a picker where you check off which
  class(es) that camera should watch -- one, several, or **All classes**
  as a single click. Press **Start All Cameras** to launch every enabled
  one at once; each camera recognizes faces against only the classes you
  assigned it and writes attendance only into those classes' files. A
  live preview grid shows every active camera at once, labeled with
  which class(es) it's watching, alongside a shared activity log and
  running Present/Total counts per camera.

  A face recognized on a camera watching several classes still gets
  attributed to its one correct class automatically -- recognition
  matches by face, and each student's photo only ever lives in one
  class's folder, so there's no ambiguity even when a camera's pool spans
  multiple classes.

  One rule: the same class can't be assigned to two different cameras at
  once (Start All will reject that combination with a clear error) --
  two cameras both writing to the same class's roster from separate
  threads isn't supported, since each camera's recognizer keeps its own
  independent in-memory copy of that roster.

  Camera capture and recognition run on a background thread per camera,
  not the main window's thread -- this is what keeps the preview smooth
  even while recognition is actively running, and is also what makes
  several cameras at once practical rather than something that fights
  each other for the window's attention.

  If your capture card instead outputs **one combined image** with
  several feeds tiled together (rather than separate camera indices),
  this app doesn't currently split that apart automatically -- tell me if
  that's your situation and I can add it back in.

- **Manage Classes** -- add, rename, or delete class/section folders.
  Deleting a class removes its student photos, but keeps its past
  attendance records.
- **Manage Students** -- pick a class, then add a student by name + a
  photo you browse to (it's checked for a detectable face before being
  accepted), or remove one. Selecting a student shows a photo preview.
- **Attendance Records** -- pick a class and date to browse that day's
  roster in a table, with a Present/Absent/Total summary.

## 4. Package it as a standalone .exe (optional)

This turns the app into `AttendanceSystem.exe`, runnable without Python
installed. **Run this on Windows**, from the same virtual environment
where `python gui_app.py` already works:

```
build_exe.bat
```

This installs `pyinstaller` and builds the app. It takes a few minutes --
the face recognition model data alone is roughly 100MB, so don't worry if
it seems to sit for a while.

When it finishes, your app is at `dist\AttendanceSystem\AttendanceSystem.exe`.
**Copy or share the whole `AttendanceSystem` folder**, not just the `.exe`
file -- it needs the rest of that folder alongside it to run (this is
`--onedir` packaging: faster to start than a single-file exe, at the cost
of being a folder instead of one file).

If the built exe closes instantly or silently does nothing when you
double-click it, that's a startup error with nowhere to display -- open
`build_exe.bat` in a text editor, remove `--windowed` from the command,
rebuild, then run the exe **from a Command Prompt** so you can actually
read the error. Put `--windowed` back once it's fixed.

**Note on this repo:** I can hand you the complete app and this build
script, but the actual `.exe` has to be built by running the script on
your own Windows machine -- `dlib`'s compiled component is
platform-specific, so a working Windows `.exe` can't be produced from a
Linux machine (which is what generated these files).

### Running it on other computers

Yes, that's the point of packaging it -- the other computer does **not**
need Python, pip, or any of the setup from Step 1. Just:

1. Copy the whole `dist\AttendanceSystem\` folder (not just the `.exe` --
   see note above) to the other machine, e.g. via USB drive or a shared
   network folder.
2. Double-click `AttendanceSystem.exe` inside it.

A few real things to expect, though:

- **Windows only.** Won't run on Mac, Linux, or Chromebooks -- it would
  need rebuilding (`build_exe.bat`) on that OS. Any reasonably modern
  64-bit Windows PC is fine.
- **A "Windows protected your PC" warning on first run is normal.** This
  isn't a code-signed, published app, so SmartScreen or antivirus
  software will likely flag it as unrecognized the first time it runs on
  a *new* machine. Click **More info -> Run anyway**. That's a one-time
  warning per machine, not an install step -- standard for any small
  unsigned Windows app, not a sign something's wrong.
- **Run it from a normal writable folder** (Desktop, Documents), not
  Program Files or a read-only network drive -- it needs to create and
  write to `students/` and `attendance/` folders next to the exe.
- **Needs a webcam** on that computer for the Take Attendance tab to do
  anything, obviously.
- **If it won't launch at all** (no window, no error, nothing) on a
  machine that otherwise meets the above: that machine may be missing the
  Microsoft Visual C++ Redistributable, which `dlib`'s compiled piece
  needs. Most Windows PCs already have it from other software, but it's
  free from Microsoft if not:
  https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist

## 5. The command-line version (optional, still supported)

```
python attendance_system.py                    # prompts you to pick class(es)
python attendance_system.py --class Grade10-A   # runs just that class
python attendance_system.py --all               # runs every class at once
python attendance_system.py --list-classes      # shows available classes
python attendance_system.py --class Grade10-A --camera 1 --tolerance 0.5
```

In VS Code, F5 -> "Run CLI (terminal version)".

## Output

Each class gets its own folder under `attendance/`, e.g.
`attendance/Grade10-A/attendance_YYYY-MM-DD.csv`. The file always lists
**every** student in that class, labeled by name:

```
Name,Status,Time
Jane Doe,Present,09:15:32
John Smith,Absent,
```

It updates live as people are recognized, and running the app again later
the same day resumes that file (previously-Present students stay Present)
instead of overwriting it. This holds true when running multiple classes
together too -- each class still gets its own separate roster file, even
in one combined session.

## Notes

- Use a well-lit, front-facing, single-person photo for each student --
  recognition accuracy depends heavily on the source photo quality.
- If two students have visually similar faces, lower the tolerance
  (stricter matching) to reduce false matches.
- Need several classrooms/entrances covered at once? That's what the
  Detect Cameras + Start All Cameras workflow in Take Attendance is for --
  each detected camera runs independently, on its own thread, against its
  own assigned class.
- If two students *in different classes* happen to share the same name,
  that's fine -- recognition matches by face, not name text, and each
  class keeps its own independent roster entry.

## Troubleshooting

**`Please install face_recognition_models` even after installing it:**
this is a known bug in that package on newer Python versions (3.12+),
where a `pkg_resources` lookup silently fails. Try, in order:

1. `pip install --upgrade setuptools`, then run again.
2. If that doesn't fix it, install Python 3.10 alongside your current
   version, rebuild your virtual environment with it specifically
   (`py -3.10 -m venv venv`), and reinstall dependencies into that venv.
   This has been the reliable fix when step 1 alone isn't enough.

**`ModuleNotFoundError: No module named 'cv2'` (or similar) despite
installing requirements:** almost always an interpreter mismatch -- pip
installed into a different Python than the one running the script. Check
both point to the same place:

```
python -c "import sys; print(sys.executable)"
```

...and compare that to the interpreter VS Code has selected (`Ctrl+Shift+P`
-> "Python: Select Interpreter"). Using a dedicated virtual environment
(`python -m venv venv`) avoids this class of problem entirely, since
there's only one Python involved once it's activated.

**The GUI window opens but the camera preview stays black / "Camera
error":** something else may already be using the webcam (close other
apps like Zoom/Teams/other camera software), or the camera index is
wrong -- try the other small numbers (0, 1, 2) in the Camera index field.

**`build_exe.bat` fails partway through:** confirm `python gui_app.py`
runs correctly first, in the exact same terminal/venv you're about to run
the build script from. Packaging can't succeed on an app that doesn't run
on its own yet.
