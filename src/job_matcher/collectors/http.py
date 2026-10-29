"""HTTP settings shared by all collectors: one polite, identifiable client."""

import httpx

USER_AGENT = "job-matcher/0.1 (+https://github.com/dim-dimy4/job-matcher)"
TIMEOUT_SECONDS = 20.0


def make_client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_SECONDS, follow_redirects=True
    )
