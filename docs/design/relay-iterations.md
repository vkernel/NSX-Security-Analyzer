# Relay · Twenty menu and workspace iterations

> **Historical record.** This document describes the design or implementation at the time it was written. Current code, tests and the main documentation take precedence.

Twenty distinct Relay layout options, each with both dark and light mode: **20 layouts × 2 themes = 40 combinations**. Relay’s selected logo, route-inspired icons, mint accent, typography and evidence-first character remain consistent. The menus and placement of supporting evidence change.

[Open the interactive chooser](relay-iterations/index.html) · [Compare two options](relay-iterations/compare.html) · [Original Relay exploration](circuit-atlas-directions/index.html?concept=relay)

## Start here

Use **Relay option** to choose a layout, then switch **Dark / Light**. The arrows move through all twenty options. **Browse all 20 options** shows the menu silhouettes and filters by menu family. The app header also contains a theme toggle. The selected option, theme and screen are preserved in its URL.

In the comparison page, choose the same option on both sides to compare its two themes, or choose different options to compare their menus. Select Segmentation or Naming review. Click a screenshot to open its interactive preview.

Visual sheets: [1–5](relay-iterations/previews/overview-1.png) · [6–10](relay-iterations/previews/overview-2.png) · [11–15](relay-iterations/previews/overview-3.png) · [16–20](relay-iterations/previews/overview-4.png). Each sheet shows both themes side by side. Interactive HTML sheets: [1–5](relay-iterations/overview-1.html), [6–10](relay-iterations/overview-2.html), [11–15](relay-iterations/overview-3.html), [16–20](relay-iterations/overview-4.html).

## Recommended shortlist

- **01 Original top bar:** closest to the Relay direction you selected; all modules remain visible.
- **02 Familiar sidebar:** the strongest default for a broad audience. Persistent, readable labels make destinations easy to find.
- **07 Primary + More:** the strongest option if you want a quieter top menu and a broad canvas.
- **17 Investigation workbench:** best to try for engineers who spend longer inspecting configuration evidence.
- **11 Focus drawer:** best to try when the map should occupy most of the workspace.

Options 09 and 15 deliberately test less conventional menu positions. They are alternatives for comparison, rather than the default recommendation. Command navigation is an optional shortcut, with an explicit button and a readable module list.

## The twenty options

| Option | Menu and layout | Useful when | Tradeoff |
|---|---|---|---|
| [01. Original top bar](relay-iterations/index.html?concept=original) | Relay as selected: all modules in one horizontal bar. | Easy to recognize; keeps the full application map wide. | More horizontal travel between modules. |
| [02. Familiar sidebar](relay-iterations/index.html?concept=sidebar) | A persistent labeled left menu with a quiet context bar. | The clearest starting point for occasional users. | Uses 224 px of horizontal workspace. |
| [03. Compact labeled rail](relay-iterations/index.html?concept=compact) | A narrow left rail with icons and short text labels. | More canvas without relying on icons alone. | Short labels need a little familiarity. |
| [04. Rail + section panel](relay-iterations/index.html?concept=dual) | A module rail and a separate panel for the current workflow. | Separates application navigation from local tasks. | Two columns consume more space on smaller laptops. |
| [05. Grouped top menu](relay-iterations/index.html?concept=grouped) | Explore, Review and Manage open small task menus. | Fewer top-level choices and clear task grouping. | Some destinations take an extra click. |
| [06. Command workspace](relay-iterations/index.html?concept=command) | A quiet header and a searchable Go to menu. | Maximum workspace for people who know their task. | Less discoverable than a permanently visible menu. |
| [07. Primary + More](relay-iterations/index.html?concept=more) | Frequent modules stay visible; secondary modules move to More. | Balances direct access and a quieter top bar. | The choice of primary modules should match your team. |
| [08. Collapsible sidebar](relay-iterations/index.html?concept=collapsible) | Explore, Review and Manage expand within the left menu. | Keeps related work together and the menu readable. | A closed section hides its destinations. |
| [09. Bottom workspace dock](relay-iterations/index.html?concept=dock) | A labeled module dock sits below the workspace. | Keeps the top edge open and navigation close together. | Unusual for enterprise web tools; try it before choosing. |
| [10. Environment tree](relay-iterations/index.html?concept=tree) | A contextual tree shows environment, evidence and review destinations. | Makes the environment relationship explicit. | A tree is denser than a simple sidebar. |
| [11. Focus drawer](relay-iterations/index.html?concept=focus) | The main menu opens only when requested. | A clear, spacious work surface with less permanent chrome. | Switching modules always requires opening the menu. |
| [12. Two-level ribbon](relay-iterations/index.html?concept=ribbon) | Global modules sit above a separate local task strip. | Makes the current module and its views easy to distinguish. | Adds another horizontal row. |
| [13. Workspace launcher](relay-iterations/index.html?concept=launcher) | A header launcher opens a grid of named modules. | Readable choices without a long permanent navigation bar. | Module changes take two clicks. |
| [14. Environment sidebar](relay-iterations/index.html?concept=environment) | An environment list sits left; modules remain in the header. | Useful when switching between the three environments often. | The environment list takes space even when working in one. |
| [15. Right-hand navigation](relay-iterations/index.html?concept=right) | A labeled navigation panel sits at the right edge. | Lets you compare a right-side menu with the familiar left one. | Less conventional and less predictable for new users. |
| [16. Section flyouts](relay-iterations/index.html?concept=flyout) | A short category rail opens labeled navigation flyouts. | Provides a compact menu with clear labels when needed. | Requires opening a category before choosing its module. |
| [17. Investigation workbench](relay-iterations/index.html?concept=workbench) | A compact task menu, full-width map and evidence strip below. | Useful for longer configuration investigations. | The evidence strip moves below the main visual. |
| [18. Review workspace](relay-iterations/index.html?concept=review) | Review destinations lead the sidebar; context appears beside evidence. | Prioritizes naming work, approvals and decommissioning. | Less direct for collection administration. |
| [19. Wide canvas](relay-iterations/index.html?concept=canvas) | A slim module strip and expansive map with evidence beneath it. | Gives application relationships the most room. | Supporting evidence is farther down the page. |
| [20. Guided workspace](relay-iterations/index.html?concept=guided) | A module menu plus an explicit select, inspect and plan sequence. | Helps occasional users see the next step. | Experts may find the persistent guidance unnecessary. |

