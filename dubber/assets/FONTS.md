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
| FZPoppins-Bold.ttf | Fz Poppins | Poppins Việt hóa bởi Vntype/FontZin (fontzin.com/poppins-viet-hoa), static |

Static instances because libass may fake-bold the default (Regular) instance of a
variable font. Passed to FFmpeg's `ass` filter via `fontsdir=`.

# Interface font

The app's own UI uses **Fz Poppins** (OFL 1.1, `font-licenses/OFL-fzpoppins.txt`). The original
Google Fonts Poppins lacks 88 Vietnamese letters (ả, ạ, ơ, ư…), so the Vietnamized build is used.
Bold is shared with the subtitles (above); Regular / Medium / SemiBold are in `../ui-fonts/`,
outside `fonts/` so libass never loads them.
