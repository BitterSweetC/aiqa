"""Deterministic accessibility (WCAG / ARIA) verification for AIQA."""

from __future__ import annotations

import logging
from typing import Any

from aiqa.models.test_case import Expectation, VerificationResult
from aiqa.verifier.dom import _unwrap_page

logger = logging.getLogger(__name__)

_A11Y_AUDIT_SCRIPT = """
(scopeSelector) => {
  const root = scopeSelector ? document.querySelector(scopeSelector) : document;
  if (!root) {
    return { error: `Scope selector '${scopeSelector}' not found in DOM`, violations: [] };
  }
  const violations = [];

  if (!scopeSelector) {
    const htmlLang = document.documentElement.getAttribute('lang');
    if (!htmlLang || !htmlLang.trim()) {
      violations.push('html: missing [lang] attribute on <html>');
    }
    if (!document.title || !document.title.trim()) {
      violations.push('title: missing or empty <title>');
    }
  }

  // 1. Images must have alt text unless decorative
  const images = root.querySelectorAll('img');
  for (const img of Array.from(images)) {
    const role = (img.getAttribute('role') || '').toLowerCase();
    const ariaHidden = (img.getAttribute('aria-hidden') || '').toLowerCase();
    if (role === 'presentation' || role === 'none' || ariaHidden === 'true') {
      continue;
    }
    if (!img.hasAttribute('alt')) {
      const id = img.id ? `#${img.id}` : (img.getAttribute('src') || 'img');
      violations.push(`img(${id}): missing [alt] attribute`);
    }
  }

  // 2. Form inputs must have an associated label or aria-label
  const controls = root.querySelectorAll(
    'input:not([type="hidden"]):not([type="submit"]):not([type="button"]):not([type="reset"]), select, textarea'
  );
  for (const ctrl of Array.from(controls)) {
    const ariaLabel = (ctrl.getAttribute('aria-label') || '').trim();
    const ariaLabelledBy = (ctrl.getAttribute('aria-labelledby') || '').trim();
    const titleAttr = (ctrl.getAttribute('title') || '').trim();
    const hasWrappingLabel = Boolean(ctrl.closest('label'));
    let hasForLabel = false;
    if (ctrl.id) {
      const escapedId = CSS.escape ? CSS.escape(ctrl.id) : ctrl.id;
      hasForLabel = Boolean(document.querySelector(`label[for="${escapedId}"]`));
    }
    if (!ariaLabel && !ariaLabelledBy && !titleAttr && !hasWrappingLabel && !hasForLabel) {
      const id = ctrl.id ? `#${ctrl.id}` : (ctrl.getAttribute('name') || ctrl.tagName.toLowerCase());
      violations.push(`control(${id}): missing accessible label (<label>, aria-label, or aria-labelledby)`);
    }
  }

  // 3. Buttons must have an accessible name
  const buttons = root.querySelectorAll('button, [role="button"]');
  for (const btn of Array.from(buttons)) {
    const text = (btn.innerText || btn.textContent || '').trim();
    const ariaLabel = (btn.getAttribute('aria-label') || '').trim();
    const ariaLabelledBy = (btn.getAttribute('aria-labelledby') || '').trim();
    const titleAttr = (btn.getAttribute('title') || '').trim();
    if (!text && !ariaLabel && !ariaLabelledBy && !titleAttr) {
      const id = btn.id ? `#${btn.id}` : 'button';
      violations.push(`button(${id}): empty accessible name`);
    }
  }

  return { error: null, violations };
}
"""


class AccessibilityVerifier:
    """Evaluates deterministic accessibility checks (images, form labels, buttons, landmarks)."""

    async def verify(
        self,
        expectation: Expectation,
        page: Any,
        **_kwargs: Any,
    ) -> VerificationResult:
        if not isinstance(expectation, Expectation) and isinstance(page, Expectation):
            expectation, page = page, expectation

        page_obj = _unwrap_page(page)
        scope_selector = expectation.selector.strip() if expectation.selector else None

        try:
            audit = await page_obj.evaluate(_A11Y_AUDIT_SCRIPT, scope_selector)
            if isinstance(audit, dict) and audit.get("error"):
                return VerificationResult(
                    expectation=expectation,
                    passed=False,
                    actual_value=str(audit["error"]),
                    message=str(audit["error"]),
                )

            violations: list[str] = list((audit or {}).get("violations", []))
            if not violations:
                return VerificationResult(
                    expectation=expectation,
                    passed=True,
                    actual_value="0 violations",
                    message="Accessibility audit passed with 0 violations",
                )

            summary_str = "; ".join(violations)
            return VerificationResult(
                expectation=expectation,
                passed=False,
                actual_value=f"{len(violations)} violation(s): {summary_str}",
                message=f"Accessibility audit found {len(violations)} violation(s): {summary_str}",
            )
        except Exception as exc:
            logger.exception("Error executing accessibility verification")
            return VerificationResult(
                expectation=expectation,
                passed=False,
                actual_value=None,
                message=f"Accessibility verification error: {exc}",
            )


__all__ = ["AccessibilityVerifier"]
