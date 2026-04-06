"""Product scraper router for pulling packaging product data from manufacturer websites."""

import json
import re
import urllib.parse
from typing import Optional

import httpx
from bs4 import BeautifulSoup
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/scraper", tags=["scraper"])

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

MANUFACTURER_DOMAINS: dict[str, str] = {
    "chubby gorilla": "chubbygorilla.com",
    "chubbygorilla": "chubbygorilla.com",
}

# Known packaging distributor sites to search directly
DISTRIBUTOR_SITES = [
    "gamutpackaging.com",
    "tricorbraun.com",
    "liquidbottles.com",
    "humiditypacks.com",
    "calyxcontainers.com",
    "berlinpackaging.com",
    "sfrpackaging.com",
]


class ScrapeRequest(BaseModel):
    manufacturer: str
    model_number: str


class ProductResult(BaseModel):
    manufacturer: str
    model_number: str
    product_name: Optional[str] = None
    description: Optional[str] = None
    image_urls: list[str] = []
    specifications: dict = {}
    source_url: Optional[str] = None
    error: Optional[str] = None


async def _fetch_page(client: httpx.AsyncClient, url: str) -> BeautifulSoup:
    """Fetch a URL and return parsed BeautifulSoup."""
    resp = await client.get(url, headers=HEADERS, follow_redirects=True, timeout=15.0)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def _extract_ld_json_images(soup: BeautifulSoup) -> list[str]:
    """Extract image URLs from JSON-LD structured data."""
    images: list[str] = []
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            ld = json.loads(script.string or "")
            if isinstance(ld, dict) and "image" in ld:
                img_data = ld["image"]
                if isinstance(img_data, str) and img_data not in images:
                    images.append(img_data)
                elif isinstance(img_data, list):
                    for i in img_data:
                        if isinstance(i, str) and i not in images:
                            images.append(i)
        except (json.JSONDecodeError, TypeError):
            pass
    return images


def _extract_gallery_images(soup: BeautifulSoup, selectors: str) -> list[str]:
    """Extract image URLs from gallery elements using CSS selectors."""
    images: list[str] = []
    for img in soup.select(selectors):
        src = img.get("src") or img.get("data-src") or img.get("data-zoom-image")
        if src:
            if src.startswith("//"):
                src = "https:" + src
            if src.startswith("http") and "placeholder" not in src.lower() and src not in images:
                images.append(src)
    og_img = soup.select_one('meta[property="og:image"]')
    if og_img:
        src = og_img.get("content", "")
        if src and src not in images:
            images.insert(0, src)
    return images


def _extract_specs(soup: BeautifulSoup) -> dict:
    """Extract product specifications from table rows."""
    specs: dict[str, str] = {}
    for row in soup.select(
        ".additional-attributes tr, .product-attributes tr, "
        "table.data tr, table tr, .specs tr"
    ):
        cells = row.select("td, th")
        if len(cells) >= 2:
            key = cells[0].get_text(strip=True)
            val = cells[1].get_text(strip=True)
            if key and val and key != val:
                specs[key] = val
    return specs


async def scrape_chubby_gorilla(client: httpx.AsyncClient, model_number: str) -> ProductResult:
    """Scrape Chubby Gorilla's Magento catalog for a product."""
    result = ProductResult(manufacturer="Chubby Gorilla", model_number=model_number)
    search_url = f"https://chubbygorilla.com/catalogsearch/result/?q={httpx.QueryParams({'q': model_number})}"
    # Clean URL - just use the query param directly
    search_url = f"https://chubbygorilla.com/catalogsearch/result/?q={model_number.replace(' ', '+')}"

    try:
        soup = await _fetch_page(client, search_url)

        # Find product links in search results
        product_links = soup.select("a.product-item-link, .product-item-info a.product-item-photo")
        if product_links:
            first_link = product_links[0].get("href")
            if first_link:
                soup = await _fetch_page(client, first_link)
                result.source_url = first_link

        # Extract product name
        name_el = soup.select_one("h1.page-title span, h1.page-title, .product-info-main h1")
        if name_el:
            result.product_name = name_el.get_text(strip=True)

        # Extract description
        desc_el = soup.select_one(
            ".product.attribute.description .value, "
            ".product.attribute.overview .value, "
            "#description .value"
        )
        if desc_el:
            result.description = desc_el.get_text(strip=True)

        # Extract images from gallery
        images = _extract_gallery_images(
            soup,
            ".fotorama__stage img, .gallery-placeholder img, .product.media img"
        )

        # Also check JSON-LD
        ld_images = _extract_ld_json_images(soup)
        for img in ld_images:
            if img not in images:
                images.append(img)

        # Fallback: regex for Magento gallery JSON
        if not images:
            page_text = str(soup)
            img_matches = re.findall(r'"full":"([^"]+)"', page_text)
            for m in img_matches:
                url = m.replace("\\/", "/")
                if url not in images:
                    images.append(url)

        result.image_urls = images

        # Extract specifications
        specs = _extract_specs(soup)
        sku_el = soup.select_one('.product.attribute.sku .value, [itemprop="sku"]')
        if sku_el:
            specs["SKU"] = sku_el.get_text(strip=True)
        result.specifications = specs

        if not result.product_name and not result.image_urls:
            result.error = "No product found on chubbygorilla.com for this search term"

    except Exception as e:
        result.error = f"Scraping error: {str(e)}"

    return result


