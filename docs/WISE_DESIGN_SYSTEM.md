# Wise Design System — UX Patterns for EA

## 1. Page Transitions (4 types, one purpose each)

| Transition | Direction | When | Flutter |
|---|---|---|---|
| **Upwards** | Screen slides up from bottom | Starting new flow (Send Money, Compose) | `SlideTransition(begin: Offset(0,1))` |
| **Sideways** | Lateral slide | Continuing within flow (list→detail) | `SlideTransition(begin: Offset(1,0))` |
| **Modal** | Overlay, dismissible | Supplementary task (selectors, filters) | `showModalBottomSheet` |
| **Bottom sheet** | Partial slide up, draggable | Small action (quick reply) | `DraggableScrollableSheet` |

**EA implementation:** Replace GoRouter's single `MaterialPageRoute` with 4 named transition builders. Each has semantic meaning — users learn: "slide up = new, slide right = deeper."

## 2. Segmented Control / Tabs

**Active state slides.** Not a snap — the indicator animates to the new position with spring physics. The inactive text color transitions smoothly.

**Flutter:** `TabBar` with `indicator: UnderlineTabIndicator()` + `AnimatedContainer` for the sliding pill. Duration: 200ms spring.

## 3. Button Press States

**Three interaction states**, each with its own animation:

| State | What happens | Duration |
|---|---|---|
| **Press** (pointer down) | Scale down 0.97 + slightly darker background | 100ms |
| **Release** (pointer up) | Scale back to 1.0 + snap-back overshoot | 200ms spring |
| **Hover** | Subtle background shift, no scale | 150ms ease |

**Disabled:** Don't just grey out — always explain WHY. "Complete form above" vs just grey button.

**Flutter:** `AnimatedScale` + `InkWell` with `splashFactory: InkRipple.splashFactory` for Material-style ripple. Custom `ButtonStyle` with `overlayColor` for hover.

## 4. List Items (Tap Feedback)

**Not a flash — a gentle pulse.** The item slightly darkens, content scales 0.98 briefly, then springs back.

**Navigation list items:** Left-to-right ripple on tap, new page slides in sideways.

**Flutter:** `ListTile` with `InkWell`. On web/desktop: hover highlight via `onHover`. Use `Curves.easeOutBack` for spring return.

## 5. Cards

**Entry animation:** Staggered — each card fades in + slides up 20px with 50ms delay between cards. Uses spring, not linear.

**Tap interaction:** Card scales 0.97 briefly, then springs back. If navigable, a subtle shadow elevation increase during press.

**Flutter:** `StaggeredAnimation` with `Interval` + `SlideTransition` + `FadeTransition`. Delay = index * 50ms.

## 6. Progress Indicators

**Two types, clear semantic difference:**

| Type | Behavior | When |
|---|---|---|
| **Indeterminate** | Smooth continuous animation loop | Unknown wait time |
| **Determinate** | 0→100% fill before exiting frame | Known/very fast progress |

**Never:** Keep a completed progress bar sitting at 100% — it always exits the frame after reaching 100%.

**Flutter:** `LinearProgressIndicator` for both. Use `TweenAnimationBuilder` for determinate fill.

## 7. Snackbar / Notifications

**Auto-dismissing, no user action needed.** Fades in from bottom, stays 4s, fades out. Optional single action button.

**Never:** More than one button. The same action should always be available elsewhere on screen.

**Flutter:** `SnackBar` with `duration: Duration(seconds: 4)`. Slide transition with `Curves.easeOut`.

## 8. Bottom Sheet

**Three dismiss methods:**
1. Close button (top left)
2. Action within sheet content
3. Drag/swipe down (the key differentiator from modal)

Rests above a dimmer overlay. Dragging transitions smoothly — friction-based, not linear.

**Flutter:** `DraggableScrollableSheet` with `initialChildSize: 0.4`. Dismiss on drag velocity > threshold.

## 9. Motion Principles (apply to every animation)

| Principle | % | What it means |
|---|---|---|
| **Snappy** | 60% | Fast, satisfying, match-cut. Like flipping a coin. Not erratic. |
| **Fluid** | 30% | Organic rhythm, steady beat. Elements flow in/out. Not directionless. |
| **Intuitive** | 10% | Natural pace, finger-swipe physics. UI moves how you'd move it. |

**Speed graphs:** Always natural (ease-out-back) — never mechanical (linear). The curve should feel like a finger swipe.

**Accessibility:** No more than 3 color changes per second (prevent flashing). All video with captions + transcripts. Respect `prefers-reduced-motion`.

## 10. Implementing in Assistant's Flutter App

**Phase 1 (quick wins, ~100 lines):**
- Replace GoRouter default transitions with 4 named builders (upward, sideways, modal, sheet)
- Add spring animation to all `ListTile` taps
- Stagger entry animation for email/memory lists

**Phase 2 (interaction depth, ~200 lines):**
- Button press scale animation (0.97 press, 1.0 spring back)
- Segmented control sliding indicator
- Card hover/press with shadow elevation
- Progress bar determinate fill animation

**Phase 3 (motion system, ~150 lines):**
- Define `WiseMotion` widget with three duration presets: snappy (200ms), fluid (400ms), intuitive (600ms)
- Spring curve preset: `SpringSimulation(stiffness: 200, damping: 20)`
- Accessibility: `MediaQuery.of(context).disableAnimations` check before all animations
