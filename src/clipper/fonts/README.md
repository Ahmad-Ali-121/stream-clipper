# Caption fonts

Twenty free fonts for the burned-in captions, so a caption font does not have to be installed
on the PC. FFmpeg's `ass` filter (libass) finds them when this folder is given as `fontsdir`.

- Every `.ttf` here comes unchanged from <https://github.com/google/fonts> (the `source` path
  in `fonts.json` says where). They are static files, one heavy weight per family.
- `fonts.json` lists them in display order. `family` is the name to write as `Fontname` in an
  ASS style so libass picks that exact file; `weight` is the font's own weight
  (use `Bold: -1` in the style when it is 600 or more, `0` otherwise).
- The licence text of each font is beside it, as `<font file name without .ttf>.LICENSE.txt`.

| # | Font | File | Kind | Licence |
|---|------|------|------|---------|
| 1 | Be Vietnam Pro ExtraBold | `BeVietnamPro-ExtraBold.ttf` | sans | SIL Open Font License 1.1 |
| 2 | Poppins ExtraBold | `Poppins-ExtraBold.ttf` | sans | SIL Open Font License 1.1 |
| 3 | Bebas Neue | `BebasNeue-Regular.ttf` | condensed | SIL Open Font License 1.1 |
| 4 | Anton | `Anton-Regular.ttf` | condensed | SIL Open Font License 1.1 |
| 5 | Fjalla One | `FjallaOne-Regular.ttf` | condensed | SIL Open Font License 1.1 |
| 6 | Bangers | `Bangers-Regular.ttf` | comic | SIL Open Font License 1.1 |
| 7 | Luckiest Guy | `LuckiestGuy-Regular.ttf` | comic | Apache License 2.0 |
| 8 | Fira Sans Black | `FiraSans-Black.ttf` | sans | SIL Open Font License 1.1 |
| 9 | Kanit ExtraBold | `Kanit-ExtraBold.ttf` | sans | SIL Open Font License 1.1 |
| 10 | Lato Black | `Lato-Black.ttf` | sans | SIL Open Font License 1.1 |
| 11 | Prompt ExtraBold | `Prompt-ExtraBold.ttf` | sans | SIL Open Font License 1.1 |
| 12 | Paytone One | `PaytoneOne-Regular.ttf` | rounded | SIL Open Font License 1.1 |
| 13 | Archivo Black | `ArchivoBlack-Regular.ttf` | sans | SIL Open Font License 1.1 |
| 14 | Barlow Condensed ExtraBold | `BarlowCondensed-ExtraBold.ttf` | condensed | SIL Open Font License 1.1 |
| 15 | Lilita One | `LilitaOne-Regular.ttf` | rounded | SIL Open Font License 1.1 |
| 16 | Permanent Marker | `PermanentMarker-Regular.ttf` | marker | Apache License 2.0 |
| 17 | Russo One | `RussoOne-Regular.ttf` | display | SIL Open Font License 1.1 |
| 18 | Chewy | `Chewy-Regular.ttf` | comic | Apache License 2.0 |
| 19 | Titan One | `TitanOne-Regular.ttf` | rounded | SIL Open Font License 1.1 |
| 20 | Passion One Bold | `PassionOne-Bold.ttf` | display | SIL Open Font License 1.1 |

Both licences allow the fonts to be bundled and shipped with a program, as long as the licence
text stays with them. Do not rename or edit the font files: several of the OFL fonts have a
reserved font name, which a modified copy may not keep.
