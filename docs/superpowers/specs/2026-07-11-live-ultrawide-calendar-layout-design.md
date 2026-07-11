# Live Ultrawide And Calendar Layout Design

## Goal

Keep the Live page compact and readable on standard desktop and ultrawide displays. Prevent the monthly calendar order detail from expanding the whole page when a selected day contains many settled orders.

## Scope

- Stabilize the BTC market and account equity chart layout.
- Keep both chart cards visually aligned at desktop widths.
- Prevent excessive chart stretching on ultrawide displays.
- Make the selected-day order list independently scrollable.
- Preserve the existing data sources, calculations, and interactions.

## Chart Layout

At `xl` and wider viewports, the market section uses two equal-width grid tracks. Both cards stretch to the same row height and use the same fixed plot height: 230px on compact screens and 300px from the medium breakpoint upward.

The Live content area gains a centered maximum width suitable for a trading console. On ultrawide screens, additional viewport width becomes outer whitespace instead of stretching SVG charts indefinitely. Below `xl`, the charts remain stacked and full width.

The equity card uses a three-row layout: header, fixed-height plot, and footer. The BTC card keeps its existing content but participates in the stretched grid row so the two outer cards align.

## Monthly Calendar Detail

At desktop widths, the calendar and detail panel share one bounded layout. The detail panel uses a flex column: header and daily summary remain visible, while only the order list scrolls. The order list has a visible but restrained scrollbar and does not increase the section height.

On smaller screens, the detail panel remains below the calendar. Its order list has a maximum height of 420px and scrolls independently. Empty, loading, and error states remain visible without creating an empty oversized panel.

## Accessibility And Behavior

- Keep keyboard-accessible day buttons and existing labels.
- Preserve selected-day state and close behavior.
- Keep summary information outside the scrolling list.
- Use stable dimensions to avoid layout shifts as data loads.
- Do not change API requests, settlement calculations, or trading behavior.

## Verification

- Add component or browser assertions for equal desktop chart heights.
- Verify the selected-day order list has bounded height and vertical scrolling.
- Test standard desktop and 3440x1440 ultrawide viewports.
- Confirm no horizontal page overflow and no overlap at mobile width.
- Run the production build and focused Live Playwright suite.
