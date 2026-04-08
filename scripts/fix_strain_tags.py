#!/usr/bin/env python3
"""
Fix strain tagging issues:
1. Apply definitive manual corrections for low-confidence items
2. Retry 5 failed saves
3. Retry 4 API error items via Anthropic
4. Fix inconsistent tags across product variants
5. Generate updated report
"""

import os
import json
import re
import time
import httpx
import asyncio
from collections import defaultdict

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
API_BASE = "https://thd-inventory-api.fly.dev"

# ── MANUAL CORRECTIONS (Issue 1) ──────────────────────────────
# These are definitive — no API call needed.
# Format: partial product name match → (strain_type, effect_tag)

MANUAL_CORRECTIONS = {
    # Sativa = Energy
    "BLUE DREAM": ("Sativa", "Energy"),
    "BURBERRY": ("Hybrid", "Energy"),  # sativa-dominant hybrid w/ Blue Dream genetics
    "CBD FULL SPECTRUM WAX TWO GRAMS BLUE DREAM": ("Sativa", "Energy"),
    "CBD ROSIN ONE GRAM SATIVA ECTO COOLER": ("Sativa", "Energy"),
    "CBD VAPE CARTRIDGE ONE GRAM SATIVA TROPICANA COOKIES": ("Sativa", "Energy"),
    "GREEN CRACK SNOWCAPS": ("Sativa", "Energy"),
    "HAWAIIAN SMALLS": ("Sativa", "Energy"),
    "TANGELO DIAMONDS": ("Sativa", "Energy"),
    "TRAINWRECK CRUMBLE": ("Sativa", "Energy"),
    "WHITE WIDOW CRUMBLE": ("Sativa", "Energy"),
    "BLUE SCREAM BADDER": ("Hybrid", "Energy"),  # sativa-dominant hybrid

    # Indica = Sleep
    "GRANDDADDY PURPLE": ("Indica", "Sleep"),
    "TAHOE OG": ("Indica", "Sleep"),
    "KING LOUIS": ("Indica", "Sleep"),
    "9 LB HAMMER": ("Indica", "Sleep"),
    "9 Lb Hammer": ("Indica", "Sleep"),
    "CBD VAPE CARTRIDGE ONE GRAM INDICA GRANDDADDY PURPLE": ("Indica", "Sleep"),
    "CBD VAPE CARTRIDGE ONE GRAM INDICA KING LOUIS XIII": ("Indica", "Sleep"),
    "DELTA 8 THC DISPOSABLE VAPE ONE GRAM INDICA KING LOUIS": ("Indica", "Sleep"),

    # Hybrid = Relax
    "BANANA RUNTZ": ("Hybrid", "Relax"),
    "CBG/CBD FLOWER GELATO HYBRID": ("Hybrid", "Relax"),
    "SUPER BOOF CRUMBLE": ("Hybrid", "Relax"),
    "HONEY BANANAS LIVE ROSIN": ("Hybrid", "Relax"),
    "ICE CREAM COOKIES": ("Hybrid", "Relax"),
    "THC DISPOSABLE VAPE ONE GRAM HYBRID CEREAL MILK": ("Hybrid", "Relax"),
    "THC VAPE CARTRIDGE ONE GRAM HYBRID GRAPEFRUIT ROMULAN": ("Hybrid", "Relax"),
    "THC WAX THREE GRAMS HYBRID TWISTED CITRUS": ("Hybrid", "Relax"),
    "CBD ROSIN ONE GRAM MAI TAI": ("Hybrid", "Relax"),
    "GUAVA": ("Hybrid", "Relax"),

    # Hybrid = Energy (sativa-dominant hybrids)
    "DELTA 9 THC COLD BREW COFFEE": ("N/A", "Energy"),

    # Focus
    "CBD/CBG GUMMIES FRUIT VARIETY": ("N/A", "Focus"),
    "CBD/CBG/CBN GUMMIES": ("N/A", "Relax"),  # multi-blend → Relax

    # Skip (cleaning product)
    "CBG HEMP PEPPERMINT ALL-PURPOSE CLEANER": ("SKIP", "SKIP"),
}

