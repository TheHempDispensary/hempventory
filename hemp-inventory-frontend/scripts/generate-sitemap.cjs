#!/usr/bin/env node

/**
 * Generates public/sitemap.xml by fetching product slugs from the backend API
 * and combining them with static page URLs.
 *
 * Usage:  node scripts/generate-sitemap.cjs
 */

const https = require("https");
const fs = require("fs");
const path = require("path");

const API_URL =
  "https://thd-inventory-api.fly.dev/api/ecommerce/products?limit=10000";
const BASE_URL = "https://www.thehempdispensary.com";
const OUTPUT_PATH = path.resolve(__dirname, "../public/sitemap.xml");

const today = new Date().toISOString().split("T")[0]; // YYYY-MM-DD

const STATIC_PAGES = [
  { loc: "/", changefreq: "weekly", priority: "1.0" },
  { loc: "/shop", changefreq: "weekly", priority: "0.9" },
  { loc: "/shop/flower", changefreq: "weekly", priority: "0.9" },
  { loc: "/shop/edibles", changefreq: "weekly", priority: "0.9" },
  { loc: "/shop/concentrates", changefreq: "weekly", priority: "0.9" },
  { loc: "/shop/vapes", changefreq: "weekly", priority: "0.9" },
  { loc: "/shop/tinctures", changefreq: "weekly", priority: "0.9" },
  { loc: "/shop/topicals", changefreq: "weekly", priority: "0.9" },
  { loc: "/shop/accessories", changefreq: "weekly", priority: "0.9" },
  { loc: "/about", changefreq: "monthly", priority: "0.6" },
  { loc: "/on-sale", changefreq: "weekly", priority: "0.9" },
  { loc: "/exotic_thca_flower", changefreq: "weekly", priority: "0.9" },
];

function fetchJSON(url, timeoutMs = 30000) {
  return new Promise((resolve, reject) => {
    const req = https
      .get(url, (res) => {
        if (res.statusCode < 200 || res.statusCode >= 300) {
          reject(new Error(`API returned HTTP ${res.statusCode}`));
          res.resume();
          return;
        }
        let data = "";
        res.on("data", (chunk) => (data += chunk));
        res.on("end", () => {
          try {
            resolve(JSON.parse(data));
          } catch (e) {
            reject(new Error(`Failed to parse JSON: ${e.message}`));
          }
        });
      })
      .on("error", reject);
    req.setTimeout(timeoutMs, () => {
      req.destroy();
      reject(new Error(`Request timed out after ${timeoutMs}ms`));
    });
  });
}

function escapeXml(str) {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

function buildUrlEntry({ loc, lastmod, changefreq, priority }) {
  return `  <url>
    <loc>${escapeXml(loc)}</loc>
    <lastmod>${lastmod}</lastmod>
    <changefreq>${changefreq}</changefreq>
    <priority>${priority}</priority>
  </url>`;
}

async function main() {
  console.log("Fetching products from API...");
  let products = [];
  try {
    const data = await fetchJSON(API_URL);
    products = data.products || [];
    console.log(`Fetched ${products.length} products.`);
  } catch (err) {
    console.warn(`WARNING: Could not fetch products: ${err.message}`);
    console.warn("Generating sitemap with static pages only.");
  }

  const entries = [];

  // Static pages
  for (const page of STATIC_PAGES) {
    entries.push(
      buildUrlEntry({
        loc: `${BASE_URL}${page.loc}`,
        lastmod: today,
        changefreq: page.changefreq,
        priority: page.priority,
      })
    );
  }

  // Product pages
  for (const product of products) {
    if (!product.slug) continue;
    entries.push(
      buildUrlEntry({
        loc: `${BASE_URL}/products/product/${product.slug}`,
        lastmod: today,
        changefreq: "weekly",
        priority: "0.8",
      })
    );
  }

  const xml = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${entries.join("\n")}
</urlset>
`;

  fs.writeFileSync(OUTPUT_PATH, xml, "utf-8");
  console.log(`Sitemap written to ${OUTPUT_PATH}`);
  console.log(`Total URLs: ${entries.length}`);
}

main().catch((err) => {
  console.error("Error generating sitemap:", err);
  process.exit(1);
});
