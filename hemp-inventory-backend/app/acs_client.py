import httpx
import asyncio
import logging
import os

log = logging.getLogger(__name__)

ACS_BASE_URL = "https://portal.acslabcannabis.com/api"

CONCURRENCY = 50
PAGE_TIMEOUT = 30.0


class ACSLabClient:
    """Client for ACS Laboratory MAHI portal API."""

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("ACS_LAB_API_KEY", "")
        self.base_url = ACS_BASE_URL

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }

    async def _request_with_retry(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        max_retries: int = 3,
        **kwargs,
    ) -> httpx.Response:
        for attempt in range(max_retries):
            resp = await getattr(client, method)(url, **kwargs)
            if resp.status_code == 429:
                wait_time = min(2 ** attempt * 1.5, 10)
                await asyncio.sleep(wait_time)
                continue
            resp.raise_for_status()
            return resp
        resp = await getattr(client, method)(url, **kwargs)
        resp.raise_for_status()
        return resp

    async def get_user(self) -> dict:
        """Get the authenticated user profile."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await self._request_with_retry(
                client, "get", f"{self.base_url}/user", headers=self._headers()
            )
            return resp.json()

    async def _fetch_page(
        self,
        client: httpx.AsyncClient,
        sem: asyncio.Semaphore,
        url: str,
        page: int,
    ) -> tuple[int, list[dict]]:
        async with sem:
            try:
                resp = await self._request_with_retry(
                    client,
                    "get",
                    f"{url}?page={page}",
                    headers=self._headers(),
                )
                data = resp.json()
                return page, data if isinstance(data, list) else []
            except Exception:
                log.warning("Failed to fetch page %d from %s", page, url)
                return page, []

    async def _identify_user_business(self, client: httpx.AsyncClient) -> str:
        """Determine the authenticated user's business from page 1 of alternate results."""
        url = f"{self.base_url}/admin/clientdashboard/get-analyte-results-alternate"
        resp = await self._request_with_retry(
            client, "get", f"{url}?page=1", headers=self._headers()
        )
        data = resp.json()
        if isinstance(data, list) and data:
            return data[0].get("business_name", "")
        return ""

    async def get_all_analyte_results_alternate(
        self,
        on_progress: object = None,
    ) -> list[dict]:
        """Fetch all pages of alternate analyte results for the authenticated
        user's business using concurrent requests.

        The alternate endpoint returns 1 sample per page and is scoped so that
        the authenticated user's business appears first.  We page forward until
        we hit a different business (or an empty page).

        ``on_progress`` is an optional ``async callable(fetched, total_est)``
        invoked after each batch so callers can report progress.
        """
        url = f"{self.base_url}/admin/clientdashboard/get-analyte-results-alternate"
        sem = asyncio.Semaphore(CONCURRENCY)
        all_results: list[dict] = []

        async with httpx.AsyncClient(timeout=PAGE_TIMEOUT) as client:
            user_business = await self._identify_user_business(client)
            if not user_business:
                log.warning("Could not determine user business; fetching page-1 only")
                return await self._get_single_page(client, url)

            log.info("[acs] User business identified as %r", user_business)

            page = 1
            done = False
            while not done:
                batch_end = page + CONCURRENCY
                tasks = [
                    self._fetch_page(client, sem, url, p)
                    for p in range(page, batch_end)
                ]
                results = await asyncio.gather(*tasks)

                for _pg, rows in sorted(results, key=lambda x: x[0]):
                    if not rows:
                        done = True
                        break
                    biz = rows[0].get("business_name", "")
                    if biz != user_business:
                        done = True
                        break
                    all_results.extend(rows)

                page = batch_end

                if on_progress:
                    await on_progress(len(all_results), page)

                log.info(
                    "[acs] Fetched %d results so far (through page %d)",
                    len(all_results),
                    page - 1,
                )

        log.info("[acs] Total alternate results fetched: %d", len(all_results))
        return all_results

    async def _get_single_page(
        self, client: httpx.AsyncClient, url: str
    ) -> list[dict]:
        resp = await self._request_with_retry(
            client, "get", url, headers=self._headers()
        )
        data = resp.json()
        return data if isinstance(data, list) else []

    async def get_analyte_results(self) -> list[dict]:
        """Get analyte test results (first page only — kept for quick checks)."""
        async with httpx.AsyncClient(timeout=60.0) as client:
            return await self._get_single_page(
                client,
                f"{self.base_url}/admin/clientdashboard/get-analyte-results",
            )

    async def get_analyte_results_alternate(self) -> list[dict]:
        """Get analyte results alternate (first page only — kept for quick checks)."""
        async with httpx.AsyncClient(timeout=60.0) as client:
            return await self._get_single_page(
                client,
                f"{self.base_url}/admin/clientdashboard/get-analyte-results-alternate",
            )

    async def get_panels(self) -> list[dict]:
        """Get all available test panels."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await self._request_with_retry(
                client,
                "get",
                f"{self.base_url}/getAllPanels",
                headers=self._headers(),
            )
            data = resp.json()
            return data if isinstance(data, list) else []

    async def get_report_units(self) -> list[str]:
        """Get available report units."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await self._request_with_retry(
                client,
                "get",
                f"{self.base_url}/report-units",
                headers=self._headers(),
            )
            data = resp.json()
            return data if isinstance(data, list) else []

    async def get_qr_clients(self) -> list[dict]:
        """Get QR-linked client businesses."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await self._request_with_retry(
                client,
                "get",
                f"{self.base_url}/qr-clients",
                headers=self._headers(),
            )
            data = resp.json()
            return data if isinstance(data, list) else []
