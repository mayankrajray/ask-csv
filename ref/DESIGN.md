---
name: Cognitive Mobile Intelligence
colors:
  surface: '#f8f9ff'
  surface-dim: '#cbdbf5'
  surface-bright: '#f8f9ff'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#eff4ff'
  surface-container: '#e5eeff'
  surface-container-high: '#dce9ff'
  surface-container-highest: '#d3e4fe'
  on-surface: '#0b1c30'
  on-surface-variant: '#464556'
  inverse-surface: '#213145'
  inverse-on-surface: '#eaf1ff'
  outline: '#777588'
  outline-variant: '#c7c4d9'
  surface-tint: '#4d3cf2'
  primary: '#412ce7'
  on-primary: '#ffffff'
  primary-container: '#5b4dff'
  on-primary-container: '#efebff'
  inverse-primary: '#c4c0ff'
  secondary: '#565e74'
  on-secondary: '#ffffff'
  secondary-container: '#dae2fd'
  on-secondary-container: '#5c647a'
  tertiary: '#9e260a'
  on-tertiary: '#ffffff'
  tertiary-container: '#c03e21'
  on-tertiary-container: '#ffe9e4'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#e3dfff'
  primary-fixed-dim: '#c4c0ff'
  on-primary-fixed: '#110068'
  on-primary-fixed-variant: '#3311dc'
  secondary-fixed: '#dae2fd'
  secondary-fixed-dim: '#bec6e0'
  on-secondary-fixed: '#131b2e'
  on-secondary-fixed-variant: '#3f465c'
  tertiary-fixed: '#ffdad2'
  tertiary-fixed-dim: '#ffb4a3'
  on-tertiary-fixed: '#3d0600'
  on-tertiary-fixed-variant: '#8c1900'
  background: '#f8f9ff'
  on-background: '#0b1c30'
  surface-variant: '#d3e4fe'
typography:
  headline-lg:
    fontFamily: Plus Jakarta Sans
    fontSize: 32px
    fontWeight: '700'
    lineHeight: 40px
  headline-lg-mobile:
    fontFamily: Plus Jakarta Sans
    fontSize: 24px
    fontWeight: '700'
    lineHeight: 32px
  headline-md:
    fontFamily: Plus Jakarta Sans
    fontSize: 20px
    fontWeight: '600'
    lineHeight: 28px
  headline-sm:
    fontFamily: Plus Jakarta Sans
    fontSize: 16px
    fontWeight: '600'
    lineHeight: 24px
  body-lg:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 24px
  body-md:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
  body-sm:
    fontFamily: Inter
    fontSize: 12px
    fontWeight: '400'
    lineHeight: 16px
  label-md:
    fontFamily: Inter
    fontSize: 13px
    fontWeight: '600'
    lineHeight: 18px
  label-sm:
    fontFamily: Inter
    fontSize: 11px
    fontWeight: '600'
    lineHeight: 14px
  label-xs:
    fontFamily: Inter
    fontSize: 10px
    fontWeight: '700'
    lineHeight: 12px
rounded:
  sm: 0.25rem
  DEFAULT: 0.5rem
  md: 0.75rem
  lg: 1rem
  xl: 1.5rem
  full: 9999px
spacing:
  gutter: 0.75rem
  gutter-mobile: 0.5rem
  margin: 1rem
  margin-mobile: 0.75rem
  space-xs: 0.25rem
  space-sm: 0.5rem
  space-md: 0.75rem
  space-lg: 1rem
  space-xl: 1.5rem
---

## Brand & Style

This design system defines an executive-grade AI data intelligence environment optimized for high-density, vertical mobile contexts. It translates complex querying, schema exploration, pipeline monitoring, and natural-language telemetry into an authoritative, tactile mobile experience.

### Core Philosophy & Visual Aesthetics
- **Style Direction:** Modern Corporate with Elevated Technical Tactility. It marries deep, calm slate-navy foundational tones with an electrifying violet core and precise warm coral telemetry accents.
- **Personality:** Authoritative, razor-sharp, cognitive, and friction-free.
- **Mobile Paradigm:** Information architecture is re-engineered from wide desktop dashboards into vertical scannability: stacked metric capsules, swipeable telemetry trays, inline conversational query threads, and thumb-anchored prompt mechanics.
- **Emotional Response:** Empowers data scientists and tech executives with clarity and decision confidence directly from handheld devices without feeling crowded or dumbed down.

## Colors

The palette breaks away from washed-out corporate neutrals by anchoring in rich deep-slate slate navies, illuminated by an energetic electric violet and balanced with sharp analytical coral highlights.

### Functional Roles
- **Primary (`#5B4DFF` - Electric Violet / Indigo):** Drives the primary action tier, active AI processing states, selected segment pills, and key interactive prompts. Provides cognitive focus against light canvas backgrounds.
- **Secondary (`#0F172A` - Midnight Slate):** Serves as structural ink, high-contrast headings, active tab indicators, and grounding surfaces for dark-mode cards or bottom sheets.
- **Tertiary (`#FF6B4A` - Warm Analytical Coral):** Reserved strictly for anomaly alerts, high-priority telemetry deltas, critical notifications, and highlight triggers that demand immediate thumb interaction.
- **Neutral (`#64748B` - Slate Muted):** Controls secondary metadata, column type badges, inactive icons, and technical schema subtitles.

### Surface Architecture
- **Base Canvas (`#F8FAFC`):** Soft, anti-glare canvas designed to keep high-frequency mobile sessions fatigue-free.
- **Surface Elevation 01 (`#FFFFFF`):** High-density cards, bottom sheets, and conversational bubbles.
- **Surface Elevation 02 (`#EEF2F6`):** Chip containers, code block backgrounds, and input wells.
- **Borders & Dividers (`#E2E8F0`):** Hairline precision borders (0.5px to 1px) separating dense tabular elements without cluttering screen real estate.

