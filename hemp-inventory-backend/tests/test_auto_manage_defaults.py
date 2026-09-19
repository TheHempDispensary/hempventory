import os
import tempfile

os.environ.setdefault("DB_PATH", os.path.join(tempfile.gettempdir(), "thd_auto_manage_test.db"))

from app.routers.inventory_router import ItemCreate, ItemGroupCreate


def test_item_create_defaults_to_auto_manage():
    item = ItemCreate(name="Test item", price=100)

    assert item.auto_manage is True


def test_item_group_create_defaults_to_auto_manage():
    group = ItemGroupCreate(
        name="Test group",
        price=100,
        variants=[{"attribute_name": "Size", "option_names": ["Small"]}],
    )

    assert group.auto_manage is True
