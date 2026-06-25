import httpx
import asyncio
import os


ACS_BASE_URL = "https://portal.acslabcannabis.com/api"


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

    async def get_analyte_results(self) -> list[dict]:
        """Get analyte test results for all samples."""
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await self._request_with_retry(
                client,
                "get",
                f"{self.base_url}/admin/clientdashboard/get-analyte-results",
                headers=self._headers(),
            )
            data = resp.json()
            return data if isinstance(data, list) else []

    async def get_analyte_results_alternate(self) -> list[dict]:
        """Get analyte results in alternate format (includes homogeneity)."""
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await self._request_with_retry(
                client,
                "get",
                f"{self.base_url}/admin/clientdashboard/get-analyte-results-alternate",
                headers=self._headers(),
            )
            data = resp.json()
            return data if isinstance(data, list) else []

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