## Typography

The typographic hierarchy pairs the contemporary geometric poise of **Plus Jakarta Sans** for structural context with the neutral, hyper-legible matrix of **Inter** for dense analytical workloads.

### Rules & Guidance
- **Display & Headings:** Rendered in `Plus Jakarta Sans`. Kerning is tightened slightly (-0.02em) on mobile headlines to ensure dense headers (like query execution titles and dataset labels) occupy minimal vertical height.
- **Analytical & Body Copy:** Set in `Inter`. Designed for quick vertical scanning of tabular headers, AI prompt streaming text, and column definitions.
- **Labels & Schema Tokens:** High-weight, reduced-scale tokens (`label-xs` and `label-sm`) use uppercase transforms and open tracking (+0.04em) for column data types (`VARCHAR`, `BIGINT`, `TIMESTAMP`) and data health pills.

## Layout & Spacing

This layout system is optimized for portrait mobile displays (375px to 430px base widths) while fluidly expanding for tablets and foldable form factors.

### Mobile-First Spacing Rhythm
- **Outer Canvas Margins:** Strict `0.75rem` (12px) on compact handhelds, extending to `1rem` (16px) on larger devices. Maximize horizontal area for charts, conversational turns, and tabular rows.
- **Vertical Flow Hierarchy:** Cards and thread updates decouple via `space-md` (12px) to maintain continuous narrative momentum without feeling disjointed.
- **Data Inset Density:** Metric pills, table cells, and conversational chip suggestions use `space-xs` (4px) to `space-sm` (8px) internal padding, ensuring high information density per viewport height.
- **Thumb Reach Zones:** High-touch interactions—the AI conversational prompt bar, quick filter drawers, and dataset switcher—are docked to the bottom viewport with safe-area offsets (`env(safe-area-inset-bottom)`).

## Elevation & Depth

Visual hierarchy uses a refined layering system combining tinted ambient drop shadows with micro-borders, establishing tactile depth on mobile screens without relying on heavy skeuomorphism.

### Depth Levels
- **Ground (Level 0):** Background canvas (`#F8FAFC`). Flat surface.
- **Cards & Data Modules (Level 1):** Solid white (`#FFFFFF`) with a 1px border (`#E2E8F0`) and an ambient tinted shadow: `0 2px 8px -2px rgba(15, 23, 42, 0.05), 0 1px 3px -1px rgba(15, 23, 42, 0.03)`. Gives clear boundary definition against the canvas.
- **Floating Prompts & Pinned Nav (Level 2):** Input bar and bottom sheets use elevated depth: `0 8px 24px -4px rgba(15, 23, 42, 0.08), 0 2px 6px -1px rgba(15, 23, 42, 0.04)` combined with a frosted backdrop filter (`blur(12px)`) when hovering over scrollable data.
- **Modals & Filter Trays (Level 3):** Bottom-anchored action sheets feature high-elevation depth: `0 20px 40px -8px rgba(15, 23, 42, 0.16)` alongside a 30% slate overlay.

## Shapes

The design system adopts a balanced **Rounded (Level 2)** shape identity. It introduces modern, friendly edges that keep technical data approachable while maintaining enough crisp structure for multi-column telemetry.

### Radius Assignments
- **Core Cards & Drawers:** `rounded-lg` (16px / 1rem) for high-level analytical cards, chart modules, and chat interaction bubbles.
- **Inputs & Interactive Controls:** `rounded` (8px / 0.5rem) for text input fields, search inputs, and inline action toggles.
- **Badges, Schema Tags & Metric Pills:** Fully rounded pills (`9999px`) provide contrast against rectangular cards and highlight statuses like data types, health metrics, and active filters.

## Components

### Buttons & Interactive Controls
- **Primary AI Trigger:** Filled with `#5B4DFF`, text in white `label-md`, 44px minimum touch height, subtle interior glow (`inset 0 1px 0 rgba(255, 255, 255, 0.2)`).
- **Secondary Actions:** White card background, 1px border (`#E2E8F0`), text in `#0F172A`. Active state turns to `#EEF2F6`.
- **Destructive/Critical Actions:** Tinted warm coral wash (`#FFF1EE`), text in `#FF6B4A`, 1px border (`#FFD8D0`).

### Metric Capsules & Telemetry Badges
- **Status Pills:** 20px height, 8px horizontal padding, `label-xs` uppercase tracking.
  - *Ready/Operational:* `#ECFDF5` background, `#059669` text, `#A7F3D0` border.
  - *Anomaly Detected:* `#FFF1EE` background, `#FF6B4A` text, `#FFC7BA` border.
  - *Type Identifier:* `#F1F5F9` background, `#475569` text (`VARCHAR`, `INT`, `JSON`).

### Analytical Conversational Cards
- **Agent Responses:** White card, full width, padded with `space-md`, featuring an avatar badge top-left and an inline horizontal scroll tray for follow-up prompt chips.
- **Embedded Visualizations:** Mobile charts reflow into simplified, touch-scrubbable sparklines or bar distributions with 32px bar touch targets and auto-collapsing secondary legends.

### Input Fields & Bottom Prompt Bar
- **Prompt Input Dock:** Anchored to the mobile base with a frosted glass backdrop, containing an upload pin icon, expanding multiline text field, and an electric violet send button.
- **Schema Search Inputs:** 36px compact height, `#F1F5F9` background with inline glass icon and instant keyboard clear action.