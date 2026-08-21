"""Shared test configuration.

The fine search deliberately simulates the cost of fetching and OCR-ing a
drawing (about a second per part). That is right for a demo and wrong for a
test suite, so disable it here rather than relying on the caller to export the
variable.
"""

import os

os.environ.setdefault("FINE_SEARCH_DELAY_S", "0")
