"""Product scraper router for pulling packaging product data from manufacturer websites."""

import json
import re
from typing import Optional
from html import unescape

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

# Map lowercase manufacturer names/aliases to their domain and platform type.
# "shopify" domains use the /products.json API; "magento" uses catalogsearch.
MANUFACTURER_CATALOG: dict[str, dict] = {
    "chubby gorilla": {"domain": "chubbygorilla.com", "platform": "magento"},
    "chubbygorilla": {"domain": "chubbygorilla.com", "platform": "magento"},
    "calyx": {"domain": "calyxcontainers.com", "platform": "shopify"},
    "calyx containers": {"domain": "calyxcontainers.com", "platform": "shopify"},
    "calyxcontainers": {"domain": "calyxcontainers.com", "platform": "shopify"},
    "crc": {"domain": "crccontainers.com", "platform": "shopify"},
    "crc containers": {"domain": "crccontainers.com", "platform": "shopify"},
    "loud lock": {"domain": "www.loudlock.com", "platform": "shopify"},
    "loudlock": {"domain": "www.loudlock.com", "platform": "shopify"},
    "dispensary supply": {"domain": "dispensarysupply.com", "platform": "shopify"},
    "kush supply": {"domain": "kushsupply.com", "platform": "shopify"},
    "kushsupply": {"domain": "kushsupply.com", "platform": "shopify"},
    "sana packaging": {"domain": "sanapackaging.com", "platform": "shopify"},
    "sana": {"domain": "sanapackaging.com", "platform": "shopify"},
    "n2 packaging": {"domain": "n2packagingsystems.com", "platform": "shopify"},
    "n2": {"domain": "n2packagingsystems.com", "platform": "shopify"},
}


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


async def _fetch_json(client: httpx.AsyncClient, url: str) -> dict:
    """Fetch a URL and return parsed JSON."""
    resp = await client.get(url, headers=HEADERS, follow_redirects=True, timeout=15.0)
    resp.raise_for_status()
    return resp.json()


def _strip_html(html_str: str) -> str:
    """Strip HTML tags and return plain text."""
    if not html_str:
        return ""
    soup = BeautifulSoup(html_str, "html.parser")
    return unescape(soup.get_text(separator=" ", strip=True))


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


def _score_product(product: dict, query_terms: list[str]) -> int:
    """Score a Shopify product by how well it matches query terms."""
    title_lower = (product.get("title") or "").lower()
    handle_lower = (product.get("handle") or "").lower()
    tags_lower = ""
    if isinstance(product.get("tags"), list):
        tags_lower = " ".join(product["tags"]).lower()
    body_lower = _strip_html(product.get("body_html") or "").lower()

    score = 0
    for term in query_terms:
        t = term.lower()
        if t in title_lower:
            score += 10
        if t in handle_lower:
            score += 5
        if t in tags_lower:
            score += 3
        if t in body_lower:
            score += 1
    return score


async def scrape_shopify(
    client: httpx.AsyncClient,
    domain: str,
    manufacturer: str,
    model_number: str,
) -> ProductResult:
    """Search a Shopify store for a product via the /products.json API."""
    result = ProductResult(manufacturer=manufacturer, model_number=model_number)

    try:
        all_products: list[dict] = []
        page = 1
        while True:
            url = f"https://{domain}/products.json?limit=250&page={page}"
            data = await _fetch_json(client, url)
            products = data.get("products", [])
            if not products:
                break
            all_products.extend(products)
            if len(products) < 250:
                break
            page += 1

        if not all_products:
            result.error = f"No products found on {domain}"
            return result

        query_terms = model_number.lower().split()
        scored = [(p, _score_product(p, query_terms)) for p in all_products]
        scored.sort(key=lambda x: x[1], reverse=True)

        best_product, best_score = scored[0]

        if best_score == 0:
            titles = ", ".join(p["title"] for p in all_products[:5])
            result.error = (
                f"No matching product found on {domain} for '{model_number}'. "
                f"Available products: {titles}"
            )
            return result

        result.product_name = best_product.get("title")
        result.description = _strip_html(best_product.get("body_html") or "")[:1500]
        result.source_url = f"https://{domain}/products/{best_product['handle']}"

        images: list[str] = []
        for img in best_product.get("images", []):
            src = img.get("src", "")
            if src and src not in images:
                images.append(src)
        result.image_urls = images[:10]

        specs: dict[str, str] = {}
        if best_product.get("product_type"):
            specs["Type"] = best_product["product_type"]
        if best_product.get("vendor"):
            specs["Vendor"] = best_product["vendor"]
        variants = best_product.get("variants", [])
        if variants:
            first_variant = variants[0]
            if first_variant.get("sku"):
                specs["SKU"] = first_variant["sku"]
            if first_variant.get("price"):
                specs["Price"] = f"${first_variant['price']}"
            if first_variant.get("weight") and first_variant.get("weight_unit"):
                specs["Weight"] = f"{first_variant['weight']} {first_variant['weight_unit']}"
            for opt in best_product.get("options", []):
                opt_name = opt.get("name", "")
                opt_values = opt.get("values", [])
                if opt_name and opt_values and opt_name != "Title":
                    specs[opt_name] = ", ".join(opt_values)

        result.specifications = specs

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            result.error = f"Product catalog not available on {domain}."
        else:
            result.error = f"Error accessing {domain}: {e.response.status_code}"
    except Exception as e:
        result.error = f"Scraping error: {str(e)}"

    return result


