# Five brand and interface directions

> **Historical record.** This document describes the design or implementation at the time it was written. Current code, tests and the main documentation take precedence.

These five Unslop explorations propose new identities for NSX Security Analyzer. They are standalone, interactive design concepts using the same illustrative data. The application and published Docker image are unchanged.

Open [the interactive comparison](brand-directions/index.html) in a browser. Switch between five directions, then choose Segmentation, Naming review or Login & brand. The map, evidence drawers, VM-pair lookup and naming selection demonstrate interactions locally. No credentials are sent or stored and no application APIs are called.

See [all five side by side](brand-directions/comparison.png), or open [the comparison page](brand-directions/comparison.html).

## My recommendation

**Harbor** is the strongest overall direction: approachable daily workflows with clear navigation and restrained teal highlights. **Atlas** is the strongest alternative if the application map and segmentation become the main product experience. Ledger is especially useful for evidence-heavy approval work; Circuit suits technical operators who prefer a compact dark interface; Sentinel gives the product a more distinctive security-review identity.

| # | Direction | Identity | Main layout | Best fit |
|---|---|---|---|---|
| 1 | Harbor | Teal `#0E7490`, ink navigation `#102B36`, cool neutral canvas | Ink sidebar, spacious work area, rounded controls | Daily operations and broad usability |
| 2 | Ledger | Copper `#9A4B24`, warm neutral `#F7F6F3`, charcoal text | Light sidebar, editorial headings, ruled panels | Readable evidence, reviews and approvals |
| 3 | Circuit | Mint `#6EE7B7`, graphite `#15191D`, dark raised surfaces | Compact dark console, mono evidence labels, angular controls | Focused technical investigation |
| 4 | Atlas | Cobalt `#2255C4`, white, cool neutral `#F6F7F9` | Top navigation, wide application canvas, adjacent inspector | Segmentation and relationship exploration |
| 5 | Sentinel | Burgundy `#9F2949`, pearl `#F8F7F8`, muted plum text | Light sidebar, task panel before the map, contextual actions | Distinctive branding and accountable review work |

## The five directions

All five share the same **Product**: help network administrators understand collected NSX configuration, inspect evidence and coordinate controlled changes. Their **Moments** are the application relationship map and the comparison between current and proposed group names. The remaining Unslop direction lines differ deliberately:

### 1. Harbor

- **Character:** Calm, spacious, guided.
- **World:** A network operations desk with clear routes and quiet instruments.
- **Signature:** A connected gateway mark, carried into an evidence context rail.
- **Identity:** An original gateway/network glyph, a compact NSX wordmark, rounded line icons and Avenir Next with system sans fallbacks.
- **Tradeoff:** Familiar to enterprise users; less radical than a navigation change.

[Segmentation](brand-directions/previews/harbor-segmentation.png) · [Naming](brand-directions/previews/harbor-naming.png) · [Login](brand-directions/previews/harbor-login.png) · [Mobile](brand-directions/previews/harbor-mobile.png) · [Logo](brand-directions/assets/harbor-logo.svg)

### 2. Ledger

- **Character:** Measured, legible, deliberate.
- **World:** An engineering review ledger: structured records and exact handovers.
- **Signature:** Parallel evidence tracks enclosed by a protective boundary.
- **Identity:** A flat evidence/shield mark, copper accents, system sans text and Georgia page headings, squared panels and restrained line icons.
- **Tradeoff:** Strong reading hierarchy; the editorial typography is less conventional for a technical console.

[Segmentation](brand-directions/previews/ledger-segmentation.png) · [Naming](brand-directions/previews/ledger-naming.png) · [Login](brand-directions/previews/ledger-login.png) · [Mobile](brand-directions/previews/ledger-mobile.png) · [Logo](brand-directions/assets/ledger-logo.svg)

### 3. Circuit

- **Character:** Compact, technical, composed.
- **World:** A night-shift network console with precise boundaries and readable evidence.
- **Signature:** A segmented hexagon carrying an unbroken network route.
- **Identity:** An angular network monogram, mint on graphite, system sans with prominent mono metadata, square-ended geometry.
- **Tradeoff:** Efficient for experienced operators; a light companion theme should be designed before adoption for all users.

[Segmentation](brand-directions/previews/circuit-segmentation.png) · [Naming](brand-directions/previews/circuit-naming.png) · [Login](brand-directions/previews/circuit-login.png) · [Mobile](brand-directions/previews/circuit-mobile.png) · [Logo](brand-directions/assets/circuit-logo.svg)

### 4. Atlas

- **Character:** Spatial, direct, exploratory.
- **World:** An infrastructure atlas: relationships, directions and defined inspection points.
- **Signature:** Four boundary corners framing a connected topology.
- **Identity:** An original boundary/topology mark, cobalt highlights, Helvetica Neue with system fallbacks, open headings and simple line icons.
- **Tradeoff:** More horizontal space for maps; top navigation needs validation with long labels and additional modules.

[Segmentation](brand-directions/previews/atlas-segmentation.png) · [Naming](brand-directions/previews/atlas-naming.png) · [Login](brand-directions/previews/atlas-login.png) · [Mobile](brand-directions/previews/atlas-mobile.png) · [Logo](brand-directions/assets/atlas-logo.svg)

### 5. Sentinel

- **Character:** Direct, approachable, organized.
- **World:** A security review desk: accountable decisions and clear separation of responsibilities.
- **Signature:** Opposing boundaries protecting a three-node route.
- **Identity:** A boundary/route glyph, burgundy on pearl, system sans with Trebuchet headings, softer controls and consistent rounded line icons.
- **Tradeoff:** Distinctive and task-focused; the map has less room than Atlas at intermediate desktop widths.

[Segmentation](brand-directions/previews/sentinel-segmentation.png) · [Naming](brand-directions/previews/sentinel-naming.png) · [Login](brand-directions/previews/sentinel-login.png) · [Mobile](brand-directions/previews/sentinel-mobile.png) · [Logo](brand-directions/assets/sentinel-logo.svg)

## What stays recognizable

Each concept retains the product name and real workflow concepts: environment context, collected evidence, dynamic/static groups, source/destination selection, finding ownership, distinct approvals and explicit execution steps. The previews deliberately distinguish collected configuration from observed traffic and effective access. Rule creation remains a clearly labelled placeholder.

These are alternative visual directions. They do not add proposed features to the deployed application. Choosing one should lead to a shared token and component implementation across inventory, findings, naming, segmentation, administration, login and deletion.

## Assets and validation

Each direction includes original SVG logo and mark files. SVG wordmarks use available system fonts; a selected final identity should receive production light/dark, small-size and outlined-export refinements. The interface glyphs are inline SVG line icons with a consistent size and stroke treatment. No external font, image or JavaScript service is required.

Twenty standalone screen/viewport combinations were rendered in Chrome at 1440 px desktop and 390 px mobile, with no captured page errors or document-level horizontal overflow. The concept chooser, naming selection, nested VM evidence, map focus, preview-only sign-in and mobile navigation were also checked in the browser. [Interaction checks](brand-directions/previews/interaction-checks.json) and [Browser checks](brand-directions/previews/browser-checks.json) record page errors and document dimensions. This is visual exploration, not an accessibility certification or a backend implementation test.

## Attribution

Design direction and review framework: [Unslop by Damir Sokolovsky (@heydamir)](https://github.com/heydamir/unslop), pinned revision `4652254daff586ff4739849395a127b56f7f8cc7`, licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). The SVG concept marks and code previews were created for this project.
