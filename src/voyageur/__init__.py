"""
===============================================================================
voyageur (repair drifted Playwright locators)
===============================================================================
A service that repairs locators in Playwright tests when the page they target has changed. A client sends the failing
test's error and source, plus a text-only inventory of the page's elements; the service works out which locator broke,
finds the same element again in the inventory (by what it was when the test last passed, or by asking a model to pick one
from a short quoted list), and writes the replacement locator itself, in the test's own language, from the element's
stable attributes. A model never writes locator text, so nothing a page or a model says can become code in a test.

This package holds the low-level pieces: reading and validating locators (locators, locator_steps, failing), ranking and
writing them (locator_policy), matching them against an inventory (inventory, uniqueness, elements), talking to a model
(llm, transport, netpolicy, redact), the settings readers (config) and the in-page probe that collects the inventory
(probe/probe.js).

Features:
    ✓ No evaluation of text anywhere: locators are parsed as data and checked against fixed grammars
    ✓ No browser, no page fetch and no subprocess in the service; the client collects the page evidence
    ✓ Works for Python and TypeScript tests from one language-neutral description of a locator

Environment Variables:
    None here; see config.py and llm.py for the settings each part reads.

Author: PMAC
===============================================================================
"""
__version__ = "0.1.0"
