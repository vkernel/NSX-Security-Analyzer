# Relay · Six light palette alternatives

The selected Relay option 04 layout is retained. These six alternatives replace the green-tinted light canvas and raised surfaces with neutral whites and grays. The selected Relay dark palette remains the reference.

[Open the comparison](relay-light-palettes/index.html) · [View all six](relay-light-palettes/overview.html) · [Overview image](relay-light-palettes/previews/overview.png)

The previews use actual application pages, rendered against the existing isolated synthetic database. The application implementation and saved preferences have not been changed by this exploration.

## Shortlist

**01 Arctic** is the safest all-light partner for Relay dark: white and graphite, with very little hue in the chrome. **06 Bridge** is the most visually connected pair because the dark module rail stays consistent across modes while the canvas becomes white. Choose **02 Glacier** if you prefer a more familiar blue interaction color.

## Options

| Option | Character | Canvas | Raised surface | Accent |
|---|---|---|---|---|
| [01. Arctic](relay-light-palettes/index.html?palette=arctic) | White + graphite | `#FAFAFB` | `#F0F2F4` | `#334155` |
| [02. Glacier](relay-light-palettes/index.html?palette=glacier) | Cool white + cobalt | `#F5F7FA` | `#EEF1F6` | `#275BBC` |
| [03. Alloy](relay-light-palettes/index.html?palette=alloy) | Silver + steel blue | `#F7F8FA` | `#EDF1F4` | `#126B83` |
| [04. Paper](relay-light-palettes/index.html?palette=paper) | Warm white + navy | `#FAF9F7` | `#F1EFEB` | `#30465F` |
| [05. Clay](relay-light-palettes/index.html?palette=clay) | Chalk + copper | `#FAF8F6` | `#F2ECE8` | `#A4482D` |
| [06. Bridge](relay-light-palettes/index.html?palette=bridge) | White + dark rail | `#F7F8FA` | `#EFF2F4` | `#315A70` |

Every option uses white primary panels. Bridge uses `#10161C` for the module rail, `#B7C9D3` for its labels and `#5EE0B0` for the selected rail item and mark. Its working canvas and secondary panel stay neutral. In other versions the module rail is white and uses the selected light accent.

Success, warning and error colors retain their meaning. Green can still appear in a genuine success indicator; it is not used to tint the workspace background.

## How to compare

1. Select a palette at the top.
2. Switch between Naming review, Segmentation and Sign-in.
3. Use Beside dark mode for a paired view, or Light only for a larger preview.
4. Click an image to inspect its full-resolution screenshot.

The selection is included in the local URL and survives reload. These are image-based comparisons; the pictured application controls do not submit anything. The comparison works offline once the folder is available.

## Why these fit Relay

- The same module rail, section panel, logo geometry, icon system, typography and spacing keep the identity consistent.
- Neutral canvases relate to the graphite structure of dark mode without copying its mint accent onto large light surfaces.
- Color is concentrated in selected navigation, links and primary actions.
- The comparison uses the same data and page arrangement, so differences come from the palette.

## Validation

- 30 light-theme states rendered: six palettes across three desktop pages and naming review at 390 px and 320 px. No captured JavaScript errors or document-level horizontal overflow.
- Three dark references captured from the same application and synthetic fixture.
- All six palettes checked across the three comparison screens, plus URL restoration and narrow-screen comparison controls.
- The checked body, secondary text, link, primary button and navigation token pairs exceed 4.5:1. This is a palette check, not a full accessibility certification.
- Capture animations were disabled to record the final colors rather than an intermediate transition. Sign-in was captured without an authenticated application shell.

[Viewport report](relay-light-palettes/previews/browser-checks.json) · [Interaction report](relay-light-palettes/previews/interaction-checks.json) · [Contrast report](relay-light-palettes/previews/contrast-checks.json) · [Palette values](relay-light-palettes/palettes.json)

No backend tests were rerun because this exploration changes only standalone design artifacts. No application color token, user setting, NSX object or production database was modified.

## Attribution

Color exploration guided by [Unslop by Damir Sokolovsky (@heydamir)](https://github.com/heydamir/unslop), revision `a6f121eee5f29c2f74976b75456feae8b66afb3a`, licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Original Relay identity and selected layout retained.

## Selected direction

Bridge 06 was selected and applied to the shared application theme on 3 November 2026. Light mode uses a white/silver workspace, steel-blue actions and the dark module rail. Dark mode retains the Relay palette. The comparison above remains an exploration of all six alternatives.
