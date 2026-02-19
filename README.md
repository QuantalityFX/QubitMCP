# QubitMCP

QubitMCP is a desktop Python app (Qt/PySide6) with supporting node and librarian components.

## Requirements

1. Windows 10/11
2. Git
3. Python 3.x with `py` launcher available on PATH

## Quick Start (Recommended)

1. Clone the repository and open a terminal in the repo root.
2. Run:
```bat
setup.bat
```
3. Start the app (choose one):
With launcher batch:
```bat
Run_QubitMCP.bat
```
Or run the script directly:
```bat
.\.venv\Scripts\python.exe echograph_app.py
```

`setup.bat` prepares:
- `.venv` for the main app
- `nodes/librarian/.venv` for librarian dependencies

## Manual Run (Without Launcher .bat)

After `setup.bat` completes:

```bat
.\.venv\Scripts\python.exe echograph_app.py
```

## Troubleshooting

1. `setup.bat` fails with Python not found:
   Install Python 3 and ensure `py` or `python` is on PATH.
2. Pip install errors:
   Check internet/proxy/firewall settings, then rerun `setup.bat`.
3. Double-click launch gives no window:
   Run from terminal with `.\.venv\Scripts\python.exe echograph_app.py` to see errors.
