# Committed design direction

Archetype **cli** · style family **calm-precise** · density **compact** ·
type personality **mono / mono** · surface **terminal dark**.

This is a terminal product, so the design source of truth is a token block in
`seismolog.py` (right under the imports) rather than a stylesheet. Everything the
tool prints is generated from those constants — there is no second palette and no
screen that invents its own spacing.

## Tokens

| Token | Value | Where it lives | Used for |
| --- | --- | --- | --- |
| accent (OKLCH) | `oklch(0.58 0.12 156)` | `ACCENT_OKLCH` | documentation of record; CSS contexts |
| accent (sRGB) | `#2e8f5b` | `ACCENT_HEX` | non-CSS surfaces: charts, canvas, native theming |
| accent (ANSI) | `\x1b[38;2;46;143;91m` | `ANSI_ACCENT` | the terminal itself — a non-CSS context cannot parse `oklch()` |
| reset / dim | `\x1b[0m`, `\x1b[2m` | `ANSI_RESET`, `ANSI_DIM` | returning to default; secondary metadata lines |
| rule glyph | `─` (`-` in ASCII terminals) | `GLYPHS.rule` | box-drawing separators under every table and block |
| bar / track | `█` / `░` (`#` / `.`) | `GLYPHS.bar`, `GLYPHS.track` | magnitude bars |
| type family | system monospace (`mono / mono`) | the terminal | all output; no proportional text anywhere |
| layout budget | `TERM_WIDTH = 80` | `seismolog.py` | every table is laid out to fit 80 columns unwrapped |
| spacing scale | one gap: two spaces between columns | `GAP` | identical column rhythm in `list`, `chart` and `distance` |

## How the direction is applied

* **Aligned columns.** `list`, `chart` and `distance` all render through one
  `render_table` / `attribute_block` pair: the same two-space gutter, the same rule
  character, right-aligned numerics, left-aligned text.
* **One accent, one meaning.** The accent marks primary status only — a new event id
  (`Recorded event #3`) and the boundary name in `distance`. Everything else stays
  in the default foreground or dim.
* **No colour-only signal.** Bars carry their own block glyphs, states carry words
  (`No events recorded yet.`, `collapsed — one event`), so the tool is fully readable
  on a monochrome terminal and in a piped/logged session. Colour is emitted only when
  stdout is a real TTY and `NO_COLOR` is unset; `--no-color` forces it off.
* **No chrome.** No boxes with rounded corners, no banners, no progress spinners: a
  title line, a rule, aligned rows, and a dim one-line footnote (`store: …`,
  `bundled boundary table: 15 named boundaries`).
* **Help-first output.** A bare `seismolog` prints the usage screen and exits 0;
  `record` and `distance` print their usage line and a sample command before the
  error whenever a required flag is missing.
* **Same shape on every surface.** The single-vent record echo, the multi-row list,
  the chart and the distance result all use one corner/border/elevation system:
  header, `─` rule, aligned body, optional dim footnote.
* **Honest states.** Empty stores print an invitation plus the exact command to try,
  corrupt stores print the path and the line number, and validation failures name the
  offending value — never a bare "invalid input".
* **Contrast.** Foreground text is the terminal's own (light on a dark profile at
  ≥4.5:1); the accent `#2e8f5b` is used for short labels and single values, never for
  body text, so it never carries contrast-critical content on its own.
