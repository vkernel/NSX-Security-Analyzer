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
