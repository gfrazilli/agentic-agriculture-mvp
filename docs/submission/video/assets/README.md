# Video vector assets

All files use a 1920×1080 view box. The three cards have an opaque off-white background. Files named
`callout_*.svg` are transparent full-frame overlays; render them with alpha and place them above the
screen capture but below the subtitle layer.

The title and closing cards link to the project's canonical PNG logo through a relative filesystem
path. Keep the SVG inside this directory when rendering so the original logo resolves correctly.

Suggested layer order:

1. screen capture or opaque card;
2. callout overlay;
3. privacy masks;
4. burned-in subtitles;
5. final fade.

Callouts occupy the upper-left safe area and may be scaled only uniformly. Never stretch the logo,
cards, or overlays.