async def scrape_chubby_gorilla(client: httpx.AsyncClient, model_number: str) -> ProductResult:
    """Scrape Chubby Gorilla's Magento catalog for a product."""
    result = ProductResult(manufacturer="Chubby Gorilla", model_number=model_number)
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


async def scrape_by_domain_guess(
    client: httpx.AsyncClient,
    manufacturer: str,
    model_number: str,
) -> ProductResult:
    """Try to guess the manufacturer domain and scrape it."""
    result = ProductResult(manufacturer=manufacturer, model_number=model_number)
    clean_name = re.sub(r"[^a-z0-9]", "", manufacturer.lower())

    domain_guesses = [
        f"{clean_name}.com",
        f"www.{clean_name}.com",
        f"{clean_name}packaging.com",
        f"{clean_name}containers.com",
    ]

    for domain in domain_guesses:
        # First try Shopify products.json
        try:
            url = f"https://{domain}/products.json?limit=5"
            data = await _fetch_json(client, url)
            products = data.get("products", [])
            if products:
                return await scrape_shopify(client, domain, manufacturer, model_number)
        except Exception:
            pass

        # Then try HTML search
        try:
            search_url = f"https://{domain}/search?q={model_number.replace(' ', '+')}&type=product"
            soup = await _fetch_page(client, search_url)
            product_links = soup.select(
                "a[href*='/products/'], a[href*='/product/'], "
                "a.product-item-link, .product-card a"
            )
            for link in product_links[:3]:
                href = link.get("href", "")
                if not href:
                    continue
                if not href.startswith("http"):
                    href = f"https://{domain}{href}"
                try:
                    page_soup = await _fetch_page(client, href)
                    h1 = page_soup.select_one("h1")
                    if h1:
                        result.product_name = h1.get_text(strip=True)
                    desc = page_soup.select_one(
                        '[itemprop="description"], .product-description, '
                        '.product__description, .description'
                    )
                    if desc:
                        result.description = desc.get_text(strip=True)[:1500]
                    images = _extract_gallery_images(
                        page_soup,
                        '.product-media img, .product-image img, [itemprop="image"], .gallery img'
                    )
                    ld_images = _extract_ld_json_images(page_soup)
                    for img in ld_images:
                        if img not in images:
                            images.append(img)
                    result.image_urls = images[:10]
                    result.source_url = href
                    result.specifications = _extract_specs(page_soup)
                    if result.product_name and (result.image_urls or result.description):
                        return result
                except Exception:
                    continue
        except Exception:
            continue

    result.error = (
        f"Could not find manufacturer website for '{manufacturer}'. "
        "Try one of the supported manufacturers or check the spelling."
    )
    return result


@router.post("/scrape", response_model=ProductResult)
async def scrape_product(req: ScrapeRequest):
    """Scrape a manufacturer website for product images and descriptions."""
    manufacturer = req.manufacturer.strip()
    manufacturer_key = manufacturer.lower()
    model = req.model_number.strip()
    if not manufacturer_key or not model:
        raise HTTPException(status_code=400, detail="Both manufacturer and model_number are required")

    async with httpx.AsyncClient() as client:
        catalog_entry = MANUFACTURER_CATALOG.get(manufacturer_key)

        if catalog_entry:
            domain = catalog_entry["domain"]
            platform = catalog_entry["platform"]

            if platform == "magento" and domain == "chubbygorilla.com":
                result = await scrape_chubby_gorilla(client, model)
            elif platform == "shopify":
                result = await scrape_shopify(client, domain, manufacturer, model)
            else:
                result = await scrape_by_domain_guess(client, manufacturer, model)
        else:
            result = await scrape_by_domain_guess(client, manufacturer, model)

    return result


@router.get("/manufacturers")
async def list_manufacturers():
    """List supported manufacturers with direct scraping support."""
    seen_domains: dict[str, str] = {}
    for key, info in MANUFACTURER_CATALOG.items():
        domain = info["domain"]
        if domain not in seen_domains:
            seen_domains[domain] = key

    supported = []
    for domain, key in seen_domains.items():
        info = MANUFACTURER_CATALOG[key]
        supported.append({
            "name": key.replace("_", " ").title(),
            "domain": domain,
            "platform": info["platform"],
        })

    return {
        "supported": supported,
        "generic": (
            "Any manufacturer not listed above will be searched by guessing the website domain. "
            "For best results, use one of the supported manufacturer names."
        ),
    }