# Products that explicitly need retry (failed saves)
RETRY_PRODUCTS = [
    "MAC FLURRY EVERYDAY 3.5 GRAMS",
    "MAC FLURRY EVERYDAY 7 GRAMS",
    "PRESIDENTIAL RUNTZ EVERYDAY 7 GRAMS",
    "PURPLE PUSH POP BADDER ESSENTIAL 1 GRAM",
    "PURPLE PUSH POP BADDER ESSENTIAL 2 GRAMS",
]

# API error products to retry via Anthropic
API_RETRY_PRODUCTS = [
    "GRAPE GAS LIVE ROSIN 2 GRAMS",
    "MAC FLURRY EVERYDAY",
    "PINK CERTZ LIVE RESIN EVERYDAY 2 GRAMS",
    "ROCKSTAR EVERYDAY 3.5 GRAMS",
]

SYSTEM_PROMPT = """You are an expert cannabis sommelier and budtender with 15+ years of experience.

For the given product, determine:
1. STRAIN_TYPE: Exactly one of: Sativa, Indica, Hybrid, or N/A
2. EFFECT_TAG: Exactly one of: Energy, Sleep, Relax, Focus

Respond ONLY with valid JSON:
{"strain_type": "Sativa|Indica|Hybrid|N/A", "effect_tag": "Energy|Sleep|Relax|Focus", "confidence": "high|medium|low", "reasoning": "one sentence"}"""


def match_manual_correction(product_name: str):
    """Check if a product matches any manual correction rule."""
    name_upper = product_name.upper()
    
    # Try exact matches first (longer keys), then partial
    for key in sorted(MANUAL_CORRECTIONS.keys(), key=len, reverse=True):
        if key.upper() in name_upper:
            return MANUAL_CORRECTIONS[key]
    return None


