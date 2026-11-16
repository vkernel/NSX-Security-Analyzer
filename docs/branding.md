# Relay branding

Relay is the selected NSX Security Analyzer identity. Its continuous route inside a protected hexagon represents connected infrastructure and reviewed changes. The application uses **Relay option 04: module rail + section panel**.

- [Dark wordmark](../webapp/static/nsx-security-analyzer-logo.svg): dark sign-in and other dark surfaces.
- [Light wordmark](../webapp/static/nsx-security-analyzer-logo-light.svg): light sign-in and documents.
- [Adaptive standalone mark](../webapp/static/nsx-security-analyzer-icon.svg): favicon and status pages.
- [Inline mark](../webapp/templates/inventory/relay_mark.html): rail and mobile menu; follows the selected application theme.

Use the mark at 32–40 px in navigation. Keep the SVG aspect ratio, transparent background and at least 12 px of clear space. Never squeeze the full wordmark into the 80 px module rail. The section panel identifies the current module and environment.

The dark palette uses canvas `#14191F`, surface `#1B222A`, mint `#5EE0B0` and text `#EDF3F7`. The selected **Bridge 06** light palette uses canvas `#F7F8FA`, white surfaces, silver raised areas `#EFF2F4`, steel-blue actions `#315A70` and text `#26343D`. The module rail stays dark `#10161C`, with mint selection and the Relay mark. Its section panel remains white. Semantic success, warning and error colors keep their established meanings. Main type is Avenir Next, with local system sans-serif fallbacks. No external font request is required.

Light, Dark and System can be selected from Appearance in the top bar or Personal settings. The choice is saved to the signed-in user's preferences. Changing it does not reload the page or discard entered review text. System follows the browser color-scheme preference. Reduced motion, larger text, high contrast and density settings remain available.

Keep the logo static; progress uses a separate status indicator. Give the full brand descriptive alternative text and hide decorative icons beside labels from assistive technology. Fresh standalone reports embed Relay artwork and remain self-contained. Previously stored reports and historical screenshots retain their original artwork.

Design methodology: [Unslop by Damir Sokolovsky (@heydamir)](https://github.com/heydamir/unslop), CC BY 4.0. See `NOTICE` and the [Relay exploration](design/relay-iterations.md).

## Shared style contract

`webapp/static/design-system.css` owns the light/dark palette, semantic colors, typography families, overlays and neutral shadows. Component styles consume those tokens; they must not introduce another palette or separate dark-mode overrides. `relay.css` supplies the selected rail, section panel and shared geometry.

Page-specific CSS belongs in the template's `extra_head` block, before the shared design system and Relay stylesheet. Keep stylesheet links out of page content and dynamically opened panels.

Integrated snapshots place retained report CSS in a lower-priority `snapshot-report` layer, scoped to report content and its native evidence/filter dialogs. This applies to retained presentations at display time: no new collection, report rewrite or index rebuild is required. Standalone report downloads retain their self-contained presentation.

Use neutral surfaces for evidence, fields and code viewers; reserve semantic colors for outcomes. Primary controls use `--action-text`, including administration widgets and dark mode. Dense tables scroll inside their own containers; long NSX paths and callback URLs wrap without widening the page.

See the [application-wide consistency audit](design/relay-consistency-2026-11-03.md).
