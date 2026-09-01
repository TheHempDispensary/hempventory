import os
import tempfile

os.environ.setdefault("DB_PATH", os.path.join(tempfile.gettempdir(), "thd_transfer_test.db"))

from app.routers import inventory_router as inv


def item(item_id, sku, name, qty):
    return {"id": item_id, "sku": sku, "name": name, "itemStock": {"quantity": qty}}


def test_resolves_every_copy_of_a_row_highest_stock_first():
    items = [
        item("a", "702575439857", "DELTA 8 THC WAX THREE GRAMS", 0),
        item("b", "702575439857", "DELTA 8 THC WAX THREE GRAMS BATCH 01182515", 9),
        item("c", "702575439857", "DELTA 8 THC WAX THREE GRAMS", 4),
        item("d", "111111111111", "DELTA 8 THC WAX ONE GRAM", 7),
    ]
    resolved = inv._resolve_location_items(items, "702575439857", "DELTA 8 THC WAX THREE GRAMS")
    assert [i["id"] for i in resolved] == ["b", "c", "a"]
    assert sum(inv._item_stock(i) for i in resolved) == 13


def test_resolves_by_clover_id_when_item_has_no_sku():
    items = [
        item("w0rg", "", "THC DISPOSABLE VAPE ONE GRAM", 0),
        item("x9kd", "", "THC DISPOSABLE VAPE ONE GRAM BATCH 44", 25),
        item("z1", "", "THC GUMMIES", 3),
    ]
    resolved = inv._resolve_location_items(items, "w0rg", "THC DISPOSABLE VAPE ONE GRAM")
    assert [i["id"] for i in resolved] == ["x9kd", "w0rg"]


def test_does_not_mix_copies_that_differ_by_sku():
    items = [
        item("a", "111", "THC GUMMIES 100MG", 2),
        item("b", "222", "THC GUMMIES 100MG", 30),
    ]
    resolved = inv._resolve_location_items(items, "111", "THC GUMMIES 100MG")
    assert [i["id"] for i in resolved] == ["a"]


def test_unknown_item_resolves_to_nothing():
    assert inv._resolve_location_items([item("a", "111", "THC GUMMIES", 2)], "999", "OTHER") == []
