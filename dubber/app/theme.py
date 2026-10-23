"""Look of the interface: one font, a short type scale and a 4 px spacing grid.

Every font size, gap and control height in the UI comes from here, so text and
spacing stay consistent across the main window and every dialog.
"""
from __future__ import annotations

# ── Type scale (px) ───────────────────────────────────────────────────────────
FS_CAPTION = 11   # numbers and states: %, timers, card facts, group titles, badges
FS_HINT = 12      # explanatory notes under an option, secondary sentences
FS_BODY = 13      # labels, inputs, buttons, tabs, list items — the default size
FS_TITLE = 20     # the app name in the header

# ── Spacing grid (px) ─────────────────────────────────────────────────────────
SP_XS = 4         # a note under its option, rows inside a list
SP_S = 8          # widgets in a row, items in a box
SP_M = 12         # form rows, sections in a column
SP_L = 16         # window / page / dialog margins, gap between the two columns

# Inner margins of a QGroupBox (left, top, right, bottom): the top leaves room for the title
GROUP_MARGINS = (SP_M, SP_L, SP_M, SP_M)

# ── Control heights (px) ──────────────────────────────────────────────────────
CONTROL_H = 32    # inputs, combo boxes, buttons, form rows
PRIMARY_H = 36    # the big action buttons at the bottom of the main window