## Direction

- **Product:** Network administrators inspect collected NSX configuration and coordinate reviewed changes.
- **Character:** Focused, balanced, exact.
- **World:** A control-room routing desk used by engineers and approvers.
- **Moments:** The application relationship map and the current-versus-proposed naming comparison.
- **Signature:** A continuous relay route inside the protected hexagon, with environment, collection time and coverage beside decisions.

## Design passes

| Pass | Applied treatment |
|---|---|
| Spacing | Consistent 4 px rhythm; compact, normal and spacious variants; bounded navigation and readable table rows |
| Color | One Relay palette in two themes; dark mint and a darker green for readable light-theme links and primary actions |
| Icons | Relay mark and icon system retained; short labels on compact rails; accessible full module names |
| Copy | Named menu structures, plain task labels and a best-use/tradeoff note for every option |
| Type | Avenir Next with system sans fallback; clear headings, tabular figures and monospace evidence metadata in compact variants |
| Layout | Top menus, side menus, grouped menus, launcher, command search, focus drawer, bottom dock, right menu and task-led arrangements |
| Details | Theme and option URLs, comparison controls, drawer focus restoration, keyboard Escape, result counts and contained narrow-screen scrolling |
| Visuals | The same six applications and six configuration relationships across all layouts; original native SVG branding |
| Motion | Short color feedback, no decorative motion, reduced-motion support |

## Interactive scope

Every option includes Segmentation, Naming review, Login and Brand. Menu destinations for Segmentation and Naming compliance are interactive examples; other modules display a local preview notice. All module names remain available, including in hidden menus and the mobile drawer.

Try a map node, the VM-to-VM lookup, existing rule evidence, the security matrix, naming search and the bulk-selection drawer. Grouped top menus, the workspace launcher and flyouts open actual menus. Press **⌘ K** on macOS or **Ctrl K** elsewhere to search the module list. Escape closes menus and drawers; drawers restore focus.

On narrow screens, all options use the same labeled navigation drawer. Dense maps and tables scroll within their own containers. The preview does not prescribe twenty different mobile menu systems.

Theme changes and layout changes re-render the illustrative screen and reset transient demo inputs. Production form preservation would be handled when applying the chosen option.

## Theme and identity assets

| Token | Dark | Light |
|---|---|---|
| accent | `#5EE0B0` | `#087657` |
| paper | `#14191F` | `#F4F7F6` |
| surface | `#1B222A` | `#FFFFFF` |
| raised | `#25313A` | `#E9F0ED` |
| ink | `#EDF3F7` | `#1E2B2F` |
| muted | `#ADBBC8` | `#53656B` |
| nav | `#10161C` | `#FFFFFF` |
| line | `#3B4855` | `#CBD8D2` |

[Dark wordmark](relay-iterations/assets/relay-logo-dark.svg) · [Light wordmark](relay-iterations/assets/relay-logo-light.svg) · [Dark mark](relay-iterations/assets/relay-mark-dark.svg) · [Light mark](relay-iterations/assets/relay-mark-light.svg) · [Icon specimen](relay-iterations/assets/relay-icons.svg)

These are the selected Relay identity assets, retained from the earlier exploration. The interface uses locally available fonts; no external font or imagery request is required.

## Validation

- 240 rendered states: all twenty options in both themes, four desktop screens, and segmentation at 390 px and 320 px. No captured JavaScript errors or document-level horizontal overflow.
- All 46 interaction groups passed. Checks cover each layout in both themes, menu positioning and keyboard dismissal, endpoint selection and the disabled write placeholder, rule evidence, map focus, naming search and selection, theme switching, URL persistence, narrow-screen naming and mobile navigation.
- Additional checks cover gallery filters, previous/next navigation, command search, login preview behavior, comparison images and the four overview sheets.
- Both theme palettes exceed 4.5:1 for the checked base body, secondary text, link, navigation and primary-button text/background pairs. This is a token-level measurement, not a full accessibility certification.

[Viewport report](relay-iterations/previews/browser-checks.json) · [Interaction report](relay-iterations/previews/interaction-checks.json) · [Contrast report](relay-iterations/previews/contrast-checks.json)

## Boundaries

This is a standalone design exploration using illustrative data. It does not modify the production application or connect to NSX. Login sends and stores no credentials. Rule creation remains a disabled placeholder. Configuration relationships do not establish observed traffic or effective access. Existing application approval and execution safeguards remain authoritative when a direction is implemented.

## Attribution

Design method adapted from [Unslop by Damir Sokolovsky (@heydamir)](https://github.com/heydamir/unslop), [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), revision `a6f121eee5f29c2f74976b75456feae8b66afb3a`. The method was applied locally; this package extends the original Relay concept created for NSX Security Analyzer.