def extract_strain_name(product_name: str) -> str:
    """Extract the strain/base name from a product by stripping size/weight/format suffixes."""
    name = product_name.strip()
    # Remove common size/weight patterns
    name = re.sub(r'\s+\d+(\.\d+)?\s*(GRAMS?|G|MG|OZ|ML|COUNT|PACK)\s*$', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\s+\d+\s*$', '', name)  # trailing numbers
    # Remove batch identifiers
    name = re.sub(r'\s+BATCH\s+\S+$', '', name, flags=re.IGNORECASE)
    return name.strip()


async def get_auth_token(client: httpx.AsyncClient) -> str:
    resp = await client.post(
        f"{API_BASE}/api/auth/login",
        json={"username": "admin", "password": "hempdispensary2026"},
        timeout=15,
    )
    data = resp.json()
    return data.get("access_token", "")


async def fetch_products(client: httpx.AsyncClient):
    resp = await client.get(f"{API_BASE}/api/ecommerce/products", timeout=30)
    data = resp.json()
    return data.get("products", [])


async def fetch_attributes(client: httpx.AsyncClient, token: str):
    resp = await client.get(
        f"{API_BASE}/api/inventory/product-attributes",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    data = resp.json()
    return data.get("attributes", [])


async def save_attributes(client: httpx.AsyncClient, token: str, sku: str, product_name: str, effect: str, strength: str, product_type: str):
    try:
        resp = await client.put(
            f"{API_BASE}/api/inventory/product-attributes/{sku}",
            json={
                "product_name": product_name,
                "effect": effect,
                "strength": strength,
                "product_type": product_type,
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        return resp.status_code == 200
    except Exception as e:
        print(f"  Save error for {product_name}: {e}")
        return False


async def classify_with_anthropic(client: httpx.AsyncClient, product_name: str, category: str = "", description: str = ""):
    """Call Anthropic API to classify a product."""
    user_msg = f"Product name: {product_name}"
    if category:
        user_msg += f"\nCategory: {category}"
    if description:
        user_msg += f"\nDescription: {description[:500]}"
    
    try:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 256,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": user_msg}],
            },
            timeout=30,
        )
        if resp.status_code != 200:
            print(f"  API error {resp.status_code}: {resp.text[:200]}")
            return None
        
        data = resp.json()
        text = data["content"][0]["text"].strip()
        return json.loads(text)
    except Exception as e:
        print(f"  Error classifying {product_name}: {e}")
        return None


def get_strength(effect_tag: str) -> str:
    return {"Energy": "High", "Sleep": "Medium", "Relax": "Medium", "Focus": "Medium"}.get(effect_tag, "Medium")


async def main():
    stats = {
        "manual_applied": 0,
        "retries_succeeded": 0,
        "retries_failed": 0,
        "api_retries_succeeded": 0,
        "api_retries_failed": 0,
        "variant_fixes": 0,
        "skipped_cleaning": 0,
        "strain_counts": defaultdict(int),
        "effect_counts": defaultdict(int),
        "inconsistencies_found": [],
        "inconsistencies_fixed": [],
        "all_corrections": [],
    }

    async with httpx.AsyncClient(timeout=30) as client:
        # Auth
        token = await get_auth_token(client)
        if not token:
            print("ERROR: Could not get auth token!")
            return
        print("Auth token obtained.")

        # Fetch all products
        products = await fetch_products(client)
        print(f"Fetched {len(products)} products from ecommerce API.")

        # Build product lookup by name
        product_by_name = {}
        for p in products:
            product_by_name[p["name"]] = p

        # Fetch existing attributes
        existing_attrs = await fetch_attributes(client, token)
        print(f"Fetched {len(existing_attrs)} existing attribute records.")
        attrs_by_sku = {a["sku"]: a for a in existing_attrs}
        attrs_by_name = {a["product_name"]: a for a in existing_attrs}

        # ── PHASE 1: Apply manual corrections ──────────────────
        print("\n═══ PHASE 1: Apply manual corrections ═══")
        
        for p in products:
            name = p["name"]
            sku = p.get("sku", "")
            correction = match_manual_correction(name)
            
            if correction is None:
                continue
            
            strain_type, effect_tag = correction
            
            if strain_type == "SKIP":
                print(f"  SKIP (not consumable): {name}")
                stats["skipped_cleaning"] += 1
                continue
            
            product_type_val = strain_type if strain_type != "N/A" else ""
            strength = get_strength(effect_tag)
            
            # Check if already correctly tagged
            existing = attrs_by_sku.get(sku) or attrs_by_name.get(name)
            if existing and existing.get("effect") == effect_tag and existing.get("product_type") == product_type_val:
                continue  # Already correct
            
            saved = await save_attributes(client, token, sku, name, effect_tag, strength, product_type_val)
            if saved:
                stats["manual_applied"] += 1
                stats["all_corrections"].append({"name": name, "strain": strain_type, "effect": effect_tag, "source": "manual"})
                print(f"  ✓ {name} → {strain_type} / {effect_tag}")
            else:
                print(f"  ✗ FAILED: {name}")
                stats["retries_failed"] += 1

        print(f"\nManual corrections applied: {stats['manual_applied']}")

        # ── PHASE 2: Retry failed saves ──────────────────
        print("\n═══ PHASE 2: Retry failed saves ═══")
        
        for retry_name in RETRY_PRODUCTS:
            # Find the product in the product list
            matched_product = None
            for p in products:
                if p["name"].upper() == retry_name.upper():
                    matched_product = p
                    break
            
            if not matched_product:
                # Try partial match
                for p in products:
                    if retry_name.upper() in p["name"].upper():
                        matched_product = p
                        break
            
            if not matched_product:
                print(f"  Product not found: {retry_name}")
                stats["retries_failed"] += 1
                continue
            
            name = matched_product["name"]
            sku = matched_product.get("sku", "")
            
            # Use Anthropic to classify
            categories = matched_product.get("categories", [])
            description = matched_product.get("description", "")
            result = await classify_with_anthropic(client, name, ", ".join(categories), description)
            
            if result:
                strain_type = result.get("strain_type", "N/A")
                effect_tag = result.get("effect_tag", "Relax")
                product_type_val = strain_type if strain_type != "N/A" else ""
                strength = get_strength(effect_tag)
                
                saved = await save_attributes(client, token, sku, name, effect_tag, strength, product_type_val)
                if saved:
                    stats["retries_succeeded"] += 1
                    stats["all_corrections"].append({"name": name, "strain": strain_type, "effect": effect_tag, "source": "retry"})
                    print(f"  ✓ {name} → {strain_type} / {effect_tag}")
                else:
                    stats["retries_failed"] += 1
                    print(f"  ✗ Save failed: {name}")
            else:
                stats["retries_failed"] += 1
                print(f"  ✗ API failed: {name}")
            
            await asyncio.sleep(0.5)

        # ── PHASE 3: Retry API error items ──────────────────
        print("\n═══ PHASE 3: Retry API error items ═══")
        
        for retry_name in API_RETRY_PRODUCTS:
            matched_products = []
            for p in products:
                if retry_name.upper() in p["name"].upper():
                    matched_products.append(p)
            
            if not matched_products:
                print(f"  Product not found: {retry_name}")
                stats["api_retries_failed"] += 1
                continue
            
            # Classify just once using the first match
            mp = matched_products[0]
            result = await classify_with_anthropic(client, mp["name"], ", ".join(mp.get("categories", [])), mp.get("description", ""))
            
            if result:
                strain_type = result.get("strain_type", "N/A")
                effect_tag = result.get("effect_tag", "Relax")
                product_type_val = strain_type if strain_type != "N/A" else ""
                strength = get_strength(effect_tag)
                
                # Apply to ALL matching products (all sizes)
                for mp2 in matched_products:
                    saved = await save_attributes(client, token, mp2.get("sku", ""), mp2["name"], effect_tag, strength, product_type_val)
                    if saved:
                        stats["api_retries_succeeded"] += 1
                        stats["all_corrections"].append({"name": mp2["name"], "strain": strain_type, "effect": effect_tag, "source": "api_retry"})
                        print(f"  ✓ {mp2['name']} → {strain_type} / {effect_tag}")
                    else:
                        stats["api_retries_failed"] += 1
                        print(f"  ✗ Save failed: {mp2['name']}")
            else:
                stats["api_retries_failed"] += 1
                print(f"  ✗ API failed for all: {retry_name}")
            
            await asyncio.sleep(0.5)

        # ── PHASE 4: Fix variant inconsistencies ──────────────────
        print("\n═══ PHASE 4: Fix variant inconsistencies ═══")
        
        # Re-fetch attributes after all corrections
        updated_attrs = await fetch_attributes(client, token)
        
        # Group by extracted strain name
        strain_groups = defaultdict(list)
        for attr in updated_attrs:
            base_name = extract_strain_name(attr["product_name"])
            strain_groups[base_name].append(attr)
        
        # Find inconsistencies
        for base_name, variants in strain_groups.items():
            if len(variants) < 2:
                continue
            
            effects = set(v["effect"] for v in variants if v["effect"])
            types = set(v["product_type"] for v in variants if v["product_type"])
            
            if len(effects) > 1 or len(types) > 1:
                stats["inconsistencies_found"].append({
                    "strain": base_name,
                    "variants": [{"name": v["product_name"], "effect": v["effect"], "type": v["product_type"]} for v in variants],
                })
                
                # Determine the correct tag — majority vote, preferring manual corrections
                effect_votes = defaultdict(int)
                type_votes = defaultdict(int)
                for v in variants:
                    if v["effect"]:
                        effect_votes[v["effect"]] += 1
                    if v["product_type"]:
                        type_votes[v["product_type"]] += 1
                
                correct_effect = max(effect_votes, key=effect_votes.get) if effect_votes else "Relax"
                correct_type = max(type_votes, key=type_votes.get) if type_votes else ""
                
                # Check if any manual correction applies
                for v in variants:
                    correction = match_manual_correction(v["product_name"])
                    if correction:
                        strain_type, effect_tag = correction
                        if strain_type != "SKIP":
                            correct_type = strain_type if strain_type != "N/A" else ""
                            correct_effect = effect_tag
                        break
                
                # Apply uniform tags to all variants
                for v in variants:
                    if v["effect"] != correct_effect or v["product_type"] != correct_type:
                        strength = get_strength(correct_effect)
                        saved = await save_attributes(client, token, v["sku"], v["product_name"], correct_effect, strength, correct_type)
                        if saved:
                            stats["variant_fixes"] += 1
                            stats["inconsistencies_fixed"].append({
                                "name": v["product_name"],
                                "old_effect": v["effect"],
                                "old_type": v["product_type"],
                                "new_effect": correct_effect,
                                "new_type": correct_type,
                            })
                            print(f"  ✓ Fixed: {v['product_name']} → {correct_type}/{correct_effect} (was {v['product_type']}/{v['effect']})")
                        else:
                            print(f"  ✗ Failed to fix: {v['product_name']}")

        # ── PHASE 5: Final consistency check ──────────────────
        print("\n═══ PHASE 5: Final consistency check ═══")
        
        final_attrs = await fetch_attributes(client, token)
        final_groups = defaultdict(list)
        for attr in final_attrs:
            base_name = extract_strain_name(attr["product_name"])
            final_groups[base_name].append(attr)
        
        remaining_inconsistencies = []
        for base_name, variants in final_groups.items():
            if len(variants) < 2:
                continue
            effects = set(v["effect"] for v in variants if v["effect"])
            types = set(v["product_type"] for v in variants if v["product_type"])
            if len(effects) > 1 or len(types) > 1:
                remaining_inconsistencies.append({
                    "strain": base_name,
                    "variants": [{"name": v["product_name"], "effect": v["effect"], "type": v["product_type"]} for v in variants],
                })
        
        # Count final totals
        for attr in final_attrs:
            effect = attr.get("effect", "")
            ptype = attr.get("product_type", "")
            if effect:
                stats["effect_counts"][effect] += 1
            strain = ptype if ptype else "N/A"
            stats["strain_counts"][strain] += 1

        # ── Generate Report ──────────────────
        total_tagged = len(final_attrs)
        
        report = f"""# Strain Tagging Fix Report

## Summary
- **Total products now tagged**: {total_tagged}
- **Manual corrections applied**: {stats['manual_applied']}
- **Failed saves retried**: {stats['retries_succeeded']} succeeded, {stats['retries_failed']} failed
- **API error retries**: {stats['api_retries_succeeded']} succeeded, {stats['api_retries_failed']} failed
- **Variant inconsistencies found**: {len(stats['inconsistencies_found'])}
- **Variant fixes applied**: {stats['variant_fixes']}
- **Remaining inconsistencies**: {len(remaining_inconsistencies)}
- **Skipped (not consumable)**: {stats['skipped_cleaning']}

## Updated Strain Type Distribution
| Strain | Count |
|--------|-------|
| Sativa | {stats['strain_counts'].get('Sativa', 0)} |
| Indica | {stats['strain_counts'].get('Indica', 0)} |
| Hybrid | {stats['strain_counts'].get('Hybrid', 0)} |
| N/A | {stats['strain_counts'].get('N/A', 0)} |

## Updated Effect Tag Distribution
| Effect | Count |
|--------|-------|
| Energy | {stats['effect_counts'].get('Energy', 0)} |
| Sleep | {stats['effect_counts'].get('Sleep', 0)} |
| Relax | {stats['effect_counts'].get('Relax', 0)} |
| Focus | {stats['effect_counts'].get('Focus', 0)} |

## All Corrections Applied
| # | Product | Strain | Effect | Source |
|---|---------|--------|--------|--------|
"""
        for idx, c in enumerate(stats["all_corrections"], 1):
            report += f"| {idx} | {c['name']} | {c['strain']} | {c['effect']} | {c['source']} |\n"

        if stats["inconsistencies_fixed"]:
            report += f"\n## Variant Inconsistencies Fixed\n"
            report += "| Product | Old Type/Effect | New Type/Effect |\n|---------|----------------|----------------|\n"
            for fix in stats["inconsistencies_fixed"]:
                report += f"| {fix['name']} | {fix['old_type']}/{fix['old_effect']} | {fix['new_type']}/{fix['new_effect']} |\n"

        if remaining_inconsistencies:
            report += f"\n## Remaining Inconsistencies (need manual review)\n"
            for inc in remaining_inconsistencies:
                report += f"\n### {inc['strain']}\n"
                report += "| Product | Effect | Type |\n|---------|--------|------|\n"
                for v in inc["variants"]:
                    report += f"| {v['name']} | {v['effect']} | {v['type']} |\n"
        else:
            report += "\n## Consistency Check: PASSED\nAll product variants now have uniform strain/effect tags across all sizes.\n"

        with open("/home/ubuntu/strain_tagging_fix_report.md", "w") as f:
            f.write(report)

        print(f"\n{'='*60}")
        print(f"COMPLETE!")
        print(f"  Manual corrections: {stats['manual_applied']}")
        print(f"  Retries: {stats['retries_succeeded']} ok, {stats['retries_failed']} fail")
        print(f"  API retries: {stats['api_retries_succeeded']} ok, {stats['api_retries_failed']} fail")
        print(f"  Variant fixes: {stats['variant_fixes']}")
        print(f"  Remaining inconsistencies: {len(remaining_inconsistencies)}")
        print(f"Report saved to /home/ubuntu/strain_tagging_fix_report.md")


if __name__ == "__main__":
    asyncio.run(main())
