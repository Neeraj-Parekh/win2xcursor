# Contributing

Small project, simple rules.

- Open an issue first if you're planning anything bigger than a typo fix, so we don't duplicate work.
- Keep dependencies at zero beyond Pillow (+ tkinter from the OS for the GUI, tkinterdnd2 only as an optional drag-and-drop extra). If your change needs a new hard pip requirement, it probably doesn't belong here.
- Add or update a test in `tests/` when you touch `cursor_converter.py`. Run `python3 -m unittest discover -s tests -v` before pushing.
- Test a real conversion, not just unit tests: convert one `.zip` pack to a temp dir and check the theme loads (`--out /tmp/x` keeps your icon dirs clean).
- Match the existing code style: plain stdlib, no cleverness, comments only where the format demands it (ANI chunks, DIB quirks).
