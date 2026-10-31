---
name: ktv-ui-review
description: "Review customer and admin KTV interfaces for usability, visual consistency, responsiveness, accessibility, permission boundaries, operational safety, and frontend engineering quality."
---

# KTV UI Review

Use this skill to review existing or newly implemented customer and admin UI. Inspect the repository and relevant routes, components, styles, data models, and authorization before drawing conclusions. Reuse the current framework, routing, styling, API contracts, and authentication boundaries as review constraints.

## Review scope

Evaluate:

1. Primary workflow clarity.
2. Customer and admin separation.
3. Navigation consistency.
4. Visual hierarchy and design-token consistency.
5. Component reuse.
6. Mobile, tablet, desktop, and large-touchscreen responsiveness.
7. Touch-target sizing.
8. Keyboard navigation and focus visibility.
9. Accessible naming and colour contrast.
10. Status communication beyond colour.
11. Loading, empty, error, offline, and unauthorized states.
12. Permission handling and high-impact action confirmation.
13. Console errors and build warnings, when those checks can be run.
14. Performance risks.
15. Broken or ineffective controls.
16. Unsupported or fabricated integrations.

## Accessibility checks

Check for:

- Semantic HTML.
- Keyboard navigation and visible focus indicators.
- Accessible labels for icon-only controls.
- Sufficient colour contrast.
- Logical heading structure.
- Visible form labels and field-specific errors.
- Status communication beyond colour alone.
- Touch targets of at least 44 by 44 CSS pixels.
- Reduced-motion support where appropriate.

## Responsive checks

Review at representative widths:

- 320px mobile.
- 375px mobile.
- 768px tablet portrait.
- 1024px tablet landscape.
- 1280px laptop.
- 1440px desktop.
- A large room display where relevant.

Avoid page-level horizontal scrolling and check that important content and actions remain available at each applicable size.

## Findings and review-only behavior

Classify findings as **Blocker**, **High**, **Medium**, or **Low**. For every finding, report:

- Page or component.
- Relevant file.
- Problem.
- User impact.
- Recommended correction.

When asked only to review, do not modify files. Report only evidence supported by inspection or checks actually performed; distinguish an unrun check from a passed check.

When asked to review and repair:

- Fix Blocker findings.
- Fix High findings within the approved scope.
- Fix Medium findings only when low-risk.
- Do not perform unrelated refactoring.
- Run available validation afterward and report only checks actually performed.

## Shared engineering boundaries

- Preserve the frontend framework and routing/styling systems where practical.
- Preserve API contracts, authentication, and authorization.
- Never expose admin controls to customers.
- Do not invent production APIs, fake production data, unsupported integrations, or non-functional controls.
- Avoid unnecessary dependencies and unrelated refactoring.
- Separate business logic from presentation.
- Account for relevant loading, empty, error, offline, and unauthorized states.
- Reuse existing components and report validation actually performed.

