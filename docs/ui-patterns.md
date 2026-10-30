# Shared interface patterns

The application uses shared page headers, status badges, action menus, table containers,
empty states and theme-aware surfaces across the main workspace pages. These patterns
also apply to collection, review, deletion, cleanup and settings workflows.

## Actions and navigation

Use a descriptive page title, a short purpose and environment context where relevant.
Give the next workflow action priority; put supporting links in a secondary button or
More menu. Keep approval and execution separate. Existing server-side authorization,
review requirements and live verification remain authoritative.

Preserve the originating queue and filters in return links. Returning to the same list
also restores its reading position within the browser session. Escape closes action
menus and returns focus to their summary. Technical evidence stays in expandable
sections, with object names and outcomes visible outside those sections.

## Status and refresh

Use readable status badges with text as well as colour. Completed describes a finished
request, not necessarily a successful deletion; individual object results remain visible.
Show actual phase or operation counts when available, never simulated completion.

Collections and live task regions expose Refresh now and Pause updates. Deletion and
cleanup retain their own refresh controls. Pausing only affects browser updates.
Requests time out and report refresh failures without stopping background work.
Expanded evidence and draft values survive region refreshes; focused inputs and open
dialogs postpone replacement. Validation errors marked `data-preserve-error` remain
visible until explicit refresh. No refresh submits an approval or deletion form.

## Layout and accessibility

Shared styles support light/dark themes, display density, reduced motion, focus
indicators and narrow screens. Wide tables stay inside scroll regions, which become
keyboard-focusable when they overflow. Search and filter controls wrap on narrow
screens. Empty results explain what to do next.

Use `components/page_header.html`, `components/empty_state.html`, and the
`ui_components.status_badge` inclusion tag for new pages. Load the shared assets from
`base.html`; keep page-specific styles narrowly scoped. Test workflow permissions and
refresh state preservation when changing these components.


### Table counts and owner filtering

Paginated lists show the displayed range, matching result count, rows per page and previous/next links beneath the table, including single-page and empty results. Size changes preserve filters and reset pagination. Server lists remember the selected size for the browser session; existing preference sizes remain supported alongside 25, 50 and 100. Inventory tables retain their existing page-size preference. Unfiltered totals appear only where the data source provides them.

Small unpaginated tables show rows currently displayed, not an inferred database total. Bulk selection refers to the current page and remains subject to action limits.

The finding Owner filter uses the shared searchable reviewer selector, with Anyone, Assigned to me and Unassigned shortcuts. Changing the search text retains the previous filter until a result is chosen. Apply submits the selected filter.
