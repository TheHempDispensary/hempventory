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
        params: dict | None = None,
    ) -> tuple[int, list[dict] | None]:
        """Fetch a single page.  Returns ``(page, rows)`` on success or
        ``(page, None)`` on transient failure so callers can distinguish
        errors from a genuine empty response (end-of-data)."""
        async with sem:
            try:
                query = {"page": page, **(params or {})}
                qs = "&".join(f"{k}={v}" for k, v in query.items())
                resp = await self._request_with_retry(
                    client,
                    "get",
                    f"{url}?{qs}",
                    headers=self._headers(),
                )
                data = resp.json()
                return page, data if isinstance(data, list) else []
            except Exception:
                log.warning("Failed to fetch page %d from %s", page, url)
                return page, None

    async def _get_client_id(self, client: httpx.AsyncClient) -> int | None:
        """Look up the user's primary business client_id via the
        ``allAvailableClients`` endpoint."""
        try:
            resp = await self._request_with_retry(
                client,
                "get",
                f"{self.base_url}/admin/clientdashboard/allAvailableClients",
                headers=self._headers(),
            )
            data = resp.json()
            if isinstance(data, list) and data:
                cid = data[0].get("id")
                name = data[0].get("business_name", data[0].get("name", ""))
                log.info("[acs] Resolved client_id=%s (%s)", cid, name)
                return cid
        except Exception:
            log.warning("[acs] Could not resolve client_id")
        return None

    async def get_all_samples(self) -> list[dict]:
        """Fetch all samples for the user's business via the ``/all`` endpoint.

        This endpoint returns batch-level sample records (one per accession)
        with metadata, status, and panel info.  It uses ``skip``/``take``
        pagination and supports the ``client`` parameter for business filtering.
        Returns all 359+ THE HEMP DISPENSARY batches.
        """
        all_samples: list[dict] = []
        take = 50

        async with httpx.AsyncClient(timeout=PAGE_TIMEOUT) as client:
            client_id = await self._get_client_id(client)
            if not client_id:
                log.warning("[acs] No client_id; cannot fetch from /all endpoint")
                return []

            skip = 0
            total = None
            while total is None or skip < total:
                url = (
                    f"{self.base_url}/admin/clientdashboard/all"
                    f"?client={client_id}&skip={skip}&take={take}"
                )
                try:
                    resp = await self._request_with_retry(
                        client, "get", url, headers=self._headers()
                    )
                    data = resp.json()
                except Exception:
                    log.warning("[acs] Failed to fetch /all skip=%d", skip)
                    break

                if not isinstance(data, dict):
                    break
                if total is None:
                    total = data.get("total", 0)
                    log.info("[acs] /all endpoint reports %d total samples", total)

                samples = data.get("samples", [])
                if not samples:
                    break
                all_samples.extend(samples)
                skip += take

        log.info("[acs] Fetched %d samples from /all endpoint", len(all_samples))
        return all_samples

    async def get_all_analyte_results_alternate(self, max_pages: int = 2000) -> list[dict]:
        """Fetch all alternate analyte results for the user's business.

        Uses ``client_id`` to filter to the authenticated user's business
        so only their products are returned.  The alternate endpoint returns
        1 sample per page; we paginate up to ``max_pages``.
        """
        url = f"{self.base_url}/admin/clientdashboard/get-analyte-results-alternate"
        sem = asyncio.Semaphore(CONCURRENCY)
        all_results: list[dict] = []

        async with httpx.AsyncClient(timeout=PAGE_TIMEOUT) as client:
            client_id = await self._get_client_id(client)
            extra_params = {"client_id": client_id} if client_id else {}
            if not client_id:
                log.warning("[acs] No client_id found; results may include other businesses")

            page = 1
            done = False
            while not done and page <= max_pages:
                batch_end = min(page + CONCURRENCY, max_pages + 1)
                tasks = [
                    self._fetch_page(client, sem, url, p, extra_params)
                    for p in range(page, batch_end)
                ]
                results = await asyncio.gather(*tasks)

                failed_pages: list[int] = []

                for pg, rows in sorted(results, key=lambda x: x[0]):
                    if rows is None:
                        failed_pages.append(pg)
                        continue
                    if not rows:
                        done = True
                        break
                    all_results.extend(rows)

                if failed_pages and not done:
                    log.info("[acs] Retrying %d failed pages", len(failed_pages))
                    retry_tasks = [
                        self._fetch_page(client, sem, url, p, extra_params)
                        for p in failed_pages
                    ]
                    retry_results = await asyncio.gather(*retry_tasks)
                    for pg, rows in sorted(retry_results, key=lambda x: x[0]):
                        if rows is not None and rows:
                            all_results.extend(rows)

                page = batch_end
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