async def _search_duckduckgo(client: httpx.AsyncClient, query: str) -> list[str]:
    """Search DuckDuckGo HTML and return result URLs."""
    urls: list[str] = []
    search_url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote_plus(query)}"
    try:
        resp = await client.get(search_url, headers=HEADERS, follow_redirects=True, timeout=15.0)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for link in soup.select("a.result__a"):
            href = link.get("href", "")
            # DDG wraps links as //duckduckgo.com/l/?uddg=<encoded_url>
            if "uddg=" in href:
                parsed = urllib.parse.urlparse(href)
                params = urllib.parse.parse_qs(parsed.query)
                real_url = params.get("uddg", [""])[0]
                if real_url and real_url.startswith("http") and "duckduckgo.com" not in real_url:
                    urls.append(real_url)
            elif href and href.startswith("http") and "duckduckgo.com" not in href:
                urls.append(href)
    except Exception:
        pass
    return urls


async def _scrape_product_page(
    client: httpx.AsyncClient, url: str, result: ProductResult
) -> bool:
    """Scrape a product page for name, description, images, specs. Returns True if useful data found."""
    try:
        page_soup = await _fetch_page(client, url)
        h1 = page_soup.select_one("h1")
        if h1:
            result.product_name = h1.get_text(strip=True)

        desc = page_soup.select_one(
            '[itemprop="description"], .product-description, '
            ".product__description, .product-info-description, "
            "#tab-description, .description"
        )
        if desc:
            result.description = desc.get_text(strip=True)[:1500]

        images = _extract_gallery_images(
            page_soup,
            '.product-media img, .product-image img, [itemprop="image"], '
            '.gallery img, .product__media img, .product-single__photo img'
        )
        ld_images = _extract_ld_json_images(page_soup)
        for img in ld_images:
            if img not in images:
                images.append(img)
        result.image_urls = images[:10]
        result.source_url = url
        result.specifications = _extract_specs(page_soup)

        if result.product_name and (result.image_urls or result.description):
            return True
    except Exception:
        pass
    return False


async def scrape_distributor_sites(
    client: httpx.AsyncClient, manufacturer: str, model_number: str
) -> ProductResult:
    """Search distributor sites via DuckDuckGo for the product."""
    result = ProductResult(manufacturer=manufacturer, model_number=model_number)
    query = f"{manufacturer} {model_number}"

    # Build a query targeting known distributor sites
    site_query = " OR ".join(f"site:{s}" for s in DISTRIBUTOR_SITES)
    search_query = f"{query} ({site_query})"

    try:
        urls = await _search_duckduckgo(client, search_query)
        for url in urls[:5]:
            if await _scrape_product_page(client, url, result):
                return result
    except Exception:
        pass

    if not result.product_name and not result.image_urls:
        result.error = "No product found on distributor sites."
    return result


async def scrape_generic(
    client: httpx.AsyncClient, manufacturer: str, model_number: str
) -> ProductResult:
    """Try distributor sites first, then fall back to a general DuckDuckGo search."""
    # First try distributor sites
    result = await scrape_distributor_sites(client, manufacturer, model_number)
    if result.product_name and (result.image_urls or result.description):
        return result

    # Fall back to general search
    result = ProductResult(manufacturer=manufacturer, model_number=model_number)
    query = f"{manufacturer} {model_number} packaging"
    try:
        urls = await _search_duckduckgo(client, query)
        for url in urls[:5]:
            if await _scrape_product_page(client, url, result):
                return result
    except Exception as e:
        result.error = f"Search error: {str(e)}"

    if not result.product_name and not result.image_urls:
        result.error = "No product found via search. Try a more specific model number or manufacturer name."

    return result


@router.post("/scrape", response_model=ProductResult)
async def scrape_product(req: ScrapeRequest):
    """Scrape a manufacturer website for product images and descriptions."""
    manufacturer = req.manufacturer.strip().lower()
    model = req.model_number.strip()
    if not manufacturer or not model:
        raise HTTPException(status_code=400, detail="Both manufacturer and model_number are required")

    async with httpx.AsyncClient() as client:
        if manufacturer in MANUFACTURER_DOMAINS:
            domain = MANUFACTURER_DOMAINS[manufacturer]
            if domain == "chubbygorilla.com":
                result = await scrape_chubby_gorilla(client, model)
            else:
                result = await scrape_generic(client, req.manufacturer, model)
        else:
            result = await scrape_generic(client, req.manufacturer, model)

    return result


@router.get("/manufacturers")
async def list_manufacturers():
    """List supported manufacturers with direct scraping support."""
    return {
        "supported": [
            {
                "name": "Chubby Gorilla",
                "domain": "chubbygorilla.com",
                "note": "Direct catalog search by product name or SKU",
            }
        ],
        "generic": "Any manufacturer not listed above will be searched via distributor sites and DuckDuckGo.",
    }
