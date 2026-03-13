import httpx
from typing import Optional
import base64
import asyncio

CLOVER_BASE_URL = "https://api.clover.com/v3"


class CloverClient:
    def __init__(self, merchant_id: str, api_token: str):
        self.merchant_id = merchant_id
        self.api_token = api_token
        self.base_url = f"{CLOVER_BASE_URL}/merchants/{merchant_id}"

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_token}"}

    async def _request_with_retry(self, client: httpx.AsyncClient, method: str, url: str, max_retries: int = 3, **kwargs) -> httpx.Response:
        """Make an HTTP request with retry logic for rate limiting (429)."""
        for attempt in range(max_retries):
            resp = await getattr(client, method)(url, **kwargs)
            if resp.status_code == 429:
                wait_time = min(2 ** attempt * 1.5, 10)  # 1.5s, 3s, 6s
                await asyncio.sleep(wait_time)
                continue
            resp.raise_for_status()
            return resp
        # Final attempt - let it raise
        resp = await getattr(client, method)(url, **kwargs)
        resp.raise_for_status()
        return resp

    async def get_items(self, limit: int = 1000, offset: int = 0, expand: str = "itemStock,categories") -> dict:
        """Get all inventory items with stock and category info."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            all_items = []
            current_offset = offset
            while True:
                resp = await self._request_with_retry(
                    client, "get",
                    f"{self.base_url}/items",
                    headers=self._headers(),
                    params={
                        "expand": expand,
                        "limit": limit,
                        "offset": current_offset,
                        "filter": "deleted=false",
                    },
                )
                data = resp.json()
                elements = data.get("elements", [])
                all_items.extend(elements)
                if len(elements) < limit:
                    break
                current_offset += limit
                await asyncio.sleep(0.3)  # Small delay between pages
            return {"elements": all_items}

    async def get_item(self, item_id: str) -> dict:
        """Get a single inventory item."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{self.base_url}/items/{item_id}",
                headers=self._headers(),
                params={"expand": "itemStock,categories"},
            )
            resp.raise_for_status()
            return resp.json()

    async def create_item(self, item_data: dict) -> dict:
        """Create a new inventory item."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self.base_url}/items",
                headers=self._headers(),
                json=item_data,
            )
            resp.raise_for_status()
            return resp.json()

    async def update_item(self, item_id: str, item_data: dict) -> dict:
        """Update an existing inventory item."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self.base_url}/items/{item_id}",
                headers=self._headers(),
                json=item_data,
            )
            resp.raise_for_status()
            return resp.json()

    async def update_item_stock(self, item_id: str, quantity: float) -> dict:
        """Update stock quantity for an item."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self.base_url}/item_stocks/{item_id}",
                headers=self._headers(),
                json={"quantity": quantity},
            )
            resp.raise_for_status()
            return resp.json()

    async def get_categories(self) -> dict:
        """Get all categories."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{self.base_url}/categories",
                headers=self._headers(),
                params={"limit": 1000},
            )
            resp.raise_for_status()
            return resp.json()

    async def get_orders(
        self,
        limit: int = 100,
        offset: int = 0,
        filter_str: Optional[str] = None,
        expand: str = "lineItems,customers",
    ) -> dict:
        """Get orders for sales tracking."""
        params: dict = {"limit": limit, "offset": offset, "expand": expand}
        if filter_str:
            params["filter"] = filter_str
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await self._request_with_retry(
                client, "get",
                f"{self.base_url}/orders",
                headers=self._headers(),
                params=params,
            )
            return resp.json()

    async def delete_item(self, item_id: str) -> None:
        """Delete an inventory item."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.delete(
                f"{self.base_url}/items/{item_id}",
                headers=self._headers(),
            )
            resp.raise_for_status()

    async def assign_category(self, item_id: str, category_id: str) -> dict:
        """Assign a category to an item."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self.base_url}/category_items",
                headers=self._headers(),
                json={"elements": [{"item": {"id": item_id}, "category": {"id": category_id}}]},
            )
            resp.raise_for_status()
            return resp.json()

    async def create_category(self, name: str) -> dict:
        """Create a new category."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self.base_url}/categories",
                headers=self._headers(),
                json={"name": name},
            )
            resp.raise_for_status()
            return resp.json()

    async def get_tax_rates(self) -> dict:
        """Get all tax rates."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{self.base_url}/tax_rates",
                headers=self._headers(),
                params={"limit": 100},
            )
            resp.raise_for_status()
            return resp.json()

    async def assign_tax_rate(self, item_id: str, tax_rate_id: str) -> dict:
        """Assign a tax rate to an item."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self.base_url}/tax_rate_items",
                headers=self._headers(),
                json={"elements": [{"item": {"id": item_id}, "taxRate": {"id": tax_rate_id}}]},
            )
            resp.raise_for_status()
            return resp.json()

    async def upload_item_image(self, item_id: str, image_data: bytes) -> dict:
        """Upload an image for an item."""
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self.base_url}/items/{item_id}/image",
                headers={"Authorization": f"Bearer {self.api_token}"},
                files={"image": ("product.png", image_data, "image/png")},
            )
            resp.raise_for_status()
            return resp.json()

    async def get_customers(self, limit: int = 100, offset: int = 0) -> dict:
        """Get all customers with phone numbers and emails."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            all_customers = []
            current_offset = offset
            while True:
                resp = await self._request_with_retry(
                    client, "get",
                    f"{self.base_url}/customers",
                    headers=self._headers(),
                    params={
                        "expand": "phoneNumbers,emailAddresses",
                        "limit": limit,
                        "offset": current_offset,
                    },
                )
                data = resp.json()
                elements = data.get("elements", [])
                all_customers.extend(elements)
                if len(elements) < limit:
                    break
                current_offset += limit
                await asyncio.sleep(0.5)  # Rate limit delay between pages
            return {"elements": all_customers}

    async def get_refunds(self, limit: int = 100, offset: int = 0) -> dict:
        """Get refunds for tracking returned items."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            all_refunds = []
            current_offset = offset
            while True:
                resp = await self._request_with_retry(
                    client, "get",
                    f"{self.base_url}/orders",
                    headers=self._headers(),
                    params={
                        "expand": "lineItems",
                        "limit": limit,
                        "offset": current_offset,
                        "filter": "payType!=NULL",
                        "orderBy": "createdTime DESC",
                    },
                )
                data = resp.json()
                elements = data.get("elements", [])
                # Filter to refund orders (negative or zero total, or has refund flag)
                for order in elements:
                    if order.get("isRefund") or (order.get("total", 0) < 0):
                        all_refunds.append(order)
                    else:
                        # Check for refunded line items within orders
                        line_items = order.get("lineItems", {}).get("elements", [])
                        for li in line_items:
                            if li.get("refunded") or li.get("isRefund"):
                                all_refunds.append(order)
                                break
                if len(elements) < limit:
                    break
                current_offset += limit
            return {"elements": all_refunds}

    async def get_merchant_info(self) -> dict:
        """Get merchant info."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{self.base_url}",
                headers=self._headers(),
            )
            resp.raise_for_status()
            return resp.json()

    # === Item Groups / Variants ===

    async def get_item_groups(self) -> dict:
        """Get all item groups (items with variants)."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await self._request_with_retry(
                client, "get",
                f"{self.base_url}/item_groups",
                headers=self._headers(),
                params={"expand": "attributes,attributes.options,items", "limit": 1000},
            )
            return resp.json()

    async def create_item_group(self, name: str) -> dict:
        """Create an item group for variants."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self.base_url}/item_groups",
                headers=self._headers(),
                json={"name": name},
            )
            resp.raise_for_status()
            return resp.json()

    async def delete_item_group(self, group_id: str) -> None:
        """Delete an item group."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.delete(
                f"{self.base_url}/item_groups/{group_id}",
                headers=self._headers(),
            )
            resp.raise_for_status()

    async def get_attributes(self) -> dict:
        """Get all attributes (e.g., Size, Color, Flavor)."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await self._request_with_retry(
                client, "get",
                f"{self.base_url}/attributes",
                headers=self._headers(),
                params={"expand": "options", "limit": 1000},
            )
            return resp.json()

    async def create_attribute(self, name: str, item_group_id: str) -> dict:
        """Create an attribute (e.g., Size) linked to an item group."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self.base_url}/attributes",
                headers=self._headers(),
                json={"name": name, "itemGroup": {"id": item_group_id}},
            )
            resp.raise_for_status()
            return resp.json()

    async def get_options_for_attribute(self, attribute_id: str) -> dict:
        """Get all options for an attribute."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await self._request_with_retry(
                client, "get",
                f"{self.base_url}/attributes/{attribute_id}/options",
                headers=self._headers(),
                params={"limit": 1000},
            )
            return resp.json()

    async def create_option(self, attribute_id: str, name: str) -> dict:
        """Create an option for an attribute (e.g., 'Small' for 'Size')."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self.base_url}/attributes/{attribute_id}/options",
                headers=self._headers(),
                json={"name": name},
            )
            resp.raise_for_status()
            return resp.json()

    async def delete_option(self, attribute_id: str, option_id: str) -> None:
        """Delete an option from an attribute."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.delete(
                f"{self.base_url}/attributes/{attribute_id}/options/{option_id}",
                headers=self._headers(),
            )
            resp.raise_for_status()

    async def associate_option_with_item(self, option_id: str, item_id: str) -> dict:
        """Associate an option with an item (link variant option to variant item)."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self.base_url}/option_items",
                headers=self._headers(),
                json={"elements": [{"option": {"id": option_id}, "item": {"id": item_id}}]},
            )
            resp.raise_for_status()
            return resp.json()
