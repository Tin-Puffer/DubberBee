# Subtitle fonts

Static **Bold** instances of Google Fonts families, all with full Vietnamese coverage.
Licensed under the SIL Open Font License 1.1 (see `font-licenses/`), which permits bundling.

| File | Family | Source |
|---|---|---|
| BeVietnamPro-Bold.ttf | Be Vietnam Pro | google/fonts `ofl/bevietnampro` (static) |
| Roboto-Bold.ttf | Roboto | `ofl/roboto` variable → `fonttools varLib.instancer wght=700 wdth=100` |
| Montserrat-Bold.ttf | Montserrat | `ofl/montserrat` variable → `wght=700` |
| Nunito-Bold.ttf | Nunito | `ofl/nunito` variable → `wght=700` |
| Lexend-Bold.ttf | Lexend | `ofl/lexend` variable → `wght=700` |
| NotoSans-Bold.ttf | Noto Sans | `ofl/notosans` variable → `wght=700 wdth=100` |

Static instances because libass may fake-bold the default (Regular) instance of a
variable font. Passed to FFmpeg's `ass` filter via `fontsdir=`.
