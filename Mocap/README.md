# QubitMCP GEM-X Mocap Integration

This folder keeps the GEM-X integration reproducible without vendoring the full NVIDIA GEM-X source tree into QubitMCP.

Run by double-clicking or from Command Prompt:

```bat
Mocap\setup.bat
```

The setup script initializes `Mocap/GEM-X` from the registered submodule when that entry exists. If this checkout does not yet have a submodule entry, it clones `https://github.com/NVlabs/GEM-X.git` into the same folder as a fallback. It then pins GEM-X to `953b3871c15a38acec6b7cccec18ed6019b4512b`, applies the QubitMCP patch, copies the overlay files, and runs the GEM-X environment setup.

Rerunning setup keeps the existing `Mocap/GEM-X/.venv`, but refreshes the managed GEM-X source by restoring the pinned baseline, reapplying the current QubitMCP patch, and copying the current overlay files.

The patch and overlay live under:

```text
Mocap/gemx/patches/
Mocap/gemx/overlay/
```

Retargeting support is installed by default. Pass any GEM-X setup options through this wrapper, for example:

```bat
Mocap\setup.bat --skip-retarget
Mocap\setup.bat --cuda cu130 --skip-smoke-test
```

Generated environments, checkpoints, caches, and outputs should stay out of QubitMCP source control.

If `Mocap/GEM-X` is cloned by the setup fallback instead of initialized as a registered submodule, it is ignored by `Mocap/.gitignore` so QubitMCP stays clean after setup.

Launch the UI with no console window by double-clicking:

```text
Mocap/GEM-X/run_qt_ui.vbs
```

Use `Mocap/GEM-X/run_qt_ui_debug.bat` only when you need a console for startup troubleshooting.
